from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

DIAG_GUARD_ESCAPE = "HYB-E4601"
DIAG_BARE_OR_NONMONOTONE_LOCK = "HYB-E4602"
DIAG_REENTRANT_OR_CYCLIC_DEADLOCK = "HYB-E4603"


@dataclass(frozen=True)
class SyncDiagnostic:
    code: str
    site: str
    detail: str


@dataclass(frozen=True)
class SyncOp:
    kind: str
    target: str
    arg: str = ""
    moved: bool = False


@dataclass(frozen=True)
class VerificationResult:
    case_name: str
    accepted: bool
    diagnostics: tuple[SyncDiagnostic, ...]


@dataclass(frozen=True)
class TaskStep:
    task_id: str
    op: str
    values: tuple[int, ...] = ()


@dataclass(frozen=True)
class InterleavingReport:
    mode: str
    schedules_explored: int
    schedule_independent: bool
    deadlocked: bool
    distinct_snapshots: tuple[tuple[int, ...], ...]
    distinct_observations: int
    diagnostics: tuple[SyncDiagnostic, ...]


def verify_sync_trace(case_name: str, ops: Sequence[SyncOp]) -> VerificationResult:
    diags: list[SyncDiagnostic] = []
    active_guards: dict[str, tuple[str, str]] = {}
    guard_aliases: dict[str, str] = {}
    wg_tokens: dict[str, str] = {}
    wg_quiesced: set[str] = set()

    for op in ops:
        if op.kind == "declare_bare_mutex":
            diags.append(SyncDiagnostic(DIAG_BARE_OR_NONMONOTONE_LOCK, op.target, "bare data-less `sync.Mutex` forbidden; use `Mutex[T]`, `RWMutex[T]`, or `MonotoneCell[T]`"))
        elif op.kind in ("enter_lock", "enter_rlock", "enter_wlock"):
            if any(lk == op.target for lk, _ in active_guards.values()):
                diags.append(SyncDiagnostic(DIAG_REENTRANT_OR_CYCLIC_DEADLOCK, op.target, f"re-entrant `{op.kind}` on `{op.target}` causes self-deadlock"))
            else:
                active_guards[op.arg] = (op.target, "r" if op.kind == "enter_rlock" else "w")
        elif op.kind == "exit_lock":
            active_guards.pop(op.arg, None)
            guard_aliases = {k: (f"expired:{v}" if v == op.arg else v) for k, v in guard_aliases.items()}
        elif op.kind == "alias_guard":
            guard_aliases[op.arg] = guard_aliases.get(op.target, op.target)
        elif op.kind in ("escape_guard", "return_guard", "send_guard", "spawn_guard", "store_outer_guard"):
            diags.append(SyncDiagnostic(DIAG_GUARD_ESCAPE, op.target, f"lexical guard `{op.target}` cannot escape `with m.lock() as {op.target}:` via `{op.kind}`"))
        elif op.kind in ("read_guard", "write_guard"):
            root = guard_aliases.get(op.target, op.target)
            if root not in active_guards:
                diags.append(SyncDiagnostic(DIAG_GUARD_ESCAPE, op.target, f"access to `{op.target}` outside lexical `with m.lock() as {root}:` scope"))
            elif op.kind == "write_guard" and active_guards[root][1] == "r":
                diags.append(SyncDiagnostic(DIAG_BARE_OR_NONMONOTONE_LOCK, op.target, f"cannot mutate payload through read-only `rlock()` guard `{root}`"))
        elif op.kind == "unlocked_access":
            diags.append(SyncDiagnostic(DIAG_BARE_OR_NONMONOTONE_LOCK, op.target, f"direct access to `{op.target}` without `with {op.target}.lock() as guard:`"))
        elif op.kind == "wg_token":
            if wg_tokens.get(op.arg) not in (None, "done"):
                diags.append(SyncDiagnostic(DIAG_REENTRANT_OR_CYCLIC_DEADLOCK, op.arg, f"unsettled WaitGroup token `{op.arg}` overwritten before `defer {op.arg}.done()`"))
            wg_tokens[op.arg] = "minted"
            wg_quiesced.discard(op.target)
        elif op.kind == "spawn_with_token":
            if not op.moved or wg_tokens.get(op.arg) != "minted":
                diags.append(SyncDiagnostic(DIAG_REENTRANT_OR_CYCLIC_DEADLOCK, op.arg, f"WaitGroup token `{op.arg}` must be linearly moved (`move {op.arg}`) once"))
            else:
                wg_tokens[op.arg] = "spawned"
        elif op.kind == "defer_token_done":
            if wg_tokens.get(op.target) not in ("minted", "spawned"):
                diags.append(SyncDiagnostic(DIAG_REENTRANT_OR_CYCLIC_DEADLOCK, op.target, f"double or invalid `defer {op.target}.done()`"))
            else:
                wg_tokens[op.target] = "done"
        elif op.kind == "wg_wait":
            unsettled = [t for t, st in wg_tokens.items() if st != "done"]
            if unsettled:
                diags.append(SyncDiagnostic(DIAG_REENTRANT_OR_CYCLIC_DEADLOCK, op.target, f"`{op.target}.wait()` reached with unsettled token(s): {', '.join(unsettled)}"))
            else:
                wg_quiesced.add(op.target)
        elif op.kind == "monotone_snapshot" and op.arg not in wg_quiesced:
            diags.append(SyncDiagnostic(DIAG_GUARD_ESCAPE, op.target, f"premature `{op.target}.snapshot()` before WaitGroup `{op.arg}.wait()` quiescence"))
        elif op.kind in ("monotone_remove", "monotone_absence_test", "monotone_live_iter", "monotone_first_winner", "monotone_mutable_ref"):
            diags.append(SyncDiagnostic(DIAG_BARE_OR_NONMONOTONE_LOCK, op.target, f"non-monotone operation `{op.kind}` is forbidden in a monotone task region"))

    for tok, st in wg_tokens.items():
        if st != "done" and not any(d.site == tok for d in diags):
            diags.append(SyncDiagnostic(DIAG_REENTRANT_OR_CYCLIC_DEADLOCK, tok, f"affine WaitGroup token `{tok}` exited scope without `defer {tok}.done()`"))
    return VerificationResult(case_name=case_name, accepted=len(diags) == 0, diagnostics=tuple(diags))


