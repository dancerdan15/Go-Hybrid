#!/usr/bin/env python3
"""Unit and falsifier tests for W-038 (#955) zero-copy shared memory futex rings."""
from __future__ import annotations
import contextlib, io, json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shm_futex_ring as w038  # noqa: E402


class TestW038ShmFutexRing(unittest.TestCase):
    def test_pod_send_admits_relocatable_primitives_arrays_and_nested_structs(self) -> None:
        u64 = w038.TypeSpec("primitive", "u64", byte_size=8)
        arr = w038.TypeSpec("array", "[16]u32", elem=w038.TypeSpec("primitive", "u32", byte_size=4), length=16)
        hdr = w038.TypeSpec("struct", "Header", fields=(("tag", w038.TypeSpec("primitive", "u32", byte_size=4)),
                                                        ("flags", w038.TypeSpec("primitive", "u32", byte_size=4))))
        pkt = w038.TypeSpec("struct", "Packet", fields=(("seq", u64), ("data", arr), ("hdr", hdr)))
        self.assertEqual(w038.check_pod_send(u64)["byte_size"], 8)
        self.assertEqual(w038.check_pod_send(arr)["byte_size"], 64)
        res = w038.check_pod_send(pkt, capacity=1024)
        self.assertTrue(res["ok"])
        self.assertIsNone(res["code"])
        self.assertEqual(res["byte_size"], 80)

    def test_pod_send_rejects_non_relocatable_and_intra_process_only_kinds_hyb_e6010(self) -> None:
        for kind in w038.NON_RELOCATABLE_KINDS:
            res = w038.check_pod_send(w038.TypeSpec(kind, kind))
            self.assertFalse(res["ok"], msg=f"{kind} must fail PodSend")
            self.assertEqual(res["code"], "HYB-E6010")
            self.assertEqual(res["intra_proc_sendable"], kind in {"string", "chan", "own_handle"})
        bad_struct = w038.TypeSpec(
            "struct", "BadMsg",
            fields=(("id", w038.TypeSpec("primitive", "u64", byte_size=8)),
                    ("handle", w038.TypeSpec("own_handle", "own Widget"))),
        )
        nested = w038.check_pod_send(bad_struct)
        self.assertFalse(nested["ok"])
        self.assertEqual(nested["code"], "HYB-E6010")
        self.assertIn("in field `handle`", nested["reason"])
        empty_struct = w038.check_pod_send(w038.TypeSpec("struct", "Empty", fields=()))
        self.assertFalse(empty_struct["ok"])
        self.assertEqual(empty_struct["code"], "HYB-E6010")
        rec = w038.TypeSpec("struct", "Node", fields=(("self_ref", w038.TypeSpec("struct", "Node")),))
        self.assertEqual(w038.check_pod_send(rec)["code"], "HYB-E6010")

    def test_pod_send_and_layout_reject_non_power_of_two_capacities_hyb_e6011(self) -> None:
        u64 = w038.TypeSpec("primitive", "u64", byte_size=8)
        for bad_cap in (0, 1, 3, 12, 1000):
            res = w038.check_pod_send(u64, capacity=bad_cap)
            self.assertFalse(res["ok"])
            self.assertEqual(res["code"], "HYB-E6011")
            with self.assertRaises(ValueError):
                w038.compute_ring_layout(64, capacity=bad_cap)
            with self.assertRaises(TypeError):
                w038.ShmFutexRing(u64, capacity=bad_cap)

    def test_cache_line_isolation_and_slot_stride_alignment(self) -> None:
        layout = w038.compute_ring_layout(payload_size=64, capacity=1024)
        self.assertEqual(layout["head_offset"], 0)
        self.assertEqual(layout["tail_offset"], 64)
        self.assertEqual(layout["futex_offset"], 128)
        self.assertEqual(layout["slots_offset"], 192)
        self.assertEqual(layout["slot_stride"], 128)
        self.assertTrue(layout["false_sharing_free"])
        self.assertEqual(layout["total_bytes"], 192 + 1024 * 128)

    def test_shm_futex_ring_zero_copy_wrap_around_and_wait_wake(self) -> None:
        spec = w038.TypeSpec("array", "[16]u8", elem=w038.TypeSpec("primitive", "u8", byte_size=1), length=16)
        ring = w038.ShmFutexRing(spec, capacity=4)
        try:
            self.assertIsNone(ring.recv_move())
            self.assertEqual(ring.futex_waits, 1)
            for cycle in range(2):
                for i in range(4):
                    msg = f"cycle={cycle}:slot={i:03d}".encode("ascii")
                    sent = ring.send_move(msg)
                    self.assertTrue(sent["ok"])
                self.assertEqual(ring.futex_wakes, cycle + 1)
                full = ring.send_move(b"X" * 16)
                self.assertFalse(full["ok"])
                self.assertEqual(full["reason"], "full")
                for i in range(4):
                    expected = f"cycle={cycle}:slot={i:03d}".encode("ascii")
                    self.assertEqual(ring.recv_move(), expected)
                self.assertIsNone(ring.recv_move())
        finally:
            ring.close()

    def test_shm_futex_ring_enforces_linear_move_hyb_e3001_and_payload_bounds(self) -> None:
        with self.assertRaises(TypeError) as ctx:
            w038.ShmFutexRing(w038.TypeSpec("string", "string"), capacity=4)
        self.assertIn("HYB-E6010", str(ctx.exception))
        ring = w038.ShmFutexRing(w038.TypeSpec("primitive", "u64", byte_size=8), capacity=4)
        try:
            self.assertTrue(ring.send_move(b"12345678", binding_name="pkt")["ok"])
            with self.assertRaises(ValueError) as mv_err:
                ring.send_move(b"12345678", binding_name="pkt")
            self.assertIn("HYB-E3001", str(mv_err.exception))
            with self.assertRaises(ValueError):
                ring.send_move(b"short")
        finally:
            ring.close()

    def test_futex_protocol_model_checker_proves_zero_lost_wakeups(self) -> None:
        res = w038.verify_futex_protocol()
        self.assertEqual(res["seq_cst_recheck"], {"schedules": 8, "lost_wakeups": 0})
        self.assertEqual(res["naive_no_recheck"], {"schedules": 5, "lost_wakeups": 1})
        self.assertEqual(res["naive_wake_before_commit"], {"schedules": 6, "lost_wakeups": 4})

    def test_benchmark_live_repo_audit_and_cli_check_json(self) -> None:
        bench = w038.benchmark_shm_vs_socket(messages=128, payload_size=64)
        self.assertGreater(bench["socket_ns_per_msg"], 0)
        self.assertLess(bench["native_linux_shm_p99_ns"], 500)
        self.assertGreaterEqual(bench["native_speedup_x"], 5.0)
        audit = w038.audit_live_repo()
        self.assertTrue(audit["model_has_sendable_by_type"])
        self.assertTrue(audit["hyrt_uses_process_local_table"])
        self.assertGreaterEqual(audit["corpus_and_example_files"], 130)
        self.assertEqual(audit["verdict"], "CONDITIONAL")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = w038.main(["--check", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["issue"], 955)


if __name__ == "__main__":
    unittest.main()
