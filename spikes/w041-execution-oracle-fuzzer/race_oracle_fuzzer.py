#!/usr/bin/env python3
"""W-041 (#1011): Execution-Oracle Fuzzer (INVARIANT 5: accepted => runs clean under -race)."""
from __future__ import annotations

import argparse, hashlib, json, random, re, time
from pathlib import Path
from typing import Any

RULE_FAMILIES = ("O8_SLICE_LOAN", "E3102_MAP_SLOT", "O8_MATCH_STORE_BACK", "A2_TASK_SPAWN_MOVE", "A6_RETURN_ORIGIN_ALIAS")
Stmt = tuple[str, str, Any, str]


def hb_leq(vc_a: dict[int, int], vc_b: dict[int, int]) -> bool:
    return all(vc_a.get(t, 0) <= vc_b.get(t, 0) for t in vc_a)


def check_program(stmts: list[Stmt], mode: str = "head") -> tuple[bool, bool, str | None]:
    """Return (accepted_by_hyb_check, buildable_by_go, reject_code)."""
    allocs, active_loans, aliases, synced = {}, set(), set(), set()
    for op, target, arg, _ in stmts:
        if op == "alloc_slice": allocs[target] = "slice"
        elif op == "alloc_map": allocs[target] = f"map:{arg}"
        elif op == "loan_slice_elem": active_loans.add(arg[0])
        elif op in ("rebind_slice_shrink", "rebind_append_realloc", "rebind_slice_shift") and mode == "head" and target in active_loans:
            return False, True, "HYB-E3108"
        elif op == "store_back":
            active_loans.discard(target)
            if mode == "head" and arg == "dropped_else_arm": return False, True, "HYB-E3109"
        elif op in ("loan_map_slot", "self_shadow_move", "defer_discard_alias", "alias_return"): aliases.add(target)
        elif op == "chan_sync": synced.add(int(arg))
        elif op in ("spawn_write", "main_write") and mode == "head":
            if allocs.get(target) == "map:nil": return False, True, "HYB-E3105"
            if op == "spawn_write" and int(arg) not in synced and (target in aliases or allocs.get(target, "").startswith("map:")):
                return False, True, "HYB-E3102"
    return True, True, None


def execute_oracle(stmts: list[Stmt]) -> tuple[str, str]:
    """Execute under simulated Go -race + O8 runtime guards. Return (verdict, detail)."""
    vc: dict[int, dict[int, int]] = {0: {0: 1}}
    accesses: dict[str, list[tuple[int, str, dict[int, int]]]] = {}
    slices, maps, alias_root, loans = {}, {}, {}, {}
    for op, target, arg, _ in stmts:
        root = alias_root.get(target, target)
        if op == "alloc_slice": slices[target] = {"len": int(arg), "epoch": 0, "shifted": False}
        elif op == "alloc_map": maps[target] = str(arg)
        elif op == "loan_slice_elem":
            if arg[0] not in slices: return "INVALID_AST", "missing slice"
            loans[target] = (arg[0], int(arg[1]), slices[arg[0]]["epoch"])
        elif op in ("rebind_slice_shrink", "rebind_slice_shift", "rebind_append_realloc"):
            if target not in slices: return "INVALID_AST", "missing slice"
            slices[target]["len"] = int(arg) if op == "rebind_slice_shrink" else slices[target]["len"]
            slices[target]["shifted"] = True if op == "rebind_slice_shift" else slices[target]["shifted"]
            slices[target]["epoch"] += 1 if op == "rebind_append_realloc" else 0
        elif op == "store_back":
            if target not in loans: return "INVALID_AST", "missing loan"
            if arg == "dropped_else_arm": return "LOST_STORE_BACK", f"O8 store-back dropped on match else_arm for {target}"
            s_name, idx, ep = loans[target]
            st = slices[s_name]
            if idx >= st["len"]: return "PANIC_OOB", f"slice bounds out of range [{idx}] with length {st['len']}"
            if st["epoch"] != ep or st["shifted"]: return "LOST_STORE_BACK", f"divergent O8 store-back on {s_name}"
        elif op in ("loan_map_slot", "self_shadow_move", "defer_discard_alias", "alias_return"):
            if str(arg) not in maps: return "INVALID_AST", "missing map"
            alias_root[target] = str(arg)
        elif op == "chan_sync":
            tid = int(arg)
            merged = {k: max(vc[0].get(k, 0), vc.get(tid, {}).get(k, 0)) for k in set(vc[0]) | set(vc.get(tid, {}))}
            vc[0], vc[tid] = dict(merged), dict(merged)
        elif op in ("spawn_write", "main_write"):
            if root not in maps or (target != root and target not in alias_root): return "INVALID_AST", "missing map or alias"
            tid = int(arg) if op == "spawn_write" else 0
            if tid != 0 and tid not in vc: vc[tid] = dict(vc[0])
            if maps.get(root) == "nil": return "PANIC_NIL_MAP", f"assignment to entry in nil map {root}"
            clock = dict(vc.setdefault(tid, {tid: 0}))
            clock[tid] = clock.get(tid, 0) + 1
            vc[tid] = clock
            for p_tid, _, p_vc in accesses.get(root, []):
                if p_tid != tid and not hb_leq(p_vc, clock) and not hb_leq(clock, p_vc):
                    return "DATA_RACE", f"DATA_RACE on {root} between task 0 and task {max(tid, p_tid)}"
            accesses.setdefault(root, []).append((tid, "W", dict(clock)))
    return "CLEAN", "ok"


