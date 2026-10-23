#!/usr/bin/env python3
"""Unit and integration tests for W-035 (#992): Linear Capability Passports."""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from linear_cap_passports import (  # noqa: E402
    FOUNDING_CORPUS,
    GO_PASSPORT_PREAMBLE,
    analyze_loans_rs,
    audit_repo,
    extract_expected_verdict,
    main,
    transcribe_hy_program,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class LinearCapPassportsTests(unittest.TestCase):
    def test_go_passport_preamble_zero_cost_tokens(self) -> None:
        for token in ("type Cap[T any] [0]byte", "type Unique[T any]", "type Loan[T any, Token any]", "type Consumed[T any] [0]byte", "type Locked[T any, Token any] [0]byte"):
            self.assertIn(token, GO_PASSPORT_PREAMBLE)
        res = transcribe_hy_program("// EXPECTED-VERDICT: ACCEPT\nfunc ok() {\n  m := map[string]int{}\n}\n")
        self.assertEqual((res.verdict, res.token_bytes, res.heap_escapes, res.nesting_depth), ("ACCEPT", 0, 0, 0))

    def test_use_after_move_and_alias_consume_hyb_e3003(self) -> None:
        moved = "// EXPECTED-VERDICT: REJECT\nfunc f() {\n  buf := make_buf()\n  ch <- move buf\n  println(buf)\n}\n"
        r1 = transcribe_hy_program(moved, "move-use")
        self.assertEqual((r1.verdict, r1.rule, r1.diagnostic), ("REJECT", "A1b", "HYB-E3003"))
        self.assertIn("cap.Consume(cap_buf)", r1.emitted_go)
        self.assertIn("cap.Require(cap_buf)", r1.emitted_go)

        consumed = "// EXPECTED-VERDICT: REJECT\nfunc g() {\n  enc := new_enc()\n  out := enc.finish()\n  enc.write(1)\n}\n"
        r2 = transcribe_hy_program(consumed, "consume-use")
        self.assertEqual((r2.verdict, r2.rule, r2.diagnostic), ("REJECT", "A7", "HYB-E3003"))

        alias_write = "// EXPECTED-VERDICT: REJECT\nfunc h() {\n  agg := map[string]int{}\n  m2 := agg\n  ch <- move agg\n  m2[\"k\"] = 1\n}\n"
        r3 = transcribe_hy_program(alias_write, "alias-after-move")
        self.assertEqual((r3.verdict, r3.rule, r3.diagnostic), ("REJECT", "A1b", "HYB-E3003"))

        transitive_paren = "// EXPECTED-VERDICT: REJECT\r\n// UTF-8: αβγ\r\nfunc t() {\r\n  a := map[string]int{}; b := a; c := b; move(a); use(c)\r\n}\r\n"
        r4 = transcribe_hy_program(transitive_paren, "transitive-alias-paren-move")
        self.assertEqual((r4.verdict, r4.rule, r4.diagnostic), ("REJECT", "A1b", "HYB-E3003"))

        double_move = "// EXPECTED-VERDICT: REJECT\nfunc dm() {\n  buf := make_buf()\n  ch <- move buf\n  ch <- move buf\n}\n"
        self.assertEqual(transcribe_hy_program(double_move, "double-move").diagnostic, "HYB-E3003")

    def test_borrow_mutation_conflict_hyb_e3101_and_positive_control(self) -> None:
        bad = '// EXPECTED-VERDICT: REJECT\nfunc f() {\n  for u in doc.array("users") {\n    doc.set("active", true)\n  }\n}\n'
        r_bad = transcribe_hy_program(bad, "a2-reject")
        self.assertEqual((r_bad.verdict, r_bad.rule, r_bad.diagnostic), ("REJECT", "A2", "HYB-E3101"))
        self.assertIn("cap.Borrow[any](cap_doc, _loopTok{})", r_bad.emitted_go)

        ok = '// EXPECTED-VERDICT: ACCEPT\nfunc f() {\n  for u in doc.array("users") {\n    println(u)\n  }\n  doc.set("done", true)\n}\n'
        r_ok = transcribe_hy_program(ok, "a2-accept")
        self.assertEqual((r_ok.verdict, r_ok.diagnostic), ("ACCEPT", None))
        self.assertIn("cap_doc = cap.Release(lock_doc)", r_ok.emitted_go)

    def test_map_slot_invalidation_hyb_e3102_and_positive_control(self) -> None:
        bad = '// EXPECTED-VERDICT: REJECT\nfunc f() {\n  slot := &e.services[id]\n  e.services[fresh] = svc\n  println(slot.port)\n}\n'
        r_bad = transcribe_hy_program(bad, "a3-reject")
        self.assertEqual((r_bad.verdict, r_bad.rule, r_bad.diagnostic), ("REJECT", "A3", "HYB-E3102"))

        ok = '// EXPECTED-VERDICT: ACCEPT\nfunc f() {\n  slot := &e.services[id]\n  println(slot.port)\n  e.services[fresh] = svc\n  slot = &e.services[id2]\n  println(slot.port)\n}\n'
        r_ok = transcribe_hy_program(ok, "a3-accept")
        self.assertEqual((r_ok.verdict, r_ok.diagnostic), ("ACCEPT", None))

    def test_handle_and_loan_escape_hyb_e3103_e3104(self) -> None:
        a6 = "struct Holder {\n  cached json.view\n}\n"
        r_a6 = transcribe_hy_program(a6, "a6-field")
        self.assertEqual((r_a6.verdict, r_a6.rule, r_a6.diagnostic), ("REJECT", "A6", "HYB-E3103"))
        self.assertIn("cap.Loan[json.view, _frame]", r_a6.emitted_go)

        esc = "func bad() *Item {\n  return &items[0]\n}\n"
        r_esc = transcribe_hy_program(esc, "loan-escape")
        self.assertEqual((r_esc.verdict, r_esc.rule, r_esc.diagnostic), ("REJECT", "LOAN-ESCAPE", "HYB-E3104"))

    def test_spawn_require_unique_hyb_e3001_and_positive_controls(self) -> None:
        bad = "func f() {\n  scores := map[string]int{}\n  task worker(scores)\n}\n"
        r_bad = transcribe_hy_program(bad, "a1c-reject")
        self.assertEqual((r_bad.verdict, r_bad.rule, r_bad.diagnostic), ("REJECT", "A1c", "HYB-E3001"))

        ok_managed = '// EXPECTED-VERDICT: ACCEPT-MANAGED\nmanaged("race")\nfunc f() {\n  scores := map[string]int{}\n  task worker(scores)\n}\n'
        self.assertEqual(transcribe_hy_program(ok_managed, "a1c-managed").verdict, "ACCEPT-MANAGED")

        ok_atomic = "func f() {\n  var total atomic.Int64\n  task worker(&total)\n}\n"
        self.assertEqual(transcribe_hy_program(ok_atomic, "a1c-atomic").verdict, "ACCEPT")

    def test_founding_corpus_and_loans_rs_audit(self) -> None:
        self.assertEqual(len(FOUNDING_CORPUS), 20)
        self.assertEqual(extract_expected_verdict("// EXPECTED-VERDICT: REJECT HYB-E3003 (use after move)"), ("REJECT", "HYB-E3003"))
        metrics = analyze_loans_rs(REPO_ROOT)
        self.assertGreaterEqual(metrics["loans_rs_lines"], 8000)
        self.assertGreaterEqual(metrics["total_graph_state_refs"], 450)
        missing = audit_repo(REPO_ROOT / "__missing_root__")
        self.assertEqual((missing["falsifiers_passed"], missing["corpus_total"]), (False, 0))
        report = audit_repo(REPO_ROOT)
        self.assertTrue(report["falsifiers_passed"])
        self.assertEqual((report["corpus_total"], report["corpus_matched"], report["reject_caught"], report["accept_passed"]), (20, 20, 11, 9))

    def test_cli_check_and_json_modes(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--repo-root", str(REPO_ROOT), "--check", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["falsifiers_passed"])
        self.assertEqual(payload["zero_overhead_invariants"]["token_size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
