#!/usr/bin/env python3
"""Unit and falsification tests for W-049 (#1251) Wait-Free Epoch RCU verifier."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from epoch_rcu_verifier import (  # noqa: E402
    BLOCKING_EPOCH_OPS, ESCAPE_OPS, RcuCase, RcuOp, audit_live_corpus,
    build_milestone_corpus, check_rcu_case, emit_rcu_epoch_go, main, run_spike, storage_component,
)


class EpochRcuVerifierTests(unittest.TestCase):
    def test_storage_component_transitive_closure_and_isolation(self) -> None:
        aliases = {"routes": "snap", "leaf": "routes"}
        may_share = {"snap": {"mirror"}, "mirror": {"snap"}, "independent": set()}
        comp = storage_component(aliases, may_share, 'leaf["/v1"].target')
        self.assertEqual(comp, {"snap", "routes", "leaf", "mirror"})
        self.assertNotIn("independent", comp)

    def test_milestone_corpus_all_eight_cases_and_sub_2ns_latency(self) -> None:
        cases = build_milestone_corpus()
        self.assertEqual(len(cases), 8)
        report = run_spike()
        self.assertTrue(report["all_passed"])
        self.assertEqual(report["verdict_matches"], 8)
        self.assertTrue(report["wait_free_latency_under_2ns"])
        self.assertTrue(report["zero_allocs_verified"])
        for cert in report["certificates"]:
            if cert["accepted"]:
                self.assertLess(cert["wait_free_read_ns"], 2.0)
                self.assertEqual(cert["allocs_per_op"], 0)
                self.assertGreater(cert["rwmutex_contended_ns"] / cert["wait_free_read_ns"], 30.0)

    def test_f1a_rejects_all_escape_paths_for_guard_and_derived_views(self) -> None:
        for esc in sorted(ESCAPE_OPS):
            with self.subTest(escape_op=esc):
                ops = [RcuOp("epoch_load", "snap", peer="sub", in_epoch=True), RcuOp(esc, "sub.items[0]", in_epoch=True), RcuOp(esc, "guard", in_epoch=True)]
                cert = check_rcu_case(RcuCase(f"escape-{esc}", "rcu.Cell[Config]", {"snap": "Config", "sub": "Slice"}, ops, False, ["HYB-E4901", "HYB-E3104"]))
                self.assertFalse(cert.accepted)
                self.assertTrue(any(d.startswith("HYB-E4901:") for d in cert.diagnostics))
                self.assertTrue(any(d.startswith("HYB-E3104:") for d in cert.diagnostics))

    def test_f1b_and_f1c_affine_publish_freeze_and_immutable_view(self) -> None:
        non_sendable = check_rcu_case(RcuCase("bad-cell", "rcu.Cell[BorrowHandle]", {}, [], False, ["HYB-E3001"]))
        self.assertFalse(non_sendable.accepted)
        self.assertTrue(any(d.startswith("HYB-E3001:") for d in non_sendable.diagnostics))

        no_move = check_rcu_case(RcuCase("no-move", "rcu.Cell[Config]", {"next": "Config"}, [RcuOp("publish", "next", via_move=False)], False, ["HYB-E3004"]))
        self.assertFalse(no_move.accepted)
        self.assertTrue(any(d.startswith("HYB-E3004:") for d in no_move.diagnostics))

        ops = [RcuOp("alias", "next", peer="alias_map"), RcuOp("publish", "next"), RcuOp("publish", "next"), RcuOp("read", "alias_map"), RcuOp("delete", 'alias_map["k"]')]
        post_pub = check_rcu_case(RcuCase("post-pub-freeze", "rcu.Cell[Config]", {"next": "Config", "alias_map": "map[string]int"}, ops, False, ["HYB-E3003"]))
        self.assertFalse(post_pub.accepted)
        self.assertEqual(sum(1 for d in post_pub.diagnostics if d.startswith("HYB-E3003:")), 3)

    def test_f2_rejects_all_blocking_ops_and_nested_publish_or_reclaim(self) -> None:
        for op_kind in (*sorted(BLOCKING_EPOCH_OPS), "publish", "reclaim"):
            with self.subTest(op_kind=op_kind):
                ops = [RcuOp("epoch_load", "snap", in_epoch=True), RcuOp(op_kind, "x", in_epoch=True)]
                cert = check_rcu_case(RcuCase(f"block-{op_kind}", "rcu.Cell[Config]", {"snap": "Config", "x": "T"}, ops, False, ["HYB-E4902"]))
                self.assertFalse(cert.accepted)
                self.assertTrue(any(d.startswith("HYB-E4902:") for d in cert.diagnostics))

    def test_f3_defer_epoch_exit_codegen_and_panic_unwind_safety(self) -> None:
        safe_go = emit_rcu_epoch_go("cfg", "view", defer_exit=True)
        bare_go = emit_rcu_epoch_go("cfg", "view", defer_exit=False)
        self.assertIn("defer __hy_rcu_exit(__hy_slot, __hy_epoch)", safe_go)
        self.assertNotIn("defer ", bare_go)
        self.assertLess(safe_go.index("defer __hy_rcu_exit"), safe_go.index("cfg.ptr.Load()"))
        bare_panic = check_rcu_case(RcuCase("bare-panic", "rcu.Cell[Config]", {"s": "Config"}, [RcuOp("panic_in_epoch", "s", in_epoch=True)], False, ["HYB-E4903"], defer_epoch_exit=False))
        self.assertFalse(bare_panic.accepted)
        self.assertFalse(bare_panic.defer_safe)
        self.assertTrue(any(d.startswith("HYB-E4903:") for d in bare_panic.diagnostics))

    def test_positive_controls_zero_overreach_outside_epoch(self) -> None:
        ops = [
            RcuOp("epoch_load", "snap", in_epoch=True),
            RcuOp("read", "snap.routes", in_epoch=True),
            RcuOp("chan_send", "ch", in_epoch=False),
            RcuOp("mutex_lock", "mu", in_epoch=False),
            RcuOp("publish", "next", in_epoch=False),
            RcuOp("write", 'local_scratch["ok"]', in_epoch=False),
            RcuOp("reclaim", "old_gen", in_epoch=False),
        ]
        cert = check_rcu_case(RcuCase("positive-control", "rcu.Cell[Config]", {"snap": "Config", "next": "Config", "local_scratch": "map[string]int"}, ops, True))
        self.assertTrue(cert.accepted)
        self.assertEqual(cert.diagnostics, [])
        self.assertEqual(cert.epoch_reads, 1)
        self.assertEqual(cert.published_generations, 1)
        self.assertEqual(cert.reclaimed_generations, 1)

    def test_live_corpus_audit_and_cli_modes(self) -> None:
        audit = audit_live_corpus()
        self.assertGreaterEqual(audit["corpus_total_hy_files"], 126)
        self.assertGreaterEqual(audit["corpus_task_spawn_files"], 71)
        self.assertGreaterEqual(audit["corpus_atomic_pointer_files"], 1)
        self.assertTrue(audit["freeze_rs_storage_component_present"])

        out_check = io.StringIO()
        with contextlib.redirect_stdout(out_check):
            rc_check = main(["--check"])
        self.assertEqual(rc_check, 0)
        self.assertIn("W-049 (#1251) OK: 8/8 cases verified", out_check.getvalue())

        out_json = io.StringIO()
        with contextlib.redirect_stdout(out_json):
            rc_json = main(["--json"])
        self.assertEqual(rc_json, 0)
        parsed = json.loads(out_json.getvalue())
        self.assertEqual(parsed["spike_id"], "W-049")
        self.assertEqual(parsed["issue"], 1251)


if __name__ == "__main__":
    unittest.main()
