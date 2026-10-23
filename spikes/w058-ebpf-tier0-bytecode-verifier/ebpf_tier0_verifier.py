#!/usr/bin/env python3
"""W-058 (#1289): eBPF-Style Abstract-Interpretation Bytecode Verifier for Tier-0.

Unifies Hybrid Tier-0's 4 separate AST passes — ownership/moves (freeze.rs /
sendability.rs), map-slot & borrow loans (loans.rs / addr_loan.rs), nullability
(shape.rs / emit_guard.rs), and interval range/overflow bounds (const_overflow.rs /
arith.rs / range_iter.rs) — into one linear O(V + E) abstract-interpretation pass
over the product lattice RegState = (OwnershipState, LoanMask, NullState, Interval).
"""
from __future__ import annotations
import argparse, json, sys
from dataclasses import dataclass
from enum import Enum
from typing import Union

LoanMask = int

class OwnershipState(str, Enum):
    OWNED, MOVED, SHARED, BORROWED = "OWNED", "MOVED", "SHARED", "BORROWED"

class NullState(str, Enum):
    NON_NULL, MAYBE_NULL = "NON_NULL", "MAYBE_NULL"

@dataclass(frozen=True)
class Interval:
    lo: int = 0; hi: int = 0
    def join(self, other: Interval) -> Interval: return Interval(min(self.lo, other.lo), max(self.hi, other.hi))
    def intersect(self, b: Interval) -> Interval: return Interval(max(self.lo, b.lo), max(max(self.lo, b.lo), min(self.hi, b.hi)))
    def fits_signed(self, bits: int) -> bool: return -(1 << (bits - 1)) <= self.lo and self.hi <= (1 << (bits - 1)) - 1
    def in_bounds(self, length: int) -> bool: return 0 <= self.lo and self.hi < length
    def widen(self, new: Interval, min_b: int = -32768, max_b: int = 32767) -> Interval:
        lo = min_b if new.lo < self.lo else max(min_b, self.lo)
        hi = max_b if new.hi > self.hi else min(max_b, self.hi)
        return Interval(lo, max(lo, hi))

@dataclass(frozen=True)
class RegState:
    ownership: OwnershipState = OwnershipState.OWNED
    loan_mask: LoanMask = 0
    nullability: NullState = NullState.NON_NULL
    interval: Interval = Interval(0, 0)

def join_ownership(a: OwnershipState, b: OwnershipState) -> OwnershipState:
    if a == b: return a
    if OwnershipState.MOVED in (a, b): return OwnershipState.MOVED
    return OwnershipState.BORROWED if OwnershipState.BORROWED in (a, b) else OwnershipState.SHARED

def join_nullability(a: NullState, b: NullState) -> NullState:
    return NullState.MAYBE_NULL if NullState.MAYBE_NULL in (a, b) else NullState.NON_NULL

def join_reg(a: RegState, b: RegState, widen: bool = False, bounds: tuple[int, int] = (-32768, 32767)) -> RegState:
    iv = a.interval.widen(b.interval, bounds[0], bounds[1]) if widen else a.interval.join(b.interval)
    return RegState(join_ownership(a.ownership, b.ownership), a.loan_mask | b.loan_mask, join_nullability(a.nullability, b.nullability), iv)

def join_states(s1: dict[int, RegState], s2: dict[int, RegState], widen: bool = False, bounds: tuple[int, int] = (-32768, 32767)) -> dict[int, RegState]:
    return {r: join_reg(s1[r], s2[r], widen=widen, bounds=bounds) if (r in s1 and r in s2) else s1.get(r, s2.get(r, RegState())) for r in (s1.keys() | s2.keys())}

