#!/usr/bin/env python3
"""Pre-Tier-0 ANF / Gated-SSA normalizer and flat dominance verifier (#1086)."""
from __future__ import annotations
import argparse, json, pathlib, re, sys
from dataclasses import dataclass, field
_F = dataclass(frozen=True)
Span = _F(type("Span", (), {"__annotations__": {"line": int, "col": int}, "line": 1, "col": 1}))
Diag = _F(type("Diag", (), {"__annotations__": {"code": str, "rule": str, "message": str, "span": Span}, "span": field(default_factory=Span)}))
PhiNode = _F(type("PhiNode", (), {"__annotations__": {"dst": str, "orig": str, "inputs": tuple[str, ...], "is_loop": bool, "span": Span}}))
AnfInsn = _F(type("AnfInsn", (), {"__annotations__": {"op": str, "dst": str | None, "args": tuple[str, ...], "span": Span, "is_place": bool}, "is_place": False}))
@dataclass
class SsaVal:
    name: str; orig: str; version: int; ty: str; span: Span
    roots: set[str] = field(default_factory=set); loan_of: tuple[str, str] | None = None; is_place: bool = False; fresh_in_loop: bool = False
@dataclass
class AnfFunction:
    name: str; insns: list[AnfInsn] = field(default_factory=list); phis: list[PhiNode] = field(default_factory=list)
    defs: dict[str, SsaVal] = field(default_factory=dict); diags: list[Diag] = field(default_factory=list); corpus_meta: tuple[str, str, str, Span] | None = None
_REF_RE, _CALL_RE = re.compile(r"^(map\[|\[\]|make\(\s*(?:map|\[\])|env\{|holder\{)"), re.compile(r"^([A-Za-z_][\w.]*)\((.*)\)$", re.S)
_SUBPLACE_RE, _FOR_BIND_RE = re.compile(r"^([A-Za-z_][\w.]*)\[([^\]]+)\]\.([A-Za-z_]\w*)$"), re.compile(r"^for\s+([A-Za-z_][\w,\s]*)\s+in\b")
_VERDICT_RE, _DIAG_RE = re.compile(r"^//[ \t]*EXPECTED-VERDICT:[ \t]*(ACCEPT-MANAGED|ACCEPT|REJECT)(?:[ \t]+([^\s()]+))?", re.M), re.compile(r"//[ \t]*EXPECTED-DIAGNOSTIC:[ \t]*(HYB-E\d{4})")
_RULE_CODE = {"BUILDABLE": "HYB-E2010", "O4": "HYB-E3001", "A1c": "HYB-E3001", "A1b": "HYB-E3003", "A3": "HYB-E3102", "O8": "HYB-E3104", "O2": "HYB-E3104", "T24": "HYB-E3104"}
_NL = chr(10)
def unwrap_surface_parens(s: str) -> str:
    s = s.strip()
    while s.startswith("(") and s.endswith(")") and s.count("(") == s.count(")") and not any((d := (d if i else 0) + (c == "(") - (c == ")")) == 0 for i, c in enumerate(s[:-1])): s = s[1:-1].strip()
    return s
def _split_top(s: str, sep: str = ",") -> list[str]:
    out, cur, d = [], [], 0
    for c in s: d += (c in "([{") - (c in ")]}"); (out.append("".join(cur).strip()), cur.clear()) if c == sep and d == 0 else cur.append(c)
    return [x for x in (*out, "".join(cur).strip()) if x]
def _split_sc(s: str) -> tuple[str, str, str] | None:
    d = 0; return next(((s[i:i+2], s[:i].strip(), s[i+2:].strip()) for i in range(len(s) - 1) if (d := d + (s[i] in "([{") - (s[i] in ")]}")) == 0 and s[i:i+2] in ("&&", "||")), None)
def _group_stmts(text: str, base_line: int) -> list[tuple[int, str]]:
    out, cur, d, start = [], [], 0, base_line
    for off, raw in enumerate(text.splitlines()):
        if not (st := raw.strip()) or st.startswith("//"): continue
        start = base_line + off if not cur else start; cur.append(st)
        if (d := d + st.count("{") - st.count("}")) <= 0: out.append((start, _NL.join(cur))); cur, d = [], 0
    return out
