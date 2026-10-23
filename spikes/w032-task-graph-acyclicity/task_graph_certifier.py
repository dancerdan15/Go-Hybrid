#!/usr/bin/env python3
"""W-032 (#950): Certified Task-Graph Acyclicity & Topological Join Certificates (HYB-E7001)."""
from __future__ import annotations

import argparse, json, re, sys
from dataclasses import asdict, dataclass
from pathlib import Path

CORPUS_FIXTURES = (
    "15-fanin-per-task-state.hy",
    "16-move-send-clean.hy",
    "17-atomic-channel-immutable.hy",
    "33-select-send-safe-values.hy",
)


@dataclass(frozen=True)
class WaitEdge:
    src: str
    dst: str
    via: str
    kind: str


@dataclass(frozen=True)
class TaskSpec:
    name: str
    sends: tuple[str, ...] = ()
    recvs: tuple[str, ...] = ()
    joins: tuple[str, ...] = ()
    select_nonblocking: bool = False
    dynamic_spawn: bool = False
    explicit_epoch: int | None = None
    loc: int = 12


@dataclass(frozen=True)
class CertResult:
    case_id: str
    verdict: str
    diagnostic: str | None
    epochs: dict[str, int]
    topo_order: tuple[str, ...]
    cycle_witness: tuple[str, ...]
    edges: tuple[WaitEdge, ...]
    annotation_lines: int
    loc: int


def build_wait_graph(tasks: list[TaskSpec], unbuffered: set[str] | None = None) -> tuple[dict[str, list[WaitEdge]], list[WaitEdge]]:
    unbuf, adj = unbuffered or set(), {t.name: [] for t in tasks}
    senders, recvers, by_name, all_edges = {}, {}, {t.name: t for t in tasks}, []
    for t in tasks:
        for ch in t.sends:
            senders.setdefault(ch, []).append(t.name)
        for ch in t.recvs:
            recvers.setdefault(ch, []).append(t.name)
    def add_edge(src: str, dst: str, via: str, kind: str) -> None:
        edge = WaitEdge(src, dst, via, kind)
        adj[src].append(edge)
        all_edges.append(edge)
    for t in tasks:
        for target in (j for j in t.joins if j in adj):
            add_edge(t.name, target, f"wait({target})", "join")
        for ch in t.recvs if not t.select_nonblocking else ():
            srcs = [s for s in senders.get(ch, []) if s != t.name]
            if not srcs and ch not in t.sends:
                add_edge(t.name, t.name, ch, "orphan_recv")
            for s in srcs:
                add_edge(t.name, s, ch, "chan")
        for ch in t.sends:
            if (ch in unbuf or ch.startswith(("unbuf_", "latch_", "sync_"))) and not [r for r in recvers.get(ch, []) if r != t.name]:
                add_edge(t.name, t.name, ch, "orphan_send")
        for edge in list(adj[t.name]):
            dst_task = by_name.get(edge.dst)
            if t.explicit_epoch is not None and dst_task and dst_task.explicit_epoch is not None and t.explicit_epoch <= dst_task.explicit_epoch and edge.src != edge.dst:
                add_edge(edge.dst, edge.src, f"epoch({t.explicit_epoch}<={dst_task.explicit_epoch})", "epoch")
    return adj, all_edges


def find_canonical_cycle(nodes: list[str], adj: dict[str, list[WaitEdge]]) -> tuple[str, ...]:
    for u in sorted(nodes):
        if any(e.dst == u for e in adj.get(u, [])):
            return (u, u)
        queue = [(e.dst, (u, e.dst)) for e in sorted(adj.get(u, []), key=lambda x: x.dst)]
        seen = {e.dst for e in adj.get(u, [])}
        while queue:
            curr, path = queue.pop(0)
            for nxt in sorted(adj.get(curr, []), key=lambda x: x.dst):
                if nxt.dst == u:
                    return (*path, u)
                if nxt.dst not in seen:
                    seen.add(nxt.dst)
                    queue.append((nxt.dst, (*path, nxt.dst)))
    return ()