@dataclass(frozen=True)
class InitReg: dst: int; ownership: OwnershipState = OwnershipState.OWNED; nullability: NullState = NullState.NON_NULL; interval: Interval = Interval(0, 0); loan_mask: LoanMask = 0
@dataclass(frozen=True)
class MoveReg: dst: int; src: int | None = None
@dataclass(frozen=True)
class UseReg: reg: int
@dataclass(frozen=True)
class TaskSpawnMove: reg: int
@dataclass(frozen=True)
class BorrowMapSlot: dst: int; map_reg: int; key: str = "k"; loan_bit: int | None = None
@dataclass(frozen=True)
class ReleaseLoan: loan_reg: int; map_reg: int = 0
@dataclass(frozen=True)
class MutateMap: map_reg: int; op: str = "insert"
@dataclass(frozen=True)
class GuardNonNull: reg: int
@dataclass(frozen=True)
class DerefReg: reg: int
@dataclass(frozen=True)
class GuardRange: reg: int; lo: int; hi: int
@dataclass(frozen=True)
class BinOpChecked: dst: int; op: str; lhs: int; rhs: int; bit_width: int = 8
@dataclass(frozen=True)
class IndexSlice: dst: int; arr_reg: int; idx_reg: int; arr_len: int
@dataclass(frozen=True)
class BranchCond: cond_reg: int; true_target: int; false_target: int
@dataclass(frozen=True)
class Jump: target: int
@dataclass(frozen=True)
class Return: pass

Instruction = Union[InitReg, MoveReg, UseReg, TaskSpawnMove, BorrowMapSlot, ReleaseLoan, MutateMap, GuardNonNull, DerefReg, GuardRange, BinOpChecked, IndexSlice, BranchCond, Jump, Return]

@dataclass
class VerificationResult:
    accepted: bool; diagnostics: list[str]; entry_states: dict[int, dict[int, RegState]]; exit_states: dict[int, dict[int, RegState]]; steps: int
    @property
    def error_codes(self) -> list[str]: return sorted({d.split(":")[0] for d in self.diagnostics})

def _eval_interval_op(op: str, a: Interval, b: Interval) -> Interval:
    if op == "+": return Interval(a.lo + b.lo, a.hi + b.hi)
    if op == "-": return Interval(a.lo - b.hi, a.hi - b.lo)
    if op == "*": return Interval(min(p := (a.lo * b.lo, a.lo * b.hi, a.hi * b.lo, a.hi * b.hi)), max(p))
    raise ValueError(f"unsupported op: {op}")