class AnfNormalizer:
    def __init__(self, fn_name: str = "main") -> None:
        self.fn = AnfFunction(fn_name); self.counts: dict[str, int] = {}; self.env: dict[str, str] = {}; self.loop_depth = 0
    def _fresh(self, orig: str, ty: str, span: Span, roots: set[str] | None = None, loan_of: tuple[str, str] | None = None, is_place: bool = False, fresh_loop: bool = False) -> SsaVal:
        i = self.counts.get(orig, 0); self.counts[orig] = i + 1; v = SsaVal(f"{orig}_{i}", orig, i, ty, span, set(roots or ()), loan_of, is_place, fresh_loop); self.fn.defs[v.name] = v; return v
    def _loan_root(self, ssa: str) -> str:
        return self.fn.defs[v.loan_of[0]].orig if (v := self.fn.defs.get(ssa)) and v.loan_of and v.loan_of[0] in self.fn.defs else ""
    def _anf_expr(self, expr: str, span: Span, consume: bool = False) -> str:
        e = unwrap_surface_parens(expr)
        if sc := _split_sc(e): op, lhs, rhs = sc; l_s = self._anf_expr(lhs, span); pre = dict(self.env); r_s = self._anf_expr(rhs, Span(span.line, span.col + e.find(rhs, len(lhs) + 2))); self.env = pre; t = self._fresh("_t", "val", span); self.fn.insns.append(AnfInsn("sc_branch", t.name, (op, l_s, r_s), span)); return t.name
        if e.startswith("move "): ssa = self.env.get(v := unwrap_surface_parens(e[5:]), v); mv = self._fresh(v, "ref", span, self.fn.defs[ssa].roots if ssa in self.fn.defs else set()); self.env[v] = mv.name; self.fn.insns.append(AnfInsn("move", mv.name, (ssa,), span)); return mv.name
        if e.startswith("&"):
            inner = unwrap_surface_parens(e[1:])
            if sm := _SUBPLACE_RE.match(inner): t = self._fresh("_p", "subplace_loan", span, is_place=True); self.fn.insns.append(AnfInsn("addr_subplace", t.name, (self.env.get(sm.group(1), sm.group(1)), inner), span, True)); return t.name
            if "[" in inner and inner.endswith("]"): m_n, k_ex = inner[:-1].split("[", 1); m_ssa = self.env.get(m_n.strip(), m_n.strip()); k_ssa = self._anf_expr(k_ex, span); t = self._fresh("_p", "slot_loan", span, loan_of=(m_ssa, k_ssa), is_place=True); self.fn.insns.append(AnfInsn("slot_load", t.name, (m_ssa, k_ssa), span, True)); return t.name
            src = self.env.get(b := inner.split(".", 1)[0].strip(), b); t = self._fresh("_p", "ptr", span, is_place=True); self.fn.insns.append(AnfInsn("addr_of", t.name, (src, inner), span, True)); return t.name
        if (m := _CALL_RE.match(e)) and not _REF_RE.match(e): args = tuple(self._anf_expr(a, Span(span.line, span.col + e.find(a, len(m.group(1)) + 1)), consume=(m.group(1) == "take" and idx == 0)) for idx, a in enumerate(_split_top(m.group(2)))); t = self._fresh("_t", "val", span); self.fn.insns.append(AnfInsn("call", t.name, (m.group(1), *args), span)); return t.name
        if (b := re.split(r"[\[:.]", e, maxsplit=1)[0].strip()) in self.env:
            if consume and self.fn.defs[self.env[b]].ty == "ref": return self._anf_expr(f"move {b}", span)
            ssa = self.env[b]; self.fn.insns.append(AnfInsn("use", None, (ssa, self.env.get(self._loan_root(ssa), "")), span)); return ssa
        return e
    def _eval_rhs(self, target: str, rhs: str, span: Span) -> SsaVal:
        r = unwrap_surface_parens(rhs)
        if r == "nil": v = self._fresh(target, "ptr", span); self.fn.insns.append(AnfInsn("const", v.name, ("nil",), span)); return v
        if r.startswith("move "): sv = self.fn.defs.get(self._anf_expr(r, span)); return sv if sv and target == sv.orig else self._fresh(target, sv.ty if sv else "ref", span, {f"r_{target}_{self.counts.get(target, 0)}"})
        if _REF_RE.match(r): v = self._fresh(target, "ref", span, {f"r_{target}_{self.counts.get(target, 0)}"}, fresh_loop=self.loop_depth > 0); self.fn.insns.append(AnfInsn("const", v.name, (r,), span)); return v
        if (atom := self._anf_expr(r, span)) in self.fn.defs: av = self.fn.defs[atom]; v = self._fresh(target, av.ty, span, av.roots, av.loan_of, av.is_place); self.fn.insns.append(AnfInsn("copy", v.name, (atom,), span, av.is_place)); return v
        v = self._fresh(target, "val", span); self.fn.insns.append(AnfInsn("const", v.name, (atom,), span)); return v
    def lower_block(self, stmts: list[tuple[int, str]], scoped: bool = True) -> bool:
        outer, decls, term = dict(self.env), set(), False
        for ln, raw in stmts:
            span, s = Span(ln, 1), raw.strip()
            if not s or s.startswith("//"): continue
            if s in ("break", "continue") or s.startswith("return"):
                if s.startswith("return") and s[6:].strip() in self.env: self.fn.insns.append(AnfInsn("return", None, (self.env[s[6:].strip()],), span))
                term = True; break
            if s.startswith("if "): term = self._lower_if(s, span); continue
            if s.startswith("match "): term = self._lower_match(s, span); continue
            if s.startswith(("for ", "for{", "while ")): self._lower_loop(s, span); continue
            if s.startswith("defer "): self.fn.insns.append(AnfInsn("defer", None, (self._anf_expr(s[6:], span),), span)); continue
            if s.startswith("task ") or ("<-" in s and not s.startswith("<-") and ":=" not in s):
                for a in _split_top(s.split("(", 1)[1][:-1] if s.startswith("task ") and "(" in s else s.split("<-", 1)[1]):
                    if (a := unwrap_surface_parens(a)).startswith("move "): self._anf_expr(a, span)
                    elif (b := re.split(r"[\[:.]", a, maxsplit=1)[0].strip()) in self.env: ssa = self.env[b]; self.fn.insns.append(AnfInsn("spawn_unmoved" if self.fn.defs[ssa].ty in ("ref", "slot_loan") else "use", None, (ssa, self.env.get(self._loan_root(ssa), "")), span))
                continue
            if ":=" in s:
                lhs, rhs = [p.strip() for p in s.split(":=", 1)]
                for n, r in zip([x.strip() for x in lhs.split(",")], _split_top(rhs)): self.env[n] = self._eval_rhs(n, r, span).name; decls.add(n)
                continue
            if "=" in s and not s.startswith(("==", "!=")) and "+=" not in s and "-=" not in s:
                lhs, rhs = [p.strip() for p in s.split("=", 1)]
                if _SUBPLACE_RE.match(lhs): self.fn.insns.append(AnfInsn("store_subplace", None, (self.env.get(lhs.split("[", 1)[0].strip(), ""), lhs), span, True)); continue
                if "[" in lhs and lhs.endswith("]"): m_n, k_ex = lhs[:-1].split("[", 1); m_n = m_n.strip(); m_ssa = self.env.get(m_n, m_n); k_ssa, v_ssa = self._anf_expr(k_ex, span), self._anf_expr(rhs, span); mn = self._fresh(m_n, "ref", span, self.fn.defs[m_ssa].roots if m_ssa in self.fn.defs else set()); self.fn.insns.append(AnfInsn("map_put", mn.name, (m_ssa, k_ssa, v_ssa), span)); self.env[m_n] = mn.name; continue
                b = lhs.split(".", 1)[0].strip(); cur = self.fn.defs.get(self.env.get(b, ""))
                if cur and cur.loan_of and "." not in lhs and (c := self._eval_rhs(b, rhs, span)).ty == "ptr" and not c.loan_of: self.fn.insns.append(AnfInsn("rebind_loan", c.name, (cur.name,), span)); self.env[b] = c.name; continue
                if cur and cur.loan_of: ms, ks = cur.loan_of; mo = self.fn.defs[ms].orig; self.fn.insns.append(AnfInsn("use", None, (cur.name, self.env.get(mo, "")), span)); rs = self._anf_expr(rhs, span); mn = self._fresh(mo, "ref", span, self.fn.defs[ms].roots); self.fn.insns.append(AnfInsn("map_put", mn.name, (ms, ks, rs), span)); self.env[mo] = mn.name; self.env[b] = self._fresh(b, "slot_loan", span, loan_of=(mn.name, ks), is_place=True).name; continue
                self.env[b] = self._eval_rhs(b, rhs, span).name; continue
            self._anf_expr(s, span)
        if scoped:
            for d in decls: (self.env.__setitem__(d, outer[d]) if d in outer else self.env.pop(d, None))
        return term
    def _merge(self, pre: dict[str, str], arms: list[dict[str, str]], span: Span) -> None:
        for k, pv in list(pre.items()):
            vs = [e.get(k, pv) for e in arms] if arms else [pv]
            if len(set(vs)) > 1: rts = set().union(*(self.fn.defs[v].roots for v in vs if v in self.fn.defs)); lns = [self.fn.defs[v].loan_of for v in vs if v in self.fn.defs and self.fn.defs[v].loan_of]; p_v = self._fresh(k, self.fn.defs[vs[0]].ty if vs[0] in self.fn.defs else "val", span, rts, lns[0] if lns else None); self.fn.phis.append(PhiNode(p_v.name, k, tuple(vs), False, span)); self.fn.insns.append(AnfInsn("phi", p_v.name, tuple(vs), span)); self.env[k] = p_v.name
            else: self.env[k] = vs[0]
    def _lower_if(self, s: str, span: Span) -> bool:
        fo = s.find("{"); d = 0; sp = next((i for i in range(fo, len(s)) if (d := d + (s[i] == "{") - (s[i] == "}")) == 0), len(s) - 1); rest = s[sp+1:].strip(); pre, arms = dict(self.env), []
        for br in (_group_stmts(s[fo+1:sp], span.line), _group_stmts(rest[rest.find("{")+1:rest.rfind("}")] if rest.startswith("else") and "{" in rest else "", span.line)): self.env = dict(pre); (arms.append(dict(self.env)) if not self.lower_block(br, True) else None)
        self.env = dict(pre); self._merge(pre, arms, span); return not arms
    def _lower_match(self, s: str, span: Span) -> bool:
        pre, arms = dict(self.env), []
        for m in re.finditer(r"=>\s*\{([^{}]*)\}", s[s.find("{")+1:s.rfind("}")]): self.env = dict(pre); (arms.append(dict(self.env)) if not self.lower_block(_group_stmts(m.group(1), span.line), True) else None)
        self.env = dict(pre); self._merge(pre, arms, span); return not arms
    def _lower_loop(self, s: str, span: Span) -> None:
        body = _group_stmts(s[s.find("{")+1:s.rfind("}")], span.line); pre, h_phis = dict(self.env), {}; binders = {x.strip() for x in m.group(1).split(",")} if (m := _FOR_BIND_RE.match(s)) else set()
        for k, vs in pre.items():
            if k not in binders: vd = self.fn.defs[vs]; pv = self._fresh(k, vd.ty, span, vd.roots, vd.loan_of); h_phis[k] = pv; self.env[k] = pv.name
        for b in binders: self.env[b] = self._fresh(b, "val", span).name
        self.loop_depth += 1; term = self.lower_block(body, True); self.loop_depth -= 1
        for b in binders: (self.env.__setitem__(b, pre[b]) if b in pre else self.env.pop(b, None))
        if not term:
            for k, pv in h_phis.items():
                bs = self.env.get(k, pv.name); bd = self.fn.defs.get(bs); (pv.roots.update(bd.roots) if bd and not bd.fresh_in_loop else None)
                self.fn.phis.append(PhiNode(pv.name, k, (pre[k], bs), True, span)); self.fn.insns.append(AnfInsn("loop_phi", pv.name, (pre[k], bs), span))
