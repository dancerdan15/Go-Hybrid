#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fail-to-Pass Unit & Integration Test Suite for W-026 (#673 / #610):
- Proof-Carrying FFI Boundary Contracts (`hy-ffi-cert`, W-026 / #610)
- N1/N2 Untyped-Nil Local Declaration & Foreign-Perimeter Verification (`HYB-E4003`, #673 / #672)
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hy_ffi_cert import (  # noqa: E402
    build_benchmark_suite,
    check_hy_ffi_cert,
    check_hy_nil_declarations,
    check_legacy_unverified_boundary,
    compute_certified_stack_class,
    compute_contract_bundle_sha256,
    compute_export_contract_sha256,
    extract_go_broker_bindings_from_repo,
    extract_hyrt_exports_from_repo,
    measure_ffi_tcb_reduction,
)


class HyFfiCertAndUntypedNilVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.suite = build_benchmark_suite(self.repo_root)

    def test_f1_untyped_nil_local_decl_and_closure_walk_fail_to_pass(self) -> None:
        scenario = self.suite["F1_untyped_nil_local_decl_and_closure_walk"]
        # Legacy ascription-gated checker misses all 7 untyped `nil` local declarations
        self.assertEqual(len(check_legacy_unverified_boundary(scenario)), 0)
        diags = check_hy_ffi_cert(scenario)
        self.assertEqual(len(diags), 7)
        for d in diags:
            self.assertEqual(d.code, "HYB-E4003")
        self.assertEqual(
            [d.symbol_or_binding for d in diags],
            ["ch", "x", "y", "a", "_", "inner", "z"],
        )

    def test_f2_recursive_json_parse_small_fastpath_lie_rejected_with_e7002(self) -> None:
        scenario = self.suite["F2_recursive_json_parse_small_fastpath_lie"]
        self.assertEqual(len(check_legacy_unverified_boundary(scenario)), 0)
        diags = check_hy_ffi_cert(scenario)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, "HYB-E7002")
        self.assertEqual(diags[0].symbol_or_binding, "hy_json_parse")
        self.assertIn("DEEP", diags[0].message)

    def test_f3_regex_compile_22kb_frame_default_guard_lie_rejected_with_e7002(self) -> None:
        scenario = self.suite["F3_regex_compile_22kb_frame_default_guard_lie"]
        self.assertEqual(len(check_legacy_unverified_boundary(scenario)), 0)
        diags = check_hy_ffi_cert(scenario)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, "HYB-E7002")
        self.assertEqual(diags[0].symbol_or_binding, "hy_regex_is_match")
        self.assertIn("22976 B", diags[0].message)

    def test_f4_retained_pinned_borrow_and_callback_escape_rejected_with_e7003_and_e7004(self) -> None:
        scenario = self.suite["F4_retained_pinned_borrow_and_callback_escape"]
        self.assertEqual(len(check_legacy_unverified_boundary(scenario)), 0)
        diags = check_hy_ffi_cert(scenario)
        codes = {d.code for d in diags}
        self.assertIn("HYB-E7003", codes)
        self.assertIn("HYB-E7004", codes)

    def test_f5_abi_arity_and_embedded_hash_drift_rejected_with_e7001(self) -> None:
        scenario = self.suite["F5_abi_arity_and_embedded_hash_drift"]
        self.assertEqual(len(check_legacy_unverified_boundary(scenario)), 0)
        diags = check_hy_ffi_cert(scenario)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, "HYB-E7001")
        self.assertEqual(diags[0].symbol_or_binding, "hy_sha3_stream_checksum")

    def test_all_five_positive_controls_accept_and_satisfy_3way_contract_and_nil_home_invariants(self) -> None:
        for case_id in (
            "P1_sha3_small_fastpath_certified",
            "P2_serde_json_split_small_leaf_and_deep_tree",
            "P3_regex_deep_pool_trampoline_certified",
            "P4_ascribed_error_nil_and_zero_value_n1_n3_controls",
            "P5_local_const_nil_e6003_single_locus_control",
        ):
            with self.subTest(case_id=case_id):
                scenario = self.suite[case_id]
                self.assertEqual(check_legacy_unverified_boundary(scenario), [])
                diags = check_hy_ffi_cert(scenario)
                self.assertEqual(diags, [])
                self.assertEqual(scenario.expected_verdict, "ACCEPT")

    def test_all_fifteen_issue_673_untyped_nil_shapes_and_single_locus_controls(self) -> None:
        # 11 negative shapes across top-level fn, closure, for, match, if-init, blank, multi-var
        negative_shapes = [
            ("var ch = nil", ["ch"]),
            ("ch := nil", ["ch"]),
            ("let x = nil", ["x"]),
            ("var _ = nil", ["_"]),
            ("a, b := nil, 1", ["a"]),
            ("var a, b = nil, 1", ["a"]),
            ("g := fn() { x := nil }", ["x"]),
            ("g := fn() { var y = nil }", ["y"]),
            ("for i := 0; i < 1; i = i + 1 { x := nil }", ["x"]),
            ("if x := nil; true { }", ["x"]),
            ("var a, b = nil, nil", ["a", "b"]),
        ]
        for snippet, expected_names in negative_shapes:
            with self.subTest(snippet=snippet):
                self.assertEqual(
                    check_hy_nil_declarations(snippet, legacy_ascription_gated=True),
                    [],
                )
                diags = check_hy_nil_declarations(snippet, legacy_ascription_gated=False)
                self.assertEqual([d.code for d in diags], ["HYB-E4003"] * len(expected_names))
                self.assertEqual([d.symbol_or_binding for d in diags], expected_names)

        # 4 positive / single-locus controls (#673 specification):
        # 1. `var e error = nil` -> 0 diags (N1 ascribed error home)
        self.assertEqual(check_hy_nil_declarations("var e error = nil"), [])
        # 2. `var v serde_json.value = nil` -> 0 diags (N2 foreign perimeter nilable handle)
        self.assertEqual(check_hy_nil_declarations("var v serde_json.value = nil"), [])
        # 3. `var s []int = nil` -> HYB-E4001 only (never E4003)
        e4001_diags = check_hy_nil_declarations("var s []int = nil")
        self.assertEqual([d.code for d in e4001_diags], ["HYB-E4001"])
        # 4. `const c = nil` -> HYB-E6003 only (never E4003)
        e6003_diags = check_hy_nil_declarations("const c = nil")
        self.assertEqual([d.code for d in e6003_diags], ["HYB-E6003"])

    def test_live_hyrt_21_exports_and_go_broker_bindings_parity(self) -> None:
        exports = extract_hyrt_exports_from_repo(self.repo_root)
        self.assertEqual(len(exports), 21)
        exports_by_sym = {e.symbol: e for e in exports}
        bindings = extract_go_broker_bindings_from_repo(self.repo_root, exports_by_sym)
        self.assertGreaterEqual(len(bindings), 18)
        self.assertIn("hyrt_abi_word", exports_by_sym)
        self.assertIn("hy_json_live_count", exports_by_sym)
        self.assertIn("hy_json_parse", exports_by_sym)
        self.assertIn("hy_regex_is_match", exports_by_sym)

    def test_deterministic_sha256_contract_bundle_and_export_digest_reproducibility(self) -> None:
        exports_a = extract_hyrt_exports_from_repo(self.repo_root)
        exports_b = extract_hyrt_exports_from_repo(self.repo_root)
        sha_a = compute_contract_bundle_sha256(exports_a)
        sha_b = compute_contract_bundle_sha256(exports_b)
        self.assertEqual(sha_a, sha_b)
        self.assertEqual(len(sha_a), 64)
        for exp in exports_a:
            recomputed = compute_export_contract_sha256(
                symbol=exp.symbol,
                c_params=exp.c_params,
                c_return=exp.c_return,
                call_mode=exp.call_mode,
                may_retain_ptr=exp.may_retain_ptr,
                may_callback_go=exp.may_callback_go,
                may_block_sync=exp.may_block_sync,
                closed_frame_bytes=exp.closed_frame_bytes,
                is_recursive_or_tree=exp.is_recursive_or_tree,
                certified_stack_class=exp.certified_stack_class,
                abi_handshake_word=exp.abi_handshake_word,
            )
            self.assertEqual(exp.contract_sha256, recomputed)

    def test_compositional_stack_class_lattice_soundness(self) -> None:
        self.assertEqual(compute_certified_stack_class("hy_sha3_256", 512, False), "SMALL")
        self.assertEqual(compute_certified_stack_class("hy_custom_mid", 2048, False), "DEFAULT")
        self.assertEqual(compute_certified_stack_class("hy_json_parse", 384, True), "DEEP")
        self.assertEqual(compute_certified_stack_class("hy_regex_compile", 22528, True), "DEEP")

    def test_ffi_tcb_complexity_reduction_exceeds_35_percent(self) -> None:
        metrics = measure_ffi_tcb_reduction(self.repo_root)
        self.assertGreater(metrics["total_boundary_tcb_lines"], 1800)
        self.assertGreater(metrics["tcb_reduction_ratio"], 0.35)


if __name__ == "__main__":
    unittest.main(verbosity=2)
