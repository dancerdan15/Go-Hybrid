#!/usr/bin/env python3
"""Unit and falsifier verification suite for W029 Certified Heap-Freedom (no_heap, #1172)."""
from __future__ import annotations
import io, json, sys, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import no_heap_certifier as nhc  # noqa: E402

class TestW029NoHeapCertifier(unittest.TestCase):
    def test_parse_gcflags_m2_directives_and_flows(self) -> None:
        sample = (
            "kernel.go:4:2: buf escapes to heap:\n"
            "  flow: buf -> return_escape (slice escapes frame)\n"
            "  from return buf (return) at kernel.go:5:2\n"
            "kernel.go:6:2: moved to heap: x\n"
            "kernel.go:8:2: scratch (stack_slice) does not escape\n"
        )
        parsed = nhc.parse_gcflags_m2(sample)
        self.assertEqual(len(parsed), 3)
        self.assertTrue(parsed[0]["escapes"])
        self.assertEqual((parsed[0]["subject"], len(parsed[0]["flows"])), ("buf", 2))
        self.assertEqual((parsed[1]["escapes"], parsed[1]["subject"], parsed[2]["escapes"]), (True, "x", False))

    def test_all_14_critical_cases(self) -> None:
        self.assertEqual(len(nhc.CRITICAL_CASES), 14)
        for name, (exp_cert, exp_code, src) in nhc.CRITICAL_CASES.items():
            with self.subTest(case=name):
                res = nhc.certify_no_heap(src, fn_name=name)
                self.assertEqual(res.no_heap_certified, exp_cert)
                if exp_cert:
                    self.assertEqual((len(res.escape_diags), res.allocs_per_run), (0, 0))
                else:
                    self.assertEqual(res.escape_diags[0].code, exp_code)
                    self.assertGreater(res.allocs_per_run, 0)

    def test_transitive_alias_escape_rejected(self) -> None:
        res = nhc.certify_no_heap("fn alias_esc() {\n  buf := make([]int, 8, 16)\n  alias := buf\n  return alias\n}", "alias_esc")
        self.assertEqual((res.no_heap_certified, res.escape_diags[0].code, "buf" in res.frame_local_bindings), (False, "HYB-E6010", False))
        self.assertTrue(all(not nhc.certify_function(bad).no_heap_certified for bad in ("fn f() {\n  q := &s.f\n  p := q\n  return p\n}", "fn f() {\n  p := (&s.a)\n  return p\n}", "fn f() {\n  return (&x)\n}", "fn f() {\n  c := fn(x int) int { return &x }\n}")))
        self.assertEqual(nhc.certify_no_heap("fn f() {\n  buf := make([]int, 8, 16)\n  c := fn(x int) int { return buf[0] + x }\n  return c\n}").frame_local_bindings, [])

    def test_p1_corpus_and_200_seeded_zero_false_accepts(self) -> None:
        rep = nhc.evaluate_suite()
        p1 = rep["p1_oracle"]
        self.assertTrue(rep["all_pass"])
        self.assertEqual((rep["critical_passed"], p1["corpus_total"], p1["corpus_no_heap_certified"], p1["corpus_heap_rejected"]), (14, 126, 6, 120))
        self.assertEqual((p1["seeded_total"], p1["seeded_certified"], p1["false_accepts"]), (200, 75, 0))
        s1, s2 = nhc.generate_seeded_programs(1172, 200), nhc.generate_seeded_programs(1172, 200)
        self.assertEqual(s1, s2)
        self.assertTrue(all(not item["false_accept"] for item in s1))

    def test_p2_five_kernels_stack_promotion_and_4096_boundary(self) -> None:
        p2 = nhc.evaluate_p2_kernels()
        self.assertEqual((p2["kernels_total"], p2["kernels_promoted"], p2["stack_cap_4096_respected"]), (5, 5, True))
        self.assertTrue(all((r["naive_allocs_per_run"], r["guided_allocs_per_run"], r["promoted_to_zero"]) == (1, 0, True) for r in p2["kernels"]))
        self.assertTrue(all(not nhc.certify_no_heap(bad).no_heap_certified for bad in ("fn f() {\n  a := [1000000]int{}\n}", "fn f() {\n  a := [-1]int{}\n}", "fn f() {\n  b := make([]int, -1, 16)\n}", "fn f() {\n  b1 := make([]int, 512, 512)\n  b2 := make([]int, 512, 512)\n}")))

    def test_p3_fnstate_gap_and_foreign_perimeter(self) -> None:
        p3 = nhc.evaluate_p3_foreign_perimeter(Path(__file__).resolve().parents[3])
        self.assertEqual((p3["hy_check_fnstate_refs"], p3["hy_emit_fnstate_refs"], nhc.evaluate_p3_foreign_perimeter(Path("/nonexistent_w029"))["hy_check_fnstate_refs"]), (236, 0, 0))
        self.assertEqual((p3["foreign_rs_lines"], p3["shapes_rs_lines"], p3["shapes_types_count"], p3["shapes_members_count"], p3["no_heap_safe_descriptors"]), (1280, 664, 18, 74, 42))
        self.assertTrue(nhc.certify_no_heap("// Árvíztűrő\r\nfn crlf() {\r\n  b := make([]int, 8, 16)\r\n}\r\n").no_heap_certified)

    def test_cli_check_and_json_modes(self) -> None:
        buf_check, buf_json = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_check): rc_check = nhc.main(["--check"])
        with redirect_stdout(buf_json): rc_json = nhc.main(["--json"])
        self.assertEqual((rc_check, rc_json), (0, 0))
        self.assertIn("false_accepts=0", buf_check.getvalue())
        self.assertTrue(json.loads(buf_json.getvalue())["all_pass"])


if __name__ == "__main__":
    unittest.main()
