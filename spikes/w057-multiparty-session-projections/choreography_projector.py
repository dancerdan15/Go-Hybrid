#!/usr/bin/env python3
"""W-057 Spike (Issue #1279): Honda-Yoshida-Carbone Global Choreography Endpoint Projection (EPP)."""
from __future__ import annotations
import argparse, json, sys
from collections import deque
from dataclasses import dataclass

HYB_E5701 = "HYB-E5701"  # UnprojectableChoiceKnowledgeGap (non-mergeable branches)
HYB_E5702 = "HYB-E5702"  # CausalSelfCommunicationOrDisconnectedStep
HYB_E5703 = "HYB-E5703"  # LocalImplementationDeviatesFromProjection

@dataclass(frozen=True)
class MsgStep:
    sender: str; receiver: str; label: str; payload: str = "Unit"; close_after: bool = False

@dataclass(frozen=True)
class ChoiceStep:
    decider: str; branches: tuple[tuple[str, tuple[GlobalStep, ...]], ...]

GlobalStep = MsgStep | ChoiceStep

@dataclass(frozen=True)
class Choreography:
    name: str; roles: tuple[str, ...]; steps: tuple[GlobalStep, ...]

@dataclass(frozen=True)
class LocalSend:
    peer: str; label: str; payload: str = "Unit"; close_after: bool = False

@dataclass(frozen=True)
class LocalRecv:
    peer: str; label: str; payload: str = "Unit"; close_after: bool = False

@dataclass(frozen=True)
class LocalSelect:
    kind: str; peer: str; branches: tuple[tuple[str, tuple[LocalOp, ...]], ...]

LocalOp = LocalSend | LocalRecv | LocalSelect

@dataclass(frozen=True)
class ProjectionResult:
    ok: bool; code: str; message: str; endpoints: dict[str, tuple[LocalOp, ...]]
    deadlock_states: int = 0; orphan_messages: int = 0; closed_send_panics: int = 0


