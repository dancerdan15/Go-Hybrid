#!/usr/bin/env python3
"""Unit and falsification tests for W-051 (#1258) deterministic linear drop synthesizer."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from linear_drop_synthesizer import (  # noqa: E402
    RESURRECTION_OPS,
    DropCase,
    DropOp,
    audit_live_corpus,
    build_milestone_corpus,
    check_drop_case,
    compute_pass_a_last_use,
    main,
    run_spike,
    storage_component,
)


class LinearDropSynthesizerTests(unittest.TestCase):
    def test_storage_component_transitive_closure_and_isolation(self) -> None:
        aliases = {"view": "self", "sub": "view"}
        may_share = {"self": {"view"}, "view": {"self", "sub"}, "sub": {"view"}, "other": set()}
        self.assertEqual(storage_component(aliases, may_share, "sub.ptr[0]"), {"self", "view", "sub"})
        self.assertEqual(storage_component(aliases, may_share, "other.fd"), {"other"})

    def test_pass_a_last_use_and_dominance_frontier_synthesis(self) -> None:
        ops = [DropOp(0, "define", "f"), DropOp(1, "read", "f.fd"), DropOp(4, "write", "f.buf[0]")]
        self.assertEqual(compute_pass_a_last_use(ops), {"f": 4})

        straight = check_drop_case(DropCase("straight.hy", "OsFile", {"f": "OsFile"}, ops, True))
        self.assertTrue(straight.accepted and not straight.drop_flag_needed)
        self.assertEqual(straight.synthesized_drop_sites, ["after_stmt_4"])
        self.assertEqual(straight.gc_finalizer_bytes, 0)
        self.assertIn("OsFile_drop(&f) // synthesized at after_stmt_4", straight.emitted_go)

        moved = check_drop_case(DropCase("moved.hy", "HyrtSlab", {"s": "HyrtSlab"}, [DropOp(0, "define", "s"), DropOp(2, "move_to_task", "s")], True))
        self.assertTrue(moved.accepted and not moved.drop_flag_needed)
        self.assertEqual(moved.synthesized_drop_sites, [])

        else_moved = check_drop_case(
            DropCase("else_move.hy", "OsFile", {"f": "OsFile"}, [DropOp(0, "define", "f"), DropOp(1, "read", "f.fd", branch="then"), DropOp(2, "move_to_chan", "f", branch="else")], True)
        )
        self.assertTrue(else_moved.accepted and else_moved.drop_flag_needed)
        self.assertEqual(else_moved.synthesized_drop_sites, ["dominance_frontier:then_branch_exit"])

    def test_f1_drop_resurrection_escape_rejected_and_clean_drop_accepted(self) -> None:
        for kind in sorted(RESURRECTION_OPS):
            ops = [DropOp(0, "alias", "self", peer="alias_v", in_drop_fn=True), DropOp(1, kind, "alias_v", in_drop_fn=True)]
            cert = check_drop_case(DropCase(f"resurrect_{kind}.hy", "OsFile", {"self": "OsFile", "alias_v": "[]u8"}, ops, False, ["HYB-E5101", "HYB-E3003"]))
            self.assertFalse(cert.accepted)
            self.assertTrue(any(d.startswith("HYB-E5101") for d in cert.diagnostics))
            self.assertTrue(any(d.startswith("HYB-E3003") for d in cert.diagnostics))

        clean = check_drop_case(DropCase("clean.hy", "OsFile", {"self": "OsFile"}, [DropOp(0, "read", "self.fd", in_drop_fn=True), DropOp(1, "write", "self.fd", in_drop_fn=True)], True))
        self.assertTrue(clean.accepted)
        self.assertEqual(clean.diagnostics, [])

    def test_f2_loop_backedge_double_drop_vs_reinit_and_loop_local(self) -> None:
        bad = check_drop_case(DropCase("bad.hy", "OsFile", {"f": "OsFile"}, [DropOp(0, "define", "f"), DropOp(1, "explicit_drop", "f", in_loop=True), DropOp(2, "loop_backedge", "f", in_loop=True)], False, ["HYB-E5102"]))
        self.assertFalse(bad.accepted)
        self.assertIn("HYB-E5102:loop_backedge_double_drop_or_uninit(f)", bad.diagnostics)

        reinit = check_drop_case(DropCase("reinit.hy", "OsFile", {"f": "OsFile"}, [DropOp(0, "define", "f"), DropOp(1, "explicit_drop", "f", in_loop=True), DropOp(2, "reinit", "f", in_loop=True), DropOp(3, "loop_backedge", "f", in_loop=True)], True))
        self.assertTrue(reinit.accepted)

        local = check_drop_case(DropCase("local.hy", "OsFile", {"f": "OsFile"}, [DropOp(0, "define", "f", in_loop=True), DropOp(1, "read", "f.fd", in_loop=True), DropOp(2, "loop_backedge", "f", in_loop=True)], True))
        self.assertTrue(local.accepted)
        self.assertEqual(local.synthesized_drop_sites, ["loop_end"])

    def test_f3_setfinalizer_ban_and_panic_unwind_defer_guard(self) -> None:
        banned = check_drop_case(DropCase("ban.hy", "OsFile", {"f": "OsFile"}, [DropOp(0, "define", "f"), DropOp(1, "set_finalizer", "f")], False, ["HYB-E5103"]))
        self.assertFalse(banned.accepted)
        self.assertEqual(banned.gc_finalizer_bytes, 32)
        self.assertIn("HYB-E5103:banned_runtime_set_finalizer(f)", banned.diagnostics)

        safe = check_drop_case(DropCase("safe.hy", "OsFile", {"f": "OsFile"}, [DropOp(0, "define", "f"), DropOp(1, "may_panic", "f"), DropOp(2, "read", "f.fd")], True, open_coded_defer=True))
        self.assertTrue(safe.accepted and safe.unwind_safe and safe.drop_flag_needed)
        self.assertEqual(safe.gc_finalizer_bytes, 0)
        self.assertIn("defer func() { if __hy_drop_f { OsFile_drop(&f) } }()", safe.emitted_go)

    def test_use_after_move_and_milestone_corpus_and_cli(self) -> None:
        uaf = check_drop_case(DropCase("uaf.hy", "OsFile", {"f": "OsFile"}, [DropOp(0, "define", "f"), DropOp(1, "move_to_task", "f"), DropOp(2, "read", "f.fd"), DropOp(3, "explicit_drop", "f")], False, ["HYB-E3003"]))
        self.assertFalse(uaf.accepted)
        self.assertEqual(len(uaf.diagnostics), 2)

        report = run_spike()
        self.assertEqual(report["total_cases"], len(build_milestone_corpus()))
        self.assertEqual(report["verdict_matches"], 8)
        self.assertTrue(report["all_passed"] and report["zero_gc_finalizer_bytes"])

        audit = audit_live_corpus()
        self.assertGreaterEqual(audit["corpus_total_hy_files"], 126)
        self.assertEqual(audit["corpus_finalizer_managed_files"], 0)
        self.assertTrue(audit["walk_rs_has_pass_a_last_use"])

        buf_check, buf_json = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_check):
            self.assertEqual(main(["--check"]), 0)
        self.assertIn("W-051 (#1258) OK: 8/8 cases verified", buf_check.getvalue())
        with redirect_stdout(buf_json):
            self.assertEqual(main(["--json"]), 0)
        self.assertEqual(json.loads(buf_json.getvalue())["issue"], 1258)


if __name__ == "__main__":
    unittest.main()
