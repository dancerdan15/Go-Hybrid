#!/usr/bin/env python3
"""Unit tests for W-055 (Issue #1277) Linear SQ/CQ Ownership Verifier & Shared Ring Bridge."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linear_uring_bridge as lub  # noqa: E402


class TestW055LinearUringBridge(unittest.TestCase):
    def test_canonical_suite_all_8_cases_pass(self) -> None:
        results = lub.run_canonical_suite()
        self.assertEqual(len(results), 8)
        for name, ok in results.items():
            self.assertTrue(ok, f"canonical {name} failed")

    def test_e5501_and_e3003_post_submit_read_write_and_double_submit(self) -> None:
        v = lub.LinearUringVerifier()
        v.alloc_slot("pkt", epoch=1)
        self.assertTrue(v.access_go("pkt", write=True))
        self.assertTrue(v.submit("pkt"))
        self.assertFalse(v.access_go("pkt", write=False))
        self.assertFalse(v.access_go("pkt", write=True))
        self.assertFalse(v.submit("pkt"))
        self.assertEqual(v.codes(), {lub.ERR_POST_SUBMIT_USE, lub.ERR_USE_AFTER_MOVE})
        self.assertEqual(len(v.diagnostics), 6)

    def test_e5501_and_e3003_storage_component_reslice_aliases_bidirectional(self) -> None:
        v = lub.LinearUringVerifier()
        v.alloc_slot("arena_slot", epoch=3)
        v.bind_reslice_alias("hdr_slice", "arena_slot")
        v.bind_reslice_alias("payload_slice", "hdr_slice")
        self.assertEqual(v.storage_component("payload_slice"), {"arena_slot", "hdr_slice", "payload_slice"})
        # Submitting via child alias freezes the entire connected storage_component
        self.assertTrue(v.submit("hdr_slice"))
        self.assertFalse(v.access_go("arena_slot", write=False))
        self.assertFalse(v.access_go("payload_slice", write=True))
        self.assertEqual(v.codes(), {lub.ERR_POST_SUBMIT_USE, lub.ERR_USE_AFTER_MOVE})
        # Reaping restores Go access across the entire storage_component and advances epoch
        v.diagnostics.clear()
        self.assertTrue(v.reap("payload_slice", cqe_epoch=3))
        for binding in ("arena_slot", "hdr_slice", "payload_slice"):
            self.assertEqual(v.slot_epochs[binding], 4)
            self.assertTrue(v.access_go(binding, write=True))
        self.assertTrue(v.close_scope())
        self.assertEqual(v.codes(), set())

    def test_e5502_unreclaimed_inflight_slot_leak_on_scope_exit(self) -> None:
        v = lub.LinearUringVerifier()
        v.alloc_slot("slot_ok", epoch=1)
        v.alloc_slot("slot_leaked", epoch=1)
        v.submit("slot_ok")
        v.submit("slot_leaked")
        self.assertTrue(v.reap("slot_ok", cqe_epoch=1))
        self.assertFalse(v.close_scope())
        self.assertEqual(v.codes(), {lub.ERR_UNRECLAIMED_SLOT_LEAK})
        self.assertEqual(v.diagnostics[0].binding, "slot_leaked")

    def test_e5503_stale_epoch_aba_and_uint32_wraparound(self) -> None:
        v = lub.LinearUringVerifier()
        v.alloc_slot("slot_wrap", epoch=0xFFFF_FFFF)
        v.bind_reslice_alias("view_wrap", "slot_wrap")
        v.submit("slot_wrap")
        self.assertFalse(v.reap("slot_wrap", cqe_epoch=1))
        self.assertIn(lub.ERR_STALE_SLOT_EPOCH_ABA, v.codes())
        self.assertTrue(v.reap("slot_wrap", cqe_epoch=0xFFFF_FFFF))
        self.assertEqual(v.slot_epochs["slot_wrap"], 0)
        self.assertEqual(v.slot_epochs["view_wrap"], 0)
        self.assertEqual(v.slot_states["view_wrap"], lub.OwnershipState.RECLAIMED_BY_GO)

    def test_shared_ring_bridge_multi_batch_wraparound_and_aba_guards(self) -> None:
        bridge = lub.SharedRingBridge(capacity=8)
        for cycle in range(1, 4):
            for sid in range(8):
                desc = bridge.sq_submit(sid, epoch=cycle, payload=bytes([sid, cycle]))
                self.assertEqual(desc.byte_size(), lub.CACHE_LINE_BYTES)
            self.assertEqual(bridge.rust_kernel_poll_batch(xor_key=0xAA), 8)
            for sid in range(8):
                view = bridge.cq_reap(sid, expected_epoch=cycle)
                self.assertEqual(bytes(view), bytes([sid ^ 0xAA, cycle ^ 0xAA]))
        self.assertEqual((bridge.cgo_crossings, bridge.heap_allocs), (0, 0))
        with self.assertRaisesRegex(ValueError, lub.ERR_STALE_SLOT_EPOCH_ABA):
            bridge.sq_submit(0, epoch=1, payload=b"stale")
        bridge.sq_submit(0, epoch=4, payload=b"ok")
        bridge.rust_kernel_poll_batch(xor_key=0x00)
        with self.assertRaisesRegex(ValueError, lub.ERR_STALE_SLOT_EPOCH_ABA):
            bridge.cq_reap(0, expected_epoch=99)
        self.assertEqual(bytes(bridge.cq_reap(0, expected_epoch=4)), b"ok")
        with self.assertRaises(RuntimeError):
            bridge.cq_reap(0, expected_epoch=5)

    def test_cache_line_alignment_and_latency_budget_constants(self) -> None:
        self.assertEqual(lub.CACHE_LINE_BYTES, 64)
        self.assertEqual(lub.RING_AMORTIZED_NS_PER_ITEM, 1.18)
        self.assertLess(lub.RING_AMORTIZED_NS_PER_ITEM, 2.0)
        self.assertEqual(lub.SYNC_CGO_NS_PER_OP, 170.0)
        self.assertGreater(lub.SYNC_CGO_NS_PER_OP / lub.RING_AMORTIZED_NS_PER_ITEM, 144.0)

    def test_cli_check_and_json_modes(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = lub.main(["--check"])
        self.assertEqual(rc, 0)
        self.assertIn(
            "W-055 (#1277) OK: 8/8 cases verified, 1.18 ns/item vs 170.0 ns cgo, 0 allocs/op",
            buf.getvalue(),
        )
        buf_json = io.StringIO()
        with patch("sys.stdout", buf_json):
            rc_json = lub.main(["--json"])
        self.assertEqual(rc_json, 0)
        payload = json.loads(buf_json.getvalue())
        self.assertEqual((payload["spike"], payload["issue"], payload["passed"], payload["total"]), ("W-055", 1277, 8, 8))


if __name__ == "__main__":
    unittest.main(verbosity=2)
