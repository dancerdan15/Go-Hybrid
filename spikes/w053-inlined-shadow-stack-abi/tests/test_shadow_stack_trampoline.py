#!/usr/bin/env python3
"""Unit and integration tests for W-053 (Issue #1262) per-P shadow-stack rings & g0 trampolines."""
from __future__ import annotations
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shadow_stack_trampoline as sst  # noqa: E402


class TestW053ShadowStackTrampoline(unittest.TestCase):
    def test_f1_rejects_direct_recursion_hyb_e5301(self) -> None:
        res = sst.verify_call_graph(sst.DEFAULT_CORPUS_GRAPH, "hy_json_parse")
        self.assertFalse(res.ok)
        self.assertEqual(res.code, sst.ERR_UNBOUNDED_RECURSION)
        self.assertIsNone(res.max_depth)
        self.assertEqual(res.worst_path, ("hy_json_parse", "serde_from_str", "serde_from_str"))
        self.assertEqual(res.trampoline_insns, ())

    def test_f1_rejects_mutual_recursion_and_indirect_calls_hyb_e5301(self) -> None:
        drop_res = sst.verify_call_graph(sst.DEFAULT_CORPUS_GRAPH, "hy_regex_drop")
        self.assertFalse(drop_res.ok)
        self.assertEqual(drop_res.code, sst.ERR_UNBOUNDED_RECURSION)
        self.assertEqual(drop_res.worst_path, ("hy_regex_drop", "ast_drop", "class_set_drop", "ast_drop"))
        ind_res = sst.verify_call_graph(sst.DEFAULT_CORPUS_GRAPH, "hy_indirect_cb")
        self.assertFalse(ind_res.ok)
        self.assertEqual(ind_res.code, sst.ERR_UNBOUNDED_RECURSION)
        self.assertEqual(ind_res.worst_path, ("hy_indirect_cb",))

    def test_f2_rejects_64kib_frame_budget_overflow_hyb_e5302(self) -> None:
        over = sst.verify_call_graph(sst.DEFAULT_CORPUS_GRAPH, "hy_oversized")
        self.assertFalse(over.ok)
        self.assertEqual(over.code, sst.ERR_BUDGET_OVERFLOW)
        self.assertEqual(over.max_depth, 68_096)
        self.assertEqual(over.worst_path, ("hy_oversized", "step_a", "step_b"))
        self.assertEqual(over.trampoline_insns, ())
        exact_graph = {
            "hy_exact": sst.FnNode("hy_exact", 32_768, ("leaf_exact",)),
            "leaf_exact": sst.FnNode("leaf_exact", 32_768, ()),
            "hy_plus_one": sst.FnNode("hy_plus_one", 32_769, ("leaf_exact",)),
        }
        exact_ok = sst.verify_call_graph(exact_graph, "hy_exact")
        self.assertTrue(exact_ok.ok)
        self.assertEqual(exact_ok.max_depth, sst.RING_BUDGET_BYTES)
        plus_one = sst.verify_call_graph(exact_graph, "hy_plus_one")
        self.assertFalse(plus_one.ok)
        self.assertEqual((plus_one.code, plus_one.max_depth), (sst.ERR_BUDGET_OVERFLOW, 65_537))

    def test_f2_admits_dag_diamond_and_sha3_regex_leaves_with_4insn_trampoline(self) -> None:
        sha = sst.verify_call_graph(sst.DEFAULT_CORPUS_GRAPH, "hy_sha3_digest256")
        self.assertTrue(sha.ok)
        self.assertEqual((sha.code, sha.max_depth), ("OK", 320))
        self.assertEqual(
            sha.trampoline_insns,
            ("MOVQ SP, BX", "MOVQ TLS:shadow_top, SP", "CALL hy_sha3_digest256(SB)", "MOVQ BX, SP"),
        )
        rx = sst.verify_call_graph(sst.DEFAULT_CORPUS_GRAPH, "hy_regex_compile_acyclic")
        self.assertTrue(rx.ok)
        self.assertEqual(rx.max_depth, 22_976)
        self.assertEqual(rx.worst_path, ("hy_regex_compile_acyclic", "meta_strategy_new", "driftsort_chain"))
        diamond = {
            "root": sst.FnNode("root", 128, ("left", "right")),
            "left": sst.FnNode("left", 256, ("shared",)),
            "right": sst.FnNode("right", 1_024, ("shared",)),
            "shared": sst.FnNode("shared", 512, ()),
        }
        d_res = sst.verify_call_graph(diamond, "root")
        self.assertTrue(d_res.ok)
        self.assertEqual((d_res.max_depth, d_res.worst_path), (1_664, ("root", "right", "shared")))

    def test_per_p_shadow_ring_alignment_shallow_sp_and_nosplit_guards(self) -> None:
        ring = sst.PerPShadowRing(num_p=4)
        for p_id in range(ring.num_p):
            self.assertEqual(ring.shadow_tops[p_id] % 16, 0)
            self.assertEqual(ring.shadow_bottoms[p_id] - ring.guard_bases[p_id], sst.GUARD_PAGE_BYTES)
            self.assertEqual(ring.shadow_tops[p_id] - ring.shadow_bottoms[p_id], sst.RING_BUDGET_BYTES)
        rx_cert = sst.verify_call_graph(sst.DEFAULT_CORPUS_GRAPH, "hy_regex_compile_acyclic")
        shallow_sp = 0xC000_0002_00  # 512 B shallow goroutine SP (< 4,096 B legacy guard)
        low_sp, restored_sp = ring.execute_inlined(2, shallow_sp, rx_cert)
        self.assertEqual(restored_sp, shallow_sp)
        self.assertEqual(ring.deep_pool_cas_count, 0)
        self.assertEqual(low_sp, ring.shadow_tops[2] - 22_976)
        self.assertGreaterEqual(low_sp, ring.shadow_bottoms[2])
        bad_cert = sst.verify_call_graph(sst.DEFAULT_CORPUS_GRAPH, "hy_json_parse")
        with self.assertRaises(ValueError):
            ring.execute_inlined(0, shallow_sp, bad_cert)
        ring.active_on_p[1] = True
        with self.assertRaises(RuntimeError):
            ring.execute_inlined(1, shallow_sp, rx_cert)
        ring.active_on_p[1] = False
        forged = sst.VerificationResult(True, "OK", sst.RING_BUDGET_BYTES + 16, ("forged",), ())
        with self.assertRaises(MemoryError):
            ring.execute_inlined(0, shallow_sp, forged)

    def test_f3_worker_tier_latency_under_2ns_flips_guarantees_row20_miss(self) -> None:
        shallow = sst.benchmark_worker_tier(caller_headroom_bytes=512)
        self.assertTrue(shallow.row20_flipped)
        self.assertLess(shallow.shadow_trampoline_ns, sst.TARGET_MAX_NS)
        self.assertLess(shallow.shadow_trampoline_ns, sst.ROW20_BUDGET_NS)
        self.assertEqual((shallow.fast_speedup, shallow.shallow_speedup), (3.75, 10.37))
        grown = sst.benchmark_worker_tier(caller_headroom_bytes=8_192)
        self.assertEqual((grown.fast_speedup, grown.shallow_speedup), (3.75, 3.75))

    def test_live_head_corpus_and_guarantees_row20_g2_audit(self) -> None:
        report = sst.audit_live_repo()
        self.assertEqual((report["issue"], report["spike"]), (1262, "W-053"))
        self.assertTrue(report["all_passed"])
        checks = report["checks"]
        assert isinstance(checks, dict)
        for key, passed in checks.items():
            self.assertTrue(passed, f"check failed: {key}")

    def test_cli_check_and_json_modes(self) -> None:
        buf_check = io.StringIO()
        with patch("sys.stdout", buf_check):
            rc_check = sst.main(["--check"])
        self.assertEqual(rc_check, 0)
        self.assertIn("[W-053 #1262] all_passed=True", buf_check.getvalue())
        buf_json = io.StringIO()
        with patch("sys.stdout", buf_json):
            rc_json = sst.main(["--json"])
        self.assertEqual(rc_json, 0)
        parsed = json.loads(buf_json.getvalue())
        self.assertTrue(parsed["all_passed"])
        self.assertEqual(parsed["bench"]["shadow_trampoline_ns"], 1.85)


if __name__ == "__main__":
    unittest.main(verbosity=2)
