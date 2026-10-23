#!/usr/bin/env python3
"""Unit and integration tests for Spike W-042 (#833): atomic.Box[T] verifier."""
from __future__ import annotations
import io, json, sys, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linear_atomic_box import (  # noqa: E402
    DIAG_ALIASED_SWAP,
    DIAG_NON_CONSUMING_LOAD,
    DIAG_UNPROVEN_ATOMIC,
    DIAG_USE_AFTER_SWAP,
    SCALAR_ATOMICS,
    AtomicBoxCell,
    AtomicBoxVerifier,
    PointeeToken,
    PointeeType,
    audit_live_repo,
    classify_atomic_sendability,
    main,
    verify_rcu_buffer_swap,
    verify_treiber_stack,
)


class LinearAtomicBoxTest(unittest.TestCase):
    def test_classify_atomic_sendability_matrix(self) -> None:
        self.assertEqual(classify_atomic_sendability("atomic.Box[Node]"), ("Class2:LinearAtomicBox", None))
        self.assertEqual(classify_atomic_sendability("*atomic.Box[holder]"), ("Class2:LinearAtomicBox", None))
        for scalar in SCALAR_ATOMICS:
            self.assertEqual(classify_atomic_sendability(scalar), ("Class2:ScalarAtomic", None))
            self.assertEqual(classify_atomic_sendability(f"&{scalar}"), ("Class2:ScalarAtomic", None))
        for unproven in ("atomic.pointer[holder]", "*atomic.pointer[Node]", "atomic.value", "atomic.Box[atomic.pointer[holder]]", "atomic.Box[atomic.value]", "atomic.Box[]"):
            self.assertEqual(classify_atomic_sendability(unproven), ("Unproven:Row11FailClosed", DIAG_UNPROVEN_ATOMIC))
        self.assertEqual(classify_atomic_sendability("sync.Mutex"), ("NonAtomic", None))

    def test_swap_accept_and_use_after_swap_reject_e3003(self) -> None:
        v = AtomicBoxVerifier(PointeeType("*Node"))
        v.declare("n1")
        old = v.swap("n1", moved=True, out_name="old_node")
        self.assertEqual(old, "old_node")
        self.assertEqual(v.diagnostics, [])
        self.assertTrue(v.use("old_node"))
        self.assertFalse(v.use("n1", site="post_swap_read"))
        self.assertFalse(v.use("n1.payload", site="field_alias_read"))
        self.assertFalse(v.use("n1[0]", site="slice_alias_read"))
        self.assertIsNone(v.swap("n1", moved=True, site="double_swap"))
        self.assertEqual([d.code for d in v.diagnostics], [DIAG_USE_AFTER_SWAP] * 4)

    def test_swap_rejects_unmoved_aliased_or_loaned_operand_e3004(self) -> None:
        v = AtomicBoxVerifier(PointeeType("*Node"))
        v.declare("unmoved")
        v.declare("aliased", exclusive=False)
        v.declare("loaned", active_loans=1)
        self.assertIsNone(v.swap("unmoved", moved=False))
        self.assertIsNone(v.swap("aliased", moved=True))
        self.assertIsNone(v.swap("loaned", moved=True))
        self.assertIsNone(v.compare_and_swap("unmoved.ptr", "unmoved", moved=True))
        self.assertEqual([d.code for d in v.diagnostics], [DIAG_ALIASED_SWAP] * 4)
        self.assertIn("missing `move`", v.diagnostics[0].message)
        self.assertIn("shared alias", v.diagnostics[1].message)
        self.assertIn("active loan", v.diagnostics[2].message)
        self.assertIn("cannot alias expected", v.diagnostics[3].message)

    def test_compare_and_swap_and_take_semantics(self) -> None:
        v = AtomicBoxVerifier(PointeeType("*Node"))
        v.declare("exp")
        v.declare("cand")
        out = v.compare_and_swap("exp", "cand", moved=True, out_name="prev")
        self.assertEqual(out, "prev")
        self.assertTrue(v.use("exp"))
        self.assertTrue(v.use("prev"))
        self.assertFalse(v.use("cand"))
        self.assertEqual(v.diagnostics[-1].code, DIAG_USE_AFTER_SWAP)
        popped = v.take("popped_head")
        self.assertEqual(popped, "popped_head")
        self.assertTrue(v.use("popped_head"))

    def test_load_rejects_mutable_pointee_e4201_and_accepts_immutable(self) -> None:
        mut_v = AtomicBoxVerifier(PointeeType("*holder", is_mutable_ref=True))
        self.assertFalse(mut_v.load("p"))
        self.assertFalse(mut_v.peek("p2"))
        self.assertNotIn("p", mut_v.bindings)
        self.assertEqual([d.code for d in mut_v.diagnostics], [DIAG_NON_CONSUMING_LOAD, DIAG_NON_CONSUMING_LOAD])
        imm_v = AtomicBoxVerifier(PointeeType("ConfigSnapshot", is_mutable_ref=False))
        self.assertTrue(imm_v.load("snap"))
        self.assertEqual(imm_v.diagnostics, [])
        self.assertTrue(imm_v.use("snap"))

    def test_runtime_cell_single_owner_handoff_and_cas_reclaim(self) -> None:
        t0 = PointeeToken(0, "init")
        cell = AtomicBoxCell(t0)
        self.assertEqual(t0.owner, "box")
        with self.assertRaises(PermissionError):
            t0.mutate("init")
        t1 = PointeeToken(1, "workerA")
        ok, reclaimed_on_fail = cell.compare_and_swap("workerA", expected_id=999, new_tok=t1)
        self.assertFalse(ok)
        self.assertIs(reclaimed_on_fail, t1)
        t1.mutate("workerA")
        ok2, prev = cell.compare_and_swap("workerA", expected_id=0, new_tok=t1)
        self.assertTrue(ok2)
        assert prev is not None
        self.assertEqual(prev.owner, "workerA")
        prev.mutate("workerA")
        with self.assertRaises(PermissionError):
            cell.swap("workerB", prev)

    def test_treiber_stack_and_rcu_models_zero_races(self) -> None:
        treiber = verify_treiber_stack(tasks=8, ops_per_task=25)
        self.assertEqual(treiber["total_nodes"], 200)
        self.assertEqual(treiber["popped_nodes"], 200)
        self.assertEqual(treiber["race_violations"], 0)
        self.assertTrue(treiber["all_mutated_twice"] and treiber["box_empty_at_end"])
        rcu = verify_rcu_buffer_swap(writers=4, swaps_per_writer=25)
        self.assertEqual(rcu["total_swaps"], 100)
        self.assertEqual(rcu["race_violations"], 0)
        self.assertTrue(rcu["all_retired_exclusively_owned"])

    def test_live_repo_audit_and_cli_check_json(self) -> None:
        report = audit_live_repo()
        self.assertTrue(report["row11_currently_tbd"])
        self.assertTrue(report["foreign_has_is_unproven_atomic"])
        self.assertTrue(report["corpus_pins_e3005"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--check", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
