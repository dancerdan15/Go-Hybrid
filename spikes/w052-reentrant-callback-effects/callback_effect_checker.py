#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


class Modality(StrEnum):
    SYNC_SCOPED = "SyncScoped"
    CONCURRENT_MULTI = "ConcurrentMulti"


@dataclass(frozen=True)
class CallbackContract:
    callee: str
    modality: Modality
    subject_arg: int | None = None


@dataclass(frozen=True)
class Diagnostic:
    code: str
    name: str
    subject: str
    message: str


@dataclass
class Binding:
    name: str
    kind: str
    ty: str
    root: str
    is_mutable_gc: bool = False
    is_channel: bool = False

    def __post_init__(self) -> None:
        self.is_mutable_gc = self.ty.startswith(("map[", "[]", "*"))
        self.is_channel = self.ty.startswith(("chan ", "chan<-", "<-chan", "chan"))


CURATED_CALLBACK_CONTRACTS: dict[str, CallbackContract] = {
    "sort.slice": CallbackContract("sort.slice", Modality.SYNC_SCOPED, subject_arg=0),
    "filepath.walk": CallbackContract("filepath.walk", Modality.SYNC_SCOPED, subject_arg=0),
    "bytes.map": CallbackContract("bytes.map", Modality.SYNC_SCOPED, subject_arg=1),
    "http.handle_func": CallbackContract("http.handle_func", Modality.CONCURRENT_MULTI),
    "http.listen_and_serve": CallbackContract("http.listen_and_serve", Modality.CONCURRENT_MULTI),
    "time.after_func": CallbackContract("time.after_func", Modality.CONCURRENT_MULTI),
    "mux.handle": CallbackContract("mux.handle", Modality.CONCURRENT_MULTI),
    "http.handler": CallbackContract("http.handler", Modality.CONCURRENT_MULTI),
}

_BASE_RE = re.compile(r"^&?([A-Za-z_]\w*)(?:\[[^\]]*\])?$")
_FN_LIT_RE = re.compile(r"\bfn\s*\((?P<params>[^)]*)\)[^{]*\{(?P<body>.*)\}")
_MANAGED_RE = re.compile(r'^managed\s*\(\s*"[^"]+"\s*,\s*"interop"\s*\)\s*\{\s*')
_DECL_RE = re.compile(r"^\s*(?:(let|const|var)\s+([A-Za-z_]\w*)(?:\s+([^=]+?))?(?:\s*=\s*(.+))?|([A-Za-z_]\w*)\s*:=\s*(.+))$")
_CALL_FN_RE = re.compile(
    r"^(?:managed\s*\([^)]*\)\s*\{\s*)?(?P<callee>[A-Za-z_]\w*\.[A-Za-z_]\w*)\((?P<prefix>.*?)\bfn\s*\((?P<params>[^)]*)\)[^{]*\{(?P<body>.*)\}(?P<suffix>[^)]*)\)"
)
_CALL_VAR_RE = re.compile(r"^(?:managed\s*\([^)]*\)\s*\{\s*)?(?P<callee>[A-Za-z_]\w*\.[A-Za-z_]\w*)\((?P<args>[^)]*)\)")


def _infer_type(rhs: str, explicit_ty: str | None, env: dict[str, Binding]) -> tuple[str, str]:
    rhs, explicit_ty = rhs.strip(), (explicit_ty.strip() if explicit_ty else None)
    if (m_base := _BASE_RE.match(rhs)) and (base := m_base.group(1)) in env:
        return (f"*{env[base].ty}" if rhs.startswith("&") else (explicit_ty or env[base].ty)), env[base].root
    if explicit_ty:
        return explicit_ty, ""
    for pfx, inferred in ((("make(chan ", "make(chan<-"), "chan"), (("map[", "make(map["), "map[any]any"), (("[]", "make([]"), "[]any"), (("&",), "*any")):
        if rhs.startswith(pfx):
            return inferred, ""
    return ("string", "") if rhs.startswith(('"', "'")) else ("int", "")


def _clean_body(body: str) -> str:
    return re.sub(r'"[^"]*"', '""', body)


