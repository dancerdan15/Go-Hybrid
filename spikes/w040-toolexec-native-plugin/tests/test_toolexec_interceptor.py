"""Unit and integration tests for W-040 (#951) -toolexec native compiler plugin."""
from __future__ import annotations
import io, json, sys, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import toolexec_interceptor as ti  # noqa: E402


class TestToolexecInterceptor(unittest.TestCase):
    def test_parse_toolexec_argv_variants_and_errors(self) -> None:
        with self.assertRaises(ValueError):
            ti.parse_toolexec_argv([])
        probe = ti.parse_toolexec_argv([r"C:\Go\pkg\tool\windows_amd64\compile.exe", "-V=full"])
        self.assertEqual(probe.tool, "compile")
        self.assertTrue(probe.is_version_probe)
        inv = ti.parse_toolexec_argv([
            "/usr/local/go/pkg/tool/linux_amd64/compile",
            "-o", " work/b001/_pkg_.a", "-p", "example.com/hy", "-complete",
            "-importcfg", "work/b001/importcfg", "main.go", "calc.hy",
        ])
        self.assertEqual(inv.tool, "compile")
        self.assertEqual(inv.package, "example.com/hy")
        self.assertEqual(inv.output, " work/b001/_pkg_.a")
        self.assertEqual(inv.inputs, ("main.go", "calc.hy"))
        self.assertFalse(inv.is_version_probe)
        other = ti.parse_toolexec_argv(["/usr/bin/vet", "main.go"])
        self.assertEqual(other.tool, "other")

    def test_rewrite_toolexec_argv_compile_link_and_probe(self) -> None:
        probe = ti.parse_toolexec_argv(["/go/tool/compile", "-V=full", "-lhyrt", "calc.hy"])
        self.assertEqual(ti.rewrite_toolexec_argv(probe), ["/go/tool/compile", "-V=full", "-lhyrt", "calc.hy"])
        comp = ti.parse_toolexec_argv(["/go/tool/compile", "-p", "main", "-o", "out.a", "main.go", "calc.hy"])
        self.assertEqual(
            ti.rewrite_toolexec_argv(comp),
            ["/go/tool/compile", "-p", "main", "-o", "out.a", "main.go", "calc.hy.go"],
        )
        self.assertEqual(
            ti.rewrite_toolexec_argv(comp, {"calc.hy": "/tmp/gen_calc.hy.go"})[-1],
            "/tmp/gen_calc.hy.go",
        )
        link = ti.parse_toolexec_argv([
            "/go/tool/link", "-o", "app", "-L", "/path/libhyrt", "-lhyrt", "main.a", "/path/libhyrt.a",
        ])
        rewritten_link = ti.rewrite_toolexec_argv(link)
        self.assertEqual(rewritten_link, ["/go/tool/link", "-o", "app", "main.a"])

    def test_ast_invariants_reject_all_negative_hazards(self) -> None:
        for name, (src, code) in ti.NEGATIVE_CASES.items():
            diags = ti.check_ast_invariants(src, f"{name}.hy")
            self.assertTrue(any(code in d for d in diags), f"expected {code} for {name}, got {diags}")
        # Transitive 3-hop alias freeze (HYB-E3003) + move-rebind mutability (HYB-E3001)
        d_alias = ti.check_ast_invariants(
            "fn f() {\n  a := map[string]int{}\n  b := a\n  c := b\n  d := move a\n  _ = c\n  task w(d)\n}", "alias.hy"
        )
        self.assertTrue(any("HYB-E3003" in d and "'c'" in d for d in d_alias) and any("HYB-E3001" in d and "'d'" in d for d in d_alias))
        # Map delete during live slot loan (HYB-E3102)
        d_del = ti.check_ast_invariants(
            "fn f() {\n  m := map[string]int{}\n  p := &m[k]\n  delete(m, k)\n  _ = p\n}", "del.hy"
        )
        self.assertTrue(any("HYB-E3102" in d for d in d_del))
        # Unmoved mutable channel send (HYB-E3002)
        d_send = ti.check_ast_invariants(
            "fn f() {\n  xs := []int{1, 2}\n  ch <- xs\n}", "send.hy"
        )
        self.assertTrue(any("HYB-E3002" in d for d in d_send))
        # Direct address return (HYB-E3104)
        d_ret = ti.check_ast_invariants(
            "fn f() {\n  m := map[string]int{}\n  return &m[k]\n}", "ret.hy"
        )
        self.assertTrue(any("HYB-E3104" in d for d in d_ret))

    def test_ast_invariants_accept_all_positive_controls(self) -> None:
        for name, src in ti.CANONICAL_PATTERNS.items():
            self.assertEqual(ti.check_ast_invariants(src, f"{name}.hy"), [], f"unexpected false positive in {name}")

    def test_transpile_hy_to_go_and_dwarf_diagnostic_remapping(self) -> None:
        src = (
            "// header comment\n"
            "pub fn sum_concurrent(xs []int, shards int) int {\n"
            "    for _ in 0..shards {\n"
            "        for v in xs {\n"
            "            task worker(move v)\n"
            "        }\n"
            "    }\n"
            "    return 0\n"
            "}\n"
        )
        res = ti.transpile_hy_to_go(src, "calc.hy", "main")
        self.assertEqual(res.diagnostics, ())
        self.assertIn("func SumConcurrent(xs []int, shards int) int {", res.go_source)
        self.assertIn("for hyBlank0 := 0; hyBlank0 < shards; hyBlank0++ {", res.go_source)
        self.assertIn("for _, v := range xs {", res.go_source)
        self.assertIn("go worker(v)", res.go_source)
        self.assertIn("//line calc.hy:2:1", res.go_source)
        first_map = res.mappings[0]
        self.assertEqual(ti.remap_dwarf_line(first_map.go_line, res.mappings), ("calc.hy", 2, 1))
        self.assertIsNone(ti.remap_dwarf_line(9999, res.mappings))
        raw_err = f"calc.hy.go:{first_map.go_line}:12: undefined: worker"
        self.assertEqual(ti.remap_diagnostic(raw_err, res.mappings, "calc.hy.go"), "calc.hy:2:12: undefined: worker")
        self.assertEqual(ti.remap_diagnostic("calc.hy.go:999:1: eof", res.mappings, "calc.hy.go"), "calc.hy.go:999:1: eof")

    def test_live_audit_repo_and_cli_entrypoints(self) -> None:
        rep = ti.audit_repo()
        self.assertGreaterEqual(rep["corpus_hy_files"], 120)
        self.assertGreaterEqual(rep["example_hy_files"], 10)
        self.assertGreater(rep["hy_check_src_loc"], 30000)
        self.assertGreater(rep["tier0_core_loc"], 10000)
        self.assertEqual(rep["calc_hy_line_mappings"], 26)
        self.assertEqual(rep["calc_hy_diagnostics"], 0)
        self.assertEqual(rep["positive_patterns_passed"], 5)
        self.assertEqual(rep["negative_cases_caught"], 4)
        self.assertFalse(rep["falsified"])
        buf_check = io.StringIO()
        with redirect_stdout(buf_check):
            rc_check = ti.main(["--check"])
        self.assertEqual(rc_check, 0)
        self.assertIn("w040-toolexec-native-plugin OK: 5/5 positive, 4/4 negative, 26 //line mappings", buf_check.getvalue())
        buf_json = io.StringIO()
        with redirect_stdout(buf_json):
            rc_json = ti.main(["--json"])
        self.assertEqual(rc_json, 0)
        payload = json.loads(buf_json.getvalue())
        self.assertEqual(payload["issue"], 951)
        self.assertFalse(payload["falsified"])


if __name__ == "__main__":
    unittest.main()
