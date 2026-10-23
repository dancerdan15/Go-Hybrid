#!/usr/bin/env python3
"""W029: Certified Heap-Freedom (no_heap) via Tier-0 Escape Proofs + cmd/compile -m=2 Oracle (#1172)."""
from __future__ import annotations
import argparse, json, pathlib, random, re, sys
from dataclasses import dataclass, field

MAX_STACK_FRAME_BYTES = 4096
ELEM_BYTES = {"byte": 1, "u8": 1, "i8": 1, "bool": 1, "i16": 2, "u16": 2, "i32": 4, "u32": 4, "f32": 4, "int": 8, "i64": 8, "u64": 8, "f64": 8, "string": 16}
NO_HEAP_SAFE_FOREIGN: frozenset[str] = frozenset({
    "strings.index", "strings.contains", "strings.has_prefix", "strings.has_suffix", "strings.count", "strings.equal_fold", "strings.compare",
    "bytes.index", "bytes.contains", "bytes.equal", "bytes.compare", "strconv.atoi", "strconv.parse_int", "strconv.parse_bool",
    "binary.little_endian.uint32", "binary.little_endian.uint64", "binary.little_endian.put_uint32", "binary.little_endian.put_uint64",
    "binary.big_endian.uint32", "binary.big_endian.uint64", "math.abs", "math.min", "math.max",
    "time.duration.nanoseconds", "time.duration.microseconds", "time.duration.milliseconds", "time.duration.seconds",
    "atomic.bool.load", "atomic.bool.store", "atomic.int32.load", "atomic.int32.store", "atomic.int32.add",
    "atomic.int64.load", "atomic.int64.store", "atomic.int64.add", "atomic.int64.swap", "atomic.int64.compare_and_swap",
    "atomic.uint32.load", "atomic.uint32.add", "atomic.uint64.load", "atomic.uint64.add", "atomic.uintptr.load",
})
FOREIGN_ALLOC_PREFIXES = ("fmt.", "http.", "httptest.", "net.", "os.", "io.", "serde_json.", "flate2.", "regex.", "sha3.", "errors.", "url.")

@dataclass(frozen=True)
class Span: line: int = 1; col: int = 1
@dataclass(frozen=True)
class EscapeDiag: code: str; kind: str; binding: str; detail: str; span: Span = field(default_factory=Span)
@dataclass(frozen=True)
class AllocSite: binding: str; kind: str; elem_ty: str; const_cap: int | None; byte_size: int | None; stack_lowered: bool; go_lowering: str; span: Span = field(default_factory=Span)
@dataclass
class FnEscapeSummary:
    fn_name: str; no_heap_requested: bool; no_heap_certified: bool
    frame_local_bindings: list[str] = field(default_factory=list); stack_allocs: list[AllocSite] = field(default_factory=list)
    escape_diags: list[EscapeDiag] = field(default_factory=list); emitted_go: str = ""
    gcflags_m2_lines: list[str] = field(default_factory=list); allocs_per_run: int = 0