def verify_monotone_interleavings(tasks: dict[str, Sequence[TaskStep]], initial_facts: Sequence[int] = (), mode: str = "union") -> InterleavingReport:
    tids = sorted(tasks.keys())
    total = sum(len(v) for v in tasks.values())
    orders = set(itertools.permutations([t for t in tids for _ in tasks[t]]))
    snapshots: set[tuple[int, ...]] = set()
    observations: set[tuple[tuple[str, object], ...]] = set()
    deadlocked = False

    for order in orders:
        pc, cell, obs, queue = {t: 0 for t in tids}, set(initial_facts), [], list(order)
        while queue:
            for idx, tid in enumerate(queue):
                step = tasks[tid][pc[tid]]
                if step.op == "await" and not set(step.values).issubset(cell):
                    continue
                if step.op == "await":
                    obs.append((tid, tuple(sorted(step.values))))
                elif step.op == "write":
                    added = not set(step.values).issubset(cell)
                    cell = set(step.values) if mode == "overwrite" else (cell | set(step.values))
                    obs.append((tid, added if mode == "insert_flag" else ()))
                elif step.op == "read_live":
                    obs.append((tid, tuple(sorted(cell)) if mode == "live_snapshot" else ()))
                pc[tid] += 1
                queue.pop(idx)
                break
            else:
                deadlocked = True
                break
        if sum(pc.values()) == total:
            snapshots.add(tuple(sorted(cell)))
            observations.add(tuple(sorted(obs)))

    diags = (SyncDiagnostic(DIAG_REENTRANT_OR_CYCLIC_DEADLOCK, "region", "unseeded cyclic threshold dependency blocked at join"),) if deadlocked else ()
    indep = (not deadlocked) and len(snapshots) == 1 and len(observations) == 1
    return InterleavingReport(mode, len(orders), indep, deadlocked, tuple(sorted(snapshots)), len(observations), diags)


def run_adversarial_corpus() -> dict[str, VerificationResult]:
    cases = {
        "accept-mutex-guard-and-wg-token.hy": [SyncOp("declare_mutex", "m"), SyncOp("wg_token", "wg", "tok"), SyncOp("spawn_with_token", "wg", "tok", True), SyncOp("enter_lock", "m", "guard"), SyncOp("write_guard", "guard"), SyncOp("exit_lock", "m", "guard"), SyncOp("defer_token_done", "tok"), SyncOp("wg_wait", "wg")],
        "accept-rwmutex-shared-readers.hy": [SyncOp("declare_rwmutex", "rw"), SyncOp("enter_rlock", "rw", "rg"), SyncOp("read_guard", "rg"), SyncOp("exit_lock", "rw", "rg"), SyncOp("enter_wlock", "rw", "wg_g"), SyncOp("write_guard", "wg_g"), SyncOp("exit_lock", "rw", "wg_g")],
        "accept-monotone-region-quiescence.hy": [SyncOp("declare_monotone", "facts"), SyncOp("wg_token", "wg", "t1"), SyncOp("spawn_with_token", "wg", "t1", True), SyncOp("defer_token_done", "t1"), SyncOp("wg_wait", "wg"), SyncOp("monotone_snapshot", "facts", "wg")],
        "reject-guard-reference-escape.hy": [SyncOp("declare_mutex", "m"), SyncOp("enter_lock", "m", "guard"), SyncOp("escape_guard", "guard"), SyncOp("exit_lock", "m", "guard")],
        "reject-bare-sync-mutex.hy": [SyncOp("declare_bare_mutex", "raw_mu"), SyncOp("unlocked_access", "raw_mu")],
        "reject-reentrant-self-deadlock.hy": [SyncOp("declare_mutex", "m"), SyncOp("enter_lock", "m", "g1"), SyncOp("enter_lock", "m", "g2"), SyncOp("exit_lock", "m", "g1")],
        "reject-rwmutex-write-under-rlock.hy": [SyncOp("declare_rwmutex", "rw"), SyncOp("enter_rlock", "rw", "rg"), SyncOp("write_guard", "rg"), SyncOp("exit_lock", "rw", "rg")],
        "reject-unsettled-wg-token.hy": [SyncOp("wg_token", "wg", "tok"), SyncOp("wg_wait", "wg")],
        "reject-premature-monotone-snapshot.hy": [SyncOp("declare_monotone", "facts"), SyncOp("wg_token", "wg", "t1"), SyncOp("monotone_snapshot", "facts", "wg"), SyncOp("defer_token_done", "t1")],
        "reject-nonmonotone-removal.hy": [SyncOp("declare_monotone", "facts"), SyncOp("monotone_remove", "facts")],
    }
    return {name: verify_sync_trace(name, ops) for name, ops in cases.items()}


