"""Compile-time lock-order DAG and linear LockGuard[L] typestate verifier (Ref #1291)."""
from __future__ import annotations

import argparse
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import AbstractSet, Iterable, Iterator, Mapping, Sequence

DIAGNOSTIC_CODES: dict[str, str] = {
    "HYB-E6100": "lock hierarchy cycle",
    "HYB-E6101": "AB-BA lock-order inversion",
    "HYB-E6102": "re-entrant / same-level lock acquisition",
    "HYB-E6103": "unreleased lock across return or ? try edge",
    "HYB-E6104": "asymmetric lock state across control-flow merge",
}


@dataclass(frozen=True)
class LockDiagnostic:
    code: str; fn_name: str; message: str; detail: dict[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class MutexSpec: name: str; level: str
@dataclass(frozen=True)
class HeldLock: guard_id: str; mutex_name: str; level: str
@dataclass(frozen=True)
class AcquireLock: guard_id: str; mutex_name: str; level: str
@dataclass(frozen=True)
class ReleaseLock: guard_id: str
@dataclass(frozen=True)
class DeferUnlock: guard_id: str
@dataclass(frozen=True)
class TryEdge: site: str = "?"
@dataclass(frozen=True)
class Return: site: str = "return"
@dataclass(frozen=True)
class Break: site: str = "break"
@dataclass(frozen=True)
class CallFn: callee: str; site: str = ""
@dataclass(frozen=True)
class BranchJoin:
    left: tuple[LockOp, ...]; right: tuple[LockOp, ...]; site: str = "if/else"
    def __init__(self, left: Sequence[LockOp], right: Sequence[LockOp], site: str = "if/else") -> None:
        object.__setattr__(self, "left", tuple(left)); object.__setattr__(self, "right", tuple(right)); object.__setattr__(self, "site", site)
LockOp = AcquireLock | ReleaseLock | DeferUnlock | TryEdge | Return | Break | CallFn | BranchJoin
TryReturn, ReturnEdge, BreakEdge = TryEdge, Return, Break
@dataclass(frozen=True)
class FunctionSpec:
    name: str; ops: tuple[LockOp, ...] = (); acquires: frozenset[str] = frozenset()
    def __init__(self, name: str, ops: Sequence[LockOp] = (), acquires: Iterable[str] = ()) -> None:
        object.__setattr__(self, "name", name); object.__setattr__(self, "ops", tuple(ops)); object.__setattr__(self, "acquires", frozenset(acquires))
FnSpec = FunctionDef = FunctionSpec
@dataclass(frozen=True)
class VerificationReport:
    accepted: bool; diagnostics: tuple[LockDiagnostic, ...]; fn_summaries: dict[str, frozenset[str]] = field(default_factory=dict)
    def codes(self) -> list[str]: return [d.code for d in self.diagnostics]

class LockHierarchy:
    """Partial order (L1 < L2) over compile-time lock levels."""
    def __init__(self, levels: Iterable[str] = (), precedence: Iterable[tuple[str, str]] = ()) -> None:
        self.levels, self.edges = set(levels), {lvl: set() for lvl in levels}
        for lower, higher in precedence: self.add_precedence(lower, higher)
    def add_level(self, level: str) -> None: self.levels.add(level); self.edges.setdefault(level, set())
    def add_precedence(self, lower: str, higher: str) -> None: self.add_level(lower); self.add_level(higher); self.edges[lower].add(higher)
    def _reachable(self, src: str, dst: str) -> bool:
        if src not in self.edges or dst not in self.levels: return False
        queue, seen = deque(self.edges[src]), set(self.edges[src])
        while queue:
            if (curr := queue.popleft()) == dst: return True
            for nxt in self.edges.get(curr, ()) - seen: seen.add(nxt); queue.append(nxt)
        return False
    def strictly_precedes(self, lower: str, higher: str) -> bool:
        return lower != higher and self._reachable(lower, higher) and not self._reachable(higher, lower)
    def find_cycles(self) -> list[tuple[str, ...]]:
        state, stack, cycles = {lvl: 0 for lvl in self.levels}, [], []
        def dfs(node: str) -> None:
            state[node] = 1; stack.append(node)
            for nxt in sorted(self.edges.get(node, ())):
                if state.get(nxt, 0) == 0: dfs(nxt)
                elif state.get(nxt) == 1: cycles.append(tuple(stack[stack.index(nxt) :] + [nxt]))
            stack.pop(); state[node] = 2
        for lvl in sorted(self.levels):
            if state[lvl] == 0: dfs(lvl)
        return cycles
    def validate(self) -> list[LockDiagnostic]:
        return [LockDiagnostic("HYB-E6100", "<hierarchy>", f"lock hierarchy cycle: {' -> '.join(c)}", {"cycle": c}) for c in self.find_cycles()]

def _iter_ops(ops: Sequence[LockOp]) -> Iterator[LockOp]:
    for op in ops:
        yield op
        if isinstance(op, BranchJoin): yield from _iter_ops(op.left); yield from _iter_ops(op.right)

@dataclass
class _LockState:
    active_guards: dict[str, HeldLock] = field(default_factory=dict); defer_unlocked_guards: set[str] = field(default_factory=set); terminated: bool = False
    def clone(self) -> _LockState: return _LockState(dict(self.active_guards), set(self.defer_unlocked_guards), self.terminated)
    def unreleased(self) -> list[str]: return [gid for gid in self.active_guards if gid not in self.defer_unlocked_guards]

class LockOrderVerifier:
    """Verifies lock-level partial order (Row 8) and linear LockGuard[L] must-unlock typestate (Row 16)."""
    def __init__(self, hierarchy: LockHierarchy, functions: Sequence[FunctionSpec] = ()) -> None:
        self.hierarchy, self.functions = hierarchy, {fn.name: fn for fn in functions}
    def register_function(self, fn: FunctionSpec) -> None: self.functions[fn.name] = fn
    def compute_summaries(self, functions: Sequence[FunctionSpec] | None = None) -> dict[str, frozenset[str]]:
        fn_map = {**self.functions, **{f.name: f for f in (functions or ())}}
        acquires = {n: set(f.acquires) | {op.level for op in _iter_ops(f.ops) if isinstance(op, AcquireLock)} for n, f in fn_map.items()}
        calls = {n: {op.callee for op in _iter_ops(f.ops) if isinstance(op, CallFn)} for n, f in fn_map.items()}
        changed = True
        while changed:
            changed = False
            for name, callees in calls.items():
                for callee in callees:
                    if not acquires.get(callee, set()).issubset(acquires[name]):
                        acquires[name].update(acquires.get(callee, set())); changed = True
        return {k: frozenset(v) for k, v in acquires.items()}
    def _check_ops(self, fn_name: str, ops: Sequence[LockOp], state: _LockState, summaries: Mapping[str, AbstractSet[str]], diags: list[LockDiagnostic]) -> None:
        for op in ops:
            if state.terminated: break
            if isinstance(op, AcquireLock):
                for held in state.active_guards.values():
                    if not self.hierarchy.strictly_precedes(held.level, op.level):
                        code = "HYB-E6102" if held.level == op.level else "HYB-E6101"
                        diags.append(LockDiagnostic(code, fn_name, f"{DIAGNOSTIC_CODES[code]}: {op.mutex_name}[{op.level}] while holding {held.mutex_name}[{held.level}]", {"held": held.level, "requested": op.level, "guard_id": op.guard_id}))
                state.active_guards[op.guard_id] = HeldLock(op.guard_id, op.mutex_name, op.level); state.defer_unlocked_guards.discard(op.guard_id)
            elif isinstance(op, ReleaseLock):
                state.active_guards.pop(op.guard_id, None); state.defer_unlocked_guards.discard(op.guard_id)
            elif isinstance(op, DeferUnlock) and op.guard_id in state.active_guards:
                state.defer_unlocked_guards.add(op.guard_id)
            elif isinstance(op, (TryEdge, Return, Break)):
                if leaked := (list(state.active_guards) if isinstance(op, Break) else state.unreleased()):
                    diags.append(LockDiagnostic("HYB-E6103", fn_name, f"unreleased lock across return or ? try edge ({op.site}): {', '.join(leaked)}", {"edge": op.site, "unreleased_guards": tuple(leaked)}))
                if not isinstance(op, TryEdge): state.terminated = True
            elif isinstance(op, CallFn):
                for callee_lvl in sorted(summaries.get(op.callee, ())):
                    for held in state.active_guards.values():
                        if not self.hierarchy.strictly_precedes(held.level, callee_lvl):
                            diags.append(LockDiagnostic("HYB-E6101", fn_name, f"AB-BA lock-order inversion across call {op.callee}(): callee acquires {callee_lvl} while holding {held.mutex_name}[{held.level}]", {"callee": op.callee, "held": held.level, "callee_level": callee_lvl}))
            elif isinstance(op, BranchJoin):
                left_st, right_st = state.clone(), state.clone()
                self._check_ops(fn_name, op.left, left_st, summaries, diags); self._check_ops(fn_name, op.right, right_st, summaries, diags)
                l_keys, r_keys = set(left_st.active_guards), set(right_st.active_guards)
                if l_keys != r_keys or left_st.defer_unlocked_guards != right_st.defer_unlocked_guards:
                    diags.append(LockDiagnostic("HYB-E6104", fn_name, f"asymmetric lock state across control-flow merge ({op.site}): left={sorted(l_keys)}, right={sorted(r_keys)}", {"left": tuple(sorted(l_keys)), "right": tuple(sorted(r_keys)), "diff": tuple(sorted(l_keys ^ r_keys))}))
                common = l_keys & r_keys
                state.active_guards = {k: left_st.active_guards[k] for k in left_st.active_guards if k in common}
                state.defer_unlocked_guards = (left_st.defer_unlocked_guards | right_st.defer_unlocked_guards) & common
                state.terminated = left_st.terminated and right_st.terminated
    def verify_function(self, fn: FunctionSpec, summaries: Mapping[str, AbstractSet[str]] | None = None) -> VerificationReport:
        resolved, diags, state = (dict(summaries) if summaries is not None else self.compute_summaries([fn])), [], _LockState()
        self._check_ops(fn.name, fn.ops, state, resolved, diags)
        if not state.terminated and (leaked := state.unreleased()):
            diags.append(LockDiagnostic("HYB-E6103", fn.name, f"unreleased lock across return or ? try edge (fn_exit): {', '.join(leaked)}", {"edge": "fn_exit", "unreleased_guards": tuple(leaked)}))
        return VerificationReport(len(diags) == 0, tuple(diags), {k: frozenset(v) for k, v in resolved.items()})
    def verify_program(self, functions: Sequence[FunctionSpec] | None = None) -> VerificationReport:
        fn_list = list(functions) if functions is not None else list(self.functions.values())
        diags, summaries = self.hierarchy.validate(), self.compute_summaries(fn_list)
        for fn in fn_list: diags.extend(self.verify_function(fn, summaries).diagnostics)
        return VerificationReport(len(diags) == 0, tuple(diags), summaries)
    verify = verify_program

def verify_program(hierarchy: LockHierarchy, functions: Sequence[FunctionSpec]) -> VerificationReport:
    return LockOrderVerifier(hierarchy, functions).verify_program(functions)

def run_self_check() -> VerificationReport:
    assert [d.code for d in LockHierarchy(precedence=[("L1", "L2"), ("L2", "L1")]).validate()] == ["HYB-E6100"]
    dag = LockHierarchy(precedence=[("L_NET", "L_SESSION"), ("L_SESSION", "L_STORAGE")])
    helper = FunctionSpec("flush_net", [AcquireLock("gn", "mu_net", "L_NET"), ReleaseLock("gn")])
    valid_fn = FunctionSpec("ordered_tx", [AcquireLock("g1", "mu_net", "L_NET"), DeferUnlock("g1"), TryEdge("decode_header()?"), AcquireLock("g2", "mu_store", "L_STORAGE"), BranchJoin([ReleaseLock("g2")], [ReleaseLock("g2")])])
    ok_rep = verify_program(dag, [helper, valid_fn])
    assert ok_rep.accepted and not ok_rep.diagnostics
    bad_rep = verify_program(dag, [helper,
        FunctionSpec("invert", [AcquireLock("a", "m2", "L_SESSION"), AcquireLock("b", "m1", "L_NET"), ReleaseLock("b"), ReleaseLock("a")]),
        FunctionSpec("reentrant", [AcquireLock("a", "m1", "L_NET"), AcquireLock("b", "m1b", "L_NET"), ReleaseLock("b"), ReleaseLock("a")]),
        FunctionSpec("try_leak", [AcquireLock("a", "m1", "L_NET"), TryEdge("io()?"), ReleaseLock("a")]),
        FunctionSpec("branch_mismatch", [AcquireLock("a", "m1", "L_NET"), BranchJoin([ReleaseLock("a")], [])]),
        FunctionSpec("call_inversion", [AcquireLock("a", "m2", "L_SESSION"), CallFn("flush_net"), ReleaseLock("a")])])
    assert bad_rep.codes() == ["HYB-E6101", "HYB-E6102", "HYB-E6103", "HYB-E6104", "HYB-E6101"]
    return ok_rep

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify static lock-order DAG & LockGuard[L] typestate.")
    parser.add_argument("--check", action="store_true", help="Run built-in self-check scenarios.")
    if parser.parse_args(argv).check:
        print(f"w060 lock_order_verifier self-check: PASS (verified summaries={len(run_self_check().fn_summaries)})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
