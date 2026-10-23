#!/usr/bin/env python3
"""Unit and integration tests for W-036 (#958) DWARF CFI & Foreign ABI Verifier."""
from __future__ import annotations
import io, json, sys, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cfi_bridge_verifier as v


class TestCfiBridgeVerifier(unittest.TestCase):
    def test_uleb128_and_fde_stream_roundtrip(self) -> None:
        for val in (0, 1, 63, 127, 128, 255, 1024, 4096, 7648, 15328, 1048576):
            enc = v.encode_uleb128(val)
            dec, idx = v.read_uleb128(enc, 0)
            self.assertEqual(dec, val)
            self.assertEqual(idx, len(enc))
        fde = v.build_fde_stream(320, ("RBX", "R12", "R15"))
        self.assertEqual(len(fde) % 4, 0)
        cfa, saved = v.parse_fde_stream(fde)
        self.assertEqual(cfa, 320)
        self.assertEqual(saved, {"RBX", "R12", "R15"})
        # Verify DW_CFA_restore (0xC0 | reg_num) removes a previously saved register
        restored_fde = fde + bytes([0xC0 | v.CALLEE_SAVED["R12"], v.DW_CFA_NOP, v.DW_CFA_NOP, v.DW_CFA_NOP])
        _, saved_after = v.parse_fde_stream(restored_fde)
        self.assertEqual(saved_after, {"RBX", "R15"})
        # Adversarial Vector #1: truncated multi-byte ULEB128 and truncated FDE opcodes must not raise IndexError
        self.assertEqual(v.read_uleb128(b"\x80\x80", 0), (0, 2))
        self.assertEqual(v.parse_fde_stream(b"\x0e"), (8, set()))
        self.assertEqual(v.parse_fde_stream(b"\x0c\x07\x80"), (8, set()))

    def test_register_alignment_and_pointer_hazards(self) -> None:
        routines = [
            v.BridgeRoutine("amd64_g_clobber", "asm", "amd64", "SMALL", v.build_fde_stream(16), clobbers=("R14",)),
            v.BridgeRoutine("arm64_g_clobber", "asm", "arm64", "SMALL", v.build_fde_stream(16), clobbers=("R28",)),
            v.BridgeRoutine("rbx_unrestored", "c", "amd64", "SMALL", v.build_fde_stream(32), clobbers=("RBX",)),
            v.BridgeRoutine("rbx_cfi_saved", "c", "amd64", "SMALL", v.build_fde_stream(32, ("RBX",)), clobbers=("RBX",)),
            v.BridgeRoutine("sp_and_ptr_bad", "c", "win64", "SMALL", v.build_fde_stream(32), sp_aligned_16=False, unpinned_go_ptr=True),
            v.BridgeRoutine("multi_hazard_all", "asm", "arm64", "SMALL", v.build_fde_stream(2048), clobbers=("R28", "RBX"), sp_aligned_16=False, unpinned_go_ptr=True),
        ]
        certs = {c.name: c for c in v.verify_corpus(routines)}
        self.assertIn("HYB-E6030:G_REG_CLOBBER(R14)", certs["amd64_g_clobber"].diagnostics)
        self.assertIn("HYB-E6030:G_REG_CLOBBER(R28)", certs["arm64_g_clobber"].diagnostics)
        self.assertIn("HYB-E6030:CALLEE_SAVED_CLOBBER(RBX)", certs["rbx_unrestored"].diagnostics)
        self.assertTrue(certs["rbx_cfi_saved"].passed)
        self.assertEqual(certs["rbx_cfi_saved"].diagnostics, ())
        self.assertIn("HYB-E6030:SP_MISALIGNED", certs["sp_and_ptr_bad"].diagnostics)
        self.assertIn("HYB-E6030:GO_PTR_ESCAPE", certs["sp_and_ptr_bad"].diagnostics)
        self.assertEqual(
            certs["multi_hazard_all"].diagnostics,
            (
                "HYB-E6030:G_REG_CLOBBER(R28)",
                "HYB-E6030:CALLEE_SAVED_CLOBBER(RBX)",
                "HYB-E6030:SP_MISALIGNED",
                "HYB-E6030:GO_PTR_ESCAPE",
                "HYB-E6030:STACK_BUDGET_EXCEEDED(2048>1024)",
            ),
        )

    def test_call_graph_budgets_recursion_and_alloca(self) -> None:
        routines = [
            v.BridgeRoutine("leaf_600", "rust", "amd64", "SMALL", v.build_fde_stream(600)),
            v.BridgeRoutine("mid_300", "rust", "amd64", "SMALL", v.build_fde_stream(300), callees=("leaf_600",)),
            v.BridgeRoutine("top_3level_over_small", "rust", "amd64", "SMALL", v.build_fde_stream(256), callees=("mid_300", "leaf_600")),
            v.BridgeRoutine("caller_over_small", "rust", "amd64", "SMALL", v.build_fde_stream(500), callees=("leaf_600",)),
            v.BridgeRoutine("caller_fits_default", "rust", "amd64", "DEFAULT", v.build_fde_stream(500), callees=("leaf_600",)),
            v.BridgeRoutine("mut_rec_a", "c", "amd64", "DEFAULT", v.build_fde_stream(64), callees=("mut_rec_b",)),
            v.BridgeRoutine("mut_rec_b", "c", "amd64", "DEEP", v.build_fde_stream(64), callees=("mut_rec_a",)),
            v.BridgeRoutine("vla_small", "c", "amd64", "SMALL", v.build_fde_stream(64), dynamic_alloca=True),
        ]
        certs = {c.name: c for c in v.verify_corpus(routines)}
        self.assertEqual(certs["top_3level_over_small"].max_stack_bytes, 1156)
        self.assertIn("HYB-E6030:STACK_BUDGET_EXCEEDED(1156>1024)", certs["top_3level_over_small"].diagnostics)
        self.assertFalse(certs["caller_over_small"].passed)
        self.assertEqual(certs["caller_over_small"].max_stack_bytes, 1100)
        self.assertEqual(certs["caller_over_small"].inferred_class, "DEFAULT")
        self.assertIn("HYB-E6030:STACK_BUDGET_EXCEEDED(1100>1024)", certs["caller_over_small"].diagnostics)
        self.assertTrue(certs["caller_fits_default"].passed)
        self.assertFalse(certs["mut_rec_a"].bounded)
        self.assertIn("HYB-E6030:RECURSIVE_UNBOUNDED", certs["mut_rec_a"].diagnostics)
        self.assertTrue(certs["mut_rec_b"].passed)
        self.assertFalse(certs["vla_small"].bounded)
        self.assertIn("HYB-E6030:RECURSIVE_UNBOUNDED", certs["vla_small"].diagnostics)

    def test_live_repo_audit_and_synthetic_asm_hazards(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        live = v.audit_live_repo(repo_root)
        self.assertEqual(live["descriptors_count"], 18)
        self.assertEqual(live["trampolines_verified"], 39)
        self.assertEqual(live["asm_hazards"], [])
        self.assertEqual(live["ledger"]["FnDigest256"], "SMALL")
        self.assertEqual(live["ledger"]["FnParse"], "DEEP")
        self.assertEqual(live["ledger"]["hy_nop"], "DEFAULT")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "interfaces").mkdir()
            (root / "runtime" / "go" / "hybridge").mkdir(parents=True)
            (root / "interfaces" / "descriptors.md").write_text("| `FnTest` | SMALL |\n", encoding="utf-8")
            (root / "runtime" / "go" / "hybridge" / "call_amd64.s").write_text(
                "TEXT ·callFast1S(SB), WRAPPER, $0-16\n\tMOVQ AX, R14\n\tADDQ $256, CX\n\tRET\n",
                encoding="utf-8",
            )
            bad = v.audit_live_repo(root)
            self.assertEqual(len(bad["asm_hazards"]), 3)
            self.assertIn("HYB-E6030:MISSING_NOSPLIT(callFast1S)", bad["asm_hazards"])
            self.assertIn("HYB-E6030:G_REG_CLOBBER(callFast1S)", bad["asm_hazards"])
            self.assertIn("HYB-E6030:GUARD_MISMATCH(callFast1S)", bad["asm_hazards"])

    def test_cli_check_and_json_modes(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = v.main(["--check", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["issue"], "#958")
        self.assertEqual(payload["routines_tested"], 20)
        self.assertGreater(payload["non_recursive_bounded_ratio"], 0.90)
        self.assertTrue(payload["falsifier_bounded_gt_90pct"])
        self.assertTrue(payload["falsifier_latency_lt_500ms"])


if __name__ == "__main__":
    unittest.main()
