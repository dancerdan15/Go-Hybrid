from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import defer_err_verifier as v


class DeferErrTypestateVerifierTests(unittest.TestCase):
    def test_e4801_rejects_discarded_defer_error_and_accepts_defer_try_or_join(self) -> None:
        bad = v.verify_resource_trace(
            "discard_close",
            [v.ResourceOp("open", "f"), v.ResourceOp("defer_close", "f", channel="discard")],
            fn_returns_error=True,
        )
        self.assertFalse(bad.accepted)
        self.assertEqual([d.code for d in bad.diagnostics], [v.DIAG_DEFER_DISCARDED_ERROR])

        non_err_fn = v.verify_resource_trace(
            "defer_try_non_err_fn",
            [v.ResourceOp("open", "f"), v.ResourceOp("defer_close", "f", channel="defer_try")],
            fn_returns_error=False,
        )
        self.assertFalse(non_err_fn.accepted)
        self.assertEqual([d.code for d in non_err_fn.diagnostics], [v.DIAG_DEFER_DISCARDED_ERROR])

        ok_try = v.verify_resource_trace(
            "ok_defer_try",
            [v.ResourceOp("open", "f"), v.ResourceOp("defer_close", "f", channel="defer_try")],
            fn_returns_error=True,
        )
        self.assertTrue(ok_try.accepted)
        self.assertEqual(ok_try.lowered_join_sites, 1)

        ok_void = v.verify_resource_trace(
            "ok_infallible_cleanup",
            [v.ResourceOp("defer_cleanup", "wg", returns_error=False, channel="void")],
            fn_returns_error=False,
        )
        self.assertTrue(ok_void.accepted)
        self.assertEqual(ok_void.lowered_join_sites, 0)

    def test_e4802_lifo_and_direct_close_before_flush_typestate(self) -> None:
        bad_lifo = v.verify_resource_trace(
            "bad_lifo_order",
            [
                v.ResourceOp("open", "f"),
                v.ResourceOp("wrap_buffered", "bw", parent="f"),
                v.ResourceOp("defer_flush", "bw", channel="defer_try"),
                v.ResourceOp("defer_close", "f", channel="defer_try"),
                v.ResourceOp("write", "bw"),
            ],
        )
        self.assertFalse(bad_lifo.accepted)
        self.assertEqual(bad_lifo.lifo_exec_order, ("defer_close:f", "defer_flush:bw"))
        self.assertIn(v.DIAG_CLOSE_BEFORE_FLUSH, [d.code for d in bad_lifo.diagnostics])

        good_lifo = v.verify_resource_trace(
            "good_lifo_order",
            [
                v.ResourceOp("open", "f"),
                v.ResourceOp("defer_close", "f", channel="defer_try"),
                v.ResourceOp("wrap_buffered", "bw", parent="f"),
                v.ResourceOp("defer_flush", "bw", channel="defer_try"),
                v.ResourceOp("write", "bw"),
            ],
        )
        self.assertTrue(good_lifo.accepted)
        self.assertEqual(good_lifo.lifo_exec_order, ("defer_flush:bw", "defer_close:f"))

        good_direct = v.verify_resource_trace(
            "good_direct_order",
            [v.ResourceOp("open", "f"), v.ResourceOp("wrap_buffered", "bw", parent="f"), v.ResourceOp("write", "bw"), v.ResourceOp("direct_flush", "bw"), v.ResourceOp("direct_close", "f")],
        )
        self.assertTrue(good_direct.accepted)

    def test_e4803_unconsumed_resource_on_early_try_return_and_scope_exit(self) -> None:
        early_leak = v.verify_resource_trace(
            "early_return_before_defer",
            [v.ResourceOp("open", "f"), v.ResourceOp("early_try_return", "parse_header"), v.ResourceOp("defer_close", "f", channel="defer_try")],
        )
        self.assertFalse(early_leak.accepted)
        self.assertEqual([d.code for d in early_leak.diagnostics], [v.DIAG_UNCONSUMED_RESOURCE])

        safe_early = v.verify_resource_trace(
            "early_return_after_defer",
            [v.ResourceOp("open", "f"), v.ResourceOp("defer_close", "f", channel="defer_try"), v.ResourceOp("early_try_return", "parse_header")],
        )
        self.assertTrue(safe_early.accepted)

    def test_adversarial_corpus_and_live_repo_oracle(self) -> None:
        corpus = v.run_adversarial_corpus()
        self.assertEqual(len(corpus), 10)
        self.assertEqual(sum(1 for r in corpus.values() if r.accepted), 4)
        self.assertEqual(sum(1 for r in corpus.values() if not r.accepted), 6)

        audit = v.audit_live_repo(Path(__file__).resolve().parents[3])
        self.assertEqual((audit["buildable_e1002_sites"], audit["emit_guard_e1002_sites"], audit["builtins_e2001_sites"], audit["corpus_total_files"]), (120, 4, 4, 126))

        oracle = v.evaluate_go_types_oracle(audit)
        self.assertEqual((oracle.hit_rate_pct, oracle.corpus_accept_false_positives, oracle.retirable_e1002_guards, oracle.defer_typestate_caught_by_go_types), (100.0, 0, 124, 0))

    def test_cli_check_and_json_modes(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = v.main(["--check", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual((payload["issue"], payload["all_invariants_passed"]), (428, True))


if __name__ == "__main__":
    unittest.main()
