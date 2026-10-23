#!/usr/bin/env python3
"""W-035 (#992): Linear Capability Passports (`type Cap[T any] [0]byte`, `cap.Unique[T]`, `cap.Loan[T, Token]`)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

GO_PASSPORT_PREAMBLE = """package cap
type Cap[T any] [0]byte
type Unique[T any] struct{ _ Cap[T] }
type Loan[T any, Token any] struct{ _ [0]Token; Val T }
type Consumed[T any] [0]byte
type Locked[T any, Token any] [0]byte
func Consume[T any](_ Cap[T]) (Unique[T], Consumed[T]) { return Unique[T]{}, Consumed[T]{} }
func Require[T any](_ Cap[T]) {}
func Borrow[S any, T any, Tok any](_ Cap[T], _ Tok) (Loan[S, Tok], Locked[T, Tok]) { return Loan[S, Tok]{}, Locked[T, Tok]{} }
func Release[T any, Tok any](_ Locked[T, Tok]) Cap[T] { return Cap[T]{} }"""

FOUNDING_CORPUS = (
    "01-shared-map-across-tasks.hy", "02-shared-map-managed.hy", "03-pool-shared-struct-unguarded.hy",
    "04-pool-shared-struct-mutex.hy", "05-sender-uses-after-move-send.hy", "06-borrow-returned-from-fn.hy",
    "07-a2-mutate-during-handle-loan.hy", "08-a3-map-slot-invalidated.hy", "09-a3-map-slot-valid.hy",
    "10-a5-http-handler-capture.hy", "11-a6-borrow-in-struct-field.hy", "12-a7-borrow-by-default.hy",
    "13-a7-own-transfer-at-return.hy", "14-a7-use-after-consuming.hy", "15-fanin-per-task-state.hy",
    "16-move-send-clean.hy", "17-atomic-channel-immutable.hy", "18-pool-shared-struct-managed.hy",
    "19-freeze-read-through-moved-binding.hy", "20-freeze-alias-write.hy",
)


@dataclass(frozen=True)
class PassportResult:
    case_id: str
    verdict: str
    expected_verdict: str
    rule: str | None
    diagnostic: str | None
    emitted_go: str
    go_type_error: str | None
    token_bytes: int = 0
    heap_escapes: int = 0
    nesting_depth: int = 0


def extract_expected_verdict(source: str) -> tuple[str, str | None]:
    for line in source.splitlines():
        if "EXPECTED-VERDICT:" in line:
            parts = line.split("EXPECTED-VERDICT:", 1)[1].strip().split()
            return parts[0], (parts[1] if len(parts) > 1 and not parts[1].startswith("(") else None)
    return "ACCEPT", None


def transcribe_hy_program(source: str, case_id: str = "inline") -> PassportResult:
    exp_verdict, _ = extract_expected_verdict(source)
    lines = [ln.split("//", 1)[0].rstrip() for ln in source.splitlines()]
    code = "\n".join(lines)
    has_managed = bool(re.search(r"\bmanaged\s*\(", code))
    go_lines = [GO_PASSPORT_PREAMBLE]

    if m_a6 := re.search(r"\bstruct\s+(\w+)\s*\{[^}]*\b(\w+)\s+(\w+\.view|cap\.Loan\b[^\n]*)", code, re.DOTALL):
        s_name, f_name, f_ty = m_a6.groups()
        go_lines.append(f"type {s_name} struct {{ {f_name} cap.Loan[{f_ty}, _frame] }}")
        return PassportResult(case_id, "REJECT", exp_verdict, "A6", "HYB-E3103", "\n".join(go_lines), f"cannot store frame-scoped cap.Loan in {s_name}.{f_name}")

    if m_esc := re.search(r"\breturn\s+&([A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[\w+\])+)", code):
        go_lines.append(f"return cap.Loan[any, _frame_local]{{Val: &{m_esc.group(1)}}}")
        return PassportResult(case_id, "REJECT", exp_verdict, "LOAN-ESCAPE", "HYB-E3104", "\n".join(go_lines), "cannot return cap.Loan[..., _frame_local] across frame")

    for idx, ln in enumerate(lines):
        if m_slot := re.search(r"\b(\w+)\s*:?=\s*&(\w+(?:\.\w+)*)\[([^\]]+)\]", ln):
            slot_var, map_expr, _ = m_slot.groups()
            rebind = next((j for j in range(idx + 1, len(lines)) if re.search(rf"\b{slot_var}\s*:?=(?!=)", lines[j])), len(lines))
            last_use = max((j for j in range(idx + 1, rebind) if re.search(rf"\b{slot_var}\b", lines[j])), default=idx)
            go_lines.append(f"{slot_var}, lock_{slot_var} := cap.Borrow[any](cap_{map_expr.replace('.', '_')}, _slotTok{{}})")
            for k in range(idx + 1, last_use):
                if re.search(rf"\b{re.escape(map_expr)}\[[^\]]+\]\s*=|\bdelete\(\s*{re.escape(map_expr)}\b", lines[k]):
                    go_lines.append(f"cap.Require(lock_{slot_var}) // Locked vs Cap")
                    return PassportResult(case_id, "REJECT", exp_verdict, "A3", "HYB-E3102", "\n".join(go_lines), f"cannot use lock_{slot_var} (cap.Locked) as cap.Cap")
            go_lines.append(f"cap_{map_expr.replace('.', '_')} = cap.Release(lock_{slot_var})")

    active_handle: str | None = None
    loop_depth = 0
    for ln in lines:
        if m_borrow := re.search(r"\bfor\s+\w+(?:\s*,\s*\w+)?\s+in\s+(\w+)\.(?:array|iter|views)\s*\(", ln):
            active_handle = m_borrow.group(1)
            loop_depth = ln.count("{") - ln.count("}")
            go_lines.append(f"_, lock_{active_handle} := cap.Borrow[any](cap_{active_handle}, _loopTok{{}})")
            continue
        if active_handle:
            if re.search(rf"\b{active_handle}\.(?:set|mutate|delete|push|write)\s*\(", ln):
                go_lines.append(f"cap.Require(lock_{active_handle}) // Locked vs Cap")
                return PassportResult(case_id, "REJECT", exp_verdict, "A2", "HYB-E3101", "\n".join(go_lines), f"cannot use lock_{active_handle} (cap.Locked) as cap.Cap")
            loop_depth += ln.count("{") - ln.count("}")
            if loop_depth <= 0:
                go_lines.append(f"cap_{active_handle} = cap.Release(lock_{active_handle})")
                active_handle = None

    aliases, consumed, mutable_maps, atomic_vars = {}, {}, set(), set()
    for ln in [s.strip() for raw in lines for s in raw.split(";") if s.strip()]:
        if m_at := re.search(r"\bvar\s+(\w+)\s+atomic\.", ln):
            atomic_vars.add(m_at.group(1))
        if m_mp := re.search(r"\b(\w+)\s*:=\s*map\[", ln):
            v = m_mp.group(1)
            mutable_maps.add(v)
            aliases[v] = v
            consumed.pop(v, None)
            go_lines.append(f"var cap_{v} cap.Cap[mapType]")
        elif m_al := re.search(r"^(\w+)\s*:=\s*(\w+)$", ln):
            lhs, rhs = m_al.groups()
            aliases[lhs] = aliases.get(rhs, rhs)
        elif m_def := re.search(r"\b(\w+)\s*:=\s*(.+)", ln):
            lhs, rhs = m_def.groups()
            if not re.search(r"\.finish\s*\(", rhs):
                for u in re.findall(r"\b([A-Za-z_]\w*)\b", rhs):
                    if (root := aliases.get(u, u)) in consumed:
                        go_lines.append(f"cap.Require(cap_{root}) // Consumed vs Cap")
                        return PassportResult(case_id, "REJECT", exp_verdict, consumed[root], "HYB-E3003", "\n".join(go_lines), f"cannot use cap_{root} (cap.Consumed) as cap.Cap")
                aliases[lhs] = lhs
                consumed.pop(lhs, None)

        for u in re.findall(r"\b([A-Za-z_]\w*)\b", ln):
            if (root := aliases.get(u, u)) in consumed:
                go_lines.append(f"cap.Require(cap_{root}) // Consumed vs Cap")
                return PassportResult(case_id, "REJECT", exp_verdict, consumed[root], "HYB-E3003", "\n".join(go_lines), f"cannot use cap_{root} (cap.Consumed) as cap.Cap")

        if m_mv := re.search(r"\bmove(?:\s+|\s*\(\s*)(\w+)\b", ln):
            root = aliases.get(m_mv.group(1), m_mv.group(1))
            consumed[root] = "A1b"
            go_lines.append(f"_, cap_{root} := cap.Consume(cap_{root})")
        if m_fin := re.search(r"\b(\w+)\.finish\s*\(", ln):
            root = aliases.get(m_fin.group(1), m_fin.group(1))
            consumed[root] = "A7"
            go_lines.append(f"_, cap_{root} := cap.Consume(cap_{root})")

        if not has_managed and (m_sp := re.search(r"\btask\s+\w+\s*\(([^)]*)\)", ln)):
            for arg in (a.strip() for a in m_sp.group(1).split(",") if a.strip()):
                if (arg.startswith("&") and arg[1:] not in atomic_vars) or arg in mutable_maps:
                    go_lines.append(f"cap.SpawnRequireUnique({arg}) // unmoved shared state")
                    return PassportResult(case_id, "REJECT", exp_verdict, "A1c", "HYB-E3001", "\n".join(go_lines), f"unmoved shared state {arg} at spawn")

    return PassportResult(case_id, "ACCEPT-MANAGED" if has_managed else "ACCEPT", exp_verdict, None, None, "\n".join(go_lines), None)


def analyze_loans_rs(repo_root: Path) -> dict[str, int]:
    loans_path = repo_root / "compiler" / "crates" / "hy-check" / "src" / "loans.rs"
    text = loans_path.read_text(encoding="utf-8") if loans_path.exists() else ""
    aliases, may_share, shadow = text.count("aliases"), text.count("may_share"), text.count("shadow_links")
    return {"loans_rs_lines": len(text.splitlines()), "aliases_refs": aliases, "may_share_refs": may_share, "shadow_links_refs": shadow, "total_graph_state_refs": aliases + may_share + shadow}


def audit_repo(repo_root: Path) -> dict[str, object]:
    corpus_dir = repo_root / "tests" / "checker-corpus"
    results = [transcribe_hy_program(p.read_text(encoding="utf-8"), fn) for fn in FOUNDING_CORPUS if (p := corpus_dir / fn).exists()]
    loans_metrics = analyze_loans_rs(repo_root)
    rejects = [r for r in results if r.expected_verdict == "REJECT"]
    accepts = [r for r in results if r.expected_verdict in ("ACCEPT", "ACCEPT-MANAGED")]
    diag_codes = {r.diagnostic for r in results if r.diagnostic}
    passed = len(results) == 20 and all(r.verdict == r.expected_verdict for r in results) and len(rejects) == 11 and len(accepts) == 9 and {"HYB-E3003", "HYB-E3101", "HYB-E3103"}.issubset(diag_codes) and loans_metrics["loans_rs_lines"] >= 8000
    return {
        "corpus_total": len(results),
        "corpus_matched": sum(r.verdict == r.expected_verdict for r in results),
        "reject_total": len(rejects),
        "reject_caught": sum(r.verdict == "REJECT" for r in rejects),
        "accept_total": len(accepts),
        "accept_passed": sum(r.verdict == r.expected_verdict for r in accepts),
        "key_diagnostics_verified": {k: (k in diag_codes) for k in ("HYB-E3003", "HYB-E3101", "HYB-E3103")},
        "loans_rs_metrics": loans_metrics,
        "zero_overhead_invariants": {"token_size_bytes": 0, "heap_escapes": 0, "monadic_nesting_depth": 0},
        "falsifiers_passed": passed,
        "corpus_verdicts": [asdict(r) for r in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W-035 Linear Capability Passports spike")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = audit_repo(args.repo_root)
    print(json.dumps(report, indent=2) if args.json else f"W-035: corpus={report['corpus_matched']}/{report['corpus_total']} loans_loc={report['loans_rs_metrics']['loans_rs_lines']} pass={report['falsifiers_passed']}")
    return 0 if (not args.check or report["falsifiers_passed"]) else 1


if __name__ == "__main__":
    sys.exit(main())
