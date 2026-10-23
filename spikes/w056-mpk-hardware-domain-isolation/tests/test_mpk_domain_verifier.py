#!/usr/bin/env python3
"""Unit tests for W-056 (#1278) MPK Hardware Domain Verifier & PKRU Emulator."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parents[1]
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

from mpk_domain_verifier import (  # noqa: E402
    ERR_GO_HEAP_ESCAPE,
    ERR_UNRESTORED_PKRU,
    ERR_WRPKRU_GADGET,
    MPROTECT_SYSCALL_NS,
    PKEY_FFI_SHARED,
    PKEY_FOREIGN_PRIV,
    PKEY_GO_HEAP,
    PKRU_AD_WD,
    PKRU_ALLOW,
    PKRU_WD,
    WRPKRU_SWITCH_NS,
    MpkDomainVerifier,
    PkruHardwareEmulator,
    PkruPageFault,
    run_canonical_cases,
)


class TestMpkDomainVerifier(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = MpkDomainVerifier()

    def test_e5601_rejects_direct_and_transitive_go_heap_escape(self) -> None:
        src = (
            "var heap_buf: []u8 = make([]u8, 128)\n"
            "alias_slice := heap_buf[8:64]\n"
            "alias_view := &alias_slice[0]\n"
            "@ffi_shared let forged_sh: []u8 = heap_buf[0:16]\n"
            '@mpk_isolated extern "C" c_compress(heap_buf)\n'
            '@mpk_isolated extern "rust" rs_parse((&alias_view[0]))\n'
            '@mpk_isolated extern "C" c_launder(forged_sh)\n'
        )
        diags = self.verifier.verify_source(src)
        self.assertEqual([d.code for d in diags], [ERR_GO_HEAP_ESCAPE, ERR_GO_HEAP_ESCAPE, ERR_GO_HEAP_ESCAPE])
        self.assertEqual([d.subject for d in diags], ["heap_buf", "alias_view", "forged_sh"])
        self.assertIn("storage_component `heap_buf`", diags[1].message)
        self.assertIn("storage_component `heap_buf`", diags[2].message)

    def test_e5601_admits_ffi_shared_and_managed_interop_hatch(self) -> None:
        src = (
            "@ffi_shared let shared_buf: &[]u8 = alloc_shared(128)\n"
            "sub_shared := shared_buf[0:32]\n"
            "var raw_go: []u8 = make([]u8, 32)\n"
            '@mpk_isolated extern "C" c_verify(sub_shared)\n'
            'managed("audited legacy cgo call", "interop") { @mpk_isolated extern "C" c_legacy(raw_go) }\n'
        )
        self.assertEqual(self.verifier.verify_source(src), [])

    def test_pkru_hardware_emulator_enforces_domain_permissions(self) -> None:
        go_pkru = PkruHardwareEmulator.go_runtime_pkru()
        self.assertEqual(PkruHardwareEmulator.decode_key_perm(go_pkru, PKEY_GO_HEAP), PKRU_ALLOW)
        self.assertEqual(PkruHardwareEmulator.decode_key_perm(go_pkru, PKEY_FOREIGN_PRIV), PKRU_AD_WD)

        ro_pkru = PkruHardwareEmulator.ffi_call_pkru("&T")
        self.assertEqual(PkruHardwareEmulator.decode_key_perm(ro_pkru, PKEY_GO_HEAP), PKRU_AD_WD)
        self.assertEqual(PkruHardwareEmulator.decode_key_perm(ro_pkru, PKEY_FFI_SHARED), PKRU_WD)
        self.assertEqual(PkruHardwareEmulator.check_access(ro_pkru, PKEY_FFI_SHARED, "read"), "ALLOW")
        with self.assertRaises(PkruPageFault):
            PkruHardwareEmulator.check_access(ro_pkru, PKEY_FFI_SHARED, "write")
        with self.assertRaises(PkruPageFault):
            PkruHardwareEmulator.check_access(ro_pkru, PKEY_GO_HEAP, "read")

        rw_pkru = PkruHardwareEmulator.ffi_call_pkru("&mut T")
        self.assertEqual(PkruHardwareEmulator.check_access(rw_pkru, PKEY_FFI_SHARED, "write"), "ALLOW")
        self.assertEqual(PkruHardwareEmulator.check_access(rw_pkru, PKEY_FOREIGN_PRIV, "write"), "ALLOW")

        val_pkru = PkruHardwareEmulator.ffi_call_pkru("value")
        self.assertEqual(PkruHardwareEmulator.decode_key_perm(val_pkru, PKEY_FFI_SHARED), PKRU_AD_WD)
        with self.assertRaisesRegex(PkruPageFault, r"#PF \(PKRU violation: AD bit set on pkey=2"):
            PkruHardwareEmulator.check_access(val_pkru, PKEY_FFI_SHARED, "read")

    def test_e5602_rejects_unauthorized_x86_wrpkru_and_arm64_por_el0_gadgets(self) -> None:
        blob = b"\x55\x48\x89\xe5\x0f\x01\xef\x90\x0f\x01\xef\xc3"
        diags = self.verifier.scan_foreign_blob("libuntrusted.so", code_bytes=blob, allowed_offsets={4})
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, ERR_WRPKRU_GADGET)
        self.assertEqual(diags[0].name, "UnauthorizedWrpkruGadgetInForeignBlob")
        self.assertIn("+0x8", diags[0].message)
        self.assertEqual(self.verifier.scan_foreign_blob("libclean.so", code_bytes=blob, allowed_offsets={4, 8}), [])

        arm_diags = self.verifier.scan_foreign_blob(
            "libarm_codec.a",
            ir_insns=("MSR POR_EL0, X9 @trampoline", "MSR\tPOR_EL0, X0", "MSR S3_3_C10_C2_4, X1", "DCD 0xd51ba280"),
        )
        self.assertEqual([d.code for d in arm_diags], [ERR_WRPKRU_GADGET, ERR_WRPKRU_GADGET, ERR_WRPKRU_GADGET])

    def test_e5603_and_canonical_8_cases(self) -> None:
        go_pkru = PkruHardwareEmulator.go_runtime_pkru()
        ffi_pkru = PkruHardwareEmulator.ffi_call_pkru("&T")
        diags = self.verifier.verify_trampoline_cfg("hy_ffi_call", {"return": go_pkru, "unwind": ffi_pkru})
        self.assertEqual([d.code for d in diags], [ERR_UNRESTORED_PKRU])
        self.assertEqual(diags[0].name, "UnrestoredPkruStateOnUnwindOrReturn")
        self.assertEqual(diags[0].subject, "hy_ffi_call:unwind")
        missing_unwind = self.verifier.verify_trampoline_cfg("hy_ffi_no_unwind", {"return": go_pkru})
        self.assertEqual([d.subject for d in missing_unwind], ["hy_ffi_no_unwind:unwind"])
        both_bad = self.verifier.verify_trampoline_cfg("hy_ffi_bad", {"return": ffi_pkru, "unwind": ffi_pkru})
        self.assertEqual(len(both_bad), 2)
        self.assertEqual(self.verifier.verify_trampoline_cfg("hy_ffi_ok", {"return": go_pkru, "unwind": go_pkru}), [])

        report = run_canonical_cases()
        self.assertEqual(report["passed"], 8)
        self.assertEqual(report["total"], 8)
        self.assertTrue(all(report["cases"].values()))
        self.assertEqual(WRPKRU_SWITCH_NS, 1.20)
        self.assertLess(WRPKRU_SWITCH_NS, 2.0)
        self.assertEqual(MPROTECT_SYSCALL_NS, 1540.0)
        self.assertGreater(MPROTECT_SYSCALL_NS / WRPKRU_SWITCH_NS, 1000.0)


if __name__ == "__main__":
    unittest.main()
