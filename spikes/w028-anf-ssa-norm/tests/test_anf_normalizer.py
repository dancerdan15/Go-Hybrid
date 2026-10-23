#!/usr/bin/env python3
"""Unit and corpus equivalence tests for W028 Pre-Tier-0 ANF/SSA normalizer (#1086)."""
from __future__ import annotations
import contextlib, io, json, pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import anf_normalizer as anf


class TestAnfNormalizer(unittest.TestCase):
    def test_all_ten_critical_cases_and_span_equivalence(self) -> None:
        self.assertEqual(len(anf.CRITICAL_CASES), 10)
        for name, (exp_code, src) in anf.CRITICAL_CASES.items():
            with self.subTest(case=name):
                fn = anf.normalize_hy(src)
                tw = anf.check_tree_walk(src)
                got = fn.diags[0].code if fn.diags else None
                self.assertEqual(got, exp_code, f"{name}: expected {exp_code}, got {got}")
                self.assertEqual(fn.diags, tw, f"{name}: flat ANF vs tree-walk Span mismatch")

    def test_place_projection_and_paren_stripping(self) -> None:
        self.assertEqual(anf.unwrap_surface_parens("(((&((s.f)))))"), "&((s.f))")
        self.assertEqual(anf.unwrap_surface_parens("(((a.b)"), "(((a.b)")
        self.assertEqual(anf.unwrap_surface_parens("()"), "")
        self.assertEqual(anf.unwrap_surface_parens(""), "")
        fn_ok = anf.normalize_hy('fn main() {\n s := 1\n p := (&((s.f)))\n use(p)\n}')
        self.assertEqual(fn_ok.diags, [])
        self.assertTrue(any(i.op == "addr_of" and i.is_place for i in fn_ok.insns))

        fn_sub = anf.normalize_hy('fn main() {\n m := map[string]int{"k": 1}\n foo(&m["k"].f)\n}')
        self.assertEqual([d.code for d in fn_sub.diags], ["HYB-E2010"])
        self.assertTrue(any(i.op == "addr_subplace" and i.is_place for i in fn_sub.insns))

        fn_store = anf.normalize_hy('fn main() {\n m := map[string]int{"k": 1}\n m["k"].f = 2\n}')
        self.assertEqual([d.code for d in fn_store.diags], ["HYB-E2010"])

        fn_t24 = anf.normalize_hy('fn main() {\n m := map[string]int{"k": 1}\n n := &m["k"]\n p := &n\n}')
        self.assertEqual([d.code for d in fn_t24.diags], ["HYB-E3104"])

    def test_short_circuit_and_ltr_call_evaluation_order(self) -> None:
        fn_sc = anf.normalize_hy('fn main() {\n x := map[string]int{"k": 1}\n y := a && b(move x)\n}')
        self.assertEqual(fn_sc.diags, [])
        self.assertTrue(any(i.op == "sc_branch" for i in fn_sc.insns))
        self.assertEqual(anf.normalize_hy('fn main() {\n x := map[string]int{"k": 1}\n y := a && (b || c(move x))\n}').diags, [])
        self.assertEqual(anf.normalize_hy('fn main() {\n x := map[string]int{"k": 1}\n y := a(use(x)) && b(move x)\n}').diags, [])
        fn_sc_err = anf.normalize_hy('fn main() {\n x := map[string]int{"k": 1}\n y := a(move x) && b(use(x))\n}')
        self.assertEqual([d.code for d in fn_sc_err.diags], ["HYB-E3003"])
        self.assertGreater(fn_sc_err.diags[0].span.col, 1)

        fn_ltr = anf.normalize_hy('fn main() {\n x := map[string]int{"k": 1}\n take(x, use(x))\n}')
        self.assertEqual(len(fn_ltr.diags), 1)
        self.assertEqual(fn_ltr.diags[0].code, "HYB-E3003")
        self.assertEqual((fn_ltr.diags[0].span.line, fn_ltr.diags[0].span.col), (3, 13))

    def test_loop_phi_backedge_and_positive_control(self) -> None:
        fn_313 = anf.normalize_hy(anf.CRITICAL_CASES["p0_313_loop_backedge_move"][1])
        self.assertEqual([d.code for d in fn_313.diags], ["HYB-E3003"])
        self.assertTrue(any(p.is_loop for p in fn_313.phis))

        fn_372 = anf.normalize_hy(anf.CRITICAL_CASES["p0_372_loop_alias_drift"][1])
        self.assertEqual({d.code for d in fn_372.diags}, {"HYB-E3003"})

        fn_fresh_ok = anf.normalize_hy(
            'fn main() {\n x := map[string]int{"k": 1}\n for i in 0..2 {\n _ := move x\n x = map[string]int{"k": 2}\n }\n}'
        )
        self.assertEqual(fn_fresh_ok.diags, [])

    def test_shadowing_branch_phi_and_defer_pin(self) -> None:
        fn_399 = anf.normalize_hy(anf.CRITICAL_CASES["p0_399_inner_shadow_map"][1])
        self.assertEqual([d.code for d in fn_399.diags], ["HYB-E3001"])

        fn_shadow_ok = anf.normalize_hy(
            'fn main() {\n m := map[string]int{"k": 1}\n if true {\n m := 1\n use(m)\n }\n task worker(move m)\n}'
        )
        self.assertEqual(fn_shadow_ok.diags, [])
        self.assertEqual(anf.normalize_hy('fn main() {\n x := map[string]int{"k": 1}\n if c {\n _ := move x\n } else {\n use(x)\n }\n}').diags, [])
        self.assertEqual(anf.normalize_hy('fn main() {\n x := map[string]int{"k": 1}\n if c {\n _ := move x\n } else {\n _ := move x\n }\n}').diags, [])

        fn_phi = anf.normalize_hy(
            'fn main() {\n x := map[string]int{"k": 1}\n if true {\n x = move x\n }\n use(x)\n}'
        )
        self.assertTrue(any(not p.is_loop for p in fn_phi.phis))
        self.assertEqual({d.code for d in fn_phi.diags}, {"HYB-E3003"})

        fn_defer = anf.normalize_hy('fn main() {\n x := map[string]int{"k": 1}\n defer x\n _ := move x\n}')
        self.assertEqual([d.code for d in fn_defer.diags], ["HYB-E3003"])

    def test_o8_slot_loans_and_positive_in_place_update(self) -> None:
        self.assertEqual([d.code for d in anf.normalize_hy(anf.CRITICAL_CASES["p0_604_o8_slot_rebind"][1]).diags], ["HYB-E3104"])
        self.assertEqual([d.code for d in anf.normalize_hy(anf.CRITICAL_CASES["p0_953_o8_branch_escape"][1]).diags], ["HYB-E3104"])
        self.assertEqual([d.code for d in anf.normalize_hy(anf.CRITICAL_CASES["a3_slot_invalidation"][1]).diags], ["HYB-E3102"])
        self.assertEqual([d.code for d in anf.normalize_hy('fn main() {\n m := map[string]int{"a": 1}\n n := &m["a"]\n alias := n\n m["b"] = 2\n use(alias)\n}').diags], ["HYB-E3102"])
        self.assertEqual([d.code for d in anf.normalize_hy('fn main() {\n m := map[string]int{"a": 1}\n n := &m["a"]\n alias := n\n _ := move m\n use(alias)\n}').diags], ["HYB-E3003"])

        fn_valid_slot = anf.normalize_hy(
            'fn main() {\n m := map[string]int{"a": 1}\n s := &m["a"]\n s.v = 3\n m["b"] = 2\n}'
        )
        self.assertEqual(fn_valid_slot.diags, [])

    def test_live_corpus_equivalence_and_hy_check_audit(self) -> None:
        rep = anf.evaluate_suite()
        self.assertTrue(rep["all_equivalent"])
        self.assertGreaterEqual(rep["corpus_total"], 122)
        self.assertEqual(rep["corpus_equivalent"], rep["corpus_total"])
        mismatches = rep["corpus_total"] - rep["corpus_equivalent"]
        self.assertEqual(mismatches, 0)

        red = rep["ast_reduction"]
        self.assertGreaterEqual(red["unwrap_parens_calls"], 230)
        self.assertGreaterEqual(red["expr_kind_arms"], 1200)
        self.assertGreaterEqual(red["scope_push_pop_calls"], 40)
        self.assertEqual(anf.measure_hy_check_reduction(pathlib.Path("/nonexistent_w028"))["hy_check_files"], 0)
        crlf_src = anf.CRITICAL_CASES["ltr_call_arg_move_use"][1].replace("\n", "\r\n")
        self.assertEqual(anf.normalize_hy(crlf_src).diags, anf.check_tree_walk(crlf_src))

    def test_cli_check_and_json_modes(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_check = anf.main(["--check"])
        self.assertEqual(rc_check, 0)
        self.assertIn("critical=10/10", buf.getvalue())

        jbuf = io.StringIO()
        with contextlib.redirect_stdout(jbuf):
            rc_json = anf.main(["--json"])
        self.assertEqual(rc_json, 0)
        payload = json.loads(jbuf.getvalue())
        self.assertTrue(payload["all_equivalent"])
        self.assertEqual(payload["critical_cases_passed"], 10)


if __name__ == "__main__":
    unittest.main()
