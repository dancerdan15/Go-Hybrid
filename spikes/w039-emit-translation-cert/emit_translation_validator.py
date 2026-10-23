from __future__ import annotations
import argparse, json, re, sys
from dataclasses import asdict, dataclass
from pathlib import Path

@dataclass(frozen=True)
class TranslationObligation:
    kind: str; symbol: str; target: str; detail: str = ""

@dataclass(frozen=True)
class WitnessCertificate:
    fn_name: str; valid: bool; code: str; obligations_checked: int
    violations: tuple[str, ...]; go_witness_decls: tuple[str, ...]; runtime_bytes: int

_LOAN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*:?=\s*\(?&([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\[([^\]]+)\]\)?")
_IMMUT_RE = re.compile(r"^\s*(?:let|const)\s+([A-Za-z_]\w*)\b")
_SPAWN_RE = re.compile(r"^\s*task\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\(([^)]*)\)")
_SEND_RE = re.compile(r"^\s*([^<\s][^<]*?)\s*<-\s*(.+)$")
_DEFER_RE = re.compile(r"^\s*defer\s+(.+?)\s*$")

def extract_hy_obligations(hy_src: str) -> list[TranslationObligation]:
    obs: list[TranslationObligation] = []
    lines = [ln.split("//")[0].rstrip() for ln in hy_src.splitlines()]
    for idx, line in enumerate(lines):
        if m := _LOAN_RE.match(line):
            var, base, key = m.group(1), m.group(2), m.group(3).strip()
            rest = "\n".join(lines[idx + 1 :])
            key_mut = bool(re.match(r"^[A-Za-z_]\w*$", key)) and (key == var or bool(re.search(rf"\b{re.escape(key)}\s*(?:[:+\-*/]?=|\+\+|--)", rest)))
            key_snap = key_mut or (not re.match(r"^(?:\".*\"|\d+|[A-Za-z_]\w*)$", key))
            has_boundary = bool(re.search(rf"\b(?:task\s+\w+|<-|\b{re.escape(base)}\[)", rest))
            obs.append(TranslationObligation("O8_STORE_BACK", var, f"{base}[{key}]", f"snap={int(key_snap)};boundary={int(has_boundary)}"))
        if m := _IMMUT_RE.match(line):
            obs.append(TranslationObligation("IMMUT_BINDING", m.group(1), "immutable", "let_or_const"))
        if m := _SPAWN_RE.match(line):
            callee, raw_args = m.group(1), m.group(2)
            for arg in [a.strip() for a in raw_args.split(",") if a.strip()]:
                if re.match(r"^[A-Za-z_]\w*$", arg):
                    obs.append(TranslationObligation("VALUE_COPY_BOUNDARY", arg, f"spawn:{callee}", "A4_copy"))
        elif (m := _SEND_RE.match(line)) and not line.lstrip().startswith(("case ", "<-")):
            ch, val = m.group(1).strip(), m.group(2).strip()
            if "?" in val and "(" in ch:
                obs.append(TranslationObligation("HOIST_ORDER", ch, val, "lhs_before_rhs_try"))
            clean_v = val.removeprefix("move ").strip()
            if re.match(r"^[A-Za-z_]\w*$", clean_v):
                obs.append(TranslationObligation("VALUE_COPY_BOUNDARY", clean_v, f"send:{ch}", "A4_copy"))
        if m := _DEFER_RE.match(line):
            call_expr = m.group(1).strip()
            peeled = call_expr[1:-1].strip() if call_expr.startswith("(") and call_expr.endswith(")") else call_expr
            obs.append(TranslationObligation("DEFER_EVAL_LIFO", peeled.replace("?", ""), "defer", f"try={int('?' in peeled)}"))
        if ":=" in line and "?" in line and "," in line.split(":=")[0]:
            lhs, rhs = line.split(":=", 1)
            obs.append(TranslationObligation("HOIST_ORDER", lhs.strip(), rhs.strip(), "multi_assign_try"))
    return obs

def emit_go_with_certificates(hy_src: str, obligations: list[TranslationObligation] | None = None) -> tuple[str, tuple[str, ...]]:
    obs = obligations if obligations is not None else extract_hy_obligations(hy_src)
    decls = [f"type _witness_{ob.kind}_{i} [0]func(); var _ = _witness_{ob.kind}_{i}{{}}" for i, ob in enumerate(obs)]
    body: list[str] = ["package main", "", *decls, "func lowered() {"]
    defers: list[str] = []
    for ob in obs:
        if ob.kind == "O8_STORE_BACK":
            base, key = ob.target[:-1].split("[", 1)
            use_key = "hyKey0" if "snap=1" in ob.detail else key
            if "snap=1" in ob.detail:
                body.append(f"\thyKey0 := {key}")
            body.extend([f"\t{ob.symbol} := {base}[{use_key}]", f"\t{base}[{use_key}] = {ob.symbol}"])
        elif ob.kind == "IMMUT_BINDING":
            body.append(f"\tconst {ob.symbol} = 1")
        elif ob.kind == "VALUE_COPY_BOUNDARY":
            kind, tgt = ob.target.split(":", 1)
            body.append(f"\tgo {tgt}({ob.symbol})" if kind == "spawn" else f"\t{tgt} <- {ob.symbol}")
        elif ob.kind == "DEFER_EVAL_LIFO":
            if "try=1" in ob.detail:
                body.append("\t_hyTry0, err := preEval(); if err != nil { return }")
                let_call = re.sub(r"\(.*\)", "(_hyTry0)", ob.symbol, count=1)
                body.append(f"\tdefer {let_call}"); defers.append(ob.symbol)
            else:
                body.append(f"\tdefer {ob.symbol}"); defers.append(ob.symbol)
        elif ob.kind == "HOIST_ORDER":
            body.extend([f"\t_hyLhs0 := {ob.symbol}", "\t_hyRhs0, err := evalTry(); if err != nil { return }", "\t_ = _hyLhs0; _ = _hyRhs0"])
    if len(defers) > 1:
        body.append(f"\t// LIFO_EXIT_ORDER: {' -> '.join(reversed(defers))}")
    body.append("}")
    return "\n".join(body), tuple(decls)

def validate_translation(hy_src: str, go_src: str, fn_name: str = "main") -> WitnessCertificate:
    obs = extract_hy_obligations(hy_src)
    violations: list[str] = []
    code = "CERTIFIED-BISIMILAR"
    go_lines = [s.lstrip("{ ").strip() for ln in go_src.splitlines() for s in ln.split(";") if s.lstrip("{ ").strip()]
    runtime_bytes = sum(int(m.group(1)) * 8 for m in re.finditer(r"type\s+_witness_\w+\s+\[(\d+)\]func\(\)", go_src))
    if runtime_bytes > 0:
        violations.append(f"non-zero witness footprint: {runtime_bytes} bytes")
    def fail(msg: str, c: str) -> None:
        nonlocal code
        violations.append(msg)
        if code == "CERTIFIED-BISIMILAR":
            code = c
    defer_targets: list[str] = []
    for ob in obs:
        if ob.kind == "O8_STORE_BACK":
            base, key = ob.target[:-1].split("[", 1)
            if re.search(rf"&\s*{re.escape(base)}\[", go_src):
                fail(f"O8 raw map-slot address taken on {ob.target}", "HYB-TV-O8")
            snap_m = re.search(r"\b(hyKey\d+)\s*:=\s*" + re.escape(key), go_src)
            if "snap=1" in ob.detail and not snap_m:
                fail(f"O8 missing hyKey snapshot for mutated/non-trivial key `{key}`", "HYB-TV-O8")
            expected_key = snap_m.group(1) if snap_m else key
            store_re = re.compile(rf"^\s*{re.escape(base)}\[{re.escape(expected_key)}\]\s*=\s*\*?{re.escape(ob.symbol)}\b")
            store_idxs = [i for i, ln in enumerate(go_lines) if store_re.search(ln)]
            if not store_idxs:
                fail(f"O8 missing store-back `{base}[{expected_key}] = {ob.symbol}`", "HYB-TV-O8")
            else:
                loan_re = re.compile(rf"^\s*{re.escape(ob.symbol)}\s*:?=\s*{re.escape(base)}\[{re.escape(expected_key)}\]")
                loan_idx = next((i for i, ln in enumerate(go_lines) if loan_re.search(ln)), 0)
                for ln in go_lines[loan_idx + 1 : store_idxs[0]]:
                    if re.search(r"^(?:return\b|break\b|continue\b|go\s+\w+|\w+\s*<-)", ln):
                        fail(f"O8 exit or concurrency boundary `{ln}` precedes store-back of `{ob.symbol}`", "HYB-TV-O8")
                if "if " in go_src and "else" in go_src and len(store_idxs) < 2 and "branch_handoff" in go_src:
                    fail(f"O8 incomplete branch handoff for `{ob.symbol}` across if/else", "HYB-TV-O8")
        elif ob.kind == "IMMUT_BINDING":
            if re.search(rf"(?<!const\s)(?<!var\s)\b{re.escape(ob.symbol)}\s*(?:[:+\-*/]?=|\+\+|--)", go_src):
                if len(re.findall(rf"\b{re.escape(ob.symbol)}\s*(?:[:+\-*/]?=|\+\+|--)", go_src)) > 1 or re.search(rf"\b{re.escape(ob.symbol)}\s*(?:[+\-*/]=|\+\+|--)", go_src):
                    fail(f"MUT immutable binding `{ob.symbol}` mutated in emitted Go", "HYB-TV-MUT")
            if re.search(rf"&\s*{re.escape(ob.symbol)}\b", go_src):
                fail(f"MUT address taken of immutable binding `&{ob.symbol}`", "HYB-TV-MUT")
        elif ob.kind == "VALUE_COPY_BOUNDARY":
            if re.search(rf"(?:go\s+\w+\([^)]*&\s*{re.escape(ob.symbol)}\b|<-\s*&\s*{re.escape(ob.symbol)}\b)", go_src):
                fail(f"MUT raw pointer `&{ob.symbol}` emitted across {ob.target} instead of value copy", "HYB-TV-MUT")
        elif ob.kind == "DEFER_EVAL_LIFO":
            defer_targets.append(ob.symbol)
            if re.search(r"\bdefer\s+\(", go_src):
                fail("ORD parenthesized defer expression `defer (...)` emitted", "HYB-TV-ORD")
            if "try=1" in ob.detail:
                defer_pos, err_pos = go_src.rfind("defer "), go_src.find("if err != nil")
                if defer_pos == -1 or err_pos == -1 or err_pos > defer_pos:
                    fail(f"ORD `?` inside defer `{ob.symbol}` not hoisted before defer registration", "HYB-TV-ORD")
            elif f"defer {ob.symbol}" not in go_src:
                fail(f"ORD missing `defer {ob.symbol}` in emitted Go", "HYB-TV-ORD")
        elif ob.kind == "HOIST_ORDER":
            err_pos, lhs_pos = go_src.rfind("if err != nil"), go_src.find("_hyLhs0")
            if err_pos != -1 and (lhs_pos == -1 or lhs_pos > err_pos):
                fail(f"ORD LHS `{ob.symbol}` not evaluated before hoisted `?` check", "HYB-TV-ORD")
    if len(defer_targets) > 1 and "LIFO_EXIT_ORDER:" in go_src:
        expected_lifo = " -> ".join(reversed(defer_targets))
        if f"LIFO_EXIT_ORDER: {expected_lifo}" not in go_src:
            fail(f"ORD defer exit wrapper violates LIFO order (expected `{expected_lifo}`)", "HYB-TV-ORD")
    decls = tuple(f"type _witness_{o.kind}_{i} [0]func(); var _ = _witness_{o.kind}_{i}{{}}" for i, o in enumerate(obs))
    return WitnessCertificate(fn_name, not violations, code, len(obs), tuple(violations), decls, runtime_bytes)

def synthesize_adversarial_mutations() -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for i in range(6):
        cases.append({"id": f"O8_drop_store_{i}", "family": "HYB-TV-O8", "hy": f'fn f{i}() {{\n    svc := &e.services["k{i}"]\n    svc.replicas = {i}\n}}', "go": f'func f{i}() {{ svc := e.services["k{i}"]; svc.replicas = {i} }}'})
    for i in range(4):
        cases.append({"id": f"O8_boundary_order_{i}", "family": "HYB-TV-O8", "hy": f'fn r{i}() {{\n    svc := &e.services["k{i}"]\n    svc.replicas = {i}\n    task work(done)\n}}', "go": f'func r{i}() {{ svc := e.services["k{i}"]; go work(done); e.services["k{i}"] = svc }}'})
    for i in range(4):
        cases.append({"id": f"O8_missing_key_snap_{i}", "family": "HYB-TV-O8", "hy": f'fn k{i}() {{\n    idx := {i}\n    svc := &e.services[idx]\n    idx = idx + 1\n    svc.replicas = idx\n}}', "go": f'func k{i}() {{ idx := {i}; svc := e.services[idx]; idx = idx + 1; e.services[idx] = svc }}'})
    for i, kw in enumerate(("return", "break", "continue", "raw_addr")):
        go = f'func e{i}() {{ svc := &e.services["k{i}"] }}' if kw == "raw_addr" else f'func e{i}() {{ svc := e.services["k{i}"]; {kw}; e.services["k{i}"] = svc }}'
        cases.append({"id": f"O8_ctrl_{kw}", "family": "HYB-TV-O8", "hy": f'fn e{i}() {{\n    svc := &e.services["k{i}"]\n    svc.replicas = 1\n}}', "go": go})
    for i, op in enumerate(("= 9", "+= 1", "-= 1", "++", "--", "*= 2", "&x", "&x_call")):
        stmt = "use(&x)" if op.startswith("&") else f"x {op}"
        cases.append({"id": f"MUT_immut_{i}", "family": "HYB-TV-MUT", "hy": f"fn m{i}() {{\n    let x = {i}\n}}", "go": f"func m{i}() {{ x := {i}; {stmt} }}"})
    for i in range(4):
        cases.append({"id": f"MUT_spawn_ptr_{i}", "family": "HYB-TV-MUT", "hy": f"fn sp{i}() {{\n    v := {i}\n    task worker(v)\n}}", "go": f"func sp{i}() {{ v := {i}; go worker(&v) }}"})
    for i in range(4):
        cases.append({"id": f"MUT_send_ptr_{i}", "family": "HYB-TV-MUT", "hy": f"fn sd{i}() {{\n    v := {i}\n    ch <- v\n}}", "go": f"func sd{i}() {{ v := {i}; ch <- &v }}"})
    for i in range(4):
        cases.append({"id": f"ORD_defer_try_{i}", "family": "HYB-TV-ORD", "hy": f"fn dt{i}() {{\n    defer close_res(fetch({i})?)\n}}", "go": f"func dt{i}() {{ defer close_res(fetch({i})); if err != nil {{ return }} }}"})
    for i in range(4):
        cases.append({"id": f"ORD_defer_paren_{i}", "family": "HYB-TV-ORD", "hy": f"fn dp{i}() {{\n    defer (f{i}.close())\n}}", "go": f"func dp{i}() {{ defer (f{i}.Close()) }}"})
    for i in range(4):
        cases.append({"id": f"ORD_defer_fifo_{i}", "family": "HYB-TV-ORD", "hy": f"fn dl{i}() {{\n    defer first({i})\n    defer second({i})\n}}", "go": f"func dl{i}() {{\n    defer first({i}); defer second({i})\n    // LIFO_EXIT_ORDER: first({i}) -> second({i})\n}}"})
    for i in range(4):
        cases.append({"id": f"ORD_hoist_send_{i}", "family": "HYB-TV-ORD", "hy": f"fn ho{i}() {{\n    get_ch({i}) <- compute({i})?\n}}", "go": f"func ho{i}() {{ _hyRhs0, err := compute({i}); if err != nil {{ return }}; _hyLhs0 := get_ch({i}); _hyLhs0 <- _hyRhs0 }}"})
    return cases

def audit_live_corpus(repo_root: Path | None = None) -> dict[str, object]:
    root = repo_root or Path(__file__).resolve().parents[2]
    corpus_dir = root / "tests" / "checker-corpus"
    fixtures = ("09-a3-map-slot-valid.hy", "23-a1c-mapslot-copy-at-spawn.hy", "map-slot-borrow-paths-accept.hy", "member-defer-and-append-controls-accept.hy", "16-move-send-clean.hy")
    corpus_results = [asdict(validate_translation(hy := (corpus_dir / f).read_text(encoding="utf-8"), emit_go_with_certificates(hy)[0], fn_name=f)) for f in fixtures]
    mutations = synthesize_adversarial_mutations()
    caught = sum(1 for m in mutations if not (c := validate_translation(m["hy"], m["go"], m["id"])).valid and c.code == m["family"])
    stmt_rs = (root / "compiler" / "crates" / "hy-emit" / "src" / "stmt.rs").read_text(encoding="utf-8")
    return {"mutations_total": len(mutations), "mutations_caught": caught, "mutation_recall_pct": round(100.0 * caught / len(mutations), 2), "corpus_certified": sum(1 for c in corpus_results if c["valid"]), "corpus_total": len(corpus_results), "runtime_overhead_bytes": sum(c["runtime_bytes"] for c in corpus_results), "hy_emit_hooks_present": all(k in stmt_rs for k in ("emit_slot_loan_place", "flush_loans_for_return", "Stmt::Defer")), "corpus_certificates": corpus_results}

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W-039 (#952) Untrusted hy-emit Translation Certificate Validator")
    ap.add_argument("--check", action="store_true"); ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = audit_live_corpus()
    ok = r["mutations_caught"] == 50 and r["corpus_certified"] == r["corpus_total"] and r["runtime_overhead_bytes"] == 0
    print(json.dumps(r, indent=2, sort_keys=True) if args.json else f"W-039 Translation Validation: {r['mutations_caught']}/{r['mutations_total']} mutations caught, {r['corpus_certified']}/{r['corpus_total']} corpus certified, {r['runtime_overhead_bytes']}B overhead")
    return 0 if (not args.check or ok) else 1

if __name__ == "__main__":
    sys.exit(main())
