#!/usr/bin/env python3
"""Unit and integration tests for W-041 (#1011) Execution-Oracle Fuzzer."""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import race_oracle_fuzzer as rof  # noqa: E402


class TestRaceOracleFuzzer(unittest.TestCase):
    def test_hb_leq_and_vector_clock_race_detection(self) -> None:
        self.assertTrue(rof.hb_leq({0: 1}, {0: 2, 1: 1}))
        self.assertFalse(rof.hb_leq({0: 2, 1: 1}, {0: 1, 1: 2}))
        F = rof.RULE_FAMILIES[1]
        racy = [("alloc_map", "m", "init", F), ("loan_map_slot", "s", "m", F), ("spawn_write", "s", 1, F), ("main_write", "m", 0, F)]
        verdict, detail = rof.execute_oracle(racy)
        self.assertEqual(verdict, "DATA_RACE")
        self.assertIn("task 0 and task 1", detail)
        self.assertEqual(rof.check_program(racy, mode="prefix"), (True, True, None))
        self.assertEqual(rof.check_program(racy, mode="head"), (False, True, "HYB-E3102"))
        synced = [("alloc_map", "m", "init", F), ("chan_sync", "m", 1, F), ("spawn_write", "m", 1, F), ("chan_sync", "m", 1, F), ("main_write", "m", 0, F)]
        self.assertEqual(rof.execute_oracle(synced), ("CLEAN", "ok"))
        self.assertEqual(rof.check_program(synced, mode="head"), (True, True, None))

    def test_nil_map_panic_and_head_rejection(self) -> None:
        F = rof.RULE_FAMILIES[1]
        prog = [("alloc_map", "m", "nil", F), ("main_write", "m", 0, F)]
        verdict, detail = rof.execute_oracle(prog)
        self.assertEqual(verdict, "PANIC_NIL_MAP")
        self.assertIn("nil map m", detail)
        self.assertEqual(rof.check_program(prog, mode="prefix"), (True, True, None))
        self.assertEqual(rof.check_program(prog, mode="head"), (False, True, "HYB-E3105"))

    def test_o8_slice_loan_panics_and_lost_store_backs(self) -> None:
        shapes = rof.build_seeded_p0_shapes()
        v_a, d_a = rof.execute_oracle(shapes["970a_oob_reslice_panic"])
        self.assertEqual(v_a, "PANIC_OOB")
        self.assertIn("slice bounds out of range [1]", d_a)
        self.assertEqual(rof.check_program(shapes["970a_oob_reslice_panic"], mode="head"), (False, True, "HYB-E3108"))
        for key in ("970b_wrong_elem_write", "970c_append_realloc_stale"):
            verdict, _ = rof.execute_oracle(shapes[key])
            self.assertEqual(verdict, "LOST_STORE_BACK")
            self.assertEqual(rof.check_program(shapes[key], mode="head"), (False, True, "HYB-E3108"))
        v_953, d_953 = rof.execute_oracle(shapes["953_match_else_drop_o8"])
        self.assertEqual(v_953, "LOST_STORE_BACK")
        self.assertIn("else_arm", d_953)
        self.assertEqual(rof.check_program(shapes["953_match_else_drop_o8"], mode="head"), (False, True, "HYB-E3109"))

    def test_invalid_ast_guards_and_positive_controls(self) -> None:
        F = rof.RULE_FAMILIES[0]
        self.assertEqual(rof.execute_oracle([("loan_slice_elem", "ln", ("xs", 0), F)])[0], "INVALID_AST")
        self.assertEqual(rof.execute_oracle([("rebind_slice_shrink", "xs", 1, F)])[0], "INVALID_AST")
        self.assertEqual(rof.execute_oracle([("store_back", "ln", "normal", F)])[0], "INVALID_AST")
        self.assertEqual(rof.execute_oracle([("loan_map_slot", "s", "m", F)])[0], "INVALID_AST")
        self.assertEqual(rof.execute_oracle([("main_write", "m", 0, F)])[0], "INVALID_AST")
        clean_slice = [("alloc_slice", "xs", 3, F), ("loan_slice_elem", "ln", ("xs", 1), F), ("store_back", "ln", "normal", F)]
        self.assertEqual(rof.execute_oracle(clean_slice), ("CLEAN", "ok"))
        self.assertEqual(rof.check_program(clean_slice, mode="head"), (True, True, None))

    def test_ddmin_shrink_1_minimal_witness(self) -> None:
        for name, prog in rof.build_seeded_p0_shapes().items():
            verdict, _ = rof.execute_oracle(prog)
            self.assertNotEqual(verdict, "CLEAN", msg=name)
            shrunk = rof.ddmin_shrink(prog, lambda c, v=verdict: rof.execute_oracle(c)[0] == v)
            self.assertLess(len(shrunk), len(prog), msg=name)
            self.assertEqual(rof.execute_oracle(shrunk)[0], verdict, msg=name)
            self.assertFalse(any(op == "noop" for op, _, _, _ in shrunk), msg=name)
            for idx in range(len(shrunk)):
                without_one = shrunk[:idx] + shrunk[idx + 1:]
                self.assertNotEqual(rof.execute_oracle(without_one)[0], verdict, msg=f"{name} not 1-minimal at {idx}")

    def test_dedup_key_alpha_normalization(self) -> None:
        F = rof.RULE_FAMILIES[0]
        p1 = [("alloc_slice", "xs", 2, F), ("loan_slice_elem", "ln", ("xs", 1), F), ("rebind_slice_shrink", "xs", 1, F), ("store_back", "ln", "normal", F)]
        p2 = [("alloc_slice", "arr", 2, F), ("loan_slice_elem", "el", ("arr", 1), F), ("rebind_slice_shrink", "arr", 1, F), ("store_back", "el", "normal", F)]
        k1 = rof.dedup_key(p1, "PANIC_OOB")
        k2 = rof.dedup_key(p2, "PANIC_OOB")
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 16)
        self.assertNotEqual(k1, rof.dedup_key(p1, "LOST_STORE_BACK"))

    def test_seeded_p0_recall_and_evaluation_invariants(self) -> None:
        rep = rof.run_evaluation(seed=20261023, count=100, rate_cap=3)
        self.assertEqual(rep["seeded_p0_total"], 8)
        self.assertEqual(rep["seeded_p0_recalled"], 8)
        self.assertEqual(rep["f1_recall_rate"], 1.0)
        self.assertTrue(rep["f2_all_shrunk_minimal"])
        self.assertEqual(rep["f3_head_residual_rate"], 0.0)
        self.assertEqual(rep["unique_dedup_keys"], 8)
        self.assertEqual(rep["auto_filed_capped"], 3)
        total_gen = sum(v["generated"] for v in rep["rule_family_residuals"].values())
        total_pre_div = sum(v["prefix_exec_divergences"] for v in rep["rule_family_residuals"].values())
        total_head_acc = sum(v["head_accepted"] for v in rep["rule_family_residuals"].values())
        self.assertEqual(total_gen, 100)
        self.assertEqual(total_pre_div, 25)
        self.assertEqual(total_head_acc, 75)

    def test_live_repo_audit_and_cli_modes(self) -> None:
        audit = rof.audit_live_repo()
        self.assertEqual(audit["corpus_total"], 126)
        self.assertEqual(audit["corpus_accept"], 38)
        self.assertEqual(audit["corpus_accept_managed"], 2)
        self.assertEqual(audit["corpus_reject"], 86)
        self.assertFalse(audit["hyfuzz_has_invariant_5"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = rof.main(["--check"])
        self.assertEqual(rc, 0)
        self.assertIn("recall=8/8", buf.getvalue())
        jbuf = io.StringIO()
        with redirect_stdout(jbuf):
            rc_json = rof.main(["--json", "--check"])
        self.assertEqual(rc_json, 0)
        parsed = json.loads(jbuf.getvalue())
        self.assertEqual(parsed["spike_id"], "W-041")


if __name__ == "__main__":
    unittest.main()
