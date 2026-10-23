#!/usr/bin/env python3
"""Unit and integration tests for W-050 (#1259) Affine Cancellation Trees verifier."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import affine_ctx_verifier as v  # noqa: E402


class TestAffineCtxVerifier(unittest.TestCase):
    def test_storage_component_transitive_aliases_and_may_share(self) -> None:
        aliases = {"c2": "cap", "c3": "c2"}
        may_share = {"cap": {"shared_cap"}, "shared_cap": {"cap"}}
        comp = v.storage_component(aliases, may_share, "c3.cancel[0]")
        self.assertEqual(comp, {"cap", "c2", "c3", "shared_cap"})

    def test_emit_ctx_select_go_explicit_and_deferred_cleanup(self) -> None:
        explicit_go = v.emit_ctx_select_go("ctx", "out", "pkt", "w", deferred=False)
        self.assertIn("case out <- pkt: w.Finish()", explicit_go)
        self.assertIn("case <-ctx.Done(): w.Finish(); return", explicit_go)
        deferred_go = v.emit_ctx_select_go("ctx", "out", "pkt", "w", deferred=True)
        self.assertIn("case <-ctx.Done(): // deferred handle cleanup active; return", deferred_go)

    def test_f1_cancel_cap_leak_and_positive_settlement(self) -> None:
        leaked = v.CtxCase(
            "leak", True, {"cap": "ctx.CancelCap[Req]", "c2": "ctx.CancelCap[Req]"},
            [v.CtxOp("with_deadline", "cap"), v.CtxOp("alias", "cap", peer="c2")], False, ["HYB-E5001"],
        )
        cert_leak = v.check_ctx_case(leaked)
        self.assertFalse(cert_leak.accepted)
        self.assertTrue(any(d.startswith("HYB-E5001:leaked_cancel_cap") for d in cert_leak.diagnostics))

        settled = v.CtxCase(
            "settled_via_alias", True, {"cap": "ctx.CancelCap[Req]", "c2": "ctx.CancelCap[Req]"},
            [v.CtxOp("with_cancel", "cap"), v.CtxOp("alias", "cap", peer="c2"), v.CtxOp("cancel_call", "c2")], True,
        )
        cert_ok = v.check_ctx_case(settled)
        self.assertTrue(cert_ok.accepted)
        self.assertEqual(cert_ok.settled_cancel_caps, 2)

    def test_f1_use_after_move_and_double_move_hyb_e3003(self) -> None:
        uaf = v.CtxCase(
            "uaf_cap", True, {"cap": "ctx.CancelCap[Req]", "c2": "ctx.CancelCap[Req]"},
            [v.CtxOp("with_cancel", "cap"), v.CtxOp("alias", "cap", peer="c2"), v.CtxOp("move_cap", "cap"), v.CtxOp("defer_cancel", "cap"), v.CtxOp("cancel_call", "c2.fn[0]"), v.CtxOp("move_cap", "cap")],
            False, ["HYB-E3003"],
        )
        cert = v.check_ctx_case(uaf)
        self.assertFalse(cert.accepted)
        self.assertIn("HYB-E3003:defer_cancel_after_move(cap)", cert.diagnostics)
        self.assertIn("HYB-E3003:cancel_call_after_move(c2)", cert.diagnostics)
        self.assertIn("HYB-E3003:double_move_cancel_cap(cap)", cert.diagnostics)

    def test_f2_uncancellable_channel_ops_hyb_e5002_and_non_scoped_control(self) -> None:
        scoped_bad = v.CtxCase(
            "scoped_bare", False, {"ctx": "ctx.Scope[Req]", "ch": "chan int"},
            [v.CtxOp("chan_send_bare", "ch"), v.CtxOp("chan_recv_bare", "ch"), v.CtxOp("select_no_ctx_done", "ch")],
            False, ["HYB-E5002"],
        )
        cert_bad = v.check_ctx_case(scoped_bad)
        self.assertFalse(cert_bad.accepted)
        self.assertEqual(len([d for d in cert_bad.diagnostics if d.startswith("HYB-E5002")]), 3)

        unscoped_ok = v.CtxCase(
            "unscoped_bare", False, {"ch": "chan int"},
            [v.CtxOp("chan_send_bare", "ch"), v.CtxOp("chan_recv_bare", "ch")], True,
        )
        self.assertTrue(v.check_ctx_case(unscoped_ok).accepted)

    def test_f3_linear_own_handle_cancel_path_and_select_move_hyb_e5003_e3004(self) -> None:
        unsettled = v.CtxCase(
            "unsettled_json", True, {"ctx": "ctx.Scope[Req]", "doc": "serde_json.value", "pkt": "map[string]int"},
            [v.CtxOp("select_with_ctx_done", "out", peer="pkt", via_move=False, settle_normal=False, settle_on_cancel=False)],
            False, ["HYB-E3004", "HYB-E5003"],
        )
        cert = v.check_ctx_case(unsettled)
        self.assertFalse(cert.accepted)
        self.assertIn("HYB-E3004:channel_send_requires_move(pkt)", cert.diagnostics)
        self.assertIn("HYB-E5003:unsettled_linear_handle_on_cancel_path(doc)", cert.diagnostics)
        self.assertIn("HYB-E5003:unsettled_linear_handle_at_exit(doc)", cert.diagnostics)

        double_send = v.CtxCase(
            "double_send", True, {"ctx": "ctx.Scope[Req]", "w": "flate2.writer", "pkt": "map[string]int"},
            [
                v.CtxOp("defer_handle_finish", "w"),
                v.CtxOp("select_with_ctx_done", "out", peer="pkt", settle_normal=True),
                v.CtxOp("select_with_ctx_done", "out", peer="pkt", settle_normal=True),
            ],
            False, ["HYB-E3003"],
        )
        cert_ds = v.check_ctx_case(double_send)
        self.assertFalse(cert_ds.accepted)
        self.assertIn("HYB-E3003:use_after_move_in_select(pkt)", cert_ds.diagnostics)

    def test_with_value_and_opaque_context_rejected_hyb_e3001(self) -> None:
        bad = v.CtxCase("bad_ctx", True, {"c1": "context.WithValue", "c2": "context.Context", "c3": "context.CancelFunc"}, [], False, ["HYB-E3001"])
        cert = v.check_ctx_case(bad)
        self.assertFalse(cert.accepted)
        self.assertEqual(len([d for d in cert.diagnostics if d.startswith("HYB-E3001")]), 3)

    def test_milestone_corpus_8_of_8_and_live_head_audit(self) -> None:
        report = v.run_spike()
        self.assertEqual(report["total_cases"], 8)
        self.assertEqual(report["verdict_matches"], 8)
        self.assertTrue(report["all_passed"])
        self.assertTrue(report["fast_path_select_under_2ns"])
        self.assertTrue(report["zero_allocs_verified"])
        audit = report["live_audit"]
        self.assertEqual(audit["corpus_total_hy_files"], 126)
        self.assertEqual(audit["corpus_task_spawn_files"], 71)
        self.assertEqual(audit["corpus_select_files"], 9)
        self.assertEqual(audit["corpus_own_handle_files"], 3)
        self.assertTrue(audit["freeze_rs_storage_component_present"])

    def test_cli_check_and_json_modes(self) -> None:
        buf_check = io.StringIO()
        with contextlib.redirect_stdout(buf_check):
            rc_check = v.main(["--check"])
        self.assertEqual(rc_check, 0)
        self.assertIn("8/8 cases verified", buf_check.getvalue())

        buf_json = io.StringIO()
        with contextlib.redirect_stdout(buf_json):
            rc_json = v.main(["--json"])
        self.assertEqual(rc_json, 0)
        parsed = json.loads(buf_json.getvalue())
        self.assertEqual(parsed["spike_id"], "W-050")
        self.assertEqual(parsed["issue"], 1259)


if __name__ == "__main__":
    unittest.main()
