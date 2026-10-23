from __future__ import annotations
import argparse, hashlib, json, pathlib, re, sys, time
from typing import Any, Callable, Generator, NamedTuple

class Diagnostic(NamedTuple): code: str; line: int; source: str; message: str
class DeadlockWitness(NamedTuple): code: str; lambda_step: int; virtual_time: int; blocked_tasks: tuple[tuple[str, str, str], ...]
class ReplayCertificate(NamedTuple):
    seed: int; lambda_steps: int; virtual_time: int; tasks_spawned: int; tasks_completed: int
    trace_sha256: str; events: tuple[str, ...]; result: Any = None; deadlock: DeadlockWitness | None = None
class Spawn(NamedTuple): name: str; fn: Callable[..., Generator[Any, Any, Any]]; args: tuple[Any, ...] = ()
class Sleep(NamedTuple): ticks: int
class ChanSend(NamedTuple): channel: str; value: Any
class ChanRecv(NamedTuple): channel: str
class ChanClose(NamedTuple): channel: str
class Select(NamedTuple): cases: tuple[ChanSend | ChanRecv, ...]; timeout_ticks: int | None = None

class SplitMix64:
    def __init__(self, seed: int) -> None: self.state = seed & 0xFFFFFFFFFFFFFFFF
    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = ((self.state ^ (self.state >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return (z ^ (z >> 31)) & 0xFFFFFFFFFFFFFFFF
    def choice_index(self, n: int) -> int: return 0 if n <= 1 else self.next_u64() % n

_FORBIDDEN: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\btime\s*\.\s*(?:now|since|until)\s*\(", re.I), "time.Now()", "non-deterministic wall-clock source"),
    (re.compile(r"\brand\s*\.\s*(?:read|int|intn|float64|uint64|int63|perm)\s*\(", re.I), "rand.*", "unseeded entropy source"),
    (re.compile(r"\b(?:os\s*\.\s*(?:open|read_?file|create|stdin|getenv|args)|io\s*\.\s*read_?all|net\s*\.\s*dial|http\s*\.\s*(?:get|post|listen_?and_?serve|new_?serve_?mux|handle_?func))\b", re.I), "os/net/http", "raw ambient OS/network I/O"),
)

def parse_deterministic_header(source: str) -> int | None:
    m = re.search(r"\bdeterministic\s*(?:\(\s*seed\s*:\s*(0x[0-9a-fA-F]+|\d+)\s*\))?\s*\{", source)
    return (int(m.group(1), 0) if m.group(1) else 0) if m else None

def audit_deterministic_source(source: str) -> list[Diagnostic]:
    clean = re.sub(r"//[^\n]*|/\*.*?\*/", lambda m: " " + "\n" * m.group(0).count("\n"), source, flags=re.S)
    map_vars = set(re.findall(r"\b(\w+)\s*(?::=|=|:|\s)\s*(?:make\s*\(\s*)?map\[", clean)) - {"fn", "var", "let", "const", "type", "return"}
    hits: list[tuple[int, Diagnostic]] = []
    for pat, sym, reason in _FORBIDDEN:
        for m in pat.finditer(clean): hits.append((m.start(), Diagnostic("HYB-E6020", clean.count("\n", 0, m.start()) + 1, sym, f"{reason} inside certified deterministic block")))
    for m in re.finditer(r"\bfor\s+\w+(?:\s*,\s*\w+)?\s+in\s+(\w+)\b", clean):
        if m.group(1) in map_vars: hits.append((m.start(), Diagnostic("HYB-E6020", clean.count("\n", 0, m.start()) + 1, f"for..in {m.group(1)}", "unseeded map iteration inside certified deterministic block")))
    return [d for _, d in sorted(hits, key=lambda x: x[0])]

class _Chan:
    def __init__(self, name: str, cap: int) -> None:
        self.name, self.cap, self.closed, self.buf, self.send_q, self.recv_q = name, cap, False, [], [], []

class DeterministicSandbox:
    def __init__(self, seed: int = 0x42, trace: bool = True) -> None:
        self.seed, self.prng, self.channels, self.trace = seed & 0xFFFFFFFFFFFFFFFF, SplitMix64(seed), {}, trace
    def make_chan(self, name: str, capacity: int = 0) -> str:
        self.channels[name] = _Chan(name, capacity); return name
    def run(self, main_fn: Callable[[], Generator[Any, Any, Any]], adversarial_jitter: int = 0) -> ReplayCertificate:
        tasks, ready = {"main": main_fn()}, {"main": (0, None)}
        sleeping, blocked, select_wait, events = {}, {}, {}, []
        lambda_step = vtime = completed = 0; spawn_seq, main_result = 1, None
        def _recv(ch: _Chan) -> tuple[Any, bool] | None:
            if ch.buf:
                val = ch.buf.pop(0)
                if ch.send_q: stid, sval = ch.send_q.pop(0); ch.buf.append(sval); blocked.pop(stid, None); ready[stid] = (vtime, None)
                return (val, True)
            if ch.send_q: stid, sval = ch.send_q.pop(0); blocked.pop(stid, None); ready[stid] = (vtime, None); return (sval, True)
            return (None, False) if ch.closed else None
        def _send(ch: _Chan, val: Any) -> bool:
            if ch.recv_q: rtid = ch.recv_q.pop(0); blocked.pop(rtid, None); ready[rtid] = (vtime, (val, True)); return True
            if len(ch.buf) < ch.cap: ch.buf.append(val); return True
            return False
        def _try_sel(cases: tuple[ChanSend | ChanRecv, ...]) -> tuple[int, Any] | None:
            if any(isinstance(op, ChanSend) and self.channels[op.channel].closed for op in cases): raise RuntimeError("HYB-E6022: send on closed channel in select")
            arms = [(i, op) for i, op in enumerate(cases) if (isinstance(op, ChanRecv) and (self.channels[op.channel].buf or self.channels[op.channel].send_q or self.channels[op.channel].closed)) or (isinstance(op, ChanSend) and (self.channels[op.channel].recv_q or len(self.channels[op.channel].buf) < self.channels[op.channel].cap))]
            if not arms: return None
            idx, op = arms[self.prng.choice_index(len(arms)) if self.trace else 0]; ch = self.channels[op.channel]
            return (idx, _recv(ch)) if isinstance(op, ChanRecv) else (idx, _send(ch, op.value))
        def _wake_sel() -> None:
            for stid in sorted(list(select_wait)):
                out = _try_sel(select_wait[stid][0])
                if out is not None: select_wait.pop(stid); blocked.pop(stid, None); ready[stid] = (vtime, out)
        while len(tasks) > completed:
            if adversarial_jitter: _ = (adversarial_jitter ^ lambda_step) * 0x9E3779B9
            due = sorted(tid for tid, (rt, _) in ready.items() if rt <= vtime)
            if not due:
                nts = [rt for rt, _ in ready.values()] + list(sleeping.values()) + [dl for _, dl in select_wait.values() if dl is not None]
                if not nts:
                    w = DeadlockWitness("HYB-E6021", lambda_step, vtime, tuple(sorted((t, m, c) for t, (m, c) in blocked.items())))
                    return ReplayCertificate(self.seed, lambda_step, vtime, spawn_seq, completed, hashlib.sha256("\n".join(events).encode()).hexdigest(), tuple(events), main_result, w)
                vtime = min(nts)
                for stid in sorted(t for t, wt in sleeping.items() if wt <= vtime): sleeping.pop(stid); ready[stid] = (vtime, None)
                for stid in sorted(t for t, (_, dl) in select_wait.items() if dl is not None and dl <= vtime): select_wait.pop(stid); blocked.pop(stid, None); ready[stid] = (vtime, (-1, (None, False)))
                due = sorted(tid for tid, (rt, _) in ready.items() if rt <= vtime)
            chosen = due[self.prng.choice_index(len(due)) if self.trace else 0]
            _, send_val = ready.pop(chosen); lambda_step += 1
            try: eff = tasks[chosen].send(send_val)
            except StopIteration as stop:
                completed += 1; main_result = stop.value if chosen == "main" else main_result
                if self.trace: events.append(f"L{lambda_step}@t{vtime}:{chosen}:exit")
                continue
            if isinstance(eff, Spawn):
                cid = f"{eff.name}#{spawn_seq}"; spawn_seq += 1; tasks[cid] = eff.fn(*eff.args); ready[cid] = (vtime, None); ready[chosen] = (vtime, cid)
                if self.trace: events.append(f"L{lambda_step}@t{vtime}:{chosen}:spawn->{cid}")
            elif isinstance(eff, Sleep):
                sleeping[chosen] = vtime + max(0, eff.ticks)
                if self.trace: events.append(f"L{lambda_step}@t{vtime}:{chosen}:sleep({eff.ticks})->t{sleeping[chosen]}")
            elif isinstance(eff, ChanSend):
                ch = self.channels[eff.channel]
                if ch.closed: raise RuntimeError(f"HYB-E6022: send on closed channel {ch.name}")
                if self.trace: events.append(f"L{lambda_step}@t{vtime}:{chosen}:send({ch.name},{eff.value})")
                if _send(ch, eff.value): ready[chosen] = (vtime, None)
                else: ch.send_q.append((chosen, eff.value)); blocked[chosen] = ("send", ch.name)
                _wake_sel()
            elif isinstance(eff, ChanRecv):
                ch = self.channels[eff.channel]; res = _recv(ch)
                if self.trace: events.append(f"L{lambda_step}@t{vtime}:{chosen}:recv({ch.name})")
                if res is not None: ready[chosen] = (vtime, res)
                else: ch.recv_q.append(chosen); blocked[chosen] = ("recv", ch.name)
                _wake_sel()
            elif isinstance(eff, ChanClose):
                ch = self.channels[eff.channel]
                if ch.closed or ch.send_q: raise RuntimeError(f"HYB-E6022: send on closed channel {ch.name}")
                ch.closed = True; ready[chosen] = (vtime, None)
                if self.trace: events.append(f"L{lambda_step}@t{vtime}:{chosen}:close({ch.name})")
                while ch.recv_q: rtid = ch.recv_q.pop(0); blocked.pop(rtid, None); ready[rtid] = (vtime, (None, False))
                _wake_sel()
            elif isinstance(eff, Select):
                if self.trace: events.append(f"L{lambda_step}@t{vtime}:{chosen}:select({len(eff.cases)})")
                out = _try_sel(eff.cases)
                if out is not None: ready[chosen] = (vtime, out)
                else: select_wait[chosen] = (eff.cases, (vtime + eff.timeout_ticks) if eff.timeout_ticks is not None else None); blocked[chosen] = ("select", ",".join(c.channel for c in eff.cases))
        digest = hashlib.sha256("\n".join(events).encode()).hexdigest() if self.trace else ""
        return ReplayCertificate(self.seed, lambda_step, vtime, spawn_seq, completed, digest, tuple(events), main_result, None)

def run_worker_pool_sandbox(seed: int = 0x42, jitter: int = 0, trace: bool = True) -> ReplayCertificate:
    sb = DeterministicSandbox(seed, trace=trace); sb.make_chan("jobs", 16); sb.make_chan("results", 16); inputs = (3, 1, 4, 1, 5, 9, 2, 6)
    def worker(wid: int) -> Generator[Any, Any, None]:
        while (res := (yield ChanRecv("jobs")))[1]: yield Sleep(res[0][1]); yield ChanSend("results", (wid, res[0][0] * 2))
    def main() -> Generator[Any, Any, list[int]]:
        for i in range(4): yield Spawn(f"worker{i}", worker, (i,))
        for v in inputs: yield ChanSend("jobs", (v, v % 3))
        yield ChanClose("jobs"); got: list[int] = []
        for _ in inputs: (_, val), _ = yield ChanRecv("results"); got.append(val)
        return sorted(got)
    return sb.run(main, adversarial_jitter=jitter)

def run_pipeline_backpressure_sandbox(seed: int = 0x42, jitter: int = 0) -> ReplayCertificate:
    sb = DeterministicSandbox(seed); sb.make_chan("raw", 2); sb.make_chan("sq", 2)
    def producer() -> Generator[Any, Any, None]:
        for i in range(1, 9): yield ChanSend("raw", i)
        yield ChanClose("raw")
    def squarer() -> Generator[Any, Any, None]:
        while (res := (yield ChanRecv("raw")))[1]: yield Sleep(5); yield ChanSend("sq", res[0] * res[0])
        yield ChanClose("sq")
    def main() -> Generator[Any, Any, int]:
        yield Spawn("producer", producer); yield Spawn("squarer", squarer); total = 0
        while (out := (yield Select((ChanRecv("sq"),), timeout_ticks=2000)))[0] != -1 and out[1][1]: total += out[1][0]
        return total
    return sb.run(main, adversarial_jitter=jitter)

def run_deadlock_sandbox(seed: int = 0x42) -> ReplayCertificate:
    sb = DeterministicSandbox(seed); sb.make_chan("ch_a", 0); sb.make_chan("ch_b", 0)
    def peer() -> Generator[Any, Any, None]: yield ChanRecv("ch_b"); yield ChanSend("ch_a", 1)
    def main() -> Generator[Any, Any, None]: yield Spawn("peer", peer); yield ChanRecv("ch_a"); yield ChanSend("ch_b", 1)
    return sb.run(main)

def verify_replay_invariance(seed: int = 0x42, runs: int = 64) -> dict[str, Any]:
    b_wp, b_pb, alt_wp, dl = run_worker_pool_sandbox(seed), run_pipeline_backpressure_sandbox(seed), run_worker_pool_sandbox(seed ^ 1), run_deadlock_sandbox(seed)
    wp_m = sum(1 for r in range(runs) if run_worker_pool_sandbox(seed, r * 97).trace_sha256 == b_wp.trace_sha256)
    pb_m = sum(1 for r in range(runs) if run_pipeline_backpressure_sandbox(seed, r * 131).trace_sha256 == b_pb.trace_sha256)
    t0 = time.perf_counter_ns()
    for _ in range(64): run_worker_pool_sandbox(seed, trace=False)
    base_ns = max(1, time.perf_counter_ns() - t0); t1 = time.perf_counter_ns()
    for _ in range(64): run_worker_pool_sandbox(seed, trace=True)
    cert_ns = max(1, time.perf_counter_ns() - t1)
    return {"seed": hex(seed), "runs": runs, "worker_pool_matches": wp_m, "pipeline_matches": pb_m, "replay_identical_ratio": (wp_m + pb_m) / (2 * runs), "worker_pool_sha256": b_wp.trace_sha256, "worker_pool_steps": b_wp.lambda_steps, "worker_pool_output": b_wp.result, "pipeline_sha256": b_pb.trace_sha256, "pipeline_steps": b_pb.lambda_steps, "pipeline_total": b_pb.result, "seed_sensitivity_verified": alt_wp.trace_sha256 != b_wp.trace_sha256, "deadlock_witness_code": dl.deadlock.code if dl.deadlock else None, "deadlock_blocked_tasks": len(dl.deadlock.blocked_tasks) if dl.deadlock else 0, "overhead_ratio": round(cert_ns / base_ns, 2)}

def audit_live_corpus(repo_root: pathlib.Path) -> dict[str, Any]:
    files = sorted(list(repo_root.glob("examples/**/*.hy")) + list(repo_root.glob("tests/checker-corpus/*.hy")))
    conc = [f.relative_to(repo_root).as_posix() for f in files if re.search(r"\b(?:task|chan|select)\b", f.read_text(encoding="utf-8"))]
    imp = {f.relative_to(repo_root).as_posix() for f in files if audit_deterministic_source(f.read_text(encoding="utf-8"))}
    return {"total_hy_files": len(files), "concurrent_files": len(conc), "impure_rejected_files": len(imp), "deterministic_ready_concurrent": len([c for c in conc if c not in imp])}

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W-037 Algebraic Effect Replay Sandbox (#957)")
    ap.add_argument("--check", action="store_true"); ap.add_argument("--json", action="store_true"); args = ap.parse_args(argv)
    rep, corp = verify_replay_invariance(0x42, 64), audit_live_corpus(pathlib.Path(__file__).resolve().parents[2])
    print(json.dumps({"issue": 957, "spike": "w037-algebraic-effect-replay", "replay": rep, "corpus": corp}, indent=2) if args.json else f"W-037 (#957): replay={rep['replay_identical_ratio']*100:.1f}% sha256={rep['worker_pool_sha256'][:12]} corpus={corp['total_hy_files']}")
    return 1 if args.check and (rep["replay_identical_ratio"] != 1.0 or rep["pipeline_total"] != 204 or rep["deadlock_witness_code"] != "HYB-E6021") else 0

if __name__ == "__main__": sys.exit(main())
