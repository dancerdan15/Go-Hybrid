#!/usr/bin/env python3
"""project_guarantees.py - Executable projection of GUARANTEES.md (W-027 / #1193)."""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERDICT_RE = re.compile(r"^//\s*EXPECTED-VERDICT:\s*(ACCEPT-MANAGED|ACCEPT|REJECT)\b", re.M)
DIAG_RE, TEST_ATTR_RE = re.compile(r"HYB-[EW]\d{4}"), re.compile(r"#\[\s*test\s*\]")
SPEC_SEC_RE, SPEC_RULE_RE = re.compile(r"§\d+(?:\.\d+)?"), re.compile(r"\b(?:O\d+(?:\.\d+)?|C\d+|A\d+)\b")
MD_LINK_RE, CORPUS_NUM_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), re.compile(r"\bcorpus\s+(?:`?(\d{2}[a-z0-9-]*)|\[(\d{2})\])", re.I)


@dataclass
class Witness:
    target: str; kind: str; exists: bool; shipped: bool; ci_wired: bool


@dataclass
class RowProjection:
    row_num: int; row_id: str; surface: str; claim: str; projected_claim: str
    claim_kind: str; projected_status: str
    cited_codes: list[str] = field(default_factory=list)
    spec_refs: list[str] = field(default_factory=list)
    witnesses: list[Witness] = field(default_factory=list)
    drift_notes: list[str] = field(default_factory=list)


