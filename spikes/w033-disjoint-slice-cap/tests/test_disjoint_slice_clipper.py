#!/usr/bin/env python3
"""Unit and integration tests for W-033 (#949) Disjoint Slice Capacity Clipper."""
from __future__ import annotations
import io, json, sys, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import disjoint_slice_clipper as dsc  # noqa: E402


class TestDisjointSliceClipper(unittest.TestCase):
    def test_check_partition_positive_and_rejections(self) -> None:
        ok = dsc.SlicePartition(base="buf", lo=0, mid=32, hi=64, cap=64, three_index=True)
        self.assertEqual(dsc.check_partition(ok), [])

        unclipped = dsc.SlicePartition(base="buf", three_index=False)
        diags = dsc.check_partition(unclipped)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, dsc.HYB_E3015)
        self.assertIn("unclipped 2-index slice", diags[0].message)

        overlap = dsc.SlicePartition(base="s", left_hi="mid+1", right_lo="mid")
        self.assertTrue(any("overlapping slice bounds" in d.message for d in dsc.check_partition(overlap)))

        bad_bounds = dsc.SlicePartition(base="s", lo=0, mid=40, hi=32, cap=64)
        self.assertTrue(any("invalid split bounds" in d.message for d in dsc.check_partition(bad_bounds)))

        parent_mut = dsc.SlicePartition(base="s", parent_mutated_in_split=True)
        self.assertTrue(any("parent slice 's' accessed" in d.message for d in dsc.check_partition(parent_mut)))

        elem_share = dsc.SlicePartition(base="s", elem_may_share=True)
        self.assertTrue(any("O4.6" in d.message for d in dsc.check_partition(elem_share)))

    def test_check_raw_task_slices_fail_to_pass(self) -> None:
        raw_2idx = "task worker(s[:mid])\ntask worker(s[mid:])"
        d_2idx = dsc.check_raw_task_slices(raw_2idx)
        self.assertTrue(any(d.code == dsc.HYB_E3015 and "unclipped" in d.message for d in d_2idx))

        raw_overlap = "task worker(s[:mid+1:mid+1])\ntask worker(s[mid:])"
        d_ov = dsc.check_raw_task_slices(raw_overlap)
        self.assertTrue(any(d.code == dsc.HYB_E3015 and "overlapping" in d.message for d in d_ov))

        raw_parent = "split s at mid as l, r { task worker(s); task worker(r) }"
        d_par = dsc.check_raw_task_slices(raw_parent)
        self.assertTrue(any(d.code == dsc.HYB_E3015 and "parent slice 's'" in d.message for d in d_par))

        raw_3idx_ok = "task worker(s[0:mid:mid])\ntask worker(s[mid:n])"
        self.assertEqual(dsc.check_raw_task_slices(raw_3idx_ok), [])

        split_ok = "split s[0:64] at 32 as l, r { task worker(move l); task worker(move r) }"
        self.assertEqual(dsc.check_raw_task_slices(split_ok), [])

    def test_lower_split_stmt_three_index_codegen(self) -> None:
        src = "split buf at pivot as left, right { task qsort(move left); task qsort(move right) }"
        out = dsc.lower_split_stmt(src)
        self.assertIn("left := buf[0:pivot:pivot]", out)
        self.assertIn("right := buf[pivot:len(buf):cap(buf)]", out)

        explicit = "split pcm[0:4096] at 2048 as ch_a, ch_b { task dsp(move ch_a); task dsp(move ch_b) }"
        out_exp = dsc.lower_split_stmt(explicit)
        self.assertIn("ch_a := pcm[0:2048:2048]", out_exp)
        self.assertIn("ch_b := pcm[2048:4096:cap(pcm)]", out_exp)

        with self.assertRaisesRegex(ValueError, dsc.HYB_E3015):
            dsc.lower_split_stmt("split s[0:16] at 32 as l, r { task w(move l) }")
        for bad_src in (
            "split s at -1 as l, r { task w(move l) }",
            "split s[16:n] at 8 as l, r { task w(move l) }",
            "split s[0:n] at n+1 as l, r { task w(move l) }",
        ):
            with self.assertRaisesRegex(ValueError, dsc.HYB_E3015):
                dsc.lower_split_stmt(bad_src)
        self.assertTrue(any(d.code == dsc.HYB_E3015 for d in dsc.check_raw_task_slices("task w(s[0:mid+2])\ntask w(s[mid:n])")))

    def test_simulate_append_isolation_and_alignment(self) -> None:
        r2 = dsc.simulate_append_isolation([10, 20, 30, 40], mid=2, append_val=99, three_index=False)
        self.assertTrue(r2["clobbered"])
        self.assertTrue(r2["race_detected"])
        self.assertFalse(r2["left_reallocated"])
        self.assertEqual(r2["right_first"], 99)

        r3 = dsc.simulate_append_isolation([10, 20, 30, 40], mid=2, append_val=99, three_index=True)
        self.assertFalse(r3["clobbered"])
        self.assertFalse(r3["race_detected"])
        self.assertTrue(r3["left_reallocated"])
        self.assertEqual(r3["right_first"], 30)

        with self.assertRaisesRegex(ValueError, dsc.HYB_E3015):
            dsc.simulate_append_isolation([1, 2], mid=5, append_val=9)

        self.assertEqual(dsc.align_split_index(0, 64), 0)
        self.assertEqual(dsc.align_split_index(13, 64), 16)
        self.assertEqual(dsc.align_split_index(62, 64), 64)
        self.assertEqual(dsc.align_split_index(80, 64), 64)
        for bad_kw in ({"elem_bytes": 0}, {"elem_bytes": -8}, {"line_bytes": 0}):
            with self.assertRaisesRegex(ValueError, dsc.HYB_E3015):
                dsc.align_split_index(13, 64, **bad_kw)

    def test_corpus_audit_and_cli_modes(self) -> None:
        audit = dsc.audit_corpus()
        self.assertEqual(audit["total_corpus_files"], 126)
        self.assertGreaterEqual(int(audit["slice_append_files"]), 10)
        self.assertTrue(audit["pinned_rejects_intact"])
        missing = dsc.audit_corpus(Path("/nonexistent/w033_missing_root"))
        self.assertEqual(missing["total_corpus_files"], 0)
        self.assertFalse(missing["pinned_rejects_intact"])
        crlf_out = dsc.lower_split_stmt("split buf[0:64]\r\n at 32 as l, r {\r\n task w(move l) // α-β\r\n}")
        self.assertIn("l := buf[0:32:32]", crlf_out)

        buf_chk = io.StringIO()
        with redirect_stdout(buf_chk):
            rc_chk = dsc.main(["--check"])
        self.assertEqual(rc_chk, 0)
        self.assertIn("PASS (corpus=126)", buf_chk.getvalue())

        buf_json = io.StringIO()
        with redirect_stdout(buf_json):
            rc_json = dsc.main(["--json"])
        self.assertEqual(rc_json, 0)
        payload = json.loads(buf_json.getvalue())
        self.assertEqual(payload["spike"], "W-033")
        self.assertEqual(payload["issue"], 949)
        self.assertTrue(payload["f1_expressiveness"]["all_three_index"])
        self.assertTrue(payload["f2_safety"]["overlap_rejected_e3015"])


if __name__ == "__main__":
    unittest.main()
