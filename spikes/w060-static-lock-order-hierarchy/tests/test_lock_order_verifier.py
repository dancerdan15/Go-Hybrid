"""Unit and integration tests for W-060 static lock-order hierarchy verifier (Ref #1291)."""
from __future__ import annotations

import pathlib
import sys
import unittest

SPIKE_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

from lock_order_verifier import (  # noqa: E402
    AcquireLock,
    BranchJoin,
    Break,
    CallFn,
    DeferUnlock,
    FunctionSpec,
    LockHierarchy,
    LockOrderVerifier,
    MutexSpec,
    ReleaseLock,
    Return,
    TryEdge,
    main,
    run_self_check,
    verify_program,
)


class LockOrderVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dag = LockHierarchy(
            levels=("L_NET", "L_SESSION", "L_STORAGE", "L_AUDIT"),
            precedence=(("L_NET", "L_SESSION"), ("L_SESSION", "L_STORAGE")),
        )

    def test_e6100_hierarchy_cycle_and_acyclic_positive_control(self) -> None:
        self.assertEqual(self.dag.validate(), [])
        self.assertTrue(self.dag.strictly_precedes("L_NET", "L_STORAGE"))
        self.assertFalse(self.dag.strictly_precedes("L_NET", "L_AUDIT"))
        cyclic = LockHierarchy(precedence=(("L1", "L2"), ("L2", "L3"), ("L3", "L1")))
        diags = cyclic.validate()
        self.assertEqual([d.code for d in diags], ["HYB-E6100"])
        self.assertIn("L1 -> L2 -> L3 -> L1", diags[0].message)

    def test_e6101_direct_and_incomparable_lock_order_inversion_and_positive(self) -> None:
        m_net, m_sess = MutexSpec("mu_net", "L_NET"), MutexSpec("mu_sess", "L_SESSION")
        valid_fn = FunctionSpec("valid_order", [
            AcquireLock("g1", m_net.name, m_net.level),
            AcquireLock("g2", m_sess.name, m_sess.level),
            ReleaseLock("g2"),
            ReleaseLock("g1"),
        ])
        self.assertTrue(verify_program(self.dag, [valid_fn]).accepted)
        abba_fn = FunctionSpec("abba_invert", [
            AcquireLock("g1", m_sess.name, m_sess.level),
            AcquireLock("g2", m_net.name, m_net.level),
            ReleaseLock("g2"),
            ReleaseLock("g1"),
        ])
        incomp_fn = FunctionSpec("incomparable_lock", [
            AcquireLock("g1", m_net.name, m_net.level),
            AcquireLock("g2", "mu_audit", "L_AUDIT"),
            ReleaseLock("g2"),
            ReleaseLock("g1"),
        ])
        rep = verify_program(self.dag, [abba_fn, incomp_fn])
        self.assertFalse(rep.accepted)
        self.assertEqual(rep.codes(), ["HYB-E6101", "HYB-E6101"])

    def test_e6101_interprocedural_call_summary_inversion_and_positive(self) -> None:
        leaf_store = FunctionSpec("write_store", [AcquireLock("gs", "mu_store", "L_STORAGE"), ReleaseLock("gs")])
        mid_sess = FunctionSpec("touch_sess", [AcquireLock("gm", "mu_sess", "L_SESSION"), CallFn("write_store"), ReleaseLock("gm")])
        valid_caller = FunctionSpec("valid_caller", [AcquireLock("gn", "mu_net", "L_NET"), CallFn("touch_sess"), ReleaseLock("gn")])
        ok_rep = verify_program(self.dag, [leaf_store, mid_sess, valid_caller])
        self.assertTrue(ok_rep.accepted)
        self.assertEqual(ok_rep.fn_summaries["touch_sess"], frozenset({"L_SESSION", "L_STORAGE"}))
        bad_caller = FunctionSpec("bad_caller", [AcquireLock("gs", "mu_store", "L_STORAGE"), CallFn("touch_sess"), ReleaseLock("gs")])
        bad_rep = verify_program(self.dag, [leaf_store, mid_sess, bad_caller])
        self.assertEqual(bad_rep.codes(), ["HYB-E6101", "HYB-E6101"])
        h_fn = FunctionSpec("h", [AcquireLock("gn", "mu_net", "L_NET"), ReleaseLock("gn")])
        g_fn = FunctionSpec("g", [CallFn("h")])
        f_fn = FunctionSpec("f", [AcquireLock("gs", "mu_sess", "L_SESSION"), CallFn("g"), ReleaseLock("gs")])
        self.assertEqual(verify_program(self.dag, [f_fn, g_fn, h_fn]).codes(), ["HYB-E6101"])
        rec_a = FunctionSpec("rec_a", [AcquireLock("ga", "mu_net", "L_NET"), CallFn("rec_b"), ReleaseLock("ga")])
        rec_b = FunctionSpec("rec_b", [AcquireLock("gb", "mu_sess", "L_SESSION"), CallFn("rec_a"), ReleaseLock("gb")])
        self.assertEqual(LockOrderVerifier(self.dag).compute_summaries([rec_a, rec_b])["rec_a"], frozenset({"L_NET", "L_SESSION"}))

    def test_e6102_reentrant_or_same_level_acquisition(self) -> None:
        fn = FunctionSpec("same_level", [
            AcquireLock("g1", "mu_a", "L_SESSION"),
            AcquireLock("g2", "mu_b", "L_SESSION"),
            ReleaseLock("g2"),
            ReleaseLock("g1"),
        ])
        rep = verify_program(self.dag, [fn])
        self.assertEqual(rep.codes(), ["HYB-E6102"])
        self.assertEqual(rep.diagnostics[0].detail["held"], "L_SESSION")

    def test_e6103_unreleased_lock_across_try_return_break_and_defer_positive(self) -> None:
        leak_try = FunctionSpec("leak_try", [AcquireLock("g1", "mu_net", "L_NET"), TryEdge("step()?"), ReleaseLock("g1")])
        leak_ret = FunctionSpec("leak_ret", [AcquireLock("g1", "mu_net", "L_NET"), Return("early_return")])
        leak_brk = FunctionSpec("leak_brk", [AcquireLock("g1", "mu_net", "L_NET"), Break("loop_break")])
        leak_def_brk = FunctionSpec("leak_def_brk", [AcquireLock("g1", "mu_net", "L_NET"), DeferUnlock("g1"), Break("loop_break")])
        leak_eof = FunctionSpec("leak_eof", [AcquireLock("g1", "mu_net", "L_NET")])
        rep = verify_program(self.dag, [leak_try, leak_ret, leak_brk, leak_def_brk, leak_eof])
        self.assertEqual(rep.codes(), ["HYB-E6103"] * 5)
        safe_defer = FunctionSpec("safe_defer", [AcquireLock("g1", "mu_net", "L_NET"), DeferUnlock("g1"), TryEdge("s1()?"), TryEdge("s2()?"), Return("ok")])
        safe_explicit = FunctionSpec("safe_explicit", [AcquireLock("g1", "mu_net", "L_NET"), ReleaseLock("g1"), TryEdge("io()?"), Break("loop_ok")])
        self.assertTrue(verify_program(self.dag, [safe_defer, safe_explicit]).accepted)

    def test_e6104_branch_join_asymmetry_and_symmetric_positive(self) -> None:
        asymmetric_rel = FunctionSpec("asym_rel", [AcquireLock("g1", "mu_net", "L_NET"), BranchJoin([ReleaseLock("g1")], [])])
        asymmetric_def = FunctionSpec("asym_def", [AcquireLock("g1", "mu_net", "L_NET"), BranchJoin([DeferUnlock("g1")], [ReleaseLock("g1")])])
        rep = LockOrderVerifier(self.dag).verify_program([asymmetric_rel, asymmetric_def])
        self.assertIn("HYB-E6104", rep.codes())
        self.assertEqual(rep.codes().count("HYB-E6104"), 2)
        symmetric = FunctionSpec("sym_join", [AcquireLock("g1", "mu_net", "L_NET"), BranchJoin([ReleaseLock("g1")], [ReleaseLock("g1")])])
        self.assertTrue(verify_program(self.dag, [symmetric]).accepted)

    def test_cli_self_check(self) -> None:
        self.assertTrue(run_self_check().accepted)
        self.assertEqual(main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