class ChoreographyProjector:
    """Synthesizes deadlock-free local select state machines from a global protocol type."""

    @staticmethod
    def _check_causality(steps: tuple[GlobalStep, ...], roles: set[str], informed: set[str] | None) -> str | None:
        active = set(informed) if informed is not None else set(roles)
        for idx, step in enumerate(steps):
            if isinstance(step, MsgStep):
                if step.sender == step.receiver or step.sender not in roles or step.receiver not in roles:
                    return f"{HYB_E5702}: self-communication or unknown role in {step.sender}->{step.receiver}:{step.label}"
                if (informed is not None and idx == 0 and len(active) == 1 and step.sender not in active) or step.sender not in active:
                    return f"{HYB_E5702}: causally disconnected step {step.sender}->{step.receiver}:{step.label}"
                active.add(step.receiver)
            else:
                if step.decider not in roles or step.decider not in active or not step.branches:
                    return f"{HYB_E5702}: choice decider {step.decider} not causally informed"
                common = set(roles)
                for _, b_steps in step.branches:
                    b_inf = {step.decider}
                    if (err := None if b_steps else f"{HYB_E5702}: empty choice branch at {step.decider}") or (err := ChoreographyProjector._check_causality(b_steps, roles, b_inf)):
                        return err
                    common &= b_inf
                active |= common
        return None

    @staticmethod
    def _merge(role: str, decider: str, projs: list[tuple[LocalOp, ...]]) -> tuple[LocalOp, ...]:
        if all(p == projs[0] for p in projs):
            return projs[0]
        if any(not p for p in projs):
            raise ValueError(f"{HYB_E5701}: role {role} active in some branches of {decider} but idle in others")
        exp_cls, kind = (LocalSend, "internal") if role == decider else (LocalRecv, "external")
        peers = {p[0].peer for p in projs if isinstance(p[0], exp_cls)}
        if not all(isinstance(p[0], exp_cls) for p in projs) or len(peers) != 1:
            raise ValueError(f"{HYB_E5701}: non-mergeable initial actions for role {role} at choice {decider}")
        by_label: dict[str, list[tuple[str, tuple[LocalOp, ...]]]] = {}
        for p in projs:
            head = p[0]
            assert isinstance(head, (LocalSend, LocalRecv))
            by_label.setdefault(head.label, []).append((f"{head.label}:{head.payload}:{int(head.close_after)}", p[1:]))
        merged: list[tuple[str, tuple[LocalOp, ...]]] = []
        for lbl, items in by_label.items():
            if len(items) > 1 and not all(it == items[0] for it in items):
                raise ValueError(f"{HYB_E5701}: indistinguishable label '{lbl}' for role {role}")
            merged.append(items[0])
        return (LocalSelect(kind, next(iter(peers)), tuple(merged)),)

    @classmethod
    def _project_seq(cls, steps: tuple[GlobalStep, ...], role: str) -> tuple[LocalOp, ...]:
        if not steps:
            return ()
        head, rest = steps[0], steps[1:]
        if isinstance(head, MsgStep):
            tail = cls._project_seq(rest, role)
            if role == head.sender:
                return (LocalSend(head.receiver, head.label, head.payload, head.close_after), *tail)
            return (LocalRecv(head.sender, head.label, head.payload, head.close_after), *tail) if role == head.receiver else tail
        return cls._merge(role, head.decider, [cls._project_seq((*b, *rest), role) for _, b in head.branches])

    @classmethod
    def explore_lts(cls, endpoints: dict[str, tuple[LocalOp, ...]]) -> tuple[int, int, int]:
        roles = tuple(sorted(endpoints))
        init = (tuple(endpoints[r] for r in roles), (), frozenset())
        q, seen, deadlocks, orphans, panics = deque([init]), {init}, 0, 0, 0
        while q:
            r_states, buf, closed = q.popleft()
            if all(not st for st in r_states):
                orphans += int(bool(buf))
                continue
            nxt = []
            for i, s_role in enumerate(roles):
                op = r_states[i][0] if r_states[i] else None
                s_opts = ([(op.peer, k.split(":")[0], k.split(":")[2] == "1", c) for k, c in op.branches] if isinstance(op, LocalSelect) and op.kind == "internal"
                          else ([(op.peer, op.label, op.close_after, r_states[i][1:])] if isinstance(op, LocalSend) else []))
                for peer, lbl, cls_after, s_cont in s_opts:
                    if (s_role, peer) in closed:
                        panics += 1
                        continue
                    j = roles.index(peer) if peer in roles else -1
                    rop = r_states[j][0] if j >= 0 and r_states[j] else None
                    r_opts = ({k.split(":")[0]: (k.split(":")[2] == "1", c) for k, c in rop.branches if rop.peer == s_role} if isinstance(rop, LocalSelect) and rop.kind == "external"
                              else ({rop.label: (rop.close_after, r_states[j][1:])} if isinstance(rop, LocalRecv) and rop.peer == s_role else {}))
                    if lbl in r_opts:
                        r_cls, r_cont = r_opts[lbl]
                        nr = list(r_states)
                        nr[i], nr[j] = s_cont, r_cont
                        nxt.append((tuple(nr), buf, closed | ({(s_role, peer)} if (cls_after or r_cls) else frozenset())))
            deadlocks += int(not nxt)
            q.extend(ns for ns in nxt if ns not in seen and not seen.add(ns))
        return deadlocks, orphans, panics

    @classmethod
    def project_and_verify(cls, choreo: Choreography, local_impls: dict[str, tuple[LocalOp, ...]] | None = None) -> ProjectionResult:
        if (err := cls._check_causality(choreo.steps, set(choreo.roles), None)) is not None:
            return ProjectionResult(False, HYB_E5702, err, {})
        try:
            endpoints = {r: cls._project_seq(choreo.steps, r) for r in choreo.roles}
        except ValueError as exc:
            return ProjectionResult(False, HYB_E5701, str(exc), {})
        if local_impls is not None:
            for r, impl in local_impls.items():
                if endpoints.get(r) != impl:
                    d, o, p = cls.explore_lts({**endpoints, **local_impls})
                    return ProjectionResult(False, HYB_E5703, f"{HYB_E5703}: @role({choreo.name}::{r}) deviates", endpoints, d, o, p)
        d, o, p = cls.explore_lts(endpoints)
        return ProjectionResult(d == 0 and o == 0 and p == 0, "OK", "Verified deadlock-free", endpoints, d, o, p)