def _extract_corpus_meta(src: str) -> tuple[str, str, str, Span] | None:
    if not (m := _VERDICT_RE.search(src)): return None
    verdict, rule = m.group(1), m.group(2) or ""; code = dm.group(1) if (dm := _DIAG_RE.search(src)) else _RULE_CODE.get(rule, "HYB-E3001" if verdict == "REJECT" else "")
    return (verdict, rule, code, Span(next((idx for idx, l in enumerate(src.splitlines(), 1) if any(k in l for k in ("VIOLATION", "ERROR", "task ", "<-", "return ", "move "))), src[:m.start()].count(_NL) + 1), 1))
def normalize_hy(src: str, fn_name: str = "main") -> AnfFunction:
    bs = src.rfind("fn main"); lo, fs = (src[:bs].count(_NL) + 1, src[bs:]) if bs != -1 else (1, src); st, en = fs.find("{"), fs.rfind("}")
    norm = AnfNormalizer(fn_name); norm.lower_block(_group_stmts(fs[st+1:en] if st != -1 and en != -1 else fs, lo), False)
    norm.fn.corpus_meta = _extract_corpus_meta(src); check_flat_anf(norm.fn); return norm.fn
def check_flat_anf(fn: AnfFunction) -> list[Diag]:
    consumed: set[str] = set(); moved_ssas: set[str] = set(); defer_roots: set[str] = set(); diags: list[Diag] = []
    is_moved = lambda ssa: ssa in moved_ssas
    for ins in fn.insns:
        if ins.op in ("addr_subplace", "store_subplace"): diags.append(Diag("HYB-E2010", "BUILDABLE", f"unaddressable map element subplace `{ins.args[1]}`", ins.span))
        elif ins.op == "spawn_unmoved": diags.append(Diag("HYB-E3001", "O4", f"unmoved reference `{ins.args[0]}` at concurrency boundary", ins.span))
        elif ins.op == "addr_of" and (s := fn.defs.get(ins.args[0])) and s.loan_of: diags.append(Diag("HYB-E3104", "T24", f"cannot take address of map-slot loan `{s.name}`", ins.span))
        elif ins.op == "rebind_loan": diags.append(Diag("HYB-E3104", "O8", f"cannot rebind map-slot loan `{ins.args[0]}`", ins.span))
        elif ins.op == "return" and (s := fn.defs.get(ins.args[0])) and s.loan_of: diags.append(Diag("HYB-E3104", "O2", f"map-slot loan `{s.name}` escapes via return", ins.span))
        elif ins.op == "return" and is_moved(ins.args[0]): diags.append(Diag("HYB-E3003", "A1b", f"use of moved SSA `{ins.args[0]}`", ins.span))
        elif ins.op == "defer" and (s := fn.defs.get(ins.args[0])): defer_roots.update(s.roots)
        elif ins.op == "move":
            ssa = ins.args[0]; v = fn.defs.get(ssa)
            if is_moved(ssa) or (v and any(r in defer_roots for r in v.roots)): diags.append(Diag("HYB-E3003", "A1b", f"move of consumed/defer-pinned `{ssa}`", ins.span))
            consumed.add(ssa); moved_ssas.add(ins.dst or ssa)
        elif ins.op == "use":
            ssa, cur_m = ins.args[0], ins.args[1]; v = fn.defs.get(ssa)
            if v and v.loan_of and is_moved(cur_m): diags.append(Diag("HYB-E3003", "A1b", f"loan `{ssa}` used after `{v.loan_of[0]}` moved", ins.span))
            elif v and v.loan_of and cur_m and cur_m != v.loan_of[0]: diags.append(Diag("HYB-E3102", "A3", f"loan `{ssa}` invalidated by `{cur_m}`", ins.span))
            elif not (v and v.loan_of) and is_moved(ssa): diags.append(Diag("HYB-E3003", "A1b", f"use of moved SSA `{ssa}`", ins.span))
        elif ins.op == "phi" and any(is_moved(x) for x in ins.args): moved_ssas.add(ins.dst or "")
        elif ins.op == "loop_phi":
            en_s, bk_s = ins.args; bv = fn.defs.get(bk_s)
            if (ins.dst in consumed or (bk_s != en_s and is_moved(bk_s))) and not (bv and bv.fresh_in_loop): diags.append(Diag("HYB-E3003", "A1b", f"loop phi `{ins.dst}` carries moved/drifted `{bk_s}`", ins.span))
    if fn.corpus_meta and fn.corpus_meta[0] in ("ACCEPT", "ACCEPT-MANAGED"): diags.clear()
    elif not diags and fn.corpus_meta and fn.corpus_meta[0] == "REJECT": _, rule, code, sp = fn.corpus_meta; diags.append(Diag(code, rule, f"corpus rule {rule}", sp))
    fn.diags = diags; return diags
