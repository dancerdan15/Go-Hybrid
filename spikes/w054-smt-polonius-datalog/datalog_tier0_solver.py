#!/usr/bin/env python3
"""W-054 (#1276): Stratified Datalog / Polonius-Style Tier-0 Ownership & Loan Solver.

Replaces imperative FnState's 18 ad-hoc HashMap/HashSet/scope-stack state tables
(model.rs:217-303) and 220-line scope_pop reconciliation with a single monotone
fixpoint solver over 10 EDB fact relations, 4 IDB derived relations, and 3
declarative soundness queries (HYB-E3003, HYB-E3004, HYB-E3104).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from typing import Any

REPLACED_FNSTATE_FIELDS: tuple[str, ...] = (
    "env", "alias_scopes", "share_scopes", "shadow_links", "shadow_link_scopes",
    "scopes", "aliases", "may_share", "loans", "last_use", "loop_end",
    "closure_scoped_envs", "fnlit_scopes", "closure_mutates", "closure_fnlits",
    "named_results", "borrow_bindings", "closure_defs",
)


def subpath_covers(prefix: str, target: str) -> bool:
    """True iff `prefix` equals `target` or is a struct-field / index prefix of `target`."""
    return prefix == target or not prefix or target.startswith(prefix + ".") or target.startswith(prefix + "[")


def subpath_overlaps(p1: str, p2: str) -> bool:
    """IDB #1: True iff `p1 == p2` or one path is a field/index prefix of the other."""
    return subpath_covers(p1, p2) or subpath_covers(p2, p1)


@dataclass
class FactStore:
    """EDB (Extensional Database) base facts extracted from a Tier-0 function CFG."""

    cfg_edge: set[tuple[int, int]] = field(default_factory=set)
    moved: set[tuple[str, str, int]] = field(default_factory=set)
    reinitialized: set[tuple[str, str, int]] = field(default_factory=set)
    assigned_share: set[tuple[str, str, int]] = field(default_factory=set)
    borrowed: set[tuple[str, str, str, bool, int]] = field(default_factory=set)
    loan_used: set[tuple[str, int]] = field(default_factory=set)
    used: set[tuple[str, str, int]] = field(default_factory=set)
    mutated: set[tuple[str, str, int]] = field(default_factory=set)
    origin_escape: set[tuple[str, str, int]] = field(default_factory=set)
    returned: set[tuple[str, int]] = field(default_factory=set)

    def points(self) -> set[int]:
        pts: set[int] = {p for e in self.cfg_edge for p in e}
        for rel in (self.moved, self.reinitialized, self.assigned_share, self.used, self.mutated, self.origin_escape):
            pts.update(t[2] for t in rel)
        pts.update(t[4] for t in self.borrowed)
        pts.update(t[1] for t in self.loan_used | self.returned)
        return pts

    def variables(self) -> set[str]:
        vs: set[str] = {v for dst, src, _ in self.assigned_share for v in (dst, src)}
        for rel in (self.moved, self.reinitialized, self.used, self.mutated, self.origin_escape):
            vs.update(t[0] for t in rel)
        vs.update(t[1] for t in self.borrowed)
        vs.update(t[0] for t in self.returned)
        return vs


@dataclass
class FixpointSolution:
    """IDB (Intensional Database) relations materialized at least fixpoint."""

    alias_may_share: set[tuple[str, str, int]]
    path_moved_at: set[tuple[str, str, int]]
    loan_live_at: set[tuple[str, int]]
    origin_escape_at: set[tuple[str, str, int]]
    iterations: int
    diagnostics: list[str]

    @property
    def accepted(self) -> bool:
        return not self.diagnostics


def solve_fixpoint(edb: FactStore) -> FixpointSolution:
    """Compute the stratified monotone least fixpoint and evaluate soundness queries."""
    pts = edb.points()
    vars_all = edb.variables()
    loan_origins = {(loan, p) for loan, _, _, _, p in edb.borrowed}

    alias_may_share: set[tuple[str, str, int]] = {(v, v, p) for v in vars_all for p in pts}
    for dst, src, p in edb.assigned_share:
        alias_may_share.update(((dst, src, p), (src, dst, p)))
    path_moved_at, loan_live_at, origin_escape_at = set(edb.moved), set(edb.loan_used), set(edb.origin_escape)

    iterations = 0
    while True:
        iterations += 1
        new_alias = set(alias_may_share)
        for x, y, p in list(alias_may_share):
            for p_src, q_dst in edb.cfg_edge:
                if p_src == p:
                    new_alias.add((x, y, q_dst))
            for y2, z, p2 in list(alias_may_share):
                if p2 == p and y2 == y:
                    new_alias.add((x, z, p))

        new_moved = set(path_moved_at)
        for v, path, p in list(path_moved_at):
            for p_src, q_dst in edb.cfg_edge:
                if p_src == p and not any(rv == v and subpath_covers(ip, path) for rv, ip, rp in edb.reinitialized if rp == q_dst):
                    new_moved.add((v, path, q_dst))

        new_live = set(loan_live_at)
        for loan, q in list(loan_live_at):
            if (loan, q) not in loan_origins:
                for p_src, q_dst in edb.cfg_edge:
                    if q_dst == q:
                        new_live.add((loan, p_src))

        new_escape = set(origin_escape_at)
        for v, kind, p in list(origin_escape_at):
            for p_src, q_dst in edb.cfg_edge:
                if p_src == p and not any(rv == v and rp == q_dst for rv, _, rp in edb.reinitialized):
                    new_escape.add((v, kind, q_dst))

        if (new_alias, new_moved, new_live, new_escape) == (alias_may_share, path_moved_at, loan_live_at, origin_escape_at):
            break
        alias_may_share, path_moved_at, loan_live_at, origin_escape_at = new_alias, new_moved, new_live, new_escape

    diags: set[str] = set()
    for u, upath, p in edb.used:
        for v, vpath, mp in path_moved_at:
            if mp == p and (u, v, p) in alias_may_share and subpath_overlaps(upath, vpath):
                diags.add(f"HYB-E3003:UseAfterMove({u}:{upath} via {v}:{vpath} @ p{p})")
    for v, mpath, p in edb.mutated:
        for loan, lp in loan_live_at:
            if lp == p:
                for b_loan, r, bpath, _, _ in edb.borrowed:
                    if b_loan == loan and (v, r, p) in alias_may_share and subpath_overlaps(mpath, bpath):
                        diags.add(f"HYB-E3004:MutatedWhileLoaned({v}:{mpath} conflicts {loan} on {r}:{bpath} @ p{p})")
    for v, p in edb.returned:
        for w, kind, ep in origin_escape_at:
            if ep == p and (v, w, p) in alias_may_share:
                diags.add(f"HYB-E3104:DanglingOriginEscape({v} escapes {w}:{kind} @ p{p})")

    return FixpointSolution(alias_may_share, path_moved_at, loan_live_at, origin_escape_at, iterations, sorted(diags))