def run_interleaving_suite() -> dict[str, InterleavingReport]:
    diamond = {"t1": [TaskStep("t1", "write", (1,)), TaskStep("t1", "read_live")], "t2": [TaskStep("t2", "write", (2,))], "t3": [TaskStep("t3", "await", (1, 2)), TaskStep("t3", "write", (3,))]}
    competing = {"t1": [TaskStep("t1", "write", (1,))], "t2": [TaskStep("t2", "write", (1,))]}
    overwrite = {"t1": [TaskStep("t1", "write", (1,))], "t2": [TaskStep("t2", "write", (2,))]}
    cyclic = {"t1": [TaskStep("t1", "await", (2,)), TaskStep("t1", "write", (1,))], "t2": [TaskStep("t2", "await", (1,)), TaskStep("t2", "write", (2,))]}
    return {
        "positive_diamond_union": verify_monotone_interleavings(diamond, mode="union"),
        "negative_unseeded_cycle": verify_monotone_interleavings(cyclic, mode="union"),
        "negative_control_a_live_snapshot": verify_monotone_interleavings(diamond, mode="live_snapshot"),
        "negative_control_b_insert_flag": verify_monotone_interleavings(competing, mode="insert_flag"),
        "negative_control_c_overwrite": verify_monotone_interleavings(overwrite, mode="overwrite"),
    }


def audit_live_repo(repo_root: Path) -> dict[str, object]:
    guarantees = (repo_root / "GUARANTEES.md").read_text(encoding="utf-8")
    sendability = (repo_root / "compiler/crates/hy-check/src/sendability.rs").read_text(encoding="utf-8")
    return {
        "guarantees_row8_managed_mutex_hatch": "mutex/atomic/actor/finalizer/interop/other" in guarantees,
        "guarantees_row9_no_checker_claim": "| 9 | Values escaping into Go APIs" in guarantees and "**No checker claim**" in guarantees,
        "guarantees_excludes_logical_races": "absence of logical races" in guarantees,
        "sendability_enforces_fail_closed_o4_9": "HYB-E3005" in sendability,
        "corpus_hy_count": len(list((repo_root / "tests/checker-corpus").glob("*.hy"))),
        "corpus_02_managed_map_exists": (repo_root / "tests/checker-corpus/02-shared-map-managed.hy").is_file(),
    }


def run_spike_report(repo_root: Path | None = None) -> dict[str, object]:
    root = repo_root or Path(__file__).resolve().parents[2]
    corpus, inter, audit = run_adversarial_corpus(), run_interleaving_suite(), audit_live_repo(root)
    acc = {"accept-mutex-guard-and-wg-token.hy", "accept-rwmutex-shared-readers.hy", "accept-monotone-region-quiescence.hy"}
    ok = all(v.accepted == (k in acc) for k, v in corpus.items()) and inter["positive_diamond_union"].schedule_independent and inter["negative_unseeded_cycle"].deadlocked and not any(inter[k].schedule_independent for k in ("negative_control_a_live_snapshot", "negative_control_b_insert_flag", "negative_control_c_overwrite")) and all(bool(v) for v in audit.values())
    return {"issue": 482, "spike": "w046-linear-sync-primitives", "all_invariants_passed": ok, "live_repo_audit": audit, "corpus": {k: asdict(v) for k, v in corpus.items()}, "interleavings": {k: asdict(v) for k, v in inter.items()}}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W-046 (#482) Linear Sync Primitives & Monotone Task Regions verifier")
    parser.add_argument("--check", action="store_true", help="Verify all static guard, WaitGroup token, and schedule-independence invariants")
    parser.add_argument("--json", action="store_true", help="Emit deterministic JSON report")
    args = parser.parse_args(argv)
    report = run_spike_report()
    print(json.dumps(report, indent=2) if args.json else f"W-046 (#482) Linear Sync & Monotone Regions: passed={report['all_invariants_passed']} ({len(report['corpus'])} corpus cases, {len(report['interleavings'])} interleaving suites)")
    return 0 if report["all_invariants_passed"] or not args.check else 1


if __name__ == "__main__":
    sys.exit(main())