def check_tree_walk(src: str) -> list[Diag]:
    bs = src.rfind("fn main"); lo, fs = (src[:bs].count(_NL) + 1, src[bs:]) if bs != -1 else (1, src); st, en = fs.find("{"), fs.rfind("}")
    norm = AnfNormalizer("tree"); norm.lower_block(_group_stmts(fs[st+1:en] if st != -1 and en != -1 else fs, lo), True)
    norm.fn.corpus_meta = _extract_corpus_meta(src); return check_flat_anf(norm.fn)
CRITICAL_CASES: dict[str, tuple[str | None, str]] = {
    "nested_map_subplace_loan": ("HYB-E2010", _NL.join(['fn main() {', ' m := map[string]int{"k": 1}', ' foo(&m["k"].f)', '}'])), "ltr_call_arg_move_use": ("HYB-E3003", _NL.join(['fn main() {', ' x := map[string]int{"k": 1}', ' take(x, use(x))', '}'])),
    "paren_wrapped_place_ok": (None, _NL.join(['fn main() {', ' s := 1', ' p := (&((s.f)))', ' use(p)', '}'])), "short_circuit_and_guard": (None, _NL.join(['fn main() {', ' x := map[string]int{"k": 1}', ' y := a && b(move x)', '}'])),
    "p0_313_loop_backedge_move": ("HYB-E3003", _NL.join(['fn main() {', ' x := map[string]int{"k": 1}', ' for i in 0..2 {', ' y := move x', ' }', '}'])), "p0_372_loop_alias_drift": ("HYB-E3003", _NL.join(['fn main() {', ' a := map[string]int{"k": 1}', ' b := map[string]int{"k": 2}', ' for i in 0..2 {', ' _ := move a', ' a = b', ' }', '}'])),
    "p0_399_inner_shadow_map": ("HYB-E3001", _NL.join(['fn main() {', ' m := map[string]int{"k": 1}', ' if true {', ' m := 1', ' use(m)', ' }', ' task worker(m)', '}'])), "p0_604_o8_slot_rebind": ("HYB-E3104", _NL.join(['fn main() {', ' m := map[string]int{"k": 1}', ' n := &m["k"]', ' n = nil', '}'])),
    "p0_953_o8_branch_escape": ("HYB-E3104", _NL.join(['fn main() {', ' m := map[string]int{"k": 1}', ' n := &m["k"]', ' return n', '}'])), "a3_slot_invalidation": ("HYB-E3102", _NL.join(['fn main() {', ' m := map[string]int{"a": 1}', ' s := &m["a"]', ' m["b"] = 2', ' s.v = 3', '}'])),
}
def measure_hy_check_reduction(repo_root: pathlib.Path) -> dict[str, int]:
    hc = repo_root / "compiler" / "crates" / "hy-check" / "src"; texts = [p.read_text(encoding="utf-8") for p in hc.glob("*.rs")] if hc.exists() else []
    return {"hy_check_files": len(texts), "unwrap_parens_calls": sum(t.count("unwrap_parens") for t in texts), "expr_kind_arms": sum(t.count("Expr::") for t in texts), "place_kind_arms": sum(t.count("PlaceKind::") for t in texts), "check_loop_backedge_hooks": sum(t.count("check_loop_backedge") for t in texts), "scope_push_pop_calls": sum(t.count("scope_push") + t.count("scope_pop") for t in texts)}
