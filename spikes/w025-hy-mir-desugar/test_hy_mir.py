#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fail-to-Pass Unit & Integration Test Suite for W-025 (#695):
`Eliminate the Checker/Emitter Semantic Split: Emit-Driven Verification via Explicit Desugared MIR (hy-mir)`
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hy_mir import (  # noqa: E402
    build_benchmark_suite,
    check_hy_mir,
    check_legacy_surface_ast,
    count_emitted_go_primitives,
    count_mir_primitives,
    emit_go_1to1,
    lower_ast_to_mir,
    measure_hy_emit_shadow_table_footprint,
)


class HyMirDesugarVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = build_benchmark_suite()
        self.repo_root = Path(__file__).resolve().parents[2]

    def test_f1_composite_const_to_var_task_race_fail_to_pass(self) -> None:
        ast_file, expected_verdict, expected_code = self.suite["F1_composite_const_var_task_race"]
        # Legacy surface AST misses that composite `const` lowers to mutable Go `var`
        self.assertEqual(len(check_legacy_surface_ast(ast_file)), 0)
        mir_file = lower_ast_to_mir(ast_file)
        diags = check_hy_mir(mir_file)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, expected_code)
        self.assertEqual(expected_verdict, "REJECT")

    def test_f2_composite_const_field_mutation_rejected(self) -> None:
        ast_file, _, expected_code = self.suite["F2_composite_const_field_mutation"]
        self.assertEqual(len(check_legacy_surface_ast(ast_file)), 0)
        mir_file = lower_ast_to_mir(ast_file)
        diags = check_hy_mir(mir_file)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, expected_code)

    def test_f3_implicit_addr_of_pointer_param_task_escape_fail_to_pass(self) -> None:
        ast_file, _, expected_code = self.suite["F3_implicit_addr_of_task_escape"]
        # Surface AST sees `mut_worker(s)` by value and misses emitter's `&s` coercion
        self.assertEqual(len(check_legacy_surface_ast(ast_file)), 0)
        mir_file = lower_ast_to_mir(ast_file)
        diags = check_hy_mir(mir_file)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, expected_code)
        self.assertIn("AddrOfImplicit", diags[0].message)

    def test_f4_implicit_addr_of_map_slot_loan_rejected_with_e3104(self) -> None:
        ast_file, _, expected_code = self.suite["F4_implicit_addr_of_map_slot_loan"]
        self.assertEqual(len(check_legacy_surface_ast(ast_file)), 0)
        mir_file = lower_ast_to_mir(ast_file)
        diags = check_hy_mir(mir_file)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, expected_code)

    def test_f5_branch_slot_loan_delete_before_storeslot_rejected_with_e3102(self) -> None:
        ast_file, _, expected_code = self.suite["F5_branch_slot_loan_delete_invalidation"]
        self.assertEqual(len(check_legacy_surface_ast(ast_file)), 0)
        mir_file = lower_ast_to_mir(ast_file)
        diags = check_hy_mir(mir_file)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, expected_code)

    def test_all_five_positive_controls_accept_and_satisfy_1to1_emission_invariant(self) -> None:
        for case_id in (
            "P1_scalar_const_task_share",
            "P2_value_param_struct_call",
            "P3_sync_implicit_addr_of_call",
            "P4_scoped_map_slot_loan_writeback",
            "P5_early_return_slot_loan_flush",
        ):
            with self.subTest(case_id=case_id):
                ast_file, expected_verdict, _ = self.suite[case_id]
                mir_file = lower_ast_to_mir(ast_file)
                diags = check_hy_mir(mir_file)
                self.assertEqual(diags, [])
                self.assertEqual(expected_verdict, "ACCEPT")

                go_src = emit_go_1to1(mir_file)
                mir_counts = count_mir_primitives(mir_file)
                go_counts = count_emitted_go_primitives(go_src)
                self.assertEqual(mir_counts, go_counts)

    def test_early_return_flushes_storeslot_on_both_control_flow_paths(self) -> None:
        ast_file, _, _ = self.suite["P5_early_return_slot_loan_flush"]
        mir_file = lower_ast_to_mir(ast_file)
        go_src = emit_go_1to1(mir_file)
        # Must emit `m["k"] = n` inside the early-return `if` branch AND before final `return 0`
        self.assertEqual(go_src.count('m["k"] = n'), 2)

    def test_hy_emit_shadow_table_complexity_reduction_exceeds_35_percent(self) -> None:
        metrics = measure_hy_emit_shadow_table_footprint(self.repo_root)
        self.assertGreater(metrics["hy_emit_core_lines"], 5000)
        self.assertGreater(metrics["direct_shadow_table_references"], 120)
        hy_emit_shrinkage_ratio = (
            metrics["estimated_eliminated_loc"] / metrics["hy_emit_core_lines"]
        )
        self.assertGreater(hy_emit_shrinkage_ratio, 0.35)


if __name__ == "__main__":
    unittest.main(verbosity=2)
