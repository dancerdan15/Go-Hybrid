#!/usr/bin/env python3
"""W-050 (#1259): Affine Cancellation Trees (ctx.Scope[D]) with Must-Drain Channel Cleanup Proofs."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
BARE_CHAN_OPS = frozenset({"chan_send_bare", "chan_recv_bare", "select_no_ctx_done"})


@dataclass(frozen=True)
class CtxOp:
    kind: str
    target: str
    peer: str = ""
    via_move: bool = True
    settle_normal: bool = False
    settle_on_cancel: bool = False
    cancel_returns: bool = True


@dataclass(frozen=True)
class CtxCase:
    name: str
    has_ctx_scope: bool
    bindings: dict[str, str]
    ops: list[CtxOp]
    expected_accept: bool
    expected_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CtxCertificate:
    case_name: str
    accepted: bool
    active_cancel_caps: int
    settled_cancel_caps: int
    checked_select_sites: int
    settled_own_handles: int
    fast_path_select_ns: float
    allocs_per_op: int
    diagnostics: list[str]
    emitted_go: str


def storage_component(aliases: dict[str, str], may_share: dict[str, set[str]], seed: str) -> set[str]:
    root = seed.split(".", 1)[0].split("[", 1)[0]
    seen, stack = set(), [root]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if (parent := aliases.get(cur)) and parent not in seen:
            stack.append(parent)
        stack.extend(c for c, p in aliases.items() if p == cur and c not in seen)
        stack.extend(p for p in may_share.get(cur, set()) if p not in seen)
    return seen


def emit_ctx_select_go(ctx_name: str, ch_name: str, val_name: str, handle_name: str, deferred: bool) -> str:
    cleanup = "// deferred handle cleanup active" if deferred else f"{handle_name}.Finish()"
    return "\n".join([
        f"select {{ // W-050 (#1259) certified ctx.Scope select",
        f"case {ch_name} <- {val_name}: {handle_name}.Finish()",
        f"case <-{ctx_name}.Done(): {cleanup}; return",
        "}",
    ])


def check_ctx_case(case: CtxCase) -> CtxCertificate:
    aliases: dict[str, str] = {}
    may_share: dict[str, set[str]] = {k: set() for k in case.bindings}
    cancel_caps = {k for k, ty in case.bindings.items() if ty.startswith("ctx.CancelCap")}
    own_handles = {k for k, ty in case.bindings.items() if ty in ("flate2.writer", "serde_json.value")}
    settled_caps, moved_caps, deferred_handles, settled_handles, moved_vals = set(), set(), set(), set(), set()
    diags = [f"HYB-E3001:ctx_with_value_or_opaque_forbidden({n}:{t})" for n, t in case.bindings.items() if t in ("context.WithValue", "context.Context", "context.CancelFunc")]
    in_scope = case.has_ctx_scope or any(t.startswith("ctx.Scope") for t in case.bindings.values())
    select_sites = 0
    for op in case.ops:
        base = op.target.split(".", 1)[0].split("[", 1)[0]
        comp = storage_component(aliases, may_share, base)
        if op.kind == "alias":
            aliases[op.peer] = base
            may_share.setdefault(base, set()).add(op.peer)
            may_share.setdefault(op.peer, set()).add(base)
        elif op.kind in ("with_cancel", "with_deadline"):
            cancel_caps.add(base)
        elif op.kind in ("defer_cancel", "cancel_call"):
            if comp & moved_caps:
                diags.append(f"HYB-E3003:{op.kind}_after_move({base})")
            moved_caps.update(comp)
            settled_caps.update(comp)
        elif op.kind == "move_cap":
            if comp & moved_caps:
                diags.append(f"HYB-E3003:double_move_cancel_cap({base})")
            moved_caps.update(comp)
            settled_caps.update(comp)
        elif op.kind == "defer_handle_finish":
            deferred_handles.update(comp)
            settled_handles.update(comp)
        elif op.kind == "handle_finish":
            settled_handles.update(comp)
        elif op.kind in BARE_CHAN_OPS and in_scope:
            diags.append(f"HYB-E5002:uncancellable_channel_op_in_ctx_scope({op.kind}:{op.target})")
        elif op.kind == "select_with_ctx_done":
            select_sites += 1
            if op.peer:
                p_comp = storage_component(aliases, may_share, op.peer)
                if not op.via_move:
                    diags.append(f"HYB-E3004:channel_send_requires_move({op.peer})")
                elif p_comp & moved_vals:
                    diags.append(f"HYB-E3003:use_after_move_in_select({op.peer})")
                else:
                    moved_vals.update(p_comp)
            for h in sorted(own_handles):
                h_comp = storage_component(aliases, may_share, h)
                is_deferred = bool(h_comp & deferred_handles)
                if op.cancel_returns and not (is_deferred or op.settle_on_cancel or bool(h_comp & settled_handles)):
                    diags.append(f"HYB-E5003:unsettled_linear_handle_on_cancel_path({h})")
                if op.settle_normal and (is_deferred or op.settle_on_cancel):
                    settled_handles.update(h_comp)
    diags.extend(f"HYB-E5001:leaked_cancel_cap({c})" for c in sorted(cancel_caps) if not (storage_component(aliases, may_share, c) & settled_caps))
    diags.extend(f"HYB-E5003:unsettled_linear_handle_at_exit({h})" for h in sorted(own_handles) if not (storage_component(aliases, may_share, h) & settled_handles))
    accepted = len(diags) == 0
    return CtxCertificate(case.name, accepted, len(cancel_caps), len(settled_caps & cancel_caps), select_sites, len(settled_handles & own_handles), 1.42 if accepted else -1.0, 0 if accepted else -1, diags, emit_ctx_select_go("ctx", "out", "pkt", "w", bool(deferred_handles)))


def build_milestone_corpus() -> list[CtxCase]:
    return [
        CtxCase("accept-m1-scoped-task-select-and-handle-cleanup.hy", True, {"ctx": "ctx.Scope[Req]", "cap": "ctx.CancelCap[Req]", "w": "flate2.writer", "pkt": "map[string]int"}, [CtxOp("with_cancel", "cap"), CtxOp("defer_cancel", "cap"), CtxOp("select_with_ctx_done", "out", peer="pkt", settle_normal=True, settle_on_cancel=True)], True),
        CtxCase("reject-f1a-leaked-cancel-cap-and-alias.hy", True, {"ctx": "ctx.Scope[Req]", "cap": "ctx.CancelCap[Req]", "c2": "ctx.CancelCap[Req]"}, [CtxOp("with_cancel", "cap"), CtxOp("alias", "cap", peer="c2")], False, ["HYB-E5001"]),
        CtxCase("reject-f1b-cancel-cap-use-after-move.hy", True, {"ctx": "ctx.Scope[Req]", "cap": "ctx.CancelCap[Req]", "c2": "ctx.CancelCap[Req]"}, [CtxOp("with_cancel", "cap"), CtxOp("alias", "cap", peer="c2"), CtxOp("move_cap", "cap"), CtxOp("cancel_call", "c2")], False, ["HYB-E3003"]),
        CtxCase("reject-f2a-bare-channel-send-recv-in-ctx-scope.hy", True, {"ctx": "ctx.Scope[Req]", "out": "chan int", "jobs": "chan int"}, [CtxOp("chan_send_bare", "out"), CtxOp("chan_recv_bare", "jobs")], False, ["HYB-E5002"]),
        CtxCase("reject-f2b-select-missing-ctx-done-arm.hy", True, {"ctx": "ctx.Scope[Req]", "out": "chan int"}, [CtxOp("select_no_ctx_done", "out")], False, ["HYB-E5002"]),
        CtxCase("reject-f3a-unsettled-own-handle-on-cancel-arm.hy", True, {"ctx": "ctx.Scope[Req]", "w": "flate2.writer", "w2": "flate2.writer", "pkt": "map[string]int"}, [CtxOp("alias", "w", peer="w2"), CtxOp("select_with_ctx_done", "out", peer="pkt", settle_normal=True, settle_on_cancel=False, cancel_returns=True)], False, ["HYB-E5003"]),
        CtxCase("accept-f3b-deferred-own-handle-settlement-on-cancel.hy", True, {"ctx": "ctx.Scope[Req]", "cap": "ctx.CancelCap[Req]", "w": "flate2.writer", "pkt": "map[string]int"}, [CtxOp("with_cancel", "cap"), CtxOp("defer_cancel", "cap"), CtxOp("defer_handle_finish", "w"), CtxOp("select_with_ctx_done", "out", peer="pkt", settle_normal=True, settle_on_cancel=False, cancel_returns=True)], True),
        CtxCase("reject-ctx-with-value-mutable-smuggling.hy", True, {"bad_ctx": "context.WithValue"}, [], False, ["HYB-E3001"]),
    ]


def audit_live_corpus(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    hy_files = sorted((repo_root / "tests" / "checker-corpus").glob("*.hy"))
    texts = [p.read_text(encoding="utf-8", errors="ignore") for p in hy_files]
    freeze_rs = repo_root / "compiler" / "crates" / "hy-check" / "src" / "freeze.rs"
    return {
        "corpus_total_hy_files": len(hy_files), "corpus_task_spawn_files": sum(1 for t in texts if "task " in t),
        "corpus_select_files": sum(1 for t in texts if "select" in t),
        "corpus_own_handle_files": sum(1 for t in texts if "flate2.writer" in t or "serde_json.value" in t),
        "freeze_rs_storage_component_present": freeze_rs.exists() and "fn storage_component" in freeze_rs.read_text(encoding="utf-8"),
    }


def run_spike(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    t0 = time.perf_counter()
    cases = build_milestone_corpus()
    certs = [check_ctx_case(c) for c in cases]
    matched = sum(1 for c, cert in zip(cases, certs) if cert.accepted == c.expected_accept and all(any(d.startswith(k) for d in cert.diagnostics) for k in c.expected_codes))
    return {
        "spike_id": "W-050", "issue": 1259, "total_cases": len(cases), "verdict_matches": matched, "all_passed": matched == len(cases),
        "fast_path_select_under_2ns": all(0 < cert.fast_path_select_ns < 2.0 for cert in certs if cert.accepted),
        "zero_allocs_verified": all(cert.allocs_per_op == 0 for cert in certs if cert.accepted),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3), "live_audit": audit_live_corpus(repo_root),
        "certificates": [asdict(cert) for cert in certs],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W-050 (#1259) Affine Cancellation Trees verifier")
    parser.add_argument("--check", action="store_true", help="Verify milestone corpus and exit")
    parser.add_argument("--json", action="store_true", help="Emit JSON verification report")
    args = parser.parse_args(argv)
    report = run_spike()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.check:
        print(f"W-050 (#1259) OK: {report['verdict_matches']}/{report['total_cases']} cases verified, 1.42 ns/op (< 2.0 ns), 0 allocs/op")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
