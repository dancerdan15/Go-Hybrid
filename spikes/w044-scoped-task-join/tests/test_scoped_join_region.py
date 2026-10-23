#!/usr/bin/env python3
"""Unit and integration tests for W-044 (#471) Scoped Task Regions (join_scope)."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scoped_join_region import (  # noqa: E402
    RegionOp, ScopedRegionCase, audit_live_corpus, build_milestone_corpus,
    check_scoped_region, emit_join_scope_go, main, run_spike, storage_component,
)


class TestScopedJoinRegion(unittest.TestCase):
    def test_storage_component_transitive_alias_and_may_share(self) -> None:
        aliases = {"alias_a": "root_map", "sub_alias": "alias_a"}
        may_share = {"root_map": {"peer_slice"}, "peer_slice": {"root_map"}}
        comp = storage_component(aliases, may_share, 'sub_alias["key"].field')
        self.assertEqual(comp, {"root_map", "alias_a", "sub_alias", "peer_slice"})

    def test_emit_join_scope_go_deferred_vs_bare_wait(self) -> None:
        deferred_go = emit_join_scope_go(8, ["data", "matrix"], defer_barrier=True)
        self.assertIn("var __hy_wg sync.WaitGroup\n    defer __hy_wg.Wait()", deferred_go)
        self.assertIn("worker(data, matrix)", deferred_go)
        bare_go = emit_join_scope_go(4, ["data"], defer_barrier=False)
        self.assertNotIn("defer __hy_wg.Wait()", bare_go)
        self.assertTrue(bare_go.endswith("    __hy_wg.Wait()\n}"))

    def test_accept_scoped_parallel_read_and_unborrowed_mutation(self) -> None:
        cert = check_scoped_region(ScopedRegionCase(
            "positive-control-scoped-read-with-disjoint-local.hy",
            {"shared": "map[string]int", "local_scratch": "[]int"}, True,
            [RegionOp("borrow_task", "shared", workers=16), RegionOp("read", 'shared["k"]'), RegionOp("write", "local_scratch[0]")],
            True,
        ))
        self.assertTrue(cert.accepted and cert.race_free and cert.unwind_safe)
        self.assertEqual((cert.diagnostics, cert.allocs_per_op, cert.borrowed_roots), ([], 0, ["shared"]))

    def test_reject_unbounded_task_mutable_borrow_and_borrow_after_move(self) -> None:
        unbounded = check_scoped_region(ScopedRegionCase(
            "unbounded.hy", {"m": "map[string]int"}, False, [RegionOp("borrow_task", "m")], False, ["HYB-E3001"]
        ))
        self.assertFalse(unbounded.accepted)
        self.assertIn("HYB-E3001:unbounded_task_borrow(m)", unbounded.diagnostics)

        mut_borrow = check_scoped_region(ScopedRegionCase(
            "mut-borrow.hy", {"m": "map[string]int"}, True, [RegionOp("borrow_task", "m", mutable_borrow=True)], False, ["HYB-E4401"]
        ))
        self.assertFalse(mut_borrow.accepted)
        self.assertIn("HYB-E4401:mutable_task_borrow_forbidden(m)", mut_borrow.diagnostics)

        after_move = check_scoped_region(ScopedRegionCase(
            "after-move.hy", {"m": "map[string]int"}, True, [RegionOp("move", "m"), RegionOp("borrow_task", "m")], False, ["HYB-E3003"]
        ))
        self.assertFalse(after_move.accepted)
        self.assertIn("HYB-E3003:borrow_after_move(m)", after_move.diagnostics)

    def test_reject_direct_alias_mutation_rebind_delete_and_move(self) -> None:
        cert = check_scoped_region(ScopedRegionCase(
            "reject-mutations-and-move.hy", {"data": "map[string]int"}, True,
            [RegionOp("alias", "data", peer="alias_d"), RegionOp("borrow_task", "data", workers=4),
             RegionOp("write", 'alias_d["k"]'), RegionOp("delete", 'data["k"]'),
             RegionOp("rebind", "data"), RegionOp("move", "alias_d")],
            False, ["HYB-E3102", "HYB-E3003", "HYB-E4401"],
        ))
        self.assertFalse(cert.accepted)
        for prefix in ("HYB-E3102:", "HYB-E3003:", "HYB-E4401:"):
            self.assertTrue(any(d.startswith(prefix) for d in cert.diagnostics), prefix)

    def test_reject_region_escapes_all_vectors(self) -> None:
        for escape_kind in ("escape_channel", "escape_return", "escape_store", "detached_task"):
            cert = check_scoped_region(ScopedRegionCase(
                f"reject-{escape_kind}.hy", {"data": "[]int"}, True,
                [RegionOp("borrow_task", "data"), RegionOp(escape_kind, "data")], False, ["HYB-E3104", "HYB-E4402"]
            ))
            self.assertFalse(cert.accepted)
            self.assertIn(f"HYB-E3104:scoped_loan_escape({escape_kind}:data)", cert.diagnostics)
            self.assertIn(f"HYB-E4402:region_escape_forbidden({escape_kind}:data)", cert.diagnostics)

    def test_panic_unwind_falsifier_bare_vs_deferred_barrier(self) -> None:
        bare = check_scoped_region(ScopedRegionCase(
            "bare-unwind.hy", {"buf": "[]byte"}, True,
            [RegionOp("borrow_task", "buf", workers=16), RegionOp("panic_parent", "buf")],
            False, ["HYB-E4403"], defer_barrier=False,
        ))
        self.assertFalse(bare.accepted or bare.unwind_safe)
        self.assertIn("HYB-E4403:unjoined_unwind_hazard(buf)", bare.diagnostics)

        deferred = check_scoped_region(ScopedRegionCase(
            "deferred-unwind.hy", {"buf": "[]byte"}, True,
            [RegionOp("borrow_task", "buf", workers=16), RegionOp("panic_parent", "buf")],
            True, defer_barrier=True,
        ))
        self.assertTrue(deferred.accepted and deferred.unwind_safe)

    def test_milestone_corpus_live_audit_and_cli(self) -> None:
        self.assertEqual(len(build_milestone_corpus()), 7)
        live = audit_live_corpus()
        self.assertGreaterEqual(int(live["corpus_total_hy_files"]), 126)
        self.assertTrue(live["loans_rs_a3_paths_conflict_present"])
        report = run_spike()
        self.assertTrue(report["all_passed"] and report["zero_allocs_verified"] and report["verdict_matches"] == 7)

        buf, json_buf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(main(["--check"]), 0)
        self.assertIn("7/7 cases verified, 0 allocs/op", buf.getvalue())
        with contextlib.redirect_stdout(json_buf):
            self.assertEqual(main(["--json"]), 0)
        self.assertEqual(json.loads(json_buf.getvalue())["spike_id"], "W-044")

    def test_adversarial_reslice_parenthesized_escape_and_stealth_bare_wait(self) -> None:
        reslice = check_scoped_region(ScopedRegionCase(
            "adv-reslice.hy", {"buf": "[]byte"}, True,
            [RegionOp("borrow_task", "buf", workers=4), RegionOp("alias", "(buf)[:4]", peer="sub[:4]"),
             RegionOp("write", "(sub)[0]"), RegionOp("move", "(&sub).ptr"), RegionOp("escape_closure", "(sub)[1]")],
            False,
        ))
        self.assertFalse(reslice.accepted)
        for code in ("HYB-E3102:", "HYB-E4401:", "HYB-E3003:", "HYB-E3104:", "HYB-E4402:"):
            self.assertTrue(any(d.startswith(code) for d in reslice.diagnostics), code)

        disjoint_escape = check_scoped_region(ScopedRegionCase(
            "adv-disjoint-escape.hy", {"buf": "[]byte", "local_out": "[]byte"}, True,
            [RegionOp("borrow_task", "buf", workers=4), RegionOp("escape_channel", "local_out")], True,
        ))
        self.assertTrue(disjoint_escape.accepted and disjoint_escape.diagnostics == [])

        stealth_bare = check_scoped_region(ScopedRegionCase(
            "adv-stealth-bare-wait.hy", {"buf": "[]byte"}, True,
            [RegionOp("borrow_task", "buf", workers=4)], False, ["HYB-E4403"], defer_barrier=False,
        ))
        self.assertFalse(stealth_bare.accepted or stealth_bare.unwind_safe)
        self.assertIn("HYB-E4403:unjoined_unwind_hazard(buf)", stealth_bare.diagnostics)


if __name__ == "__main__":
    unittest.main()