def _is_shadowed(clean: str, var: str) -> bool:
    return bool(re.search(rf"(?<!\.)\b(?:(?:let|const|var)\s+{re.escape(var)}\b|{re.escape(var)}\s*:=)", clean))


def _mutates_var(body: str, var: str) -> bool:
    v, clean = re.escape(var), _clean_body(body)
    return not _is_shadowed(clean, var) and any(
        re.search(p, clean)
        for p in (
            rf"(?:\*\s*)?(?<!\.)\b{v}\s*(?:\[[^\]]+\])?\s*(?:[+\-*/%&|^]=|<<=|>>=|(?<![=!<>:])=(?!=)|\+\+|--)",
            rf"\b(?:delete|clear|copy|append)\s*\(\s*{v}\b",
        )
    )


def _iter_statements(source: str) -> list[str]:
    stmts, buf, parens, in_managed = [], [], 0, False
    for raw in source.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line or re.match(r"^fn\s+[A-Za-z_]\w*\s*\(", line) or line == "}":
            continue
        in_managed = in_managed or (not buf and line.startswith("managed"))
        buf.append(line)
        parens += line.count("(") - line.count(")")
        if parens <= 0 and (not in_managed or "}" in line):
            stmts.append(" ".join(buf))
            buf, parens, in_managed = [], 0, False
    return stmts


def check_program(source: str, contracts: dict[str, CallbackContract] | None = None) -> list[Diagnostic]:
    registry = CURATED_CALLBACK_CONTRACTS if contracts is None else contracts
    env: dict[str, Binding] = {}
    closures: dict[str, tuple[set[str], str]] = {}
    diags: list[Diagnostic] = []

    for line in _iter_statements(source):
        is_managed = bool(_MANAGED_RE.match(line))
        call_info: tuple[str, list[str], set[str], str] | None = None
        if m_call := _CALL_FN_RE.search(line):
            args = [a.strip() for a in f"{m_call.group('prefix')}<fn>{m_call.group('suffix')}".split(",") if a.strip()]
            params = {p.strip().split()[0] for p in m_call.group("params").split(",") if p.strip()}
            call_info = (m_call.group("callee"), args, params, m_call.group("body"))
        elif m_var := _CALL_VAR_RE.search(line):
            args = [a.strip() for a in m_var.group("args").split(",") if a.strip()]
            for a in args:
                if a in closures:
                    call_info = (m_var.group("callee"), args, closures[a][0], closures[a][1])
                    break
        if call_info is not None:
            callee, args, params, body = call_info
            clean = _clean_body(body)
            contract = registry.get(callee)
            if contract is None:
                if not is_managed:
                    diags.append(Diagnostic("HYB-E5203", "UnannotatedForeignCallbackEscape", callee, f"unannotated callback `{callee}`"))
                continue
            if contract.modality is Modality.SYNC_SCOPED:
                subj_raw = args[contract.subject_arg] if contract.subject_arg is not None and contract.subject_arg < len(args) else ""
                subj = m.group(1) if (m := _BASE_RE.match(subj_raw)) else subj_raw
                subj_root = env[subj].root if subj in env else subj
                for b_name, b in env.items():
                    if b_name not in params and subj_root and b.root == subj_root and _mutates_var(body, b_name) and not is_managed:
                        diags.append(Diagnostic("HYB-E5201", "CallbackReentrantContainerMutation", b_name, f"reentrant mutation of `{b_name}`"))
            elif contract.modality is Modality.CONCURRENT_MULTI:
                for b_name, b in env.items():
                    if b_name in params or _is_shadowed(clean, b_name) or not re.search(rf"(?<!\.)\b{re.escape(b_name)}\b", clean):
                        continue
                    if b.is_channel or (b.kind in ("let", "const") and not b.is_mutable_gc):
                        continue
                    if (b.is_mutable_gc or _mutates_var(body, b_name)) and not is_managed:
                        diags.append(Diagnostic("HYB-E5202", "ConcurrentCallbackMutableCapture", b_name, f"concurrent mutable capture `{b_name}`"))
            continue
        if m_decl := _DECL_RE.match(line):
            kind, name, explicit_ty, rhs = (
                (m_decl.group(1), m_decl.group(2), m_decl.group(3), m_decl.group(4) or "")
                if m_decl.group(1)
                else ("var", m_decl.group(5), None, m_decl.group(6))
            )
            if m_fn := _FN_LIT_RE.search(rhs):
                closures[name] = ({p.strip().split()[0] for p in m_fn.group("params").split(",") if p.strip()}, m_fn.group("body"))
                continue
            ty, root = _infer_type(rhs, explicit_ty, env)
            env[name] = Binding(name=name, kind=kind, ty=ty, root=root or name)
    return diags