def kahn_topological_epochs(nodes: list[str], adj: dict[str, list[WaitEdge]]) -> tuple[tuple[str, ...], dict[str, int]]:
    out_deg = {u: len({e.dst for e in adj.get(u, []) if e.dst != u}) for u in nodes}
    rev: dict[str, list[str]] = {u: [] for u in nodes}
    for u in nodes:
        for d in {e.dst for e in adj.get(u, []) if e.dst != u}:
            rev[d].append(u)
    ready, order, epochs = sorted(u for u, deg in out_deg.items() if deg == 0), [], {u: 0 for u in nodes}
    while ready:
        u = ready.pop(0)
        order.append(u)
        for dep in sorted(rev[u]):
            epochs[dep] = max(epochs[dep], epochs[u] + 1)
            out_deg[dep] -= 1
            if out_deg[dep] == 0:
                ready = sorted([*ready, dep])
    return tuple(order), epochs


def certify_task_graph(case_id: str, tasks: list[TaskSpec], unbuffered: set[str] | None = None) -> CertResult:
    nodes, ann, loc = [t.name for t in tasks], sum(1 for t in tasks if t.explicit_epoch is not None), sum(t.loc for t in tasks)
    adj, edges = build_wait_graph(tasks, unbuffered)
    if any(t.dynamic_spawn for t in tasks):
        return CertResult(case_id, "REJECT-UNBOUNDED", "HYB-E7002", {}, (), (), tuple(edges), ann, loc)
    if cycle := find_canonical_cycle(nodes, adj):
        return CertResult(case_id, "REJECT-CYCLE", "HYB-E7001", {}, (), cycle, tuple(edges), ann, loc)
    order, epochs = kahn_topological_epochs(nodes, adj)
    return CertResult(case_id, "ACCEPT-CERTIFIED", None, epochs, order, (), tuple(edges), ann, loc)


