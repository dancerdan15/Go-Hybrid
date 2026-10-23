from __future__ import annotations
import io, json, sys, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from go_ast_materializer import (
     GO_KEYWORDS, BindingId, GoAstNode, O8SlotLoan, audit_live_repo,
    collect_colliding_bindings, compute_dominators, main, materialize,
    normalize_ir, place_o8_store_backs, verify_ast,
)


class GoAstMaterializerTests(unittest.TestCase):
    def test_binding_id_keyword_shadowing_and_unused(self) -> None:
        for kw in ("func", "select", "chan", "map", "type", "range", "fallthrough", "interface"):
            self.assertIn(kw, GO_KEYWORDS)
            self.assertEqual(BindingId(7, kw).go_ident(), f"hy_{kw}_7")
        self.assertEqual((BindingId(1, "count").go_ident(), BindingId(2, "a$b").go_ident(), BindingId(9, "$").go_ident()), ("count", "a_b", "v"))
        self.assertEqual(BindingId(3, "unused_tmp", used=False).go_ident(), "_")
        b_outer, b_inner, b_other = BindingId(10, "x"), BindingId(11, "x"), BindingId(12, "y")
        tree = GoAstNode("Block", children=(
            GoAstNode("VarDecl", binding=b_outer, children=(GoAstNode("Lit", value=1),)),
            GoAstNode("VarDecl", binding=b_inner, children=(GoAstNode("Lit", value=2),)),
            GoAstNode("VarDecl", binding=b_other, children=(GoAstNode("Ident", binding=b_inner),)),
        ))
        self.assertEqual((collect_colliding_bindings(tree), materialize(tree)), ({11}, "x := 1\nhy_x_11 := 2\ny := hy_x_11"))
        adv = GoAstNode("Block", children=(GoAstNode("VarDecl", binding=BindingId(10, "hy_fallthrough_0"), children=(GoAstNode("Lit", value=1),)), GoAstNode("VarDecl", binding=BindingId(0, "fallthrough", 1), children=(GoAstNode("Lit", value=2),))))
        self.assertEqual(materialize(adv), "hy_hy_fallthrough_0_10 := 1\nhy_fallthrough_0 := 2")

    def test_unused_vardecl_materializes_blank_assignment(self) -> None:
        unused = BindingId(4, "dead", used=False)
        decl = GoAstNode("VarDecl", binding=unused, children=(GoAstNode("Lit", value=42),))
        self.assertEqual(materialize(decl), "_ = 42")

    def test_normalize_ir_collapses_nested_parens(self) -> None:
        b = BindingId(1, "v")
        wrapped = GoAstNode("Paren", children=(GoAstNode("Paren", children=(
            GoAstNode("Binary", op="+", children=(
                GoAstNode("Paren", children=(GoAstNode("Ident", binding=b),)),
                GoAstNode("Lit", value=5),
            )),
        )),))
        norm = normalize_ir(wrapped)
        self.assertEqual(norm.kind, "Binary")
        self.assertEqual(norm.children[0].kind, "Ident")
        self.assertTrue(verify_ast(GoAstNode("Block", children=(
            GoAstNode("VarDecl", binding=b, children=(GoAstNode("Lit", value=0),)), norm,
        ))).ok)

    def test_materialize_precedence_non_assoc_and_unary(self) -> None:
        a, b, c = GoAstNode("Lit", value="a"), GoAstNode("Lit", value="b"), GoAstNode("Lit", value="c")
        mul_add = GoAstNode("Binary", op="*", children=(GoAstNode("Binary", op="+", children=(a, b)), c))
        self.assertEqual(materialize(mul_add), "(a + b) * c")
        add_mul = GoAstNode("Binary", op="+", children=(a, GoAstNode("Binary", op="*", children=(b, c))))
        self.assertEqual(materialize(add_mul), "a + b * c")
        sub_rhs = GoAstNode("Binary", op="-", children=(a, GoAstNode("Binary", op="-", children=(b, c))))
        self.assertEqual(materialize(sub_rhs), "a - (b - c)")
        sub_lhs = GoAstNode("Binary", op="-", children=(GoAstNode("Binary", op="-", children=(a, b)), c))
        self.assertEqual(materialize(sub_lhs), "a - b - c")
        self.assertEqual(materialize(GoAstNode("Binary", op="/", children=(a, GoAstNode("Binary", op="*", children=(b, c))))), "a / (b * c)")
        self.assertEqual(materialize(GoAstNode("Binary", op="*", children=(a, GoAstNode("Binary", op="/", children=(b, c))))), "a * (b / c)")
        self.assertEqual(materialize(GoAstNode("Binary", op="&", children=(a, GoAstNode("Binary", op="<<", children=(b, c))))), "a & (b << c)")
        neg_neg = GoAstNode("Unary", op="-", children=(GoAstNode("Unary", op="-", children=(a,)),))
        self.assertEqual((materialize(neg_neg), materialize(GoAstNode("Return"))), ("-(-a)", "return"))
        fn = BindingId(1, "work")
        call = GoAstNode("Call", children=(GoAstNode("Ident", binding=fn), a, b))
        self.assertEqual((materialize(GoAstNode("Defer", children=(call,))), materialize(GoAstNode("Go", children=(call,)))), ("defer work(a, b)", "go work(a, b)"))
        with self.assertRaises(ValueError):
            materialize(GoAstNode("UnknownKind"))

    def test_o8_store_back_ignores_shadowed_inner_binding_and_handles_early_exit(self) -> None:
        m, k, loan = BindingId(1, "m"), BindingId(2, "k"), BindingId(3, "slot")
        m_sh, k_sh, shadow = BindingId(4, "m", 1), BindingId(5, "k", 1), BindingId(6, "slot", 1)
        spec = O8SlotLoan(loan_binding=loan, map_binding=m, key_expr=GoAstNode("Ident", binding=k))
        stmts = [
            GoAstNode("VarDecl", binding=loan, children=(GoAstNode("Index", children=(GoAstNode("Ident", binding=m), GoAstNode("Ident", binding=k))),)),
            GoAstNode("VarDecl", binding=m_sh, children=(GoAstNode("Lit", value=0),)), GoAstNode("VarDecl", binding=k_sh, children=(GoAstNode("Lit", value=1),)),
            GoAstNode("VarDecl", binding=shadow, children=(GoAstNode("Lit", value=99),)), GoAstNode("Return", children=(GoAstNode("Ident", binding=shadow),)),
        ]
        placed = place_o8_store_backs(stmts, spec)
        self.assertEqual(([n.kind for n in placed], "m[k] = slot" in materialize(GoAstNode("Block", children=tuple(placed)))), (["VarDecl", "IndexAssign", "VarDecl", "VarDecl", "VarDecl", "Return"], True))
        self.assertEqual([n.kind for n in place_o8_store_backs([GoAstNode("Return", children=(GoAstNode("Ident", binding=loan),))], spec)], ["IndexAssign", "Return"])

    def test_verify_ast_rejects_e4301_and_e4302_and_accepts_valid_cfg(self) -> None:
        b = BindingId(1, "x")
        bad_surface = GoAstNode("Block", children=(
            GoAstNode("Paren", children=(GoAstNode("Lit", value=1),)),
            GoAstNode("Defer", children=(GoAstNode("Lit", value=1),)),
            GoAstNode("Return", children=(GoAstNode("Try", children=(GoAstNode("Lit", value=1),)),)),
        ))
        cert1 = verify_ast(bad_surface)
        self.assertFalse(cert1.ok)
        self.assertEqual([c for c, _ in cert1.diagnostics], ["HYB-E4301", "HYB-E4301", "HYB-E4301"])

        edges = {0: (1, 2), 1: (3,), 2: (3,), 3: ()}
        dom = compute_dominators(edges, 0)
        self.assertEqual(dom[3], frozenset({0, 3}))
        bad_ssa = GoAstNode("Block", children=(
            GoAstNode("Ident", binding=BindingId(99, "unbound")),
            GoAstNode("VarDecl", binding=b, children=(GoAstNode("Lit", value=1),), block_id=1),
            GoAstNode("Ident", binding=b, block_id=3),
            GoAstNode("Ident", binding=BindingId(5, "flushed"), block_id=0),
        ))
        cert2 = verify_ast(bad_ssa, edges=edges, flushed_loans={5})
        self.assertFalse(cert2.ok)
        self.assertEqual([c for c, _ in cert2.diagnostics], ["HYB-E4302", "HYB-E4302", "HYB-E4302"])

    def test_live_repo_audit_and_cli_flags(self) -> None:
        rep = audit_live_repo()
        self.assertEqual(rep["issue"], 585)
        self.assertTrue(rep["falsifier_1_classification"]["holds"])
        self.assertTrue(rep["falsifier_2_deletion"]["holds"])
        self.assertTrue(rep["falsifier_3_cost_benefit"]["holds"])
        self.assertGreaterEqual(rep["falsifier_3_cost_benefit"]["hy_emit_non_test_loc"], 7000)
        self.assertGreaterEqual(rep["falsifier_3_cost_benefit"]["hy_emit_paren_peel_sites"], 25)
        self.assertGreaterEqual(rep["falsifier_3_cost_benefit"]["hy_emit_lexical_name_scan_sites"], 70)
        self.assertGreaterEqual(rep["falsifier_3_cost_benefit"]["corpus_fixtures_count"], 120)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--check", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["issue"], 585)


if __name__ == "__main__":
    unittest.main()