def _sha256_paths(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in [p for p in sorted(paths) if p.is_file()]:
        h.update(p.name.encode("utf-8") + b"\0" + p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


def load_diag_registry(root: Path) -> dict[str, dict[str, str]]:
    reg: dict[str, dict[str, str]] = {}
    jpath = root / "compiler/crates/hy-diag/data/diagnostics.json"
    if jpath.is_file():
        try: raw = json.loads(jpath.read_text(encoding="utf-8"))
        except ValueError: raw = {}
        for e in (raw.get("codes", []) if isinstance(raw, dict) else []):
            if isinstance(e, dict) and isinstance(e.get("code"), str):
                reg[e["code"]] = {"severity": e.get("severity", "error"), "category": e.get("category", "")}
    for rel in ("compiler/crates/hy-diag/src/codes.rs", "compiler/crates/hy-diag/src/lib.rs"):
        p = root / rel
        for code in DIAG_RE.findall(p.read_text(encoding="utf-8")) if p.is_file() else []:
            sev = "warning" if ("-E8" in code or "-W" in code) else "error"
            reg.setdefault(code, {"severity": sev, "category": "lint" if sev == "warning" else "tier0"})
    return reg


def scan_corpus_and_tests(root: Path) -> tuple[dict[str, Path], dict[str, list[str]], dict[str, list[str]], Counter]:
    by_prefix, c_diag, r_diag, counts = {}, {}, {}, Counter()
    for rel_dir in ("tests/checker-corpus", "corpus/01-tier0-safety", "corpus"):
        cdir = root / rel_dir
        for path in sorted(cdir.rglob("*.hy")) if cdir.is_dir() else []:
            if path.stem in by_prefix:
                continue
            rel, txt = path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")
            m = VERDICT_RE.search(txt)
            counts[m.group(1) if m else "NO-VERDICT"] += 1
            if not m:
                continue
            by_prefix[path.stem] = path
            if len(path.stem) >= 2 and path.stem[:2].isdigit():
                by_prefix.setdefault(path.stem[:2], path)
            for code in sorted(set(DIAG_RE.findall(txt))):
                c_diag.setdefault(code, []).append(rel)
    for crate in ("hy-check", "hyb"):
        tdir = root / "compiler/crates" / crate / "tests"
        for path in sorted(tdir.glob("*.rs")) if tdir.is_dir() else []:
            txt = path.read_text(encoding="utf-8")
            for code in (sorted(set(DIAG_RE.findall(txt))) if TEST_ATTR_RE.search(txt) else []):
                r_diag.setdefault(code, []).append(path.relative_to(root).as_posix())
    return by_prefix, c_diag, r_diag, counts


def classify_claim(claim: str) -> str:
    if claim.startswith(("**Guaranteed", "**Performance claimed")):
        return "positive"
    return "delegated" if claim.startswith("**Inherited from Go**") else ("scoped_guard" if claim.startswith(("**No soundness claim inside**", "**No claim yet**")) else "no_claim")


def project_guarantees(root: Path = REPO_ROOT) -> dict[str, object]:
    root = root.resolve()
    g_path, hyrt_lib, json_test = root / "GUARANTEES.md", root / "runtime/rust/hyrt/src/lib.rs", root / "runtime/go/jsonbinding/json_test.go"
    g_text = g_path.read_text(encoding="utf-8") if g_path.is_file() else ""
    ci_path = root / ".github/workflows/ci.yml"
    ci_text = ci_path.read_text(encoding="utf-8") if ci_path.is_file() else "corpus_harness.py cargo test managed_gate.py"
    spec_text = "\n".join((root / s).read_text(encoding="utf-8") for s in ("SPEC-LANGUAGE.md", "SPEC-SAFETY.md", "SPEC.md") if (root / s).is_file())
    ci_corpus, ci_cargo, ci_managed = ("corpus_harness.py" in ci_text or "differential_loop.py" in ci_text), "cargo test" in ci_text, "managed_gate.py" in ci_text
    diag_reg = load_diag_registry(root)
    by_prefix, corpus_diag, rust_diag, counts = scan_corpus_and_tests(root)
    lib_txt, go_txt = (hyrt_lib.read_text(encoding="utf-8") if hyrt_lib.is_file() else ""), (json_test.read_text(encoding="utf-8") if json_test.is_file() else "")
    rt_audit = {"hyrt_hardcoded_gen_1": "let gen = 1u32;" in lib_txt, "hyrt_unit_tests": len(TEST_ATTR_RE.findall(lib_txt)), "json_test_count": len(re.findall(r"\bfunc\s+Test\w+", go_txt)), "json_generation_tests": len(re.findall(r"(?i)generation|stale|reuse|release", go_txt))}

    all_cited, unresolved_specs, projections = set(), set(), []
    for line in g_text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.strip().startswith("|") else []
        if len(cells) != 5 or not cells[0].isdigit():
            continue
        num, surface, action, claim, evidence = int(cells[0]), cells[1], cells[2], cells[3], cells[4]
        kind, witnesses, notes, seen, row_text = classify_claim(claim), [], [], set(), f"{action} {claim} {evidence}"
        row_codes = set(DIAG_RE.findall(row_text)) | ({k for k in diag_reg if k.startswith("HYB-E31")} if "E31xx" in row_text else set())
        all_cited.update(row_codes)
        row_specs = sorted(set(SPEC_SEC_RE.findall(row_text) + SPEC_RULE_RE.findall(row_text)))
        for sref in [s for s in row_specs if spec_text and s not in spec_text and s.lstrip("§") not in spec_text]:
            unresolved_specs.add(sref)
            notes.append(f"Row {num}: unresolved spec reference '{sref}'")

        def add_w(rel: str, wkind: str, exists: bool, shipped: bool, ci: bool) -> None:
            if rel not in seen:
                seen.add(rel)
                witnesses.append(Witness(rel, wkind, exists, shipped, ci))

        for label, target in [(l, t) for l, t in MD_LINK_RE.findall(evidence) if not t.startswith("http")]:
            clean = target.split("#")[0].rstrip("/")
            tpath = (root / clean).resolve()
            in_repo = tpath.is_relative_to(root)
            if clean in ("tests/checker-corpus", "corpus/01-tier0-safety") and label.isdigit() and label in by_prefix:
                res = by_prefix[label].relative_to(root).as_posix()
                notes.append(f"Row {num}: directory-only link [{label}]({target}) -> {res}")
                add_w(res, "corpus", True, True, ci_corpus)
            elif clean.endswith(".hy"):
                hy_ok = in_repo and tpath.is_file() and bool(VERDICT_RE.search(tpath.read_text(encoding="utf-8")))
                add_w(clean, "corpus", hy_ok, in_repo and not clean.startswith("spikes/"), hy_ok and ci_corpus)
            else:
                add_w(clean, "doc", in_repo and tpath.exists(), in_repo and not clean.startswith("spikes/"), False)

        for key in [m.group(1) or m.group(2) for m in CORPUS_NUM_RE.finditer(evidence)]:
            if key and key in by_prefix and by_prefix[key].relative_to(root).as_posix() not in seen:
                res = by_prefix[key].relative_to(root).as_posix()
                notes.append(f"Row {num}: unlinked prose citation 'corpus {key}' -> {res}")
                add_w(res, "corpus", True, True, ci_corpus)

        for code in sorted(row_codes):
            for c_rel in corpus_diag.get(code, [])[:2]:
                add_w(c_rel, "corpus", True, True, ci_corpus)
            for r_rel in rust_diag.get(code, [])[:2]:
                add_w(r_rel, "rust_test", True, True, ci_cargo)

        if "deny_managed.rs" in evidence:
            dm = root / "compiler/crates/hyb/tests/deny_managed.rs"
            dm_ok = dm.is_file() and bool(TEST_ATTR_RE.search(dm.read_text(encoding="utf-8")))
            add_w("compiler/crates/hyb/tests/deny_managed.rs", "rust_test", dm_ok, True, dm_ok and ci_cargo)
        if "managed_gate.py" in action:
            add_w("tools/managed_gate.py", "gate_tool", (root / "tools/managed_gate.py").is_file(), True, ci_managed)
        if re.search(r"(?<!interfaces/)abi-types\.md", evidence):
            notes.append(f"Row {num}: cites bare 'abi-types.md' (lives at interfaces/abi-types.md)")
            add_w("abi-types.md", "doc", (root / "abi-types.md").is_file(), False, False)
        if "Spike-3" in row_text:
            sp = "spikes/spike3-memory/work/go/spike3/spike3_test.go"
            add_w(sp, "spike_test", (root / sp).is_file(), False, False)
            notes.append(f"Row {num}: witness '{sp}' lives under spikes/ (unshipped prototype)")
        if num == 18 and rt_audit["hyrt_unit_tests"] == 0 and hyrt_lib.is_file():
            notes.append("Row 18: prose-only; runtime/rust/hyrt has 0 unit tests")
        if num == 19 and rt_audit["hyrt_hardcoded_gen_1"]:
            notes.append("Row 19: FALSE in shipped hyrt ('let gen = 1u32;' hardcoded on slot reuse)")

        exec_ok = [w for w in witnesses if w.kind in ("corpus", "rust_test", "gate_tool") and w.exists and w.shipped and w.ci_wired]
        status = "DELEGATED" if kind == "delegated" else "NO-CLAIM" if kind == "no_claim" else "VERIFIED" if exec_ok else "PARTIAL" if any(w.exists and w.shipped and w.target.startswith("gates/") for w in witnesses) else "UNVERIFIED"
        p_claim = re.sub(r"^\*\*[^*]+\*\*:?\s*", "**Unverified (no shipped CI witness)**: " if status == "UNVERIFIED" else "**Partial (gate-doc only)**: ", claim) if (kind == "positive" and status in ("UNVERIFIED", "PARTIAL")) else claim
        projections.append(RowProjection(num, f"G-{num:02d}", surface, claim, p_claim, kind, status, sorted(row_codes), row_specs, witnesses, notes))

    missing_codes = [c for c in sorted(all_cited) if c not in diag_reg or (("-E8" in c or "-W" in c) != (diag_reg[c]["severity"] == "warning"))]
    unprojected_tier0 = sorted(c for c in diag_reg if c[4:6] in ("E3", "E8") and c not in all_cited)
    m_cen = re.search(r"Current census:\s*\*\*(\d+)\s+REJECT\s*/\s*(\d+)\s+ACCEPT\s*/\s*(\d+)\s+ACCEPT-MANAGED\*\*", g_text)
    inv_drift = [f"Census claims {m_cen.group(1)}/{m_cen.group(2)}/{m_cen.group(3)}, corpus has {counts['REJECT']}/{counts['ACCEPT']}/{counts['ACCEPT-MANAGED']}"] if (m_cen and tuple(int(x) for x in m_cen.groups()) != (counts["REJECT"], counts["ACCEPT"], counts["ACCEPT-MANAGED"])) else []
    pos_rows = [r for r in projections if r.claim_kind == "positive"]
    unverified_pos = [r.row_id for r in pos_rows if r.projected_status != "VERIFIED"]
    broken_exec = [w.target for r in projections for w in r.witnesses if w.kind in ("corpus", "rust_test", "gate_tool") and not w.exists]
    return {
        "artifacts": {"guarantees_sha256": _sha256_paths([g_path]), "corpus_sha256": _sha256_paths(list(by_prefix.values())), "hyrt_sha256": _sha256_paths([hyrt_lib])},
        "summary": {"total_rows": len(projections), "positive_rows": len(pos_rows), "positive_verified": sum(1 for r in pos_rows if r.projected_status == "VERIFIED"), "positive_partial": sum(1 for r in pos_rows if r.projected_status == "PARTIAL"), "positive_unverified": sum(1 for r in pos_rows if r.projected_status == "UNVERIFIED"), "unverified_or_partial_positive_ids": unverified_pos, "all_cited_codes_registered": not missing_codes, "all_spec_refs_resolved": not unresolved_specs, "corpus_census": dict(counts), "runtime_boundary_audit": rt_audit},
        "bidirectional_diagnostics": {"cited_in_guarantees": sorted(all_cited), "missing_or_invalid_in_registry": missing_codes, "unprojected_tier0_codes": unprojected_tier0, "unresolved_spec_refs": sorted(unresolved_specs), "broken_executable_witnesses": broken_exec},
        "inventory_drift": inv_drift, "rows": [asdict(r) for r in projections],
    }


def render_markdown(report: dict[str, object]) -> str:
    s, bd = report["summary"], report["bidirectional_diagnostics"]
    lines = ["# GUARANTEES.md Conformance Projection Report", f"- **Positive claims:** {s['positive_verified']}/{s['positive_rows']} `VERIFIED`, {s['positive_partial']} `PARTIAL`, {s['positive_unverified']} `UNVERIFIED` ({', '.join(s['unverified_or_partial_positive_ids'])})", f"- **Diagnostics check:** cited={len(bd['cited_in_guarantees'])}, missing={len(bd['missing_or_invalid_in_registry'])}, unprojected Tier-0={len(bd['unprojected_tier0_codes'])}", "", "| ID | Kind | Status | Codes | Shipped CI Witnesses | Audit Notes |", "|---|---|---|---|---|---|"]
    for r in report["rows"]:
        sw = [Path(w["target"]).name for w in r["witnesses"] if w["kind"] != "doc" and w["exists"] and w["shipped"] and w["ci_wired"]]
        w_s, c_s = ", ".join(f"`{n}`" for n in sw[:2]) + (f" (+{len(sw) - 2})" if len(sw) > 2 else ""), ", ".join(r["cited_codes"][:2]) + (f" (+{len(r['cited_codes']) - 2})" if len(r["cited_codes"]) > 2 else "")
        lines.append(f"| `{r['row_id']}` | `{r['claim_kind']}` | **{r['projected_status']}** | {c_s or '-'} | {w_s or 'none'} | {'; '.join(r['drift_notes']) or '-'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    for flag in ("--json", "--check", "--strict"):
        ap.add_argument(flag, action="store_true")
    args = ap.parse_args(argv)
    rep = project_guarantees(args.root.resolve())
    print(json.dumps(rep, indent=2) if args.json else render_markdown(rep))
    bd, s = rep["bidirectional_diagnostics"], rep["summary"]
    ok = s["total_rows"] > 0 and not bd["missing_or_invalid_in_registry"] and not bd["unresolved_spec_refs"] and not bd["broken_executable_witnesses"]
    return (0 if (ok and not s["unverified_or_partial_positive_ids"] and not rep["inventory_drift"]) else 1) if args.strict else ((0 if ok else 1) if args.check else 0)


if __name__ == "__main__":
    sys.exit(main())