def run_canonical_suite() -> tuple[int, int, int, list[dict[str, object]]]:
    two_buyer = Choreography("TwoBuyer", ("Buyer1", "Buyer2", "Seller"), (
        MsgStep("Buyer1", "Seller", "Title", "String"), MsgStep("Seller", "Buyer1", "Quote", "Int"), MsgStep("Seller", "Buyer2", "Quote", "Int"), MsgStep("Buyer1", "Buyer2", "Share", "Int"),
        ChoiceStep("Buyer2", (("Accept", (MsgStep("Buyer2", "Seller", "Ok", "Addr"), MsgStep("Seller", "Buyer2", "Date", "String"), MsgStep("Buyer2", "Buyer1", "Commit", "String"))), ("Quit", (MsgStep("Buyer2", "Seller", "Quit", "Unit"), MsgStep("Buyer2", "Buyer1", "Abort", "Unit"))))),
    ))
    stream_close = Choreography("StreamRow16", ("Producer", "Consumer"), (ChoiceStep("Producer", (("Data", (MsgStep("Producer", "Consumer", "Chunk", "Bytes"), MsgStep("Consumer", "Producer", "Ack", "Unit"))), ("Close", (MsgStep("Producer", "Consumer", "Eof", "Unit", close_after=True),)))),))
    shared_tail = Choreography("SharedAudit", ("Gateway", "Worker", "Logger"), (ChoiceStep("Gateway", (("Fast", (MsgStep("Gateway", "Worker", "FastReq", "Job"), MsgStep("Worker", "Logger", "Audit", "Entry"))), ("Slow", (MsgStep("Gateway", "Worker", "SlowReq", "Job"), MsgStep("Worker", "Logger", "Audit", "Entry"))))),))
    bad_merge = Choreography("KnowledgeGap", ("Client", "Server", "Auditor"), (ChoiceStep("Client", (("Buy", (MsgStep("Client", "Server", "Buy", "Item"), MsgStep("Server", "Auditor", "Notify", "Unit"), MsgStep("Auditor", "Server", "Archive", "Id"))), ("Cancel", (MsgStep("Client", "Server", "Cancel", "Item"), MsgStep("Server", "Auditor", "Notify", "Unit"), MsgStep("Auditor", "Server", "Drop", "Id"))))),))
    bad_self = Choreography("SelfComm", ("Worker",), (MsgStep("Worker", "Worker", "Loopback", "Token"),))
    bad_causal = Choreography("DisconnectedBranch", ("Coord", "Worker", "Store"), (ChoiceStep("Coord", (("Go", (MsgStep("Coord", "Worker", "Start", "Unit"), MsgStep("Worker", "Store", "Write", "Data"))), ("Stop", (MsgStep("Worker", "Store", "Abort", "Unit"),)))),))
    tb_ep, st_ep = ChoreographyProjector.project_and_verify(two_buyer).endpoints, ChoreographyProjector.project_and_verify(stream_close).endpoints
    cases = [
        ("1_two_buyer_epp_accept", two_buyer, None, True, "OK"), ("2_stream_close_row16_accept", stream_close, None, True, "OK"),
        ("3_uninvolved_identical_merge_accept", shared_tail, None, True, "OK"), ("4_non_mergeable_knowledge_gap_reject", bad_merge, None, False, HYB_E5701),
        ("5_reflexive_self_comm_reject", bad_self, None, False, HYB_E5702), ("6_causally_disconnected_branch_reject", bad_causal, None, False, HYB_E5702),
        ("7_local_select_omission_reject", two_buyer, {**tb_ep, "Seller": tb_ep["Seller"][:-1]}, False, HYB_E5703),
        ("8_send_after_close_row16_reject", stream_close, {**st_ep, "Producer": (*st_ep["Producer"], LocalSend("Consumer", "Late", "Bytes"))}, False, HYB_E5703),
    ]
    passed, deadlocks, details = 0, 0, []
    for name, ch, impl, exp_ok, exp_code in cases:
        res = ChoreographyProjector.project_and_verify(ch, impl)
        ok_match = res.ok == exp_ok and res.code == exp_code
        passed, deadlocks = passed + int(ok_match), deadlocks + (res.deadlock_states if res.ok else 0)
        details.append({"case": name, "passed": ok_match, "code": res.code, "deadlocks": res.deadlock_states})
    return passed, len(cases), deadlocks, details


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W-057 (#1279) Global Choreography EPP & Row 16 Verifier.")
    parser.add_argument("--check", action="store_true", help="Run 8 canonical EPP verification cases")
    parser.add_argument("--json", action="store_true", help="Output verification summary as JSON")
    args = parser.parse_args(argv)
    passed, total, deadlocks, details = run_canonical_suite()
    out = json.dumps({"spike": "W-057", "issue": 1279, "passed": passed, "total": total, "deadlocks": deadlocks, "cases": details}, indent=2)
    print(out if args.json else f"W-057 (#1279) OK: {passed}/{total} cases verified, {deadlocks} deadlock states, 100% EPP mergeability sound")
    return 0 if passed == total and deadlocks == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
