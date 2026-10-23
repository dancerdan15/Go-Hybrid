from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from linear_sync_verifier import (  # noqa: E402
    DIAG_BARE_OR_NONMONOTONE_LOCK,
    DIAG_GUARD_ESCAPE,
    DIAG_REENTRANT_OR_CYCLIC_DEADLOCK,
    SyncOp,
    TaskStep,
    audit_live_repo,
    main,
    run_adversarial_corpus,
    run_interleaving_suite,
    run_spike_report,
    verify_monotone_interleavings,
    verify_sync_trace,
)


class LinearSyncVerifierTests(unittest.TestCase):
    def test_positive_controls_zero_false_positives(self) -> None:
        corpus = run_adversarial_corpus()
        for name in ("accept-mutex-guard-and-wg-token.hy", "accept-rwmutex-shared-readers.hy", "accept-monotone-region-quiescence.hy"):
            self.assertTrue(corpus[name].accepted, name)
            self.assertEqual(corpus[name].diagnostics, ())

    def test_hyb_e4601_guard_escape_alias_and_premature_snapshot(self) -> None:
        for escape_kind in ("escape_guard", "return_guard", "send_guard", "spawn_guard", "store_outer_guard"):
            res = verify_sync_trace(escape_kind, [SyncOp("enter_lock", "m", "g"), SyncOp(escape_kind, "g"), SyncOp("exit_lock", "m", "g")])
            self.assertFalse(res.accepted)
            self.assertEqual(res.diagnostics[0].code, DIAG_GUARD_ESCAPE)
        alias_after_exit = verify_sync_trace("alias-after-exit", [SyncOp("enter_lock", "m", "g"), SyncOp("alias_guard", "g", "ag"), SyncOp("alias_guard", "ag", "ag2"), SyncOp("read_guard", "ag2"), SyncOp("exit_lock", "m", "g"), SyncOp("enter_lock", "m2", "g"), SyncOp("read_guard", "ag2"), SyncOp("exit_lock", "m2", "g")])
        self.assertFalse(alias_after_exit.accepted)
        self.assertEqual(alias_after_exit.diagnostics[0].code, DIAG_GUARD_ESCAPE)
        premature = run_adversarial_corpus()["reject-premature-monotone-snapshot.hy"]
        self.assertFalse(premature.accepted)
        self.assertEqual(premature.diagnostics[0].code, DIAG_GUARD_ESCAPE)

    def test_hyb_e4602_bare_mutex_rlock_mutation_and_nonmonotone_ops(self) -> None:
        corpus = run_adversarial_corpus()
        for name in ("reject-bare-sync-mutex.hy", "reject-rwmutex-write-under-rlock.hy", "reject-nonmonotone-removal.hy"):
            self.assertFalse(corpus[name].accepted, name)
            self.assertTrue(all(d.code == DIAG_BARE_OR_NONMONOTONE_LOCK for d in corpus[name].diagnostics))
        for bad_op in ("monotone_remove", "monotone_absence_test", "monotone_live_iter", "monotone_first_winner", "monotone_mutable_ref"):
            res = verify_sync_trace(bad_op, [SyncOp("declare_monotone", "facts"), SyncOp(bad_op, "facts")])
            self.assertFalse(res.accepted)
            self.assertEqual(res.diagnostics[0].code, DIAG_BARE_OR_NONMONOTONE_LOCK)

    def test_hyb_e4603_reentrant_lock_and_waitgroup_token_linearity(self) -> None:
        reentrant = run_adversarial_corpus()["reject-reentrant-self-deadlock.hy"]
        self.assertFalse(reentrant.accepted)
        self.assertEqual(reentrant.diagnostics[0].code, DIAG_REENTRANT_OR_CYCLIC_DEADLOCK)
        for name, ops in (
            ("unmoved-tok", [SyncOp("wg_token", "wg", "t"), SyncOp("spawn_with_token", "wg", "t", False), SyncOp("defer_token_done", "t")]),
            ("double-done", [SyncOp("wg_token", "wg", "t"), SyncOp("defer_token_done", "t"), SyncOp("defer_token_done", "t")]),
            ("shadowed-tok", [SyncOp("wg_token", "wg", "t"), SyncOp("wg_token", "wg", "t"), SyncOp("defer_token_done", "t")]),
            ("leaked-tok", [SyncOp("wg_token", "wg", "t")]),
        ):
            res = verify_sync_trace(name, ops)
            self.assertFalse(res.accepted, name)
            self.assertEqual(res.diagnostics[0].code, DIAG_REENTRANT_OR_CYCLIC_DEADLOCK)

    def test_monotone_interleavings_diamond_cycle_and_negative_controls(self) -> None:
        suite = run_interleaving_suite()
        pos, cyc = suite["positive_diamond_union"], suite["negative_unseeded_cycle"]
        self.assertEqual((pos.schedules_explored, pos.schedule_independent, pos.deadlocked, pos.distinct_snapshots, pos.distinct_observations), (30, True, False, ((1, 2, 3),), 1))
        self.assertEqual((cyc.deadlocked, cyc.schedule_independent, cyc.diagnostics[0].code), (True, False, DIAG_REENTRANT_OR_CYCLIC_DEADLOCK))
        seeded = verify_monotone_interleavings({"t1": [TaskStep("t1", "await", (2,)), TaskStep("t1", "write", (1,))], "t2": [TaskStep("t2", "await", (1,)), TaskStep("t2", "write", (2,))]}, initial_facts=(1,))
        self.assertEqual((seeded.schedule_independent, seeded.distinct_snapshots), (True, ((1, 2),)))
        self.assertEqual(suite["negative_control_a_live_snapshot"].distinct_observations, 3)
        self.assertEqual(suite["negative_control_b_insert_flag"].distinct_observations, 2)
        self.assertEqual(suite["negative_control_c_overwrite"].distinct_snapshots, ((1,), (2,)))

    def test_live_repo_audit_and_cli_modes(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        audit = audit_live_repo(repo_root)
        self.assertTrue(all(bool(v) for v in audit.values()))
        self.assertEqual(audit["corpus_hy_count"], 126)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--check", "--json"]), 0)
        self.assertTrue(json.loads(buf.getvalue())["all_invariants_passed"])
        self.assertEqual(run_spike_report(repo_root)["issue"], 482)


if __name__ == "__main__":
    unittest.main()
