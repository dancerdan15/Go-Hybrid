#!/usr/bin/env python3
"""Fail-to-Pass & Positive-Control Unit Tests for W-058 (#1289) eBPF Tier-0 Verifier."""
from __future__ import annotations
from contextlib import redirect_stdout
import io, json, sys, unittest
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parent.parent
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

from ebpf_tier0_verifier import (  # noqa: E402
    BinOpChecked, BorrowMapSlot, BranchCond, DerefReg, GuardNonNull, GuardRange,
    IndexSlice, InitReg, Interval, Jump, MoveReg, MutateMap, NullState,
    OwnershipState, RegState, ReleaseLoan, Return, TaskSpawnMove, UseReg,
    build_canonical_cases, join_nullability, join_ownership, join_states, main, verify_bytecode,
)


class TestEbpfTier0Verifier(unittest.TestCase):
    """Verifies the 4-domain product lattice RegState and single-pass worklist engine."""

    def test_lattice_join_and_widen_laws(self) -> None:
        self.assertEqual(join_ownership(OwnershipState.OWNED, OwnershipState.MOVED), OwnershipState.MOVED)
        self.assertEqual(join_ownership(OwnershipState.OWNED, OwnershipState.BORROWED), OwnershipState.BORROWED)
        self.assertEqual(join_ownership(OwnershipState.OWNED, OwnershipState.SHARED), OwnershipState.SHARED)
        self.assertEqual(join_nullability(NullState.NON_NULL, NullState.MAYBE_NULL), NullState.MAYBE_NULL)
        self.assertEqual(Interval(0, 5).join(Interval(-2, 3)), Interval(-2, 5))
        self.assertEqual(Interval(-10, 20).intersect(Interval(0, 7)), Interval(0, 7))
        self.assertEqual(Interval(0, 1).widen(Interval(0, 2), min_b=-128, max_b=127), Interval(0, 127))
        r1 = RegState(OwnershipState.OWNED, 0b01, NullState.NON_NULL, Interval(0, 2))
        r2 = RegState(OwnershipState.MOVED, 0b10, NullState.MAYBE_NULL, Interval(1, 4))
        merged = join_states({0: r1}, {0: r2})[0]
        self.assertEqual((merged.ownership, merged.loan_mask, merged.nullability, merged.interval),
                         (OwnershipState.MOVED, 0b11, NullState.MAYBE_NULL, Interval(0, 4)))

    def test_ownership_use_after_move_and_shared_move_vs_positive_control(self) -> None:
        # Fail-to-Pass 1: HYB-E3001 use-after-move
        uam = verify_bytecode([InitReg(0), MoveReg(1, 0), UseReg(0), Return()])
        self.assertFalse(uam.accepted)
        self.assertEqual(uam.error_codes, ["HYB-E3001"])
        # Fail-to-Pass 2: HYB-E3003 moving SHARED, BORROWED, or loan-encumbered register
        shared_mv = verify_bytecode([InitReg(0, OwnershipState.SHARED), TaskSpawnMove(0), Return()])
        borrowed_mv = verify_bytecode([InitReg(0, OwnershipState.BORROWED), MoveReg(1, 0), Return()])
        loaned_mv = verify_bytecode([InitReg(0), BorrowMapSlot(1, 0, "k"), MoveReg(2, 0), Return()])
        self.assertEqual(shared_mv.error_codes, ["HYB-E3003"])
        self.assertEqual(borrowed_mv.error_codes, ["HYB-E3003"])
        self.assertEqual(loaned_mv.error_codes, ["HYB-E3003"])
        # Positive Control: OWNED moved into r1; only r1 is used afterward (0 false positives)
        ok_mv = verify_bytecode([InitReg(0), MoveReg(1, 0), UseReg(1), Return()])
        self.assertTrue(ok_mv.accepted)
        self.assertEqual(ok_mv.diagnostics, [])

    def test_map_slot_loan_invalidation_vs_release_positive_control(self) -> None:
        # Fail-to-Pass: HYB-E3102 A3 live map-slot loan invalidation on MutateMap
        bad_loan = verify_bytecode([InitReg(0), BorrowMapSlot(1, 0, "k"), MutateMap(0, "delete"), UseReg(1), Return()])
        self.assertFalse(bad_loan.accepted)
        self.assertEqual(bad_loan.error_codes, ["HYB-E3102"])
        # Multi-loan bitmask & half-branch CFG loan merge: active loan on either path rejects MutateMap
        partial = verify_bytecode([InitReg(0), BorrowMapSlot(1, 0, "k1"), BorrowMapSlot(2, 0, "k2"), ReleaseLoan(1, 0), MutateMap(0, "insert"), Return()])
        branch_loan = verify_bytecode([InitReg(0), InitReg(1), BranchCond(1, 3, 5), BorrowMapSlot(2, 0, "k"), Jump(5), MutateMap(0, "delete"), Return()])
        self.assertEqual(partial.error_codes, ["HYB-E3102"])
        self.assertEqual(branch_loan.error_codes, ["HYB-E3102"])
        # Positive Control: ReleaseLoan clears active loan bit prior to MutateMap
        ok_loan = verify_bytecode([InitReg(0), BorrowMapSlot(1, 0, "k"), UseReg(1), ReleaseLoan(1, 0), MutateMap(0, "insert"), Return()])
        self.assertTrue(ok_loan.accepted)
        self.assertEqual(ok_loan.diagnostics, [])

    def test_nullability_deref_vs_branch_guard_positive_control(self) -> None:
        # Fail-to-Pass: HYB-E4001 unguarded dereference or slice indexing of MAYBE_NULL register
        bad_null = verify_bytecode([InitReg(0, nullability=NullState.MAYBE_NULL), DerefReg(0), Return()])
        bad_slice_null = verify_bytecode([InitReg(0, nullability=NullState.MAYBE_NULL), InitReg(1, interval=Interval(0, 3)), IndexSlice(2, 0, 1, 8), Return()])
        self.assertFalse(bad_null.accepted)
        self.assertEqual(bad_null.error_codes, ["HYB-E4001"])
        self.assertEqual(bad_slice_null.error_codes, ["HYB-E4001"])
        # Positive Control: BranchCond + GuardNonNull narrows r0 to NON_NULL before DerefReg
        ok_null = verify_bytecode([InitReg(0, nullability=NullState.MAYBE_NULL), BranchCond(0, 2, 5), GuardNonNull(0), DerefReg(0), Return(), Return()])
        self.assertTrue(ok_null.accepted)
        self.assertEqual(ok_null.diagnostics, [])

    def test_interval_overflow_and_slice_bounds_vs_guarded_positive_control(self) -> None:
        # Fail-to-Pass 1: HYB-E5001 signed i8 arithmetic overflow (+ and 4-corner negative *)
        ovf = verify_bytecode([InitReg(0, interval=Interval(100, 120)), InitReg(1, interval=Interval(10, 20)), BinOpChecked(2, "+", 0, 1, 8), Return()])
        neg_mul = verify_bytecode([InitReg(0, interval=Interval(-12, -10)), InitReg(1, interval=Interval(-12, -10)), BinOpChecked(2, "*", 0, 1, 8), Return()])
        self.assertEqual(ovf.error_codes, ["HYB-E5001"])
        self.assertEqual(neg_mul.error_codes, ["HYB-E5001"])
        # Fail-to-Pass 2: HYB-E5002 slice index out of bounds ([0, 16] on len=16)
        oob = verify_bytecode([InitReg(0), InitReg(1, interval=Interval(0, 16)), IndexSlice(2, 0, 1, 16), Return()])
        self.assertEqual(oob.error_codes, ["HYB-E5002"])
        # Positive Control: GuardRange narrows [0, 64] -> [0, 15] before IndexSlice(len=16)
        ok_slice = verify_bytecode([InitReg(0), InitReg(1, interval=Interval(0, 64)), GuardRange(1, 0, 15), InitReg(2, interval=Interval(1, 10)), BinOpChecked(3, "+", 1, 2, 8), IndexSlice(4, 0, 1, 16), Return()])
        self.assertTrue(ok_slice.accepted)
        self.assertEqual(ok_slice.diagnostics, [])

    def test_cfg_diamond_join_and_bounded_loop_widening_convergence(self) -> None:
        # CFG Join: moving r0 on true branch merges OWNED |_| MOVED = MOVED at pc=6
        diamond = verify_bytecode([InitReg(0), InitReg(1), BranchCond(1, 3, 5), MoveReg(2, 0), Jump(6), UseReg(0), UseReg(0), Return()])
        self.assertFalse(diamond.accepted)
        self.assertEqual(diamond.error_codes, ["HYB-E3001"])
        self.assertEqual(diamond.entry_states[6][0].ownership, OwnershipState.MOVED)
        loop_mv = verify_bytecode([InitReg(0), InitReg(1), MoveReg(2, 0), BranchCond(1, 2, 4), Return()])
        self.assertEqual((loop_mv.accepted, loop_mv.error_codes, loop_mv.steps <= 10), (False, ["HYB-E3001"], True))
        # Bounded Loop Widening: back-edge Jump(3) widens r1 in <= 25 steps and GuardRange proves IndexSlice safe
        for name, prog, expected_ok, expected_codes in build_canonical_cases():
            res = verify_bytecode(prog)
            self.assertEqual(res.accepted, expected_ok, msg=f"case {name} failed")
            self.assertEqual(res.error_codes, expected_codes, msg=f"case {name} codes mismatch")
            self.assertLessEqual(res.steps, 25, msg=f"case {name} exceeded linear step bound")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(main(["--json"]), 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["all_passed"])
        self.assertEqual(len(payload["cases"]), 9)


if __name__ == "__main__":
    unittest.main()
