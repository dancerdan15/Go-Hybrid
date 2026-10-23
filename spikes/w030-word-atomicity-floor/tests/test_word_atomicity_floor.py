#!/usr/bin/env python3
"""Unit and integration tests for W-030 (#1210) word-atomicity floor below Tier-0."""
from __future__ import annotations
import contextlib, io, json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import word_atomicity_floor as waf  # noqa: E402


class TestWordAtomicityFloor(unittest.TestCase):
    def test_o46_single_word_scalars_pointers_and_channels_accepted(self) -> None:
        structs = waf.parse_structs("struct plain { n int; m int; k int }\nstruct locks { mu sync.mutex }")
        for ty in ("int", "int64", "uint64", "bool", "uintptr", "sync.mutex", "chan int", "<-chan string", "*int", "*plain", "plain", "locks"):
            layout = waf.classify_type(ty, structs)
            self.assertEqual(layout.kind, "SINGLE_WORD", f"expected SINGLE_WORD for {ty}")
            self.assertEqual(layout.multiword_fields, ())

    def test_o46_multi_word_headers_and_structs_classified(self) -> None:
        self.assertEqual(waf.classify_type("string").words, 2)
        self.assertEqual(waf.classify_type("any").words, 2)
        self.assertEqual(waf.classify_type("interface{}").words, 2)
        self.assertEqual(waf.classify_type("[]int").words, 3)
        for ty in ("string", "any", "interface{}", "[]int"):
            self.assertEqual(waf.classify_type(ty).kind, "MULTI_WORD_HEADER")
        structs = waf.parse_structs("struct cell { count int; s []int; label string }")
        cell_layout = waf.classify_type("*cell", structs)
        self.assertEqual(cell_layout.kind, "MULTI_WORD_STRUCT")
        self.assertEqual(cell_layout.multiword_fields, (("s", "[]int"), ("label", "string")))

    def test_map_residual_classification_and_mutex_waiver(self) -> None:
        mutex_map = 'fn main() { m := map[string]int{}; managed("ok", "mutex", "X-1") { task w(m) } }'
        other_map = 'fn main() { m := map[string]int{}; managed("bad", "other", "X-2") { task w(m) } }'
        c_ok = waf.analyze_source(mutex_map)
        c_bad = waf.analyze_source(other_map)
        self.assertEqual(len(c_ok), 1)
        self.assertEqual(c_ok[0].layout.kind, "MAP_RESIDUAL")
        self.assertFalse(c_ok[0].requires_boxing)
        self.assertFalse(c_ok[0].map_unprotected)
        self.assertTrue(c_bad[0].map_unprotected)

    def test_hyb_e8001_rejects_unboxed_multi_word_tearing_repros(self) -> None:
        for name, src, expected_field in (
            ("torn.hy", waf.TORN_HY, ("s", "[]int")),
            ("torn_write.hy", waf.TORN_WRITE_HY, ("s", "[]int")),
            ("confuse.hy", waf.CONFUSE_HY, ("v", "any")),
        ):
            boxed = [c for c in waf.analyze_source(src) if c.requires_boxing]
            self.assertEqual(len(boxed), 3, f"{name} must detect &c struct + 2 header args at HYB-E8001")
            self.assertEqual(boxed[0].arg, "&c")
            self.assertEqual(boxed[0].layout.kind, "MULTI_WORD_STRUCT")
            self.assertIn(expected_field, boxed[0].layout.multiword_fields)
            self.assertTrue(all(c.layout.kind == "MULTI_WORD_HEADER" for c in boxed[1:]))

    def test_positive_control_single_word_managed_requires_zero_boxing(self) -> None:
        src = """struct stats { hits int; misses int }
fn bump(s *stats, ch chan int) { s.hits = s.hits + 1 }
fn main() {
    managed("single-word fields only", "atomic", "STAT-1") {
        s := stats{hits: 0, misses: 0}; var ch chan int
        task bump(&s, ch)
    }
}"""
        crossings = waf.analyze_source(src)
        self.assertTrue(crossings)
        self.assertTrue(all(not c.requires_boxing for c in crossings))
        lowered, changed = waf.lower_to_word_atomic_go(src)
        self.assertEqual(changed, 0)
        self.assertEqual(lowered, src)

    def test_atomic_pointer_lowering_transforms_repros_deterministically(self) -> None:
        lowered_slice, n_slice = waf.lower_to_word_atomic_go(waf.TORN_HY)
        self.assertEqual(n_slice, 4)
        self.assertIn("s atomic.Pointer[[]int]", lowered_slice)
        self.assertIn("_box_small := small; c.s.Store(&_box_small)", lowered_slice)
        self.assertIn("_box_big := big; c.s.Store(&_box_big)", lowered_slice)
        self.assertIn("x := *c.s.Load()", lowered_slice)

        lowered_any, n_any = waf.lower_to_word_atomic_go(waf.CONFUSE_HY)
        self.assertEqual(n_any, 4)
        self.assertIn("v atomic.Pointer[any]", lowered_any)
        self.assertIn("_box_a := a; c.v.Store(&_box_a)", lowered_any)
        self.assertIn("v := *c.v.Load()", lowered_any)

    def test_falsifiers_f0_to_f4_and_live_corpus_zero_cost(self) -> None:
        rep = waf.audit_repository()
        f = rep["falsifiers"]
        self.assertTrue(f["F0_unlowered_tearing"]["reproduced"])
        self.assertEqual(f["F0_unlowered_tearing"]["confuse_fault_address"], "0x1000")
        self.assertTrue(f["F1_lowered_word_atomic_floor"]["verified_tear_free"])
        self.assertTrue(f["F2_microbenchmark_budget"]["passes_budget"])

        f3 = rep["F3_shipped_corpus_zero_cost"]
        self.assertEqual(f3["corpus_total"], 126)
        self.assertEqual(f3["accept_managed_files"], ["02-shared-map-managed.hy", "18-pool-shared-struct-managed.hy"])
        self.assertEqual(f3["examples_total"], 11)
        self.assertEqual(f3["shipped_emitted_lines_changed"], 0)
        self.assertTrue(f3["zero_regression"])
        self.assertTrue(all(rep["F4_spec_guarantees_audit"].values()))

    def test_cli_check_and_json_modes(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = waf.main(["--check", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["F3_shipped_corpus_zero_cost"]["zero_regression"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(waf.main(["--check"]), 0)

    def test_adversarial_cyclic_nested_types_and_aliases(self) -> None:
        structs = waf.parse_structs("struct Node { next *Node; s []int }\nstruct A { b B }\nstruct B { a A }\nstruct Inner { s []int }\nstruct Outer { inner Inner }\ntype SliceAlias = []int")
        self.assertEqual(waf.classify_type("*Node", structs).multiword_fields, (("s", "[]int"),))
        self.assertEqual(waf.classify_type("A", structs).kind, "SINGLE_WORD")
        self.assertEqual(waf.classify_type("*Outer", structs).multiword_fields, (("s", "[]int"),))
        self.assertEqual(waf.classify_type("SliceAlias", structs).kind, "MULTI_WORD_HEADER")

    def test_adversarial_sequential_managed_blocks_and_missing_root(self) -> None:
        src = 'fn main() {\r\n m := map[string]int{}\r\n managed("ok", "mutex", "M-1") { task w1(m) }\r\n managed("race", "atomic", "M-2") { task w2(m) }\r\n task outside(m)\r\n}'
        self.assertEqual([(c.category, c.map_unprotected) for c in waf.analyze_source(src)], [("mutex", False), ("atomic", True)])
        empty_rep = waf.audit_repository(Path("__nonexistent_adversarial_root__"))
        self.assertEqual(empty_rep["F3_shipped_corpus_zero_cost"]["corpus_total"], 0)
        self.assertFalse(any(empty_rep["F4_spec_guarantees_audit"].values()))


if __name__ == "__main__":
    unittest.main()
