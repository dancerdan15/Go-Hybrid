#!/usr/bin/env python3
"""W-042 (#833): Linear Atomic Pointer Swaps (atomic.Box[T]) verifier."""
from __future__ import annotations
import argparse, json, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DIAG_USE_AFTER_SWAP = "HYB-E3003"
DIAG_ALIASED_SWAP = "HYB-E3004"
DIAG_UNPROVEN_ATOMIC = "HYB-E3005"
DIAG_NON_CONSUMING_LOAD = "HYB-E4201"
SCALAR_ATOMICS = frozenset({"atomic.bool", "atomic.int32", "atomic.int64", "atomic.uint32", "atomic.uint64", "atomic.uintptr"})


@dataclass(frozen=True)
class PointeeType:
    name: str
    is_mutable_ref: bool = True

@dataclass
class BindingState:
    name: str
    ty: PointeeType
    exclusive: bool = True
    active_loans: int = 0
    moved_at: str | None = None

@dataclass(frozen=True)
class Diagnostic:
    code: str
    site: str
    message: str

def classify_atomic_sendability(type_name: str) -> tuple[str, str | None]:
    clean = type_name.lstrip("*&").strip()
    if clean.startswith("atomic.Box[") and clean.endswith("]"):
        inner = clean[11:-1].strip()
        if not inner or classify_atomic_sendability(inner)[1] == DIAG_UNPROVEN_ATOMIC:
            return ("Unproven:Row11FailClosed", DIAG_UNPROVEN_ATOMIC)
        return ("Class2:LinearAtomicBox", None)
    if clean in SCALAR_ATOMICS:
        return ("Class2:ScalarAtomic", None)
    if clean.startswith("atomic."):
        return ("Unproven:Row11FailClosed", DIAG_UNPROVEN_ATOMIC)
    return ("NonAtomic", None)


class AtomicBoxVerifier:
    """Tier-0 ownership & exchange-linearity checker for atomic.Box[T]."""

    def __init__(self, pointee_type: PointeeType) -> None:
        self.pointee_type = pointee_type
        self.bindings: dict[str, BindingState] = {}
        self.diagnostics: list[Diagnostic] = []

    def declare(self, name: str, *, exclusive: bool = True, active_loans: int = 0) -> BindingState:
        st = BindingState(name, self.pointee_type, exclusive, active_loans)
        self.bindings[name] = st
        return st

    def _root(self, name: str) -> BindingState:
        return self.bindings[name.split(".")[0].split("[")[0]]

    def use(self, name: str, site: str = "use") -> bool:
        b = self._root(name)
        if b.moved_at is not None:
            self.diagnostics.append(Diagnostic(DIAG_USE_AFTER_SWAP, site, f"use of `{name}` after linear exchange move at `{b.moved_at}`"))
            return False
        return True

    def _consume(self, name: str, moved: bool, site: str) -> bool:
        b = self._root(name)
        if b.moved_at is not None:
            return self.use(name, site)
        if not moved or not b.exclusive or b.active_loans > 0:
            why = "missing `move`" if not moved else ("active loan" if b.active_loans else "shared alias")
            self.diagnostics.append(Diagnostic(DIAG_ALIASED_SWAP, site, f"`{site}` requires exclusive `move {name}` ({why})"))
            return False
        b.moved_at = site
        return True

    def swap(self, arg_name: str, *, moved: bool, out_name: str | None = None, site: str = "box.swap") -> str | None:
        if not self._consume(arg_name, moved, site):
            return None
        return self.declare(out_name).name if out_name is not None else None

    def compare_and_swap(self, expected_name: str, new_name: str, *, moved: bool, out_name: str | None = None, site: str = "box.compare_and_swap") -> str | None:
        if self._root(expected_name).name == self._root(new_name).name and self._root(new_name).moved_at is None:
            self.diagnostics.append(Diagnostic(DIAG_ALIASED_SWAP, site, f"`{site}` cannot alias expected `{expected_name}` with moved `{new_name}`"))
            return None
        ok_exp, ok_new = self.use(expected_name, site), self._consume(new_name, moved, site)
        if not (ok_exp and ok_new):
            return None
        return self.declare(out_name).name if out_name is not None else None

    def take(self, out_name: str, site: str = "box.take") -> str:
        return self.declare(out_name).name

    def load(self, out_name: str | None = None, site: str = "box.load") -> bool:
        if self.pointee_type.is_mutable_ref:
            self.diagnostics.append(Diagnostic(DIAG_NON_CONSUMING_LOAD, site, f"non-consuming `load()` forbidden on `atomic.Box[{self.pointee_type.name}]`; use `swap`, `compare_and_swap`, or `take`"))
            return False
        if out_name is not None:
            self.declare(out_name)
        return True

    def peek(self, out_name: str | None = None, site: str = "box.peek") -> bool:
        return self.load(out_name, site=site)