def parse_hy_task_graph(source: str, case_id: str = "inline") -> CertResult:
    code = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
    matches = list(re.finditer(r"fn\s+(\w+)\s*\(([^)]*)\)\s*(?:[\w\[\]\*]+\s*)?\{", code))
    spawned, fn_bodies, tasks = {"main"}, {}, []
    for idx, m in enumerate(matches):
        body = code[m.end() : matches[idx + 1].start() if idx + 1 < len(matches) else len(code)]
        spawned.update(re.findall(r"\btask\s+(\w+)\s*\(", body))
        fn_bodies[m.group(1)] = (m.group(2), body, max(4, body.count("\n")))
    for name in sorted(spawned & set(fn_bodies)):
        params, body, loc = fn_bodies[name]
        chans = set(re.findall(r"\b(\w+)\s*:=\s*make\(\s*chan\b", body)) | set(re.findall(r"\b(\w+)\s+(?:<-chan|chan<-|chan\b)", params))
        sends = tuple(sorted(c for c in chans if re.search(rf"\b{c}\s*<-", body)))
        recvs = tuple(sorted(c for c in chans if re.search(rf"(?:^|[={{(\s;])\s*<-\s*{c}\b|\bfor\s+\w+\s+in\s+{c}\b", body)))
        aliases = dict(re.findall(r"\b(\w+)\s*:=\s*task\s+(\w+)\s*\(", body))
        joins = tuple(sorted({aliases.get(w, w) for w in re.findall(r"\btask\.wait\(\s*(\w+)\s*\)", body)}))
        has_select = bool(re.search(r"\bselect\s*\{", body))
        if sends and recvs and re.search(r"\bclose\(\s*\w+\s*\)", body):
            tasks.append(TaskSpec(f"{name}@send", sends=sends, select_nonblocking=has_select, loc=loc // 2))
            tasks.append(TaskSpec(f"{name}@join", recvs=recvs, joins=(*joins, f"{name}@send"), select_nonblocking=has_select, loc=loc - loc // 2))
        else:
            tasks.append(TaskSpec(name, sends, recvs, joins, has_select, False, None, loc))
    return certify_task_graph(case_id, tasks)


def benchmark_postmortems() -> list[CertResult]:
    cases: list[tuple[str, list[TaskSpec], set[str]]] = [
        ("k8s_45612_unbuffered_leak", [TaskSpec("caller", recvs=("ctx_done",), select_nonblocking=True), TaskSpec("worker", sends=("unbuf_res",))], {"unbuf_res"}),
        ("docker_24810_container_lock_cycle", [TaskSpec("daemon", sends=("exec_req",), recvs=("state_ack",)), TaskSpec("monitor", sends=("state_ack",), recvs=("exec_req",))], set()),
        ("cockroach_latch_three_way_cycle", [TaskSpec("txn_a", joins=("txn_b",)), TaskSpec("txn_b", joins=("txn_c",)), TaskSpec("txn_c", joins=("txn_a",))], set()),
        ("etcd_raft_apply_wait_cycle", [TaskSpec("applier", sends=("status_ch",), joins=("follower",)), TaskSpec("follower", recvs=("status_ch",))], set()),
        ("grpc_stream_quota_bidirectional_wait", [TaskSpec("client_stream", sends=("quota_ch",), recvs=("window_ch",)), TaskSpec("server_stream", sends=("window_ch",), recvs=("quota_ch",))], set()),
        ("nats_fanout_join_inversion", [TaskSpec("dispatcher", joins=("acker",), explicit_epoch=1), TaskSpec("acker", explicit_epoch=2)], set()),
        ("select_worker_cancel_idiom", [TaskSpec("supervisor", sends=("jobs",)), TaskSpec("worker", sends=("results",), recvs=("jobs", "ctx_done"), select_nonblocking=True)], set()),
        ("staged_fanin_pipeline_dag", [TaskSpec("producer", sends=("raw",)), TaskSpec("worker", sends=("out",), recvs=("raw",)), TaskSpec("merger", recvs=("out",), joins=("worker",))], set()),
    ]
    return [certify_task_graph(cid, specs, unbuf) for cid, specs, unbuf in cases]


def audit_repo(repo_root: Path) -> dict[str, object]:
    g_path, c_dir = repo_root / "GUARANTEES.md", repo_root / "tests" / "checker-corpus"
    guarantees = g_path.read_text(encoding="utf-8") if g_path.is_file() else ""
    row16_no_claim = "| 16 |" in guarantees and "**No claim**" in guarantees
    corpus_results = [parse_hy_task_graph((c_dir / fn).read_text(encoding="utf-8"), fn) for fn in CORPUS_FIXTURES if (c_dir / fn).is_file()]
    postmortems = benchmark_postmortems()
    cycles, controls = postmortems[:6], postmortems[6:]
    total_loc = sum(r.loc for r in corpus_results + controls)
    ann_per_50 = round((sum(r.annotation_lines for r in corpus_results + controls) / max(1, total_loc)) * 50.0, 4)
    return {
        "row16_baseline_no_claim": row16_no_claim,
        "corpus_certified": sum(r.verdict == "ACCEPT-CERTIFIED" for r in corpus_results),
        "corpus_total": len(CORPUS_FIXTURES),
        "cycles_caught": sum(r.verdict == "REJECT-CYCLE" and r.diagnostic == "HYB-E7001" for r in cycles),
        "cycles_total": len(cycles),
        "controls_accepted": sum(r.verdict == "ACCEPT-CERTIFIED" for r in controls),
        "annotation_lines_per_50_loc": ann_per_50,
        "select_false_positives": sum(r.verdict != "ACCEPT-CERTIFIED" for r in corpus_results + controls),
        "falsifiers_passed": row16_no_claim and len(corpus_results) == len(CORPUS_FIXTURES) and ann_per_50 <= 1.0 and all(r.verdict == "REJECT-CYCLE" for r in cycles) and all(r.verdict == "ACCEPT-CERTIFIED" for r in corpus_results + controls),
        "corpus_verdicts": [asdict(r) for r in corpus_results],
        "postmortem_verdicts": [asdict(r) for r in postmortems],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W-032 Certified Task-Graph Acyclicity spike")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = audit_repo(args.repo_root)
    print(json.dumps(report, indent=2) if args.json else f"W-032: corpus={report['corpus_certified']}/{report['corpus_total']} cycles={report['cycles_caught']}/{report['cycles_total']} ann/50loc={report['annotation_lines_per_50_loc']} pass={report['falsifiers_passed']}")
    return 0 if (not args.check or report["falsifiers_passed"]) else 1

if __name__ == "__main__":
    sys.exit(main())