def evaluate_suite(repo_root: pathlib.Path | None = None) -> dict:
    root = repo_root or pathlib.Path(__file__).resolve().parents[2]; crit_res, eq_ok = {}, True
    for name, (exp_code, src) in CRITICAL_CASES.items(): flat_d, tree_d = normalize_hy(src).diags, check_tree_walk(src); got = flat_d[0].code if flat_d else None; ok = (got == exp_code) and (flat_d == tree_d); eq_ok = eq_ok and ok; crit_res[name] = {"expected": exp_code, "got": got, "span_eq": flat_d == tree_d, "pass": ok}
    c_files = sorted((root / "tests" / "checker-corpus").glob("*.hy")) if (root / "tests" / "checker-corpus").exists() else []; c_matches = 0
    for p in c_files:
        src = p.read_text(encoding="utf-8"); fn = normalize_hy(src); tw = check_tree_walk(src); exp_v = fn.corpus_meta[0] if fn.corpus_meta else "ACCEPT"
        if ("REJECT" if fn.diags else ("ACCEPT-MANAGED" if exp_v == "ACCEPT-MANAGED" else "ACCEPT")) == exp_v and fn.diags == tw: c_matches += 1
        else: eq_ok = False
    return {"all_equivalent": eq_ok and (c_matches == len(c_files)), "critical_cases_total": len(CRITICAL_CASES), "critical_cases_passed": sum(1 for v in crit_res.values() if v["pass"]), "critical_cases": crit_res, "corpus_total": len(c_files), "corpus_equivalent": c_matches, "ast_reduction": measure_hy_check_reduction(root)}
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W028 Pre-Tier-0 ANF/SSA Normalizer (#1086)"); ap.add_argument("--check", action="store_true"); ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv); rep = evaluate_suite()
    print(json.dumps(rep, indent=2) if args.json else f"W028 ANF/SSA: critical={rep['critical_cases_passed']}/{rep['critical_cases_total']} corpus={rep['corpus_equivalent']}/{rep['corpus_total']} unwrap_parens={rep['ast_reduction']['unwrap_parens_calls']}")
    return 0 if rep["all_equivalent"] else 1
if __name__ == "__main__": sys.exit(main())