def verify_bytecode(program: list[Instruction], initial_regs: dict[int, RegState] | None = None, widen_bounds: tuple[int, int] = (-32768, 32767)) -> VerificationResult:
    """Single-pass worklist abstract interpreter with bounded widening on back-edges."""
    if not program: return VerificationResult(True, [], {}, {}, 0)
    n, entry_states, exit_states, diags_by_pc, worklist, steps = len(program), {0: dict(initial_regs or {})}, {}, {}, [0], 0
    def chk_live(r: int, pc: int, out: list[str]) -> RegState:
        rs = st.get(r, RegState())
        if rs.ownership == OwnershipState.MOVED: out.append(f"HYB-E3001:UseAfterMove(r{r} @ pc={pc})")
        return rs
    while worklist:
        pc = worklist.pop(0)
        if not (0 <= pc < n): continue
        steps += 1
        st, ins, pc_diags, succs = dict(entry_states[pc]), program[pc], [], ([pc + 1] if pc + 1 < n else [])
        if isinstance(ins, InitReg): st[ins.dst] = RegState(ins.ownership, ins.loan_mask, ins.nullability, ins.interval)
        elif isinstance(ins, UseReg): chk_live(ins.reg, pc, pc_diags)
        elif isinstance(ins, (MoveReg, TaskSpawnMove)):
            src = ins.reg if isinstance(ins, TaskSpawnMove) else (ins.dst if ins.src is None else ins.src)
            rs = chk_live(src, pc, pc_diags)
            if rs.ownership in (OwnershipState.SHARED, OwnershipState.BORROWED) or rs.loan_mask != 0:
                pc_diags.append(f"HYB-E3003:SharedOrBorrowedMove(r{src} state={rs.ownership.value} @ pc={pc})")
            st[src] = RegState(OwnershipState.MOVED, rs.loan_mask, rs.nullability, rs.interval)
            if isinstance(ins, MoveReg) and ins.src is not None: st[ins.dst] = RegState(OwnershipState.OWNED, 0, rs.nullability, rs.interval)
        elif isinstance(ins, BorrowMapSlot):
            ms = chk_live(ins.map_reg, pc, pc_diags)
            bit = ins.loan_bit if ins.loan_bit is not None else (1 << ins.dst)
            st[ins.map_reg] = RegState(ms.ownership, ms.loan_mask | bit, ms.nullability, ms.interval)
            st[ins.dst] = RegState(OwnershipState.BORROWED, bit, NullState.NON_NULL, Interval(0, 0))
        elif isinstance(ins, ReleaseLoan):
            ls, ms = st.get(ins.loan_reg, RegState()), st.get(ins.map_reg, RegState())
            st[ins.map_reg] = RegState(ms.ownership, ms.loan_mask & ~ls.loan_mask, ms.nullability, ms.interval)
            st[ins.loan_reg] = RegState(OwnershipState.MOVED, 0, ls.nullability, ls.interval)
        elif isinstance(ins, MutateMap):
            ms = chk_live(ins.map_reg, pc, pc_diags)
            if ms.loan_mask != 0: pc_diags.append(f"HYB-E3102:MapSlotLoanInvalidation(r{ins.map_reg} op={ins.op} mask=0x{ms.loan_mask:x} @ pc={pc})")
        elif isinstance(ins, GuardNonNull):
            rs = st.get(ins.reg, RegState())
            st[ins.reg] = RegState(rs.ownership, rs.loan_mask, NullState.NON_NULL, rs.interval)
        elif isinstance(ins, DerefReg):
            if chk_live(ins.reg, pc, pc_diags).nullability == NullState.MAYBE_NULL: pc_diags.append(f"HYB-E4001:NullableDereference(r{ins.reg} @ pc={pc})")
        elif isinstance(ins, GuardRange):
            rs = st.get(ins.reg, RegState())
            st[ins.reg] = RegState(rs.ownership, rs.loan_mask, rs.nullability, rs.interval.intersect(Interval(ins.lo, ins.hi)))
        elif isinstance(ins, BinOpChecked):
            l_st, r_st = chk_live(ins.lhs, pc, pc_diags), chk_live(ins.rhs, pc, pc_diags)
            iv = _eval_interval_op(ins.op, l_st.interval, r_st.interval)
            if not iv.fits_signed(ins.bit_width): pc_diags.append(f"HYB-E5001:ArithmeticOverflow(r{ins.dst}=[{iv.lo},{iv.hi}] exceeds i{ins.bit_width} @ pc={pc})")
            st[ins.dst] = RegState(OwnershipState.OWNED, 0, NullState.NON_NULL, iv)
        elif isinstance(ins, IndexSlice):
            a_st, i_st = chk_live(ins.arr_reg, pc, pc_diags), chk_live(ins.idx_reg, pc, pc_diags)
            if a_st.nullability == NullState.MAYBE_NULL: pc_diags.append(f"HYB-E4001:NullableDereference(r{ins.arr_reg} @ pc={pc})")
            if not i_st.interval.in_bounds(ins.arr_len): pc_diags.append(f"HYB-E5002:IndexOutOfBounds(r{ins.idx_reg}=[{i_st.interval.lo},{i_st.interval.hi}] len={ins.arr_len} @ pc={pc})")
            st[ins.dst] = RegState(OwnershipState.OWNED, 0, NullState.NON_NULL, Interval(0, 0))
        elif isinstance(ins, BranchCond):
            chk_live(ins.cond_reg, pc, pc_diags)
            succs = [ins.true_target, ins.false_target]
        elif isinstance(ins, Jump): succs = [ins.target]
        elif isinstance(ins, Return): succs = []
        diags_by_pc[pc], exit_states[pc] = pc_diags, st
        for s_pc in succs:
            if 0 <= s_pc < n:
                merged = dict(st) if s_pc not in entry_states else join_states(entry_states[s_pc], st, widen=(s_pc <= pc), bounds=widen_bounds)
                if s_pc not in entry_states or merged != entry_states[s_pc]:
                    entry_states[s_pc] = merged
                    if s_pc not in worklist: worklist.append(s_pc)
    all_diags = sorted({d for ds in diags_by_pc.values() for d in ds})
    return VerificationResult(not all_diags, all_diags, entry_states, exit_states, steps)

