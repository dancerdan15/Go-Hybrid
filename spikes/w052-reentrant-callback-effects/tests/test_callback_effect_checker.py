from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from callback_effect_checker import CURATED_CALLBACK_CONTRACTS, Modality, audit_live_corpus, check_program, main  # noqa: E402


class CallbackEffectCheckerTests(unittest.TestCase):
    def test_contract_registry_modalities_and_subject_args(self) -> None:
        self.assertEqual(CURATED_CALLBACK_CONTRACTS["sort.slice"].modality, Modality.SYNC_SCOPED)
        self.assertEqual(CURATED_CALLBACK_CONTRACTS["sort.slice"].subject_arg, 0)
        self.assertEqual(CURATED_CALLBACK_CONTRACTS["bytes.map"].subject_arg, 1)
        self.assertEqual(CURATED_CALLBACK_CONTRACTS["http.handle_func"].modality, Modality.CONCURRENT_MULTI)
        self.assertEqual(CURATED_CALLBACK_CONTRACTS["http.serve_mux.handle"].modality, Modality.CONCURRENT_MULTI)
        self.assertNotIn("mux.handle", CURATED_CALLBACK_CONTRACTS)
        self.assertNotIn("foreign.register_hook", CURATED_CALLBACK_CONTRACTS)

    def test_f1_hyb_e5201_reentrant_subject_and_alias_rejected(self) -> None:
        src = (
            "items := []int{3, 1, 2}\nalias := items\nsub := items[1:]\n"
            "sort.slice(items, fn(i int, j int) bool { items = append(items, 9); return items[i] < items[j] })\n"
            "bytes.map(fn(r rune) rune { alias[0] = 42; return r }, items)\n"
            "filepath.walk(items, fn(p string) { delete(alias, p) })\n"
            "sort.slice(items, fn(i int, j int) bool { sub[0] = 99; return items[i] < items[j] })\n"
            "sort.slice(items[1:], fn(i int, j int) bool { items[0] = 7; return true })\n"
            'managed("verified fixed-cap buffer", "interop") { sort.slice(items, fn(i int, j int) bool { items[0] = 1; return true }) }\n'
        )
        self.assertEqual(
            [(d.code, d.subject) for d in check_program(src)],
            [("HYB-E5201", "items"), ("HYB-E5201", "alias"), ("HYB-E5201", "alias"), ("HYB-E5201", "sub"), ("HYB-E5201", "items")],
        )

    def test_f1_sync_scoped_disjoint_local_accumulator_positive_control(self) -> None:
        src = (
            "items := []int{3, 1, 2}\nvar comps int = 0\nseen := map[int]int{}\n"
            "sort.slice(items, fn(i int, j int) bool { comps += 1; seen[i] = j; return items[i] < items[j] })\n"
        )
        self.assertEqual(check_program(src), [])

    def test_f2_hyb_e5202_concurrent_mutable_capture_rejected(self) -> None:
        src = (
            "counts := map[string]int{}\nhits := 0\nstate := &counts\nitems := []int{1, 2}\nlet sub = items[1:]\n"
            "mux := http.new_serve_mux()\n"
            'http.handle_func("/race", fn(w ResponseWriter, r *Request) { counts["hits"] += 1 })\n'
            'mux.handle("/ping", http.handler(fn(w ResponseWriter, r *Request) { hits |= 1 }))\n'
            "time.after_func(10, fn(t int) { println(state) })\n"
            'http.handle_func("/sub", fn(w ResponseWriter, r *Request) { sub[0] = 9 })\n'
        )
        self.assertEqual(
            [(d.code, d.subject) for d in check_program(src)],
            [("HYB-E5202", "counts"), ("HYB-E5202", "hits"), ("HYB-E5202", "state"), ("HYB-E5202", "sub")],
        )

    def test_f2_concurrent_multi_channel_and_immutable_let_positive_control(self) -> None:
        src = (
            'let prefix = "/api"\nconst version = 2\nevents := make(chan string, 8)\ncounts := map[string]int{}\nhits := 0\n'
            'http.handle_func("/ok", fn(w ResponseWriter, r *Request) { hits := 0; hits++; events <- prefix; println(version, "counts", r.counts) })\n'
        )
        self.assertEqual(check_program(src), [])

    def test_f3_hyb_e5203_unannotated_foreign_callback_fail_closed_and_managed(self) -> None:
        src = (
            'let tag = "v1"\nhook := fn(x int) { println(tag) }\n'
            "foreign.register_hook(fn(x int) { println(tag) })\n"
            "foreign.register_hook(hook)\n"
            'managed("", "memory") { foreign.register_hook(fn(x int) { println(tag) }) }\n'
            'managed("verified single-shot", "interop") { foreign.register_hook(fn(x int) { println(tag) }) }\n'
        )
        self.assertEqual([(d.code, d.subject) for d in check_program(src)], [("HYB-E5203", "foreign.register_hook")] * 3)

    def test_rule_fc_assigned_call_result_not_treated_as_closure_literal(self) -> None:
        src = (
            'let tag = "v1"\nhits := 0\nh := foreign.register_hook(fn(x int) { hits++ })\n'
            "let g = foreign.register_hook(fn(x int) { println(tag) })\n"
        )
        self.assertEqual([(d.code, d.subject) for d in check_program(src)], [("HYB-E5203", "foreign.register_hook")] * 2)
        self.assertEqual(check_program('let tag = "v1"\nhook := fn(x int) { println(tag) }\n'), [])

    def test_assigned_concurrent_call_is_analysed(self) -> None:
        src = 'counts := map[string]int{}\nerr := http.handle_func("/x", fn(w ResponseWriter, r *Request) { counts["k"] += 1 })\n'
        self.assertEqual([(d.code, d.subject) for d in check_program(src)], [("HYB-E5202", "counts")])

    def test_rule_fc_tuple_assign_underscore_return_forms(self) -> None:
        src = (
            'let tag = "v1"\nhits := 0\n'
            "h, err := foreign.register_hook(fn(x int) { hits++ })?\n"
            "h = foreign.register_hook(fn(x int) { println(tag) })\n"
            "_ = foreign.register_hook(fn(x int) { println(tag) })\n"
            "return foreign.register_hook(fn(x int) { println(tag) })\n"
        )
        self.assertEqual([(d.code, d.subject) for d in check_program(src)], [("HYB-E5203", "foreign.register_hook")] * 4)
        conc = 'counts := map[string]int{}\nh, err := http.handle_func("/x", fn(w ResponseWriter, r *Request) { counts["k"] += 1 })?\n'
        self.assertEqual([(d.code, d.subject) for d in check_program(conc)], [("HYB-E5202", "counts")])
        self.assertEqual(check_program('hits := 0\nhttp.handle_func("/ok", fn(w, r) { hits := 0; hits++ })\n'), [])

    def test_explicit_pointer_typed_mux_receiver(self) -> None:
        src = 'hits := 0\nvar mux *http.serve_mux\nmux.handle("/p", http.handler(fn(w, r) { hits++ }))\n'
        self.assertEqual([(d.code, d.subject) for d in check_program(src)], [("HYB-E5202", "hits")])

    def test_mux_contract_keyed_on_receiver_type_not_identifier(self) -> None:
        renamed = (
            'hits := 0\nrouter := http.new_serve_mux()\n'
            'router.handle("/ping", http.handler(fn(w ResponseWriter, r *Request) { hits++ }))\n'
        )
        self.assertEqual([(d.code, d.subject) for d in check_program(renamed)], [("HYB-E5202", "hits")])
        undeclared = 'mux.handle("/ping", http.handler(fn(w ResponseWriter, r *Request) { println(1) }))\n'
        self.assertEqual([(d.code, d.subject) for d in check_program(undeclared)], [("HYB-E5203", "mux.handle")])
        control = 'router := http.new_serve_mux()\nrouter.handle("/hello", http.handler(fn(w, r) { fmt.fprintln(w, "hi") }))\n'
        self.assertEqual(check_program(control), [])

    def test_live_corpus_audit_and_cli_check_and_json_modes(self) -> None:
        report = audit_live_corpus()
        self.assertTrue(report["all_passed"])
        self.assertTrue(report["renamed_receiver_ok"])
        self.assertEqual((report["examples_total"], report["examples_false_positives"]), (10, 0))
        self.assertTrue(report["contested_10_a5_flipped"])
        buf_check, buf_json = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_check):
            self.assertEqual(main(["--check"]), 0)
        self.assertIn("all_passed=True", buf_check.getvalue())
        with redirect_stdout(buf_json):
            self.assertEqual(main(["--check", "--json"]), 0)
        self.assertEqual(json.loads(buf_json.getvalue())["issue"], 1256)


if __name__ == "__main__":
    unittest.main()
