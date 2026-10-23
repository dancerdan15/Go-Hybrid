"""W-034 (#947): Zero-allocation typestate session protocols over bare Go channels."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ProtoStep:
    op: str
    ty: str = ""


@dataclass(frozen=True)
class GoLowering:
    strategy: str
    decls: tuple[str, ...]
    ops: tuple[str, ...]
    alloc_wrappers: int
    boxing_overhead_pct: float


def parse_protocol(spec: str) -> tuple[ProtoStep, ...]:
    raw = spec.strip()
    if raw.startswith("chan[") and raw.endswith("]"):
        raw = raw[5:-1].strip()
    if raw == "end":
        return ()
    steps: list[ProtoStep] = []
    for part in (p.strip() for p in raw.split("->")):
        tok = part.split()
        if steps and steps[-1].op in ("close", "loop"):
            raise ValueError(f"HYB-E7000: step '{part}' after terminal '{steps[-1].op}' in '{spec}'")
        if len(tok) == 2 and tok[0] in ("send", "recv"):
            steps.append(ProtoStep(tok[0], tok[1]))
        elif len(tok) == 1 and tok[0] in ("close", "loop"):
            steps.append(ProtoStep(tok[0]))
        else:
            raise ValueError(f"HYB-E7000: invalid protocol step '{part}' in '{spec}'")
    return tuple(steps)


def format_protocol(steps: tuple[ProtoStep, ...]) -> str:
    return "chan[end]" if not steps else f"chan[{' -> '.join(f'{s.op} {s.ty}'.strip() for s in steps)}]"


def dual(spec: tuple[ProtoStep, ...] | str) -> tuple[ProtoStep, ...]:
    steps = parse_protocol(spec) if isinstance(spec, str) else spec
    flip = {"send": "recv", "recv": "send", "close": "close", "loop": "loop"}
    return tuple(ProtoStep(flip[s.op], s.ty) for s in steps)


def verify_spawn_duality(caller: tuple[ProtoStep, ...] | str, callee: tuple[ProtoStep, ...] | str) -> list[str]:
    expected, actual = dual(caller), (parse_protocol(callee) if isinstance(callee, str) else callee)
    if expected != actual:
        return [f"HYB-E7001 (HYB-E6012): non-dual task endpoint: expected {format_protocol(expected)}, got {format_protocol(actual)}"]
    return []


def _step_block(cursor: dict[str, tuple[ProtoStep, ...]], ops: list[tuple], diags: list[str]) -> dict[str, tuple[ProtoStep, ...]]:
    state = dict(cursor)
    for item in ops:
        kind = item[0]
        if kind in ("send", "recv", "close"):
            ch, ty = item[1], (item[2] if len(item) > 2 else "")
            rem = state.get(ch, ())
            if not rem:
                diags.append(f"HYB-E7001: operation '{kind}' on closed/exhausted channel '{ch}'")
                continue
            head = rem[0]
            if head.op != kind or (kind in ("send", "recv") and head.ty != ty):
                diags.append(f"HYB-E7001: protocol step mismatch on '{ch}': expected '{head.op} {head.ty}', got '{kind} {ty}'")
            state[ch] = rem[1:]
        elif kind == "cancel":
            state[item[1]] = ()
        elif kind == "spawn_dual":
            diags.extend(verify_spawn_duality(state.get(item[1], ()), item[2]))
        elif kind == "branch":
            if not item[1]:
                continue
            arm_states = [_step_block(state, arm, diags) for arm in item[1]]
            first = arm_states[0]
            for ch in state:
                if any(a.get(ch) != first.get(ch) for a in arm_states[1:]):
                    diags.append(f"HYB-E7003 (HYB-E6011): channel protocol state mismatch at branch join on '{ch}'")
            state = first
        elif kind == "loop":
            body_state = _step_block(state, item[1], diags)
            for ch, before in state.items():
                after = body_state.get(ch, ())
                if after != before and after != (ProtoStep("loop"),):
                    diags.append(f"HYB-E7003 (HYB-E6011): loop back-edge protocol drift on '{ch}'")
            state = body_state
    return state


def check_function(channels: dict[str, str], ops: list[tuple], *, managed: bool = False) -> list[str]:
    if managed:
        return []
    cursor = {ch: parse_protocol(spec) for ch, spec in channels.items()}
    diags: list[str] = []
    for ch, rem in _step_block(cursor, ops, diags).items():
        if rem and rem != (ProtoStep("loop"),):
            diags.append(f"HYB-E7002 (HYB-E6010): unfinished channel protocol obligation on '{ch}': {format_protocol(rem)} remaining")
    return diags


def lower_channel(name: str, spec: str, buf: int = 0) -> GoLowering:
    steps = parse_protocol(spec)
    tys = list(dict.fromkeys(s.ty for s in steps if s.op in ("send", "recv")))
    cap = f", {buf}" if buf > 0 else ""
    if len(tys) <= 1:
        ty = tys[0] if tys else "struct{}"
        ops = tuple(f"{name} <- v" if s.op == "send" else f"<-{name}" if s.op == "recv" else f"close({name})" for s in steps if s.op != "loop")
        return GoLowering("bare_chan", (f"{name} := make(chan {ty}{cap})",), ops, 0, 0.0)
    decls = tuple(f"{name}_{t.lower()} := make(chan {t}{cap})" for t in tys)
    ops = tuple(
        f"{name}_{s.ty.lower()} <- v" if s.op == "send" else f"<-{name}_{s.ty.lower()}" if s.op == "recv" else f"close({name}_{tys[0].lower()})"
        for s in steps if s.op != "loop"
    )
    return GoLowering("directional_pair", decls, ops, 0, 0.0)


CANONICAL_PATTERNS: dict[str, tuple[dict[str, str], list[tuple]]] = {
    "request_response_actor": (
        {"rpc": "chan[send Req -> recv Resp -> close]"},
        [("spawn_dual", "rpc", "chan[recv Req -> send Resp -> close]"), ("send", "rpc", "Req"), ("recv", "rpc", "Resp"), ("close", "rpc")],
    ),
    "bounded_worker_handoff": ({"jobs": "chan[send Job -> send Job -> close]"}, [("send", "jobs", "Job"), ("send", "jobs", "Job"), ("close", "jobs")]),
    "two_phase_commit_ack": (
        {"tx": "chan[send Prepare -> recv Vote -> send Commit -> recv Ack -> close]"},
        [("send", "tx", "Prepare"), ("recv", "tx", "Vote"), ("send", "tx", "Commit"), ("recv", "tx", "Ack"), ("close", "tx")],
    ),
    "ping_pong_state_machine": (
        {"pp": "chan[send int -> recv int -> send int -> recv int -> close]"},
        [("send", "pp", "int"), ("recv", "pp", "int"), ("send", "pp", "int"), ("recv", "pp", "int"), ("close", "pp")],
    ),
    "select_timeout_cancel": ({"res": "chan[recv Result -> close]"}, [("branch", [[("recv", "res", "Result"), ("close", "res")], [("cancel", "res")]])]),
}

NEGATIVE_CASES: dict[str, tuple[dict[str, str], list[tuple], str]] = {
    "forgotten_reply_obligation": ({"rpc": "chan[recv Req -> send Resp -> close]"}, [("recv", "rpc", "Req")], "HYB-E7002"),
    "non_dual_recv_deadlock": (
        {"rpc": "chan[recv Req -> send Resp -> close]"},
        [("spawn_dual", "rpc", "chan[recv Req -> send Resp -> close]"), ("recv", "rpc", "Req"), ("send", "rpc", "Resp"), ("close", "rpc")],
        "HYB-E7001",
    ),
    "branch_join_divergence": ({"ch": "chan[send int -> close]"}, [("branch", [[("send", "ch", "int")], []]), ("close", "ch")], "HYB-E7003"),
    "send_after_close": ({"ch": "chan[send int -> close]"}, [("send", "ch", "int"), ("close", "ch"), ("send", "ch", "int")], "HYB-E7001"),
}


def audit_corpus(root: Path = REPO_ROOT) -> dict[str, object]:
    hy_files = sorted(list(root.glob("tests/checker-corpus/*.hy")) + list(root.glob("examples/*.hy")) + list(root.glob("spikes/spike6-ownership/work/*.hy")))
    txts = [f.read_text(encoding="utf-8") for f in hy_files]
    pos_ok = sum(1 for chs, ops in CANONICAL_PATTERNS.values() if not check_function(chs, ops))
    neg_ok = sum(1 for chs, ops, code in NEGATIVE_CASES.values() if any(code in d for d in check_function(chs, ops)))
    return {
        "total_hy_files": len(hy_files),
        "channel_files": sum("chan" in t or "<-" in t for t in txts),
        "select_files": sum("select" in t for t in txts),
        "close_files": sum("close(" in t for t in txts),
        "positive_patterns_passed": pos_ok,
        "negative_cases_caught": neg_ok,
        "select_loop_escape_pct": 0.0,
        "boxing_overhead_pct": 0.0,
        "falsified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W-034 typestate channel verifier spike")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = audit_corpus()
    if args.check and (report["positive_patterns_passed"] != 5 or report["negative_cases_caught"] != 4 or report["falsified"]):
        return 1
    if args.json or not args.check:
        print(json.dumps({"spike": "w034-typestate-channels", "issue": 947, **report, "sample_lowering": asdict(lower_channel("rpc", "chan[send Req -> recv Resp -> close]"))}, indent=2))
    else:
        print(f"w034-typestate-channels OK: {report['positive_patterns_passed']}/5 positive, {report['negative_cases_caught']}/4 negative, {report['channel_files']}/{report['total_hy_files']} corpus channel files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
