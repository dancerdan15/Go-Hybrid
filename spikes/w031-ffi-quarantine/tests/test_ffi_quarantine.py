#!/usr/bin/env python3
"""Unit and integration tests for W-031 (#1188) process-isolated FFI quarantine."""
from __future__ import annotations
import contextlib, hashlib, io, json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ffi_quarantine as fq  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestFfiQuarantine(unittest.TestCase):
    def test_wire_header_layout_and_roundtrip(self) -> None:
        self.assertEqual(fq.HEADER_STRUCT.size, 18)
        digest = hashlib.sha3_256(b"go-hybrid").digest()
        frame = fq.encode_frame(fq.WIRE_VERSION, 4, 99, digest)
        self.assertEqual(len(frame), 18 + 32)
        status, hdr, payload, alloc = fq.decode_frame(frame, expected_gen=4, expected_req_id=99)
        self.assertEqual(status, "OK")
        self.assertEqual((hdr.version, hdr.generation, hdr.request_id, hdr.length), (1, 4, 99, 32))
        self.assertEqual(payload, digest)
        self.assertEqual(alloc, 32)

    def test_preallocation_bounds_and_framing_rejection(self) -> None:
        with self.assertRaisesRegex(ValueError, "ERR_OVERSIZED_FRAME"):
            fq.encode_frame(1, 1, 1, b"x" * 65, max_payload=64)
        with self.assertRaisesRegex(ValueError, "ERR_INVALID_HEADER"):
            fq.encode_frame(-1, 1, 1, b"x")
        self.assertEqual(fq.decode_frame(b"short", 1, 1)[0], "ERR_TRUNCATED_HEADER")
        digest = hashlib.sha3_256(b"bounds").digest()
        cases = [
            (fq.HEADER_STRUCT.pack(1, 1, 1, 8192) + digest, "ERR_OVERSIZED_LENGTH"),
            (fq.HEADER_STRUCT.pack(1, 1, 1, 16) + digest[:16], "ERR_INVALID_DIGEST_LEN"),
            (fq.HEADER_STRUCT.pack(2, 1, 1, 32) + digest, "ERR_BAD_VERSION"),
            (fq.HEADER_STRUCT.pack(1, 1, 1, 32) + digest, "ERR_STALE_GENERATION"),
            (fq.HEADER_STRUCT.pack(1, 5, 1, 32) + digest, "ERR_WRONG_GENERATION"),
            (fq.HEADER_STRUCT.pack(1, 2, 9, 32) + digest, "ERR_WRONG_REQUEST_ID"),
            (fq.HEADER_STRUCT.pack(1, 2, 1, 32) + digest[:10], "ERR_TRUNCATED_PAYLOAD"),
            (fq.HEADER_STRUCT.pack(1, 2, 1, 32) + digest + b"!", "ERR_TRAILING_BYTES"),
        ]
        for raw, expected_err in cases:
            status, _, payload, alloc = fq.decode_frame(raw, expected_gen=2, expected_req_id=1, max_payload=4096)
            self.assertEqual(status, expected_err)
            self.assertIsNone(payload)
            self.assertEqual(alloc, 0)

    def test_supervisor_state_machine_and_fault_containment(self) -> None:
        with self.assertRaisesRegex(ValueError, "ERR_INVALID_CONFIG"):
            fq.QuarantineSupervisor(max_workers=0)
        sup = fq.QuarantineSupervisor(max_workers=1, max_queue=2, max_payload=128)
        r_abort = sup.call_sha3_256(b"boom", fault="abort")
        self.assertEqual((r_abort.outcome, r_abort.digest_hex, sup.host_alive, sup.generation), ("FAILED_ABORT", None, True, 2))
        self.assertIn(1, sup.retired_generations)
        for flt in ("truncate_header", "truncate_payload", "corrupt_version", "corrupt_id", "oversized_len", "bad_digest_len"):
            r_flt = sup.call_sha3_256(b"frame", fault=flt)
            self.assertEqual((r_flt.outcome, r_flt.allocated_bytes), ("FAILED_FRAMING", 0))
        stale_gen = sup.generation
        stale_raw = fq.encode_frame(1, stale_gen, sup.next_request_id, hashlib.sha3_256(b"slow").digest())
        r_to = sup.call_sha3_256(b"slow", fault="timeout")
        self.assertEqual((r_to.outcome, sup.auto_retries), ("TIMEOUT_UNKNOWN", 0))
        r_late = sup.deliver_late_frame(stale_raw)
        self.assertEqual((r_late.outcome, r_late.error), ("REJECTED_STALE", "ERR_STALE_GENERATION"))
        self.assertEqual(sup.deliver_late_frame(b"bad").outcome, "FAILED_FRAMING")

    def test_admission_gate_quotas_and_private_copy(self) -> None:
        sup = fq.QuarantineSupervisor(max_workers=1, max_queue=1, max_payload=32)
        for op in ("hy_json_parse", "hy_json_get", "hy_regex_is_match", "hy_nop", "custom_cb"):
            res = sup.call_sha3_256(b"{}", op=op)
            self.assertEqual((res.outcome, res.error, sup.fallback_invocations), ("ADMISSION_REJECTED", "ERR_UNSUPPORTED_OP", 0))
        self.assertEqual(sup.call_sha3_256(b"x" * 40).outcome, "ERR_OVERSIZED_FRAME")
        buf = bytearray(b"isolated-bytes")
        expected = hashlib.sha3_256(b"isolated-bytes").hexdigest()
        res_ok = sup.call_sha3_256(buf)
        buf[0] ^= 0xFF
        self.assertEqual((res_ok.outcome, res_ok.digest_hex), ("COMMITTED", expected))
        sup.state, sup.queued = "PENDING", 0
        self.assertEqual(sup.call_sha3_256(b"a").outcome, "ERR_BUSY")
        sup.queued = 1
        self.assertEqual(sup.call_sha3_256(b"b").outcome, "ERR_QUOTA_EXCEEDED")

    def test_real_subprocess_worker_isolation(self) -> None:
        sup = fq.QuarantineSupervisor(max_workers=1, max_queue=2, max_payload=1024)
        r_ok = sup.call_sha3_256(b"subproc-ok", use_subprocess=True)
        self.assertEqual((r_ok.outcome, r_ok.digest_hex), ("COMMITTED", hashlib.sha3_256(b"subproc-ok").hexdigest()))
        r_crash = sup.call_sha3_256(b"subproc-abort", fault="abort", use_subprocess=True)
        self.assertEqual((r_crash.outcome, r_crash.error, sup.host_alive), ("FAILED_ABORT", "EXIT_134", True))
        r_post = sup.call_sha3_256(b"subproc-post", fault="exit_after_commit", use_subprocess=True)
        self.assertEqual((r_post.outcome, r_post.digest_hex), ("COMMITTED", hashlib.sha3_256(b"subproc-post").hexdigest()))
        self.assertIn(r_post.generation, sup.retired_generations)

    def test_live_runtime_audit_falsifiers_and_cli(self) -> None:
        aud = fq.audit_live_runtime(REPO_ROOT)
        self.assertTrue(aud["panic_abort_in_hyrt"])
        self.assertEqual((aud["total_hyrt_exports"], aud["admitted_exports"]), (21, ["hy_sha3_digest256"]))
        self.assertEqual((aud["rejected_stateful_json_exports"], aud["rejected_other_exports"]), (14, 6))
        self.assertTrue(aud["static_gen_1_in_table_insert"] and aud["g18_g19_g20_present"])
        self.assertEqual(aud["go_pinner_call_sites"], 11)
        rep = fq.evaluate_falsifiers(REPO_ROOT)
        self.assertTrue(rep["all_passed"] and all(rep["falsifiers"].values()))
        out_chk, out_json = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_chk):
            self.assertEqual(fq.main(["--root", str(REPO_ROOT), "--check"]), 0)
        self.assertIn("falsifiers=5/5 exports=1/21 pinner_sites=11", out_chk.getvalue())
        with contextlib.redirect_stdout(out_json):
            self.assertEqual(fq.main(["--root", str(REPO_ROOT), "--json"]), 0)
        self.assertTrue(json.loads(out_json.getvalue())["all_passed"])


if __name__ == "__main__":
    unittest.main()