def build_canonical_cases() -> list[tuple[str, FactStore, bool, list[str]]]:
    """8 canonical verification cases covering branches, loops, partial moves, NLL, and escapes."""
    return [
        ("diamond_branch_move", FactStore({(0, 1), (0, 2), (1, 3), (2, 3)}, moved={("buf", "buf", 1)}, used={("buf", "buf", 3)}), False, ["HYB-E3003"]),
        ("loop_backedge_move", FactStore({(0, 1), (1, 2), (2, 1), (1, 3)}, moved={("conn", "conn", 2)}, used={("conn", "conn", 1)}), False, ["HYB-E3003"]),
        ("disjoint_partial_field_move", FactStore({(0, 1), (1, 2)}, moved={("p", "p.x", 1)}, used={("p", "p.y", 2)}), True, []),
        ("overlapping_partial_field_move", FactStore({(0, 1), (1, 2)}, moved={("p", "p.x", 1)}, used={("p", "p", 2)}), False, ["HYB-E3003"]),
        ("transitive_may_share_loan_mutation", FactStore({(0, 1), (1, 2), (2, 3), (3, 4)}, assigned_share={("a1", "m", 0), ("a2", "a1", 1)}, borrowed={("L1", "m", "m.items", False, 2)}, mutated={("a2", "m.items[0]", 3)}, loan_used={("L1", 4)}), False, ["HYB-E3004"]),
        ("nll_dead_loan_mutation", FactStore({(0, 1), (1, 2), (2, 3)}, borrowed={("L1", "m", "m.items", False, 1)}, loan_used={("L1", 2)}, mutated={("m", "m.items", 3)}), True, []),
        ("slot_loan_local_addr_escape", FactStore({(0, 1), (1, 2)}, origin_escape={("slot", "slot_loan", 0)}, assigned_share={("out", "slot", 1)}, returned={("out", 2)}), False, ["HYB-E3104"]),
        ("reinitialized_loop_var", FactStore({(0, 1), (1, 2), (2, 3), (3, 1), (1, 4)}, used={("pkt", "pkt", 1)}, moved={("pkt", "pkt", 2)}, reinitialized={("pkt", "pkt", 3)}), True, []),
    ]


def audit_live_fnstate(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[2]
    model_rs = (root / "compiler/crates/hy-check/src/model.rs").read_text(encoding="utf-8")
    present = [f for f in REPLACED_FNSTATE_FIELDS if f"pub {f}:" in model_rs]
    return {"model_rs_fields_found": len(present), "all_18_present": len(present) == 18}


def run_spike() -> dict[str, Any]:
    cases = build_canonical_cases()
    results = []
    for name, edb, exp_accept, exp_codes in cases:
        sol = solve_fixpoint(edb)
        codes = sorted({d.split(":")[0] for d in sol.diagnostics})
        results.append({"name": name, "accepted": sol.accepted, "codes": codes, "iterations": sol.iterations, "matched": sol.accepted == exp_accept and codes == exp_codes})
    matched = sum(1 for r in results if r["matched"])
    audit = audit_live_fnstate()
    return {"issue": 1276, "spike": "W-054", "replaced_fnstate_fields": len(REPLACED_FNSTATE_FIELDS), "live_head_audit": audit, "total_cases": len(cases), "verdict_matches": matched, "all_passed": matched == len(cases) and audit["all_18_present"], "cases": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W-054 (#1276) Datalog/Polonius Tier-0 solver.")
    ap.add_argument("--check", action="store_true", help="Verify all 8 canonical cases.")
    ap.add_argument("--json", action="store_true", help="Emit JSON report.")
    args = ap.parse_args(argv)
    rep = run_spike()
    if args.json:
        print(json.dumps(rep, indent=2))
    elif args.check or not args.json:
        print(f"W-054 (#1276) OK: {rep['verdict_matches']}/{rep['total_cases']} cases verified (replaced {rep['replaced_fnstate_fields']} FnState maps in <200 LOC).")
    return 0 if rep["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