def audit_live_corpus(repo_root: Path | None = None) -> dict[str, Any]:
    root = REPO_ROOT if repo_root is None else repo_root
    f1_src = (
        "items := []int{3, 1, 2}\nalias := items\nvar comps int = 0\n"
        "sort.slice(items, fn(i int, j int) bool { comps += 1; return items[i] == items[j] })\n"
        "sort.slice(items, fn(i int, j int) bool { items = append(items, 9); return items[i] < items[j] })\n"
        "bytes.map(fn(r rune) rune { alias[0] = 42; return r }, items)\n"
    )
    f2_src = (
        'let prefix = "/api"\nevents := make(chan string, 8)\ncounts := map[string]int{}\n'
        'http.handle_func("/ok", fn(w ResponseWriter, r *Request) { events <- prefix })\n'
        'http.handle_func("/race", fn(w ResponseWriter, r *Request) { counts["hits"] += 1 })\n'
    )
    f3_src = (
        'let tag = "v1"\nforeign.register_hook(fn(x int) { println(tag) })\n'
        'managed("verified single-shot", "interop") { foreign.register_hook(fn(x int) { println(tag) }) }\n'
    )
    f1_ok = [(d.code, d.subject) for d in check_program(f1_src)] == [("HYB-E5201", "items"), ("HYB-E5201", "alias")]
    f2_ok = [(d.code, d.subject) for d in check_program(f2_src)] == [("HYB-E5202", "counts")]
    f3_ok = [(d.code, d.subject) for d in check_program(f3_src)] == [("HYB-E5203", "foreign.register_hook")]
    ex_files = sorted((root / "examples").glob("*/program.hy"))
    ex_diags = {p.parent.name: [asdict(d) for d in check_program(p.read_text(encoding="utf-8"))] for p in ex_files}
    ex_fp = sum(len(v) for v in ex_diags.values())
    c10_path = root / "tests" / "checker-corpus" / "10-a5-http-handler-capture.hy"
    c10_diags = [asdict(d) for d in check_program(c10_path.read_text(encoding="utf-8"))] if c10_path.exists() else []
    c10_ok = [(d["code"], d["subject"]) for d in c10_diags] == [("HYB-E5202", "hits")]
    return {
        "issue": 1256, "f1_pass": f1_ok, "f2_pass": f2_ok, "f3_pass": f3_ok,
        "examples_total": len(ex_files), "examples_clean": sum(1 for v in ex_diags.values() if not v),
        "examples_false_positives": ex_fp, "contested_10_a5_flipped": c10_ok, "contested_10_a5_diags": c10_diags,
        "all_passed": bool(f1_ok and f2_ok and f3_ok and ex_files and ex_fp == 0 and c10_ok),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W-052 effect-polymorphic foreign callback contract verifier")
    ap.add_argument("--check", action="store_true", help="verify F1-F3 falsifiers and live corpus")
    ap.add_argument("--json", action="store_true", help="emit JSON verification report")
    args = ap.parse_args(argv)
    report = audit_live_corpus()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"W-052 check: F1={report['f1_pass']} F2={report['f2_pass']} F3={report['f3_pass']} "
            f"examples={report['examples_clean']}/{report['examples_total']} (fp={report['examples_false_positives']}) "
            f"contested_10_a5_flipped={report['contested_10_a5_flipped']} all_passed={report['all_passed']}"
        )
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