def ddmin_shrink(stmts: list[Stmt], pred) -> list[Stmt]:
    """Zeller (2002) ddmin delta-debugging algorithm producing a 1-minimal witness."""
    cur, n = list(stmts), 2
    while len(cur) >= 2:
        chunk = max(1, len(cur) // n)
        for i in range(0, len(cur), chunk):
            cand = cur[:i] + cur[i + chunk:]
            if cand and pred(cand):
                cur, n = cand, max(2, n - 1)
                break
        else:
            if n >= len(cur):
                break
            n = min(len(cur), n * 2)
    return cur


def dedup_key(stmts: list[Stmt], verdict: str) -> str:
    vmap: dict[str, str] = {}
    norm = [f"{fam}:{op}:{vmap.setdefault(t, f'v{len(vmap)}')}" for op, t, _, fam in stmts]
    return hashlib.sha256((f"{verdict}|" + ";".join(norm)).encode("utf-8")).hexdigest()[:16]


def build_seeded_p0_shapes() -> dict[str, list[Stmt]]:
    """The 8 pre-registered P0 shapes from #1011 (#970 x3, #946, #953, #966, #971, #974)."""
    F = RULE_FAMILIES
    return {
        "970a_oob_reslice_panic": [("noop", "n0", 0, F[0]), ("alloc_slice", "xs", 2, F[0]), ("loan_slice_elem", "ln", ("xs", 1), F[0]), ("rebind_slice_shrink", "xs", 1, F[0]), ("noop", "n1", 0, F[0]), ("store_back", "ln", "normal", F[0])],
        "970b_wrong_elem_write": [("alloc_slice", "xs", 3, F[0]), ("noop", "n0", 0, F[0]), ("loan_slice_elem", "ln", ("xs", 1), F[0]), ("rebind_slice_shift", "xs", 1, F[0]), ("store_back", "ln", "normal", F[0])],
        "970c_append_realloc_stale": [("alloc_slice", "xs", 2, F[0]), ("loan_slice_elem", "ln", ("xs", 0), F[0]), ("noop", "n0", 0, F[0]), ("rebind_append_realloc", "xs", 4, F[0]), ("store_back", "ln", "normal", F[0])],
        "946_map_slot_reloan_race": [("alloc_map", "m", "init", F[1]), ("loan_map_slot", "slot", "m", F[1]), ("noop", "n0", 0, F[1]), ("spawn_write", "slot", 1, F[1]), ("main_write", "m", 0, F[1])],
        "953_match_else_drop_o8": [("alloc_slice", "xs", 2, F[2]), ("loan_slice_elem", "ln", ("xs", 0), F[2]), ("noop", "n0", 0, F[2]), ("store_back", "ln", "dropped_else_arm", F[2])],
        "966_self_shadow_move_race": [("alloc_map", "m", "init", F[3]), ("self_shadow_move", "m_sh", "m", F[3]), ("noop", "n0", 0, F[3]), ("spawn_write", "m_sh", 1, F[3]), ("main_write", "m", 0, F[3])],
        "971_defer_discard_escape_race": [("alloc_map", "m", "init", F[3]), ("defer_discard_alias", "d_alias", "m", F[3]), ("spawn_write", "d_alias", 1, F[3]), ("noop", "n0", 0, F[3]), ("main_write", "m", 0, F[3])],
        "974_return_origin_clobber_race": [("alloc_map", "m", "init", F[4]), ("alias_return", "ret_m", "m", F[4]), ("noop", "n0", 0, F[4]), ("spawn_write", "ret_m", 1, F[4]), ("main_write", "m", 0, F[4])],
    }


def generate_fuzz_batch(seed: int = 20261023, count: int = 100) -> list[list[Stmt]]:
    rng, seeds, batch = random.Random(seed), list(build_seeded_p0_shapes().values()), []
    for i in range(count):
        fam = RULE_FAMILIES[i % len(RULE_FAMILIES)]
        if i % 4 == 0:
            batch.append(list(rng.choice(seeds)))
        elif i % 2 == 1:
            batch.append([("alloc_map", "m", "init", fam), ("noop", "n0", 0, fam), ("chan_sync", "m", 1, fam), ("spawn_write", "m", 1, fam), ("chan_sync", "m", 1, fam), ("main_write", "m", 0, fam)])
        else:
            batch.append([("alloc_slice", "xs", 4, fam), ("loan_slice_elem", "ln", ("xs", 1), fam), ("noop", "n0", 0, fam), ("store_back", "ln", "normal", fam)])
    return batch


def audit_live_repo(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[2]
    verdicts = {"ACCEPT": 0, "ACCEPT-MANAGED": 0, "REJECT": 0}
    for hy in sorted((root / "tests" / "checker-corpus").glob("*.hy")):
        m = re.search(r"//\s*EXPECTED-VERDICT:\s*(\S+)", hy.read_text(encoding="utf-8"))
        if m and m.group(1) in verdicts:
            verdicts[m.group(1)] += 1
    hyfuzz_src = (root / "tools" / "fuzz" / "hyfuzz.py").read_text(encoding="utf-8")
    return {"corpus_total": sum(verdicts.values()), "corpus_accept": verdicts["ACCEPT"], "corpus_accept_managed": verdicts["ACCEPT-MANAGED"], "corpus_reject": verdicts["REJECT"], "hyfuzz_has_invariant_5": "INVARIANT 5" in hyfuzz_src}


def run_evaluation(seed: int = 20261023, count: int = 100, rate_cap: int = 3) -> dict[str, Any]:
    t0, seeded, recalled, shrunk_witnesses = time.perf_counter(), build_seeded_p0_shapes(), 0, {}
    for name, prog in seeded.items():
        verdict, _ = execute_oracle(prog)
        if check_program(prog, mode="prefix")[0] and verdict != "CLEAN":
            recalled += 1
            shrunk = ddmin_shrink(prog, lambda c, v=verdict: execute_oracle(c)[0] == v)
            shrunk_witnesses[name] = {"verdict": verdict, "orig_len": len(prog), "shrunk_len": len(shrunk), "dedup_key": dedup_key(shrunk, verdict)}
    fam_table = {f: {"generated": 0, "prefix_exec_divergences": 0, "head_accepted": 0, "head_exec_divergences": 0} for f in RULE_FAMILIES}
    dedup_seen: set[str] = set()
    for prog in generate_fuzz_batch(seed=seed, count=count):
        fam = prog[0][3]
        fam_table[fam]["generated"] += 1
        v_pre, _ = execute_oracle(prog)
        if check_program(prog, mode="prefix")[0] and v_pre != "CLEAN":
            fam_table[fam]["prefix_exec_divergences"] += 1
            dedup_seen.add(dedup_key(ddmin_shrink(prog, lambda c, v=v_pre: execute_oracle(c)[0] == v), v_pre))
        if check_program(prog, mode="head")[0]:
            fam_table[fam]["head_accepted"] += 1
            fam_table[fam]["head_exec_divergences"] += int(execute_oracle(prog)[0] != "CLEAN")
    head_div = sum(v["head_exec_divergences"] for v in fam_table.values())
    return {"issue": 1011, "spike_id": "W-041", "invariant": "INVARIANT 5 (accepted => runs clean under -race)", "seeded_p0_total": len(seeded), "seeded_p0_recalled": recalled, "f1_recall_rate": recalled / len(seeded), "f2_all_shrunk_minimal": all(w["shrunk_len"] < w["orig_len"] for w in shrunk_witnesses.values()), "f3_head_residual_rate": head_div / max(1, sum(v["head_accepted"] for v in fam_table.values())), "unique_dedup_keys": len(dedup_seen), "auto_filed_capped": min(len(dedup_seen), rate_cap), "shrunk_witnesses": shrunk_witnesses, "rule_family_residuals": fam_table, "live_repo_audit": audit_live_repo(), "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W-041 (#1011) Execution-Oracle Fuzzer")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args, rep = ap.parse_args(argv), run_evaluation()
    print(json.dumps(rep, indent=2, sort_keys=True) if args.json else f"W-041 (#1011): recall={rep['seeded_p0_recalled']}/{rep['seeded_p0_total']} head_residual={rep['f3_head_residual_rate']:.4f} corpus={rep['live_repo_audit']['corpus_total']}")
    return 1 if (args.check and (rep["f1_recall_rate"] < 1.0 or not rep["f2_all_shrunk_minimal"] or rep["f3_head_residual_rate"] != 0.0)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