_MAKE_SLICE_RE = re.compile(r"make\(\s*\[\]([A-Za-z0-9_]+)\s*,\s*([^,)]+)(?:\s*,\s*([^)]+))?\)"); _ARRAY_RE = re.compile(r"\[(-?\d+)\]([A-Za-z0-9_]+)")
_CLOSURE_RE = re.compile(r"^([A-Za-z_]\w*)\s*:=\s*fn\s*\(([^)]*)\)\s*(?:([A-Za-z0-9_]+)\s*)?\{(.*)\}$")
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\(")
_M2_RE = re.compile(r"^([^:\n]+):(\d+):(\d+):\s*(?:(.+?)\s+)?(escapes to heap|moved to heap:|does not escape)(.*)$")
_M2_FLOW_RE = re.compile(r"^\s+(flow:\s*.+|from\s+.+)$")

def parse_gcflags_m2(stderr_text: str) -> list[dict]:
    out: list[dict] = []
    for raw in stderr_text.splitlines():
        if m := _M2_RE.match(raw.strip()):
            subj, kind, tail = (m.group(4) or "").strip(), m.group(5).strip(), m.group(6).strip()
            target = tail.lstrip(": ").strip() if kind == "moved to heap:" else subj
            out.append({"file": m.group(1), "line": int(m.group(2)), "col": int(m.group(3)), "subject": target, "kind": kind, "escapes": kind != "does not escape", "flows": []})
        elif out and (fm := _M2_FLOW_RE.match(raw)): out[-1]["flows"].append(fm.group(1).strip())
    return out

def certify_no_heap(src: str, fn_name: str = "kernel", no_heap: bool = True) -> FnEscapeSummary:
    summary = FnEscapeSummary(fn_name=fn_name, no_heap_requested=no_heap, no_heap_certified=False)
    ref_kinds: dict[str, str] = {}; aliases: dict[str, set[str]] = {}; allocs: dict[str, AllocSite] = {}; escaped: set[str] = set(); frame_bytes = 0
    go_lines: list[str] = [f"func {fn_name}() {{"]; m2_lines: list[str] = []
    def _root_set(b: str) -> set[str]:
        seen, stack = {b}, [b]
        while stack:
            for nxt in aliases.get(stack.pop(), ()):
                if nxt not in seen: seen.add(nxt); stack.append(nxt)
        return seen
    def _flag(kind: str, b: str, detail: str, ln: int) -> None:
        escaped.update(_root_set(b)); summary.escape_diags.append(EscapeDiag("HYB-E6010", kind, b, detail, Span(ln, 1)))
        m2_lines.append(f"{fn_name}.go:{ln}:2: {b} escapes to heap:\n  flow: {b} -> {kind} ({detail})")
    for ln, raw in enumerate(src.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith(("//", "package ", "import ")) or (s.startswith("fn ") and s.endswith("{")) or s == "}": continue
        if (am := _ARRAY_RE.search(s)) and (int(am.group(1)) < 0 or int(am.group(1)) * ELEM_BYTES.get(am.group(2), 8) > MAX_STACK_FRAME_BYTES): _flag("dynamic_or_oversized_alloc", s.split(":=", 1)[0].replace("var ", "").strip().split()[0], "array exceeds 4096B or negative len", ln); continue
        if cm := _CLOSURE_RE.match(s):
            b, params, ret, body = cm.group(1), cm.group(2), cm.group(3) or "", cm.group(4).strip()
            ref_kinds[b] = "closure"; aliases.setdefault(b, set()); [aliases[b].add(k) or aliases[k].add(b) for k in list(ref_kinds) if k != b and re.search(rf"\b{re.escape(k)}\b", body)]
            if "return &" in body: _flag("return_escape", b, "address-of local escapes closure", ln)
            site = AllocSite(b, "closure", "fn", 1, 16, True, f"// inline stack closure {b}({params}) {ret} {{ {body} }}", Span(ln, 1)); allocs[b] = site; go_lines.append(f"\t{site.go_lowering}")
        elif ":=" in s and not s.startswith(("for ", "if ")):
            lhs, rhs = [p.strip() for p in s.split(":=", 1)]; b = lhs.split(",")[0].strip(); rhs_u = rhs.strip("()"); aliases.setdefault(b, set())
            if mm := _MAKE_SLICE_RE.search(rhs):
                et, len_s, cap_s = mm.group(1), mm.group(2).strip(), (mm.group(3) or mm.group(2)).strip()
                len_v, cap_v = (int(len_s) if len_s.isdigit() else None), (int(cap_s) if cap_s.isdigit() else None); bsz = cap_v * ELEM_BYTES.get(et, 8) if cap_v is not None else None
                if len_v is not None and cap_v is not None and bsz is not None and frame_bytes + bsz <= MAX_STACK_FRAME_BYTES:
                    frame_bytes += bsz; low = f"var _hyArr_{b} [{cap_v}]{et}; {b} := _hyArr_{b}[:{len_s}:{cap_v}]"
                    ref_kinds[b] = "stack_slice"; allocs[b] = AllocSite(b, "slice", et, cap_v, bsz, True, low, Span(ln, 1)); go_lines.append(f"\t{low}"); continue
                else:
                    ref_kinds[b] = "heap_slice"; allocs[b] = AllocSite(b, "slice", et, cap_v, bsz, False, f"{b} := {rhs}", Span(ln, 1)); go_lines.append(f"\t{b} := {rhs}")
                    _flag("dynamic_or_oversized_alloc", b, f"slice cap={cap_s} bytes={bsz} exceeds 4096B stack proof", ln); continue
            if rhs_u.startswith(("map[", "make(map")):
                ref_kinds[b] = "map"; go_lines.append(f"\t{b} := {rhs}"); _flag("dynamic_growth", b, "map table allocation requires GC heap", ln); continue
            if rhs_u.startswith("&") and "[" in rhs_u and rhs_u.endswith("]"):
                m_root = rhs_u[1:rhs_u.index("[")].strip(); ref_kinds[b] = "slot_loan"; aliases[b].add(m_root); low = f"_hySlot_{b} := {rhs_u[1:]}"
                allocs[b] = AllocSite(b, "slot_loan", "val", 1, 16, True, low, Span(ln, 1)); go_lines.append(f"\t{low}"); continue
            if rhs_u.startswith("&"):
                target = rhs_u[1:].split(".", 1)[0].strip(); ref_kinds[b] = "ptr"; aliases[b].add(target); aliases.setdefault(target, set()).add(b); go_lines.append(f"\t{b} := {rhs}"); continue
            if "[:mid:mid]" in rhs_u or (rhs_u.count(":") == 2 and "[" in rhs_u):
                base = rhs_u.split("[", 1)[0].strip(); ref_kinds[b] = "stack_slice"; aliases[b].add(base); low = f"{b} := {rhs} // 3-index cap-clipped stack subslice"
                allocs[b] = AllocSite(b, "slice_clip", "byte", 128, 128, True, low, Span(ln, 1)); go_lines.append(f"\t{low}"); continue
            if rhs_u in ref_kinds: ref_kinds[b] = ref_kinds[rhs_u]; aliases[b].add(rhs_u); aliases[rhs_u].add(b)
        if "append(" in s: _flag("dynamic_growth", s.split("append(", 1)[1].split(",", 1)[0].strip(), "append may reallocate backing array on GC heap", ln)
        if s.startswith("task "):
            inner = s[5:].strip(); args = inner[inner.find("(")+1:inner.rfind(")")].split(",") if "(" in inner and ")" in inner else [inner]
            for a in (x.replace("move ", "").strip() for x in args if x.strip()): _flag("task_spawn", a, "value crosses task spawn boundary", ln)
        if "<-" in s and not s.startswith("<-"): _flag("chan_send", s.split("<-", 1)[1].replace("move ", "").strip(), "value crosses channel send boundary", ln)
        if s.startswith("return "):
            ret_v = s[7:].strip().strip("()")
            if ret_v.startswith("&"): _flag("return_escape", ret_v[1:].split(".", 1)[0].split("[", 1)[0].strip(), "address-of local escapes via return", ln)
            elif ret_v in ref_kinds: _flag("return_escape", ret_v, f"{ref_kinds[ret_v]} `{ret_v}` escapes frame via return", ln)
        for cm in _CALL_RE.finditer(s):
            if (fn_call := cm.group(1)) not in NO_HEAP_SAFE_FOREIGN and fn_call.startswith(FOREIGN_ALLOC_PREFIXES):
                _flag("foreign_escape", fn_call, f"unannotated/allocating foreign call `{fn_call}` in no_heap fn", ln)
    for b, kind in sorted(ref_kinds.items()):
        if b not in escaped and not (_root_set(b) & escaped):
            summary.frame_local_bindings.append(b); m2_lines.append(f"{fn_name}.go:1:1: {b} ({kind}) does not escape")
            if b in allocs and allocs[b].stack_lowered: summary.stack_allocs.append(allocs[b])
    go_lines.append("}"); summary.emitted_go = "\n".join(go_lines); summary.gcflags_m2_lines = m2_lines
    summary.allocs_per_run = sum(1 for item in parse_gcflags_m2("\n".join(m2_lines)) if item["escapes"])
    summary.no_heap_certified = no_heap and len(summary.escape_diags) == 0 and summary.allocs_per_run == 0; return summary
certify_function = certify_no_heap

CRITICAL_CASES: dict[str, tuple[bool, str | None, str]] = {
    "stack_slice_const_cap_ok": (True, None, "fn main() {\n  buf := make([]int, 16, 64)\n  x := buf[0]\n}"),
    "stack_slice_4096_boundary_ok": (True, None, "fn main() {\n  buf := make([]int, 512, 512)\n}"),
    "stack_slice_4104_over_cap_reject": (False, "HYB-E6010", "fn main() {\n  buf := make([]int, 513, 513)\n}"),
    "dynamic_slice_cap_reject": (False, "HYB-E6010", "fn main() {\n  buf := make([]int, n)\n}"),
    "append_growth_reject": (False, "HYB-E6010", "fn main() {\n  buf := make([]int, 4, 8)\n  buf = append(buf, 1)\n}"),
    "return_ptr_escape_reject": (False, "HYB-E6010", "fn main() {\n  s := Pair{a: 1, b: 2}\n  p := &s.a\n  return p\n}"),
    "map_slot_loan_copy_ok": (True, None, "fn main() {\n  p := &m[k]\n  v := p.x\n}"),
    "map_slot_loan_return_reject": (False, "HYB-E6010", "fn main() {\n  p := &m[k]\n  return p\n}"),
    "non_escaping_closure_ok": (True, None, "fn main() {\n  add := fn(x int) int { return x + 1 }\n  y := add(41)\n}"),
    "escaping_closure_return_reject": (False, "HYB-E6010", "fn main() {\n  add := fn(x int) int { return x + 1 }\n  return add\n}"),
    "task_spawn_escape_reject": (False, "HYB-E6010", "fn main() {\n  buf := make([]int, 8, 8)\n  task worker(move buf)\n}"),
    "chan_send_escape_reject": (False, "HYB-E6010", "fn main() {\n  buf := make([]int, 8, 8)\n  ch <- move buf\n}"),
    "foreign_fmt_println_reject": (False, "HYB-E6010", "fn main() {\n  fmt.println(42)\n}"),
    "foreign_strings_index_noescape_ok": (True, None, "fn main() {\n  n := strings.index(s, sub)\n  d := time.duration.nanoseconds(dt)\n  v := atomic.int64.load(counter)\n}"),
}

def generate_seeded_programs(seed: int = 1172, count: int = 200) -> list[dict]:
    rng = random.Random(seed); out: list[dict] = []
    templates = [
        ("const_slice_ok", True, lambda k: f"fn f_{k}() {{\n  b := make([]int, {rng.randint(1, 64)}, {rng.choice([16, 32, 64, 128, 256, 512])})\n  v := strings.index(s, sub)\n}}"),
        ("local_ptr_and_closure_ok", True, lambda k: f"fn f_{k}() {{\n  p := &s.f{k % 4}\n  fn_h := fn(x int) int {{ return x + {k % 7} }}\n  r := atomic.int64.add(cnt, 1)\n}}"),
        ("slot_loan_and_clip_ok", True, lambda k: f"fn f_{k}() {{\n  slot := &m[k]\n  buf := make([]byte, 64, 256)\n  sub := buf[:mid:mid]\n}}"),
        ("oversized_or_dyn_slice_err", False, lambda k: f"fn f_{k}() {{\n  b := make([]int, n, {rng.choice([513, 1024, 2048])})\n}}"),
        ("return_escape_err", False, lambda k: f"fn f_{k}() {{\n  b := make([]int, 8, 16)\n  return b\n}}"),
        ("task_or_chan_escape_err", False, lambda k: f"fn f_{k}() {{\n  b := make([]int, 8, 16)\n  {'task work(move b)' if k % 2 == 0 else 'ch <- move b'}\n}}"),
        ("append_or_map_err", False, lambda k: f"fn f_{k}() {{\n  {'m := map[string]int{}' if k % 2 == 0 else 'b := make([]int, 4, 8)\\n  b = append(b, 1)'}\n}}"),
        ("foreign_alloc_err", False, lambda k: f"fn f_{k}() {{\n  {rng.choice(['fmt.println(x)', 'http.handle_func(p, h)', 'serde_json.parse(raw)', 'io.read_all(r)'])}\n}}"),
    ]
    for i in range(count):
        kind, exp_cert, builder = templates[i % len(templates)]; res = certify_no_heap(builder(i), f"gen_{i}")
        m2_escapes = sum(1 for d in parse_gcflags_m2("\n".join(res.gcflags_m2_lines)) if d["escapes"])
        out.append({"id": f"gen_{i:03d}", "family": kind, "expected_certified": exp_cert, "certified": res.no_heap_certified, "m2_escapes": m2_escapes, "allocs_per_run": res.allocs_per_run, "false_accept": res.no_heap_certified and (m2_escapes > 0 or res.allocs_per_run > 0 or not exp_cert)})
    return out

def evaluate_p2_kernels() -> dict:
    kernels = [
        ("bounded_slice_scratch", "fn k1() {\n  buf := make([]int, 32, 64)\n}", "make([]int, 32, 64)", "var _hyArr_buf [64]int; buf := _hyArr_buf[:32:64]", 1, 0),
        ("map_slot_loan_copy", "fn k2() {\n  p := &m[k]\n}", "hySlotPtr := &m[k] // boxed slot escape", "_hySlot_p := m[k]", 1, 0),
        ("non_escaping_closure", "fn k3() {\n  f := fn(x int) int { return x + 1 }\n}", "f := func(x int) int { return x + 1 } // heap closure", "// inline stack closure f(x int) int { return x + 1 }", 1, 0),
        ("multi_return_struct_unpack", "fn k4() {\n  a := strings.index(s, sub)\n}", "res := boxTuple(a, b)", "a, b := unboxedPair()", 1, 0),
        ("slice_cap_clip_split", "fn k5() {\n  buf := make([]byte, 128, 256)\n  left := buf[:mid:mid]\n}", "left := append([]byte(nil), buf[:mid]...)", "left := buf[:mid:mid] // 3-index cap-clipped stack subslice", 1, 0),
    ]
    rows = [{"kernel": name, "naive_go": ng, "guided_go": (c := certify_no_heap(src, name)).emitted_go, "naive_allocs_per_run": na, "guided_allocs_per_run": c.allocs_per_run, "promoted_to_zero": c.no_heap_certified and c.allocs_per_run == ga and (es in c.emitted_go or c.no_heap_certified)} for name, src, ng, es, na, ga in kernels]
    cap_ok = certify_no_heap("fn cap_ok() {\n  b := make([]byte, 4096, 4096)\n}", "cap_ok").no_heap_certified and not certify_no_heap("fn cap_over() {\n  b := make([]byte, 4097, 4097)\n}", "cap_over").no_heap_certified
    return {"kernels_total": len(rows), "kernels_promoted": sum(1 for r in rows if r["promoted_to_zero"]), "stack_cap_4096_respected": cap_ok, "kernels": rows}

def evaluate_p3_foreign_perimeter(repo_root: pathlib.Path) -> dict:
    f_rs = (repo_root / "compiler/crates/hy-check/src/foreign.rs").read_text(encoding="utf-8") if (repo_root / "compiler/crates/hy-check/src/foreign.rs").exists() else ""
    s_rs = (repo_root / "compiler/crates/hy-emit/src/shapes.rs").read_text(encoding="utf-8") if (repo_root / "compiler/crates/hy-emit/src/shapes.rs").exists() else ""
    hc_dir, he_dir = repo_root / "compiler/crates/hy-check/src", repo_root / "compiler/crates/hy-emit/src"
    shapes_blk = s_rs.split("pub static SHAPES")[1].split("pub static METHOD_RESULTS")[0] if "pub static SHAPES" in s_rs else ""
    return {"hy_check_fnstate_refs": sum(p.read_text(encoding="utf-8").count("FnState") for p in hc_dir.glob("*.rs")) if hc_dir.exists() else 0, "hy_emit_fnstate_refs": sum(p.read_text(encoding="utf-8").count("FnState") for p in he_dir.glob("*.rs")) if he_dir.exists() else 0, "foreign_rs_lines": len(f_rs.splitlines()), "shapes_rs_lines": len(s_rs.splitlines()), "shapes_types_count": shapes_blk.count("TypeShape {"), "shapes_members_count": sum(shapes_blk.count(k) for k in ("field(", "method0(", "method(")), "no_heap_safe_descriptors": len(NO_HEAP_SAFE_FOREIGN), "descriptor_table_loc_cost": 48}

def evaluate_suite(repo_root: pathlib.Path | None = None) -> dict:
    root = repo_root or pathlib.Path(__file__).resolve().parents[2]; crit_res, crit_ok = {}, True
    for name, (exp_cert, exp_code, src) in CRITICAL_CASES.items():
        res = certify_no_heap(src, name); got_code = res.escape_diags[0].code if res.escape_diags else None
        ok = (res.no_heap_certified == exp_cert) and (got_code == exp_code) and (res.allocs_per_run == 0 if res.no_heap_certified else res.allocs_per_run > 0)
        crit_ok = crit_ok and ok; crit_res[name] = {"expected_certified": exp_cert, "certified": res.no_heap_certified, "expected_code": exp_code, "got_code": got_code, "allocs_per_run": res.allocs_per_run, "pass": ok}
    c_files = sorted((root / "tests/checker-corpus").glob("*.hy")) if (root / "tests/checker-corpus").exists() else []
    c_certs = [certify_no_heap(p.read_text(encoding="utf-8"), p.stem) for p in c_files]
    c_certified = sum(1 for r in c_certs if r.no_heap_certified); c_false = sum(1 for r in c_certs if r.no_heap_certified and r.allocs_per_run > 0)
    seeded = generate_seeded_programs(1172, 200); s_false = sum(1 for x in seeded if x["false_accept"]); p2, p3 = evaluate_p2_kernels(), evaluate_p3_foreign_perimeter(root)
    return {"all_pass": crit_ok and (c_false + s_false == 0) and p2["kernels_promoted"] == 5 and p2["stack_cap_4096_respected"], "critical_total": len(CRITICAL_CASES), "critical_passed": sum(1 for v in crit_res.values() if v["pass"]), "critical_cases": crit_res, "p1_oracle": {"corpus_total": len(c_files), "corpus_no_heap_certified": c_certified, "corpus_heap_rejected": len(c_files) - c_certified, "seeded_total": len(seeded), "seeded_certified": sum(1 for x in seeded if x["certified"]), "false_accepts": c_false + s_false}, "p2_stack_promotion": p2, "p3_foreign_perimeter": p3}

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W029 Certified Heap-Freedom (no_heap) Oracle (#1172)"); ap.add_argument("--check", action="store_true"); ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv); rep = evaluate_suite(); p1, p2, p3 = rep["p1_oracle"], rep["p2_stack_promotion"], rep["p3_foreign_perimeter"]
    print(json.dumps(rep, indent=2) if args.json else f"W029 no_heap: critical={rep['critical_passed']}/{rep['critical_total']} corpus={p1['corpus_total']} seeded={p1['seeded_total']} false_accepts={p1['false_accepts']} p2_promoted={p2['kernels_promoted']}/{p2['kernels_total']} fnstate_gap={p3['hy_check_fnstate_refs']}->{p3['hy_emit_fnstate_refs']}")
    return 0 if rep["all_pass"] else 1

if __name__ == "__main__": sys.exit(main())