def build_canonical_cases() -> list[tuple[str, list[Instruction], bool, list[str]]]:
    return [
        ("use_after_move", [InitReg(0), MoveReg(1, 0), UseReg(0), Return()], False, ["HYB-E3001"]),
        ("shared_spawn_move", [InitReg(0, OwnershipState.SHARED), TaskSpawnMove(0), Return()], False, ["HYB-E3003"]),
        ("diamond_branch_move_join", [InitReg(0), InitReg(1), BranchCond(1, 3, 5), MoveReg(2, 0), Jump(6), UseReg(0), UseReg(0), Return()], False, ["HYB-E3001"]),
        ("map_slot_loan_invalidation", [InitReg(0), BorrowMapSlot(1, 0, "k"), MutateMap(0, "delete"), UseReg(1), Return()], False, ["HYB-E3102"]),
        ("map_slot_loan_released_ok", [InitReg(0), BorrowMapSlot(1, 0, "k"), UseReg(1), ReleaseLoan(1, 0), MutateMap(0, "insert"), Return()], True, []),
        ("nullable_deref_unguarded", [InitReg(0, nullability=NullState.MAYBE_NULL), DerefReg(0), Return()], False, ["HYB-E4001"]),
        ("checked_i8_overflow", [InitReg(0, interval=Interval(100, 120)), InitReg(1, interval=Interval(10, 20)), BinOpChecked(2, "+", 0, 1, 8), Return()], False, ["HYB-E5001"]),
        ("slice_oob_without_guard", [InitReg(0), InitReg(1, interval=Interval(0, 16)), IndexSlice(2, 0, 1, 16), Return()], False, ["HYB-E5002"]),
        ("loop_widen_and_guarded_slice_ok", [InitReg(0, nullability=NullState.MAYBE_NULL), InitReg(1, interval=Interval(0, 0)), InitReg(2, interval=Interval(1, 1)), BranchCond(1, 4, 7), GuardRange(1, 0, 6), BinOpChecked(1, "+", 1, 2, 8), Jump(3), GuardNonNull(0), GuardRange(1, 0, 7), IndexSlice(3, 0, 1, 8), Return()], True, []),
    ]

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W-058 (#1289) eBPF-Style Tier-0 Bytecode Verifier.")
    ap.add_argument("--check", action="store_true", help="Run built-in verification scenarios.")
    ap.add_argument("--json", action="store_true", help="Emit JSON verification report.")
    args = ap.parse_args(argv)
    results = [{"name": k, "accepted": (r := verify_bytecode(p)).accepted, "codes": r.error_codes, "steps": r.steps, "matched": r.accepted == ok and r.error_codes == c} for k, p, ok, c in build_canonical_cases()]
    all_ok = all(r["matched"] for r in results)
    print(json.dumps({"issue": 1289, "spike": "W-058", "all_passed": all_ok, "cases": results}, indent=2) if args.json else f"W-058 (#1289) OK: {sum(r['matched'] for r in results)}/{len(results)} scenarios verified in single-pass abstract interpretation.")
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
