#!/usr/bin/env python3
"""Unit and falsification tests for W-054 (#1276) Datalog/Polonius Tier-0 solver."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datalog_tier0_solver import (  # noqa: E402
    REPLACED_FNSTATE_FIELDS,
    FactStore,
    audit_live_fnstate,
    build_canonical_cases,
    main,
    run_spike,
    solve_fixpoint,
    subpath_covers,
    subpath_overlaps,
)


class DatalogTier0SolverTests(unittest.TestCase):
    def test_subpath_covers_and_overlaps_disjoint_vs_prefix(self) -> None:
        self.assertTrue(subpath_covers("p", "p.x"))
        self.assertTrue(subpath_covers("p", "p[0]"))
        self.assertFalse(subpath_covers("p.x", "p"))
        self.assertTrue(subpath_overlaps("p", "p.x"))
        self.assertTrue(subpath_overlaps("p.x", "p"))
        self.assertTrue(subpath_overlaps("m.items[0]", "m.items"))
        self.assertFalse(subpath_overlaps("p.x", "p.y"))
        self.assertFalse(subpath_overlaps("p[0]", "p[1]"))

    def test_diamond_branch_and_loop_backedge_use_after_move_e3003(self) -> None:
        diamond = FactStore({(0, 1), (0, 2), (1, 3), (2, 3)}, moved={("b", "b", 1)}, used={("b", "b", 3)})
        sol_d = solve_fixpoint(diamond)
        self.assertFalse(sol_d.accepted)
        self.assertTrue(any(d.startswith("HYB-E3003:") for d in sol_d.diagnostics))

        loop = FactStore({(0, 1), (1, 2), (2, 1), (1, 3)}, moved={("c", "c", 2)}, used={("c", "c", 1)})
        sol_l = solve_fixpoint(loop)
        self.assertFalse(sol_l.accepted)
        self.assertIn(("c", "c", 1), sol_l.path_moved_at)
        self.assertTrue(any(d.startswith("HYB-E3003:") for d in sol_l.diagnostics))

    def test_partial_field_move_disjoint_accept_vs_overlap_reject_e3003(self) -> None:
        disjoint = FactStore({(0, 1), (1, 2)}, moved={("p", "p.x", 1)}, used={("p", "p.y", 2)})
        self.assertTrue(solve_fixpoint(disjoint).accepted)

        overlap = FactStore({(0, 1), (1, 2)}, moved={("p", "p.x", 1)}, used={("p", "p", 2)})
        sol_o = solve_fixpoint(overlap)
        self.assertFalse(sol_o.accepted)
        self.assertTrue(any(d.startswith("HYB-E3003:") for d in sol_o.diagnostics))

    def test_transitive_may_share_loan_mutation_e3004_vs_nll_dead_loan_accept(self) -> None:
        live_mut = FactStore(
            {(0, 1), (1, 2), (2, 3), (3, 4)},
            assigned_share={("a1", "m", 0), ("a2", "a1", 1)},
            borrowed={("L1", "m", "m.items", False, 2)},
            mutated={("a2", "m.items[0]", 3)},
            loan_used={("L1", 4)},
        )
        sol_live = solve_fixpoint(live_mut)
        self.assertFalse(sol_live.accepted)
        self.assertIn(("a2", "m", 3), sol_live.alias_may_share)
        self.assertIn(("L1", 3), sol_live.loan_live_at)
        self.assertTrue(any(d.startswith("HYB-E3004:") for d in sol_live.diagnostics))

        dead_loan = FactStore(
            {(0, 1), (1, 2), (2, 3)},
            borrowed={("L1", "m", "m.items", False, 1)},
            loan_used={("L1", 2)},
            mutated={("m", "m.items", 3)},
        )
        sol_dead = solve_fixpoint(dead_loan)
        self.assertTrue(sol_dead.accepted)
        self.assertNotIn(("L1", 3), sol_dead.loan_live_at)

    def test_dangling_origin_escape_e3104_and_reinitialized_loop_accept(self) -> None:
        for kind in ("local_addr", "slot_loan", "non_owned"):
            esc = FactStore({(0, 1), (1, 2)}, origin_escape={("slot", kind, 0)}, assigned_share={("out", "slot", 1)}, returned={("out", 2)})
            sol_esc = solve_fixpoint(esc)
            self.assertFalse(sol_esc.accepted)
            self.assertTrue(any(d.startswith("HYB-E3104:") and kind in d for d in sol_esc.diagnostics))
        safe_esc = FactStore({(0, 1), (1, 2)}, origin_escape={("slot", "local_addr", 0)}, reinitialized={("slot", "slot", 1)}, returned={("slot", 2)})
        self.assertTrue(solve_fixpoint(safe_esc).accepted)

        reinit = FactStore({(0, 1), (1, 2), (2, 3), (3, 1), (1, 4)}, used={("pkt", "pkt", 1)}, moved={("pkt", "pkt", 2)}, reinitialized={("pkt", "pkt", 3)})
        self.assertTrue(solve_fixpoint(reinit).accepted)

    def test_canonical_suite_live_head_audit_and_cli(self) -> None:
        self.assertEqual(len(REPLACED_FNSTATE_FIELDS), 18)
        self.assertTrue(audit_live_fnstate()["all_18_present"])
        rep = run_spike()
        self.assertEqual(rep["total_cases"], len(build_canonical_cases()))
        self.assertEqual(rep["verdict_matches"], 8)
        self.assertTrue(rep["all_passed"])

        buf_check, buf_json = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_check):
            self.assertEqual(main(["--check"]), 0)
        self.assertIn("W-054 (#1276) OK: 8/8 cases verified", buf_check.getvalue())
        with redirect_stdout(buf_json):
            self.assertEqual(main(["--json"]), 0)
        self.assertEqual(json.loads(buf_json.getvalue())["issue"], 1276)


if __name__ == "__main__":
    unittest.main()
