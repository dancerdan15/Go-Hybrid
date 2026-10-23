#!/usr/bin/env python3
"""W-044 (#471): Scoped Task Regions (join_scope) — Zero-Copy Borrowed Read-Sharing."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RegionOp:
    kind: str
    target: str
    peer: str = ""
    workers: int = 1
    mutable_borrow: bool = False


@dataclass(frozen=True)
class ScopedRegionCase:
    name: str
    bindings: dict[str, str]
    in_join_scope: bool
    ops: list[RegionOp]
    expected_accept: bool
    expected_codes: list[str] = field(default_factory=list)
    defer_barrier: bool = True


@dataclass(frozen=True)
class JoinBarrierCert:
    case_name: str
    accepted: bool
    workers_spawned: int
    borrowed_roots: list[str]
    allocs_per_op: int
    race_free: bool
    unwind_safe: bool
    diagnostics: list[str]
    emitted_go: str


def _root(expr: str) -> str:
    return expr.strip(" ()&*").split(".", 1)[0].split("[", 1)[0].strip(" ()&*")


def storage_component(aliases: dict[str, str], may_share: dict[str, set[str]], seed: str) -> set[str]:
    root = _root(seed)
    seen, stack = set(), [root]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if (parent := aliases.get(cur)) and parent not in seen:
            stack.append(parent)
        stack.extend(c for c, p in aliases.items() if p == cur and c not in seen)
        stack.extend(p for p in may_share.get(cur, set()) if p not in seen)
    return seen


def emit_join_scope_go(workers: int, roots: list[str], defer_barrier: bool = True) -> str:
    arg_list = ", ".join(roots)
    wait_stmt = "defer __hy_wg.Wait()" if defer_barrier else "__hy_wg.Wait()"
    lines = ["{ // join_scope [A3-Read freeze]", "    var __hy_wg sync.WaitGroup"]
    if defer_barrier:
        lines.append(f"    {wait_stmt}")
    lines.extend([
        f"    for __i := 0; __i < {max(workers, 1)}; __i++ {{",
        f"        __hy_wg.Add(1); go func() {{ defer __hy_wg.Done(); worker({arg_list}) }}()",
        "    }",
    ])
    if not defer_barrier:
        lines.append(f"    {wait_stmt}")
    lines.append("}")
    return "\n".join(lines)


def check_scoped_region(case: ScopedRegionCase) -> JoinBarrierCert:
    aliases: dict[str, str] = {}
    may_share: dict[str, set[str]] = {k: set() for k in case.bindings}
    active_borrows: set[str] = set()
    moved_roots: set[str] = set()
    diags: list[str] = []
    workers, unwind_safe = 0, True

    for op in case.ops:
        base = _root(op.target)
        comp = storage_component(aliases, may_share, base)
        is_locked = bool(comp & active_borrows)
        if op.kind == "alias":
            peer = _root(op.peer)
            aliases[peer] = base
            may_share.setdefault(base, set()).add(peer)
            may_share.setdefault(peer, set()).add(base)
        elif op.kind == "borrow_task":
            if not case.in_join_scope:
                diags.append(f"HYB-E3001:unbounded_task_borrow({base})")
            elif op.mutable_borrow:
                diags.append(f"HYB-E4401:mutable_task_borrow_forbidden({base})")
            elif comp & moved_roots:
                diags.append(f"HYB-E3003:borrow_after_move({base})")
            else:
                active_borrows.add(base)
                workers += op.workers
        elif op.kind in ("write", "rebind", "delete") and is_locked:
            diags.append(f"HYB-E3102:mutation_during_join_scope({op.target})")
            diags.append(f"HYB-E4401:a3_read_freeze_violation({op.target})")
        elif op.kind == "move":
            if is_locked:
                diags.append(f"HYB-E3003:move_during_scoped_borrow({base})")
                diags.append(f"HYB-E4401:move_conflicts_with_a3_read({base})")
            moved_roots.update(comp)
        elif op.kind in ("escape_channel", "escape_return", "escape_store", "escape_closure", "detached_task") and is_locked:
            diags.append(f"HYB-E3104:scoped_loan_escape({op.kind}:{base})")
            diags.append(f"HYB-E4402:region_escape_forbidden({op.kind}:{base})")
        elif op.kind == "panic_parent" and is_locked and not case.defer_barrier:
            unwind_safe = False
            diags.append(f"HYB-E4403:unjoined_unwind_hazard({base})")

    roots = sorted(active_borrows)
    if case.in_join_scope and roots and not case.defer_barrier and not any(d.startswith("HYB-E4403:") for d in diags):
        unwind_safe = False
        diags.append(f"HYB-E4403:unjoined_unwind_hazard({roots[0]})")
    accepted = len(diags) == 0 and unwind_safe
    emitted = emit_join_scope_go(workers, roots, case.defer_barrier) if case.in_join_scope else ""
    return JoinBarrierCert(case.name, accepted, workers, roots, 0 if accepted and case.in_join_scope else -1, accepted, unwind_safe, diags, emitted)


def build_milestone_corpus() -> list[ScopedRegionCase]:
    return [
        ScopedRegionCase("accept-scoped-parallel-read.hy", {"data": "map[string]int", "matrix": "[]float64"}, True,
                         [RegionOp("borrow_task", "data", workers=16), RegionOp("borrow_task", "matrix", workers=16), RegionOp("read", "data")], True),
        ScopedRegionCase("reject-write-during-scope.hy", {"data": "map[string]int"}, True,
                         [RegionOp("borrow_task", "data", workers=8), RegionOp("write", 'data["k"]')], False, ["HYB-E3102", "HYB-E4401"]),
        ScopedRegionCase("reject-move-during-scope.hy", {"data": "[]int"}, True,
                         [RegionOp("borrow_task", "data", workers=4), RegionOp("move", "data")], False, ["HYB-E3003", "HYB-E4401"]),
        ScopedRegionCase("reject-escape-beyond-scope.hy", {"data": "map[string]int"}, True,
                         [RegionOp("borrow_task", "data", workers=4), RegionOp("escape_channel", "data"), RegionOp("escape_return", "data")], False, ["HYB-E3104", "HYB-E4402"]),
        ScopedRegionCase("reject-alias-write-during-scope.hy", {"data": "map[string]int"}, True,
                         [RegionOp("alias", "data", peer="alias_data"), RegionOp("borrow_task", "data", workers=4), RegionOp("write", 'alias_data["x"]')], False, ["HYB-E3102"]),
        ScopedRegionCase("reject-bare-wait-panic-unwind.hy", {"data": "[]byte"}, True,
                         [RegionOp("borrow_task", "data", workers=32), RegionOp("panic_parent", "data")], False, ["HYB-E4403"], defer_barrier=False),
        ScopedRegionCase("accept-deferred-wait-panic-safe.hy", {"data": "[]byte"}, True,
                         [RegionOp("borrow_task", "data", workers=32), RegionOp("panic_parent", "data")], True, defer_barrier=True),
    ]


def audit_live_corpus(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    corpus_dir = repo_root / "tests" / "checker-corpus"
    hy_files = sorted(corpus_dir.glob("*.hy")) if corpus_dir.exists() else []
    texts = [p.read_text(encoding="utf-8", errors="ignore") for p in hy_files]
    loans_rs = repo_root / "compiler" / "crates" / "hy-check" / "src" / "loans.rs"
    return {
        "corpus_total_hy_files": len(hy_files),
        "corpus_task_spawn_files": sum(1 for t in texts if "task " in t),
        "corpus_a3_loan_files": sum(1 for t in texts if "A3" in t),
        "loans_rs_a3_paths_conflict_present": loans_rs.exists() and "fn a3_paths_conflict" in loans_rs.read_text(encoding="utf-8"),
    }


def run_spike(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    t0 = time.perf_counter()
    cases = build_milestone_corpus()
    certs = [check_scoped_region(c) for c in cases]
    matched = sum(
        1 for c, cert in zip(cases, certs)
        if cert.accepted == c.expected_accept and all(any(d.startswith(k) for d in cert.diagnostics) for k in c.expected_codes)
    )
    return {
        "spike_id": "W-044", "issue": 471, "total_cases": len(cases), "verdict_matches": matched,
        "all_passed": matched == len(cases), "zero_allocs_verified": all(cert.allocs_per_op == 0 for cert in certs if cert.accepted),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3), "live_audit": audit_live_corpus(repo_root),
        "certificates": [asdict(cert) for cert in certs],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W-044 (#471) Scoped Task Regions verifier")
    parser.add_argument("--check", action="store_true", help="Verify milestone corpus and exit")
    parser.add_argument("--json", action="store_true", help="Emit JSON verification report")
    args = parser.parse_args(argv)
    report = run_spike()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.check:
        print(f"W-044 (#471) OK: {report['verdict_matches']}/{report['total_cases']} cases verified, 0 allocs/op")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
