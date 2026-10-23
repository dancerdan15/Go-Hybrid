from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import affine_linear_pool as alp


class AffineLinearPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.buf = alp.StructContract("Buffer", ref_fields=("data",), has_reset=True, zeroed_fields=("data",))

    def test_resettable_contract_hyb_e4501(self) -> None:
        self.assertEqual(alp.verify_resettable(self.buf), [])
        no_reset = alp.StructContract("RawConn", ref_fields=("buf",), has_reset=False, zeroed_fields=())
        diags_missing = alp.verify_resettable(no_reset)
        self.assertEqual([d.code for d in diags_missing], [alp.DIAG_RESETTABLE_VIOLATION])
        partial = alp.StructContract("TenantCtx", ref_fields=("buf", "token"), has_reset=True, zeroed_fields=("buf",))
        diags_partial = alp.verify_resettable(partial)
        self.assertEqual([d.code for d in diags_partial], [alp.DIAG_RESETTABLE_VIOLATION])
        self.assertIn("token", diags_partial[0].detail)

    def test_use_after_put_and_reacquisition_hyb_e3003(self) -> None:
        res = alp.verify_pool_trace(
            "use-after-put",
            self.buf,
            [alp.PoolOp("get", "b"), alp.PoolOp("put", "b", moved=True), alp.PoolOp("use", "b")],
        )
        self.assertFalse(res.accepted)
        self.assertEqual([d.code for d in res.diagnostics], [alp.DIAG_USE_AFTER_MOVE])

        reget = alp.verify_pool_trace(
            "reacquire-ok",
            self.buf,
            [alp.PoolOp("get", "b"), alp.PoolOp("alias", "sub", source="b"), alp.PoolOp("put", "b", moved=True), alp.PoolOp("get", "b"), alp.PoolOp("get", "c"), alp.PoolOp("alias", "sub", source="c"), alp.PoolOp("put", "c", moved=True), alp.PoolOp("use", "b")],
        )
        self.assertTrue(reget.accepted)

    def test_reslice_alias_component_freeze_hyb_e3003(self) -> None:
        res = alp.verify_pool_trace(
            "transitive-reslice-after-put",
            self.buf,
            [
                alp.PoolOp("get", "b"),
                alp.PoolOp("alias", "sub", source="b"),
                alp.PoolOp("alias", "leaf", source="sub"),
                alp.PoolOp("put", "b", moved=True),
                alp.PoolOp("use", "leaf"),
            ],
        )
        self.assertFalse(res.accepted)
        self.assertEqual([d.code for d in res.diagnostics], [alp.DIAG_USE_AFTER_MOVE])
        self.assertEqual(res.diagnostics[0].binding, "leaf")

    def test_move_form_required_and_double_put_hyb_e3004_e3003(self) -> None:
        no_move = alp.verify_pool_trace(
            "missing-move",
            self.buf,
            [alp.PoolOp("get", "b"), alp.PoolOp("put", "b", moved=False)],
        )
        self.assertFalse(no_move.accepted)
        self.assertEqual([d.code for d in no_move.diagnostics], [alp.DIAG_MOVE_FORM_REQUIRED])

        double_put = alp.verify_pool_trace(
            "double-put",
            self.buf,
            [alp.PoolOp("get", "b"), alp.PoolOp("put", "b", moved=True), alp.PoolOp("put", "b", moved=True)],
        )
        self.assertFalse(double_put.accepted)
        self.assertEqual([d.code for d in double_put.diagnostics], [alp.DIAG_USE_AFTER_MOVE])

    def test_active_loan_and_escaped_alias_hyb_e3102_e4502(self) -> None:
        live_loan = alp.verify_pool_trace(
            "live-loan-across-put",
            self.buf,
            [alp.PoolOp("get", "b"), alp.PoolOp("loan", "s", source="b"), alp.PoolOp("put", "b", moved=True)],
        )
        self.assertFalse(live_loan.accepted)
        self.assertEqual([d.code for d in live_loan.diagnostics], [alp.DIAG_ACTIVE_LOAN_CONFLICT, alp.DIAG_POOL_PUT_LIVE_ALIAS])

        ended_loan = alp.verify_pool_trace(
            "ended-loan-before-put",
            self.buf,
            [alp.PoolOp("get", "b"), alp.PoolOp("loan", "s", source="b"), alp.PoolOp("end_loan", "s"), alp.PoolOp("put", "b", moved=True)],
        )
        self.assertTrue(ended_loan.accepted)

        escaped = alp.verify_pool_trace(
            "escaped-slice-alias",
            self.buf,
            [alp.PoolOp("get", "b"), alp.PoolOp("escape_alias", "g_slice", source="b"), alp.PoolOp("put", "b", moved=True), alp.PoolOp("escape_alias", "late", source="b")],
        )
        self.assertFalse(escaped.accepted)
        self.assertEqual([d.code for d in escaped.diagnostics], [alp.DIAG_POOL_PUT_LIVE_ALIAS, alp.DIAG_USE_AFTER_MOVE])

    def test_untyped_sync_pool_rejected_hyb_e3005(self) -> None:
        res = alp.verify_pool_trace("raw-sync-pool", self.buf, [alp.PoolOp("foreign_sync_pool", "raw")])
        self.assertFalse(res.accepted)
        self.assertEqual([d.code for d in res.diagnostics], [alp.DIAG_UNPROVEN_FOREIGN_POOL])

    def test_adversarial_corpus_and_benchmark_model(self) -> None:
        corpus = alp.run_adversarial_corpus()
        self.assertEqual(len(corpus), 7)
        self.assertTrue(corpus["accept-pooled-task-worker.hy"].accepted)
        for name, res in corpus.items():
            if name != "accept-pooled-task-worker.hy":
                self.assertFalse(res.accepted, msg=name)
        bench = alp.simulate_pool_benchmarks(iterations=100_000, workers=100)
        self.assertEqual(bench["hybrid_affine_pool"]["allocs_per_op"], 0.0)
        self.assertEqual(bench["hybrid_affine_pool"]["races_detected"], 0)
        self.assertEqual(bench["go_sync_pool_retained_alias"]["races_detected"], 1000)

    def test_live_repo_audit_and_cli_modes(self) -> None:
        audit = alp.audit_live_repo(Path(__file__).resolve().parents[3])
        self.assertTrue(all(audit.values()), msg=str(audit))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = alp.main(["--check", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["all_invariants_passed"])


if __name__ == "__main__":
    unittest.main()
