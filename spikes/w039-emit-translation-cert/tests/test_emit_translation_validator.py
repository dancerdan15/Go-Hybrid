from __future__ import annotations
import contextlib, io, json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import emit_translation_validator as etv


class TestEmitTranslationValidator(unittest.TestCase):
    def test_extract_hy_obligations_all_families(self) -> None:
        hy = """
        fn sample() {
            let imm = 10
            const c = 20
            idx := 0
            svc := &e.services[idx]
            idx = idx + 1
            plain := &m["k"]
            task worker(imm, idx)
            ch <- move imm
            case <-ch:
            defer (cleanup())
            defer close_it(acquire()?)
            get_ch(1) <- compute(2)?
            a, b := fallible()?
        }
        """
        obs = etv.extract_hy_obligations(hy)
        kinds = [o.kind for o in obs]
        self.assertEqual(
            kinds,
            ["IMMUT_BINDING"] * 2 + ["O8_STORE_BACK"] * 2 + ["VALUE_COPY_BOUNDARY"] * 3 + ["DEFER_EVAL_LIFO"] * 2 + ["HOIST_ORDER"] * 2,
        )
        o8_mut, o8_plain = [o for o in obs if o.kind == "O8_STORE_BACK"]
        self.assertEqual(o8_mut.detail, "snap=1;boundary=1")
        self.assertEqual(o8_plain.detail, "snap=0;boundary=1")
        defers = [o for o in obs if o.kind == "DEFER_EVAL_LIFO"]
        self.assertEqual((defers[0].symbol, defers[0].detail), ("cleanup()", "try=0"))
        self.assertEqual((defers[1].symbol, defers[1].detail), ("close_it(acquire())", "try=1"))

    def test_emit_go_with_certificates_and_zero_overhead(self) -> None:
        hy = """
        fn lowered_ok() {
            let x = 1
            k := 0
            slot := &m[k]
            k = k + 1
            task run(x)
            ch <- x
            defer first()
            defer second(step()?)
            sink(1) <- calc()?
        }
        """
        go_src, decls = etv.emit_go_with_certificates(hy)
        self.assertEqual(len(decls), 7)
        self.assertTrue(all("[0]func()" in d for d in decls))
        self.assertIn("hyKey0 := k", go_src)
        self.assertIn("defer second(_hyTry0)", go_src)
        self.assertIn("LIFO_EXIT_ORDER: second(step()) -> first()", go_src)
        cert = etv.validate_translation(hy, go_src, fn_name="lowered_ok")
        self.assertTrue(cert.valid)
        self.assertEqual(cert.code, "CERTIFIED-BISIMILAR")
        self.assertEqual(cert.violations, ())
        self.assertEqual(cert.runtime_bytes, 0)
        self.assertEqual(cert.obligations_checked, 7)

    def test_validate_translation_rejects_non_zero_witness_footprint(self) -> None:
        hy = 'fn f() {\n    let x = 1\n}'
        bad_go = "package main\ntype _witness_IMMUT_BINDING_0 [1]func()\nfunc f() { const x = 1 }"
        cert = etv.validate_translation(hy, bad_go, fn_name="f")
        self.assertFalse(cert.valid)
        self.assertEqual(cert.runtime_bytes, 8)
        self.assertTrue(any("non-zero witness footprint" in v for v in cert.violations))

    def test_validate_translation_o8_mut_ord_And_positive_controls(self) -> None:
        hy_o8 = 'fn f() {\n    svc := &m["k"]\n}'
        cert_branch = etv.validate_translation(
            hy_o8,
            'func f() {\n    svc := m["k"]\n    if cond {\n        m["k"] = svc\n    } else {\n    }\n    // branch_handoff\n}',
        )
        self.assertFalse(cert_branch.valid)
        self.assertEqual(cert_branch.code, "HYB-TV-O8")
        self.assertTrue(any("incomplete branch handoff" in v for v in cert_branch.violations))
        cert_dual = etv.validate_translation('fn f() {\n    a := &m["k1"]\n    b := &m["k2"]\n}', 'func f() { a := m["k1"]; b := m["k2"]; m["k1"] = a }')
        self.assertEqual((cert_dual.valid, cert_dual.code), (False, "HYB-TV-O8"))

        hy_mut = "fn f() {\n    let x = 1\n    task w(x)\n}"
        cert_ok = etv.validate_translation(hy_mut, "func f() { x := 1; y := 2; y++; go w(x) }")
        self.assertTrue(cert_ok.valid)
        cert_bad = etv.validate_translation(hy_mut, "func f() { x := 1; x = 3; x++; x += 2; go w(&x) }")
        self.assertFalse(cert_bad.valid)
        self.assertEqual(cert_bad.code, "HYB-TV-MUT")
        self.assertEqual(len(cert_bad.violations), 3)

        hy_ord = "fn f() {\n    defer cleanup()\n}"
        cert_missing = etv.validate_translation(hy_ord, "func f() { cleanup() }")
        self.assertFalse(cert_missing.valid)
        self.assertEqual(cert_missing.code, "HYB-TV-ORD")

    def test_adversarial_mutation_suite_100_percent_recall(self) -> None:
        mutations = etv.synthesize_adversarial_mutations()
        self.assertEqual(len(mutations), 50)
        counts = {"HYB-TV-O8": 0, "HYB-TV-MUT": 0, "HYB-TV-ORD": 0}
        for m in mutations:
            counts[m["family"]] += 1
            cert = etv.validate_translation(m["hy"], m["go"], fn_name=m["id"])
            self.assertFalse(cert.valid, f"Mutation {m['id']} escaped detection")
            self.assertEqual(cert.code, m["family"], f"Mutation {m['id']} wrong code {cert.code}")
        self.assertEqual(counts, {"HYB-TV-O8": 18, "HYB-TV-MUT": 16, "HYB-TV-ORD": 16})

    def test_live_corpus_audit_and_cli_modes(self) -> None:
        report = etv.audit_live_corpus()
        self.assertEqual(report["mutations_total"], 50)
        self.assertEqual(report["mutations_caught"], 50)
        self.assertEqual(report["mutation_recall_pct"], 100.0)
        self.assertEqual(report["corpus_certified"], 5)
        self.assertEqual(report["corpus_total"], 5)
        self.assertEqual(report["runtime_overhead_bytes"], 0)
        self.assertTrue(report["hy_emit_hooks_present"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = etv.main(["--check", "--json"])
        self.assertEqual(rc, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["mutations_caught"], 50)


if __name__ == "__main__":
    unittest.main()