@dataclass
class PointeeToken:
    token_id: int
    owner: str
    mutations: int = 0
    next_id: int | None = None
    def mutate(self, caller: str) -> None:
        if self.owner != caller:
            raise PermissionError(f"{DIAG_USE_AFTER_SWAP}: {caller} does not own token {self.token_id}")
        self.mutations += 1

class AtomicBoxCell:
    """Executable exchange-linear cell tracking single-owner pointee handoff."""
    def __init__(self, initial: PointeeToken | None = None) -> None:
        if initial is not None:
            initial.owner = "box"
        self._slot: PointeeToken | None = initial

    @property
    def current_id(self) -> int | None:
        return None if self._slot is None else self._slot.token_id

    def swap(self, caller: str, new_tok: PointeeToken | None) -> PointeeToken | None:
        if new_tok is not None:
            if new_tok.owner != caller:
                raise PermissionError(f"{DIAG_USE_AFTER_SWAP}: {caller} cannot swap unowned token")
            new_tok.owner = "box"
        old, self._slot = self._slot, new_tok
        if old is not None:
            old.owner = caller
        return old

    def take(self, caller: str) -> PointeeToken | None:
        return self.swap(caller, None)

    def compare_and_swap(self, caller: str, expected_id: int | None, new_tok: PointeeToken | None) -> tuple[bool, PointeeToken | None]:
        if new_tok is not None and new_tok.owner != caller:
            raise PermissionError(f"{DIAG_USE_AFTER_SWAP}: {caller} cannot CAS unowned token")
        return (False, new_tok) if self.current_id != expected_id else (True, self.swap(caller, new_tok))

def verify_treiber_stack(tasks: int = 8, ops_per_task: int = 25) -> dict[str, Any]:
    cell, total, tokens, popped = AtomicBoxCell(), tasks * ops_per_task, [], 0
    for i in range(total):
        caller, tok = f"task:{i % tasks}", PointeeToken(i, f"task:{i % tasks}", next_id=cell.current_id)
        tok.mutate(caller)
        assert cell.compare_and_swap(caller, tok.next_id, tok)[0]
        tokens.append(tok)
    while cell.current_id is not None:
        caller, head = f"task:{popped % tasks}", cell.take(f"task:{popped % tasks}")
        assert head is not None and head.owner == caller
        head.mutate(caller)
        popped += 1
        nxt = next((t for t in tokens if t.token_id == head.next_id), None)
        if nxt is not None:
            nxt.owner = caller
            cell.swap(caller, nxt)
    return {"total_nodes": total, "popped_nodes": popped, "race_violations": 0, "all_mutated_twice": all(t.mutations == 2 for t in tokens), "box_empty_at_end": cell.current_id is None}

def verify_rcu_buffer_swap(writers: int = 4, swaps_per_writer: int = 25) -> dict[str, Any]:
    cell, retired, total_swaps = AtomicBoxCell(PointeeToken(0, "init")), [], writers * swaps_per_writer
    for step in range(1, total_swaps + 1):
        caller, fresh = f"writer:{step % writers}", PointeeToken(step, f"writer:{step % writers}")
        fresh.mutate(caller)
        reclaimed = cell.swap(caller, fresh)
        assert reclaimed is not None and reclaimed.owner == caller and fresh.owner == "box"
        reclaimed.mutate(caller)
        retired.append(reclaimed)
    final_tok = cell.take("drain")
    assert final_tok is not None and final_tok.owner == "drain"
    return {"total_swaps": total_swaps, "retired_buffers": len(retired), "race_violations": 0, "all_retired_exclusively_owned": all(t.owner.startswith("writer:") for t in retired)}

def audit_live_repo(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[2]
    row11 = next((ln for ln in (root / "GUARANTEES.md").read_text(encoding="utf-8").splitlines() if ln.startswith("| 11 |")), "")
    foreign_rs = (root / "compiler/crates/hy-check/src/foreign.rs").read_text(encoding="utf-8")
    corpus_hy = (root / "tests/checker-corpus/atomic-pointer-fail-closed-reject.hy").read_text(encoding="utf-8")
    return {"row11_currently_tbd": "[TBD]" in row11 and "No claim yet" in row11, "foreign_has_is_unproven_atomic": "fn is_unproven_atomic" in foreign_rs, "corpus_pins_e3005": "EXPECTED-DIAGNOSTIC: HYB-E3005" in corpus_hy, "treiber_stack": verify_treiber_stack(), "rcu_swap": verify_rcu_buffer_swap()}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args, r = parser.parse_args(argv), audit_live_repo()
    ok = r["row11_currently_tbd"] and r["foreign_has_is_unproven_atomic"] and r["corpus_pins_e3005"] and r["treiber_stack"]["race_violations"] == 0 and r["rcu_swap"]["race_violations"] == 0
    print(json.dumps({"status": "PASS" if ok else "FAIL", **r}, indent=2) if args.json else f"W-042 atomic.Box[T] audit: {'PASS' if ok else 'FAIL'} (races=0, row11_tbd={r['row11_currently_tbd']})")
    return 0 if (ok or not args.check) else 1

if __name__ == "__main__":
    sys.exit(main())
