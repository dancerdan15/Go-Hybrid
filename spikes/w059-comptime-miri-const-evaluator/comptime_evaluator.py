from __future__ import annotations
import argparse, ast, json, math, operator, re, sys
from dataclasses import dataclass
from enum import Enum; from fractions import Fraction; from typing import Any, Mapping
F32_MAX, F64_MAX, STRING_INT_LIMIT, MAX_SHIFT_BITS = (Fraction(2) - Fraction(1, 1 << 23)) * (1 << 127), (Fraction(2) - Fraction(1, 1 << 52)) * (1 << 1023), 1 << 512, 4096
_INT_RANGES: dict[str, tuple[int, int]] = {"i8": (-128, 127), "int8": (-128, 127), "u8": (0, 255), "uint8": (0, 255), "byte": (0, 255), "i16": (-32768, 32767), "int16": (-32768, 32767), "u16": (0, 65535), "uint16": (0, 65535), "i32": (-(1 << 31), (1 << 31) - 1), "int32": (-(1 << 31), (1 << 31) - 1), "rune": (-(1 << 31), (1 << 31) - 1), "u32": (0, (1 << 32) - 1), "uint32": (0, (1 << 32) - 1), "i64": (-(1 << 63), (1 << 63) - 1), "int64": (-(1 << 63), (1 << 63) - 1), "int": (-(1 << 63), (1 << 63) - 1), "isize": (-(1 << 63), (1 << 63) - 1), "u64": (0, (1 << 64) - 1), "uint64": (0, (1 << 64) - 1), "uint": (0, (1 << 64) - 1), "usize": (0, (1 << 64) - 1), "uintptr": (0, (1 << 64) - 1)}
_GO_NAMES = {"i8": "int8", "u8": "uint8", "i16": "int16", "u16": "uint16", "i32": "int32", "u32": "uint32", "i64": "int64", "u64": "uint64", "isize": "int", "usize": "uint", "f32": "float32", "f64": "float64"}
class ScalarType(str, Enum):
    I8 = "i8"; INT8 = "int8"; U8 = "u8"; UINT8 = "uint8"; BYTE = "byte"; I16 = "i16"; INT16 = "int16"; U16 = "u16"; UINT16 = "uint16"; I32 = "i32"; INT32 = "int32"; RUNE = "rune"; U32 = "u32"; UINT32 = "uint32"; I64 = "i64"; INT64 = "int64"; INT = "int"; ISIZE = "isize"; U64 = "u64"; UINT64 = "uint64"; UINT = "uint"; USIZE = "usize"; UINTPTR = "uintptr"; F32 = "f32"; FLOAT32 = "float32"; F64 = "f64"; FLOAT64 = "float64"; BOOL = "bool"; STRING = "string"
    from_name = classmethod(lambda cls, name: name if isinstance(name, cls) else cls(name))
    def int_range(self) -> tuple[int, int] | None: return _INT_RANGES.get(self.value)
    def float_spec(self) -> tuple[int, int, Fraction] | None: return (24, -149, F32_MAX) if self.value in ("f32", "float32") else ((53, -1074, F64_MAX) if self.value in ("f64", "float64") else None)
    def go_name(self) -> str: return _GO_NAMES.get(self.value, self.value)
@dataclass(frozen=True)
class ComptimeDiagnostic: code: str; message: str; name: str | None = None
class ComptimeError(Exception):
    def __init__(self, code: str, message: str, name: str | None = None) -> None: super().__init__(f"{code}: {message}"); self.code, self.message, self.name = code, message, name
    def diagnostic(self, name: str | None = None) -> ComptimeDiagnostic: return ComptimeDiagnostic(self.code, self.message, name or self.name)
def _to_frac(v: Any) -> Fraction:
    if isinstance(v, Fraction) or not isinstance(v, str): return v if isinstance(v, Fraction) else Fraction(v)
    s = v.replace("_", "").strip()
    if not s.lower().startswith(("0x", "-0x", "+0x")):
        if ("e" in s or "E" in s) and abs(int(re.split(r"[eE]", s)[1])) > MAX_SHIFT_BITS: raise ComptimeError("HYB-E5001", f"float exponent in `{v}` exceeds limit")
        return Fraction(s)
    neg, body = s.startswith("-"), s.lstrip("+-")[2:]; mant, _, exp_s = body.partition("p") if "p" in body else body.partition("P"); exp = int(exp_s or "0")
    if abs(exp) > MAX_SHIFT_BITS: raise ComptimeError("HYB-E5001", f"hex float exponent {exp} exceeds limit")
    q = Fraction(int(mant.replace(".", ""), 16), 1 << (4 * (len(mant.split(".")[1]) if "." in mant else 0)))
    return -(q * (1 << exp) if exp >= 0 else q / (1 << -exp)) if neg else (q * (1 << exp) if exp >= 0 else q / (1 << -exp))
def _fmt_frac(q: Fraction) -> str:
    if q.denominator == 1: return f"{q.numerator}.0"
    d, p2, p5 = q.denominator, 0, 0
    while d % 2 == 0 and p2 < 30: d //= 2; p2 += 1
    while d % 5 == 0 and p5 < 30: d //= 5; p5 += 1
    if d != 1: return repr(float(q))
    k = max(p2, p5); s = str(abs(q.numerator) * (2 ** (k - p2)) * (5 ** (k - p5))).rjust(k + 1, "0")
    return ("-" if q.numerator < 0 else "") + f"{s[:-k]}.{s[-k:].rstrip('0') or '0'}"
def ieee754_round(val: Fraction, sig_bits: int, min_sub_exp: int) -> Fraction:
    if val == 0: return Fraction(0)
    sign, x = (-1 if val < 0 else 1), abs(val)
    e = x.numerator.bit_length() - x.denominator.bit_length() - (1 if (x.numerator < (x.denominator << max(0, x.numerator.bit_length() - x.denominator.bit_length())) if x.numerator.bit_length() >= x.denominator.bit_length() else (x.numerator << (x.denominator.bit_length() - x.numerator.bit_length())) < x.denominator) else 0)
    sh = max(e - (sig_bits - 1), min_sub_exp); q = round(x / (1 << sh) if sh >= 0 else x * (1 << -sh))
    return sign * (Fraction(q * (1 << sh), 1) if sh >= 0 else Fraction(q, 1 << -sh))
class ConstVal:
    scalar_type: ScalarType | None = None; val = property(lambda self: getattr(self, "value"))
    def to_go_literal(self) -> str: raise NotImplementedError
@dataclass(frozen=True)
class UntypedInt(ConstVal):
    value: int; kind: str = "int"
    def __post_init__(self) -> None: object.__setattr__(self, "value", int(self.value))
    def to_go_literal(self) -> str: return str(self.value)
@dataclass(frozen=True)
class UntypedFloat(ConstVal):
    value: Fraction; kind: str = "float"
    def __post_init__(self) -> None: object.__setattr__(self, "value", _to_frac(self.value))
    def to_go_literal(self) -> str: return _fmt_frac(self.value)
@dataclass(frozen=True)
class UntypedComplex(ConstVal):
    real: Fraction; imag: Fraction = Fraction(0); kind: str = "complex"
    def __post_init__(self) -> None: object.__setattr__(self, "real", _to_frac(self.real)); object.__setattr__(self, "imag", _to_frac(self.imag))
    value = property(lambda self: (self.real, self.imag))
    def to_go_literal(self) -> str: return f"({_fmt_frac(self.real)}{'+' if self.imag >= 0 else '-'}{_fmt_frac(abs(self.imag))}i)"
@dataclass(frozen=True)
class UntypedBool(ConstVal):
    value: bool; kind: str = "bool"
    def __post_init__(self) -> None: object.__setattr__(self, "value", bool(self.value))
    def to_go_literal(self) -> str: return "true" if self.value else "false"
@dataclass(frozen=True)
class UntypedString(ConstVal):
    value: str; kind: str = "str"
    def to_go_literal(self) -> str: return json.dumps(self.value)
@dataclass(frozen=True)
class TypedVal(ConstVal):
    ty: ScalarType; value: Any
    def __post_init__(self) -> None: t = ScalarType.from_name(self.ty); object.__setattr__(self, "ty", t); v = self.value.val if isinstance(self.value, ConstVal) else self.value; object.__setattr__(self, "value", _to_frac(v) if t.float_spec() is not None else v)
    kind = property(lambda self: "int" if self.ty.int_range() else ("float" if self.ty.float_spec() else ("bool" if self.ty == ScalarType.BOOL else "str"))); scalar_type = property(lambda self: self.ty)
    def to_go_literal(self) -> str: return (("true" if self.value else "false") if self.ty == ScalarType.BOOL else json.dumps(self.value)) if self.ty in (ScalarType.BOOL, ScalarType.STRING) else f"{self.ty.go_name()}({_fmt_frac(_to_frac(self.value)) if self.ty.float_spec() else self.value})"
@dataclass(frozen=True)
class ComptimeResult: values: dict[str, ConstVal]; emitted_go: dict[str, str]; diagnostics: list[ComptimeDiagnostic]; steps_used: int
_TOK_RE = re.compile(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|(\b0[xX][0-9a-fA-F_]*\.?[0-9a-fA-F_]*[pP][+-]?[0-9_]+)|(\b[0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9_]+)?i\b)|(\b[0-9][0-9_]*(?:\.[0-9_]+(?:[eE][+-]?[0-9_]+)?|[eE][+-]?[0-9_]+))|(\b(?:0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+|[0-9][0-9_]*)\b)')
def parse_int_literal(raw: str) -> int: s = raw.replace("_", "").strip(); return int(s, 16 if s[1] in "xX" else (8 if s[1] in "oO" else 2)) if s.lower().startswith(("0x", "0o", "0b")) else int(s, 8 if (len(s) > 1 and s.startswith("0") and all(c in "01234567" for c in s)) else 10)
class ComptimeEnv:
    def __init__(self, consts: Mapping[str, Any] | None = None, *, max_steps: int = 1024, wrap_sub64: bool = False) -> None:
        self.raw_decls: dict[str, tuple[Any, ScalarType | None]] = {}; self.cache: dict[str, ConstVal] = {}; self.max_steps, self.wrap_sub64, self.steps = max_steps, wrap_sub64, 0
        for k, v in (consts or {}).items(): self.define(k, *(v if isinstance(v, tuple) and len(v) == 2 else (v, None)))
    def define(self, name: str, expr: Any, ty: str | ScalarType | None = None) -> None: self.raw_decls[name] = (expr, ScalarType.from_name(ty) if ty else None); self.cache.pop(name, None)
    def _charge(self, cost: int = 1) -> None:
        self.steps += cost
        if self.steps > self.max_steps: raise ComptimeError("HYB-E5005", f"comptime gas budget exhausted (steps={self.steps} > max_steps={self.max_steps})")
    def convert(self, val: ConstVal, target: str | ScalarType, *, allow_wrap: bool | None = None) -> TypedVal:
        ty, wrap, k, v = ScalarType.from_name(target), (self.wrap_sub64 if allow_wrap is None else allow_wrap), val.kind, val.val
        if k == "complex" and ty not in (ScalarType.STRING, ScalarType.BOOL):
            if v[1] != 0: raise ComptimeError("HYB-E5003", f"complex {val.to_go_literal()} truncated to {ty.value}")
            k, v = "float", v[0]
        if ty.int_range() is not None:
            lo, hi = ty.int_range()  # type: ignore[misc]
            if k not in ("int", "float") or (k == "float" and _to_frac(v).denominator != 1): raise ComptimeError("HYB-E5003", f"constant {val.to_go_literal()} truncated to {ty.value}")
            iv = int(_to_frac(v))
            if iv < lo or iv > hi:
                bits = (hi - lo + 1).bit_length() - 1
                if wrap and bits < 64: m = iv & ((1 << bits) - 1); return TypedVal(ty, m - (1 << bits) if (lo < 0 and (m >> (bits - 1)) & 1) else m)
                raise ComptimeError("HYB-E5001", f"constant {iv} overflows {ty.value} [{lo}, {hi}]")
            return TypedVal(ty, iv)
        if ty.float_spec() is not None:
            sig_bits, min_exp, fmax = ty.float_spec()  # type: ignore[misc]
            if k not in ("int", "float"): raise ComptimeError("HYB-E5003", f"cannot convert {val} to {ty.value}")
            q = _to_frac(v)
            if abs(q) > fmax: raise ComptimeError("HYB-E5001", f"constant {_fmt_frac(q)} overflows {ty.value}")
            return TypedVal(ty, ieee754_round(q, sig_bits, min_exp))
        if ty == ScalarType.STRING and k in ("int", "str"):
            if k == "int" and abs(v) >= STRING_INT_LIMIT: raise ComptimeError("HYB-E5001", "integer >= 2^512 overflows string conversion")
            return TypedVal(ty, (chr(v) if (0 <= v <= 0x10FFFF and not (0xD800 <= v <= 0xDFFF)) else "\ufffd") if k == "int" else v)
        if ty == ScalarType.BOOL and k == "bool": return TypedVal(ty, bool(v))
        raise ComptimeError("HYB-E5003", f"invalid constant conversion to {ty.value}")
    def resolve(self, name: str, _visiting: list[str] | None = None) -> ConstVal:
        if name in self.cache: return self.cache[name]
        stack = _visiting if _visiting is not None else []
        if name in stack: raise ComptimeError("HYB-E5005", f"const dependency cycle: {' -> '.join(stack[stack.index(name):] + [name])}", name=name)
        if name not in self.raw_decls: raise ComptimeError("HYB-E6003", f"unresolved const `{name}`", name=name)
        expr, decl_ty = self.raw_decls[name]; stack.append(name)
        try: val = self.eval_expr(expr, ty=decl_ty, _visiting=stack); self.cache[name] = val; return val
        finally: stack.pop()
    def eval_expr(self, expr: str | ConstVal, ty: str | ScalarType | None = None, *, _visiting: list[str] | None = None) -> ConstVal:
        self._charge(1)
        if isinstance(expr, ConstVal): return self.convert(expr, ty) if ty else expr
        rewritten = _TOK_RE.sub(lambda m: m.group(1) or (f'_flt("{m.group(2) or m.group(4)}")' if (m.group(2) or m.group(4)) else (f'_imag("{m.group(3)[:-1]}")' if m.group(3) else f'_int("{m.group(5)}")')), expr.strip())
        try: body = ast.parse(rewritten, mode="eval").body
        except SyntaxError as exc: raise ComptimeError("HYB-E6003", f"invalid constant expression syntax: {expr}") from exc
        val = self._eval_node(body, _visiting or []); return self.convert(val, ty) if ty else val
    def _eval_node(self, node: ast.AST, visiting: list[str]) -> ConstVal:
        self._charge(1)
        if isinstance(node, ast.Constant): return UntypedBool(node.value) if isinstance(node.value, bool) else (UntypedString(node.value) if isinstance(node.value, str) else UntypedInt(int(node.value)))
        if isinstance(node, ast.Name): return UntypedBool(node.id in ("true", "True")) if node.id in ("true", "True", "false", "False") else self.resolve(node.id, visiting)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = node.func.id
            if fn in ("_int", "_flt", "_imag") and len(node.args) == 1 and isinstance(node.args[0], ast.Constant): s = str(node.args[0].value); return UntypedInt(parse_int_literal(s)) if fn == "_int" else (UntypedFloat(_to_frac(s)) if fn == "_flt" else UntypedComplex(Fraction(0), _to_frac(s)))
            args = [self._eval_node(a, visiting) for a in node.args]
            if fn == "len" and len(args) == 1 and args[0].kind == "str": return UntypedInt(len(args[0].val.encode("utf-8")))
            if fn == "complex" and len(args) == 2: return UntypedComplex(_to_frac(args[0].val), _to_frac(args[1].val))
            if fn in ("real", "imag") and len(args) == 1 and args[0].kind == "complex": return UntypedFloat(args[0].val[0 if fn == "real" else 1])
            if fn in ScalarType._value2member_map_ and len(args) == 1: return self.convert(args[0], ScalarType(fn))
            raise ComptimeError("HYB-E6003", f"call to `{fn}` is not a constant expression")
        if isinstance(node, ast.UnaryOp):
            opnd = self._eval_node(node.operand, visiting)
            if isinstance(node.op, ast.UAdd): return opnd
            if isinstance(node.op, ast.USub): res: ConstVal = UntypedComplex(-opnd.val[0], -opnd.val[1]) if opnd.kind == "complex" else (UntypedFloat(-_to_frac(opnd.val)) if opnd.kind == "float" else UntypedInt(-int(opnd.val))); return self.convert(res, opnd.scalar_type) if opnd.scalar_type else res
            if isinstance(node.op, ast.Invert):
                if opnd.kind != "int": raise ComptimeError("HYB-E5003", "bitwise complement requires integer operand")
                rng = opnd.scalar_type.int_range() if opnd.scalar_type else None
                return TypedVal(opnd.scalar_type, rng[1] ^ int(opnd.val)) if (opnd.scalar_type and rng and rng[0] == 0) else (self.convert(UntypedInt(~int(opnd.val)), opnd.scalar_type) if opnd.scalar_type else UntypedInt(~int(opnd.val)))
            if isinstance(node.op, ast.Not): return UntypedBool(not bool(opnd.val))
        if isinstance(node, ast.BinOp): return self._eval_binop(node.op, self._eval_node(node.left, visiting), self._eval_node(node.right, visiting))
        if isinstance(node, ast.Compare) and len(node.ops) == 1: lhs, rhs = self._eval_node(node.left, visiting), self._eval_node(node.comparators[0], visiting); return UntypedBool({ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}[type(node.ops[0])](_to_frac(lhs.val) if lhs.kind in ("int", "float") else lhs.val, _to_frac(rhs.val) if rhs.kind in ("int", "float") else rhs.val))
        if isinstance(node, ast.BoolOp): return UntypedBool((all if isinstance(node.op, ast.And) else any)(bool(self._eval_node(v, visiting).val) for v in node.values))
        raise ComptimeError("HYB-E6003", "unsupported constant expression syntax")
    def _eval_binop(self, op: ast.operator, lhs: ConstVal, rhs: ConstVal) -> ConstVal:
        tty = lhs.scalar_type or rhs.scalar_type
        if lhs.kind == "str" and rhs.kind == "str" and isinstance(op, ast.Add): return TypedVal(ScalarType.STRING, lhs.val + rhs.val) if tty == ScalarType.STRING else UntypedString(lhs.val + rhs.val)
        if isinstance(op, (ast.LShift, ast.RShift)):
            if lhs.kind not in ("int", "float") or rhs.kind not in ("int", "float") or _to_frac(lhs.val).denominator != 1 or _to_frac(rhs.val).denominator != 1: raise ComptimeError("HYB-E5004", "shift operand/count must be integer")
            li, ri = int(_to_frac(lhs.val)), int(_to_frac(rhs.val))
            if ri < 0: raise ComptimeError("HYB-E5004", f"negative shift count: {ri}")
            if ri > MAX_SHIFT_BITS or (isinstance(op, ast.LShift) and li != 0 and li.bit_length() + ri > MAX_SHIFT_BITS + 1): raise ComptimeError("HYB-E5001", f"shift count {ri} exceeds MAX_SHIFT_BITS ({MAX_SHIFT_BITS})")
            self._charge(ri // 64); res = UntypedInt((li << ri) if isinstance(op, ast.LShift) else (li >> ri)); return self.convert(res, lhs.scalar_type) if lhs.scalar_type else res
        if lhs.kind == "complex" or rhs.kind == "complex":
            (lr, li), (rr, ri) = (lhs.val if lhs.kind == "complex" else (_to_frac(lhs.val), Fraction(0))), (rhs.val if rhs.kind == "complex" else (_to_frac(rhs.val), Fraction(0)))
            if isinstance(op, (ast.Add, ast.Sub)): return UntypedComplex(lr + rr, li + ri) if isinstance(op, ast.Add) else UntypedComplex(lr - rr, li - ri)
            if isinstance(op, ast.Mult): return UntypedComplex(lr * rr - li * ri, lr * ri + li * rr)
            if isinstance(op, ast.Div):
                if rr == 0 and ri == 0: raise ComptimeError("HYB-E5002", "complex division by zero")
                d = rr * rr + ri * ri; return UntypedComplex((lr * rr + li * ri) / d, (li * rr - lr * ri) / d)
        if lhs.kind == "float" or rhs.kind == "float" or (tty and tty.float_spec() is not None):
            fl, fr = _to_frac(lhs.val), _to_frac(rhs.val); fops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
            if isinstance(op, ast.Div) and fr == 0: raise ComptimeError("HYB-E5002", "float division by zero")
            if type(op) not in fops: raise ComptimeError("HYB-E5003", "invalid float operator")
            return self.convert(UntypedFloat(fops[type(op)](fl, fr)), tty) if tty else UntypedFloat(fops[type(op)](fl, fr))
        il, ir = int(lhs.val), int(rhs.val); iops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor}
        if isinstance(op, (ast.Div, ast.FloorDiv, ast.Mod)):
            if ir == 0: raise ComptimeError("HYB-E5002", "division or modulo by zero")
            q = -(abs(il) // abs(ir)) if (il < 0) ^ (ir < 0) else (abs(il) // abs(ir)); iv = q if not isinstance(op, ast.Mod) else (il - q * ir)
        elif type(op) in iops: iv = iops[type(op)](il, ir)
        else: raise ComptimeError("HYB-E5003", f"unsupported operator {op}")
        return self.convert(UntypedInt(iv), tty) if tty else UntypedInt(iv)
    def resolve_all(self) -> ComptimeResult:
        vals, emitted, diags = {}, {}, []
        for name in self.raw_decls:
            try: v = self.resolve(name); vals[name] = v; emitted[name] = v.to_go_literal()
            except ComptimeError as err: diags.append(err.diagnostic(name))
        return ComptimeResult(vals, emitted, diags, self.steps)
def _run_self_check() -> dict[str, Any]:
    env = ComptimeEnv({"SHIFT": "1 << 200 >> 195", "EXACT_FLT": "1e30 + 1 - 1e30", "CMP": "1e30 + 1 - 1e30 == 1", "BYTE_OK": "u8(SHIFT)"}); res = env.resolve_all()
    assert res.values["SHIFT"] == UntypedInt(32) and res.emitted_go["SHIFT"] == "32" and res.values["EXACT_FLT"] == UntypedFloat(Fraction(1)) and res.values["CMP"] == UntypedBool(True) and res.values["BYTE_OK"] == TypedVal(ScalarType.U8, 32) and res.emitted_go["BYTE_OK"] == "uint8(32)" and env.eval_expr("f32((1 << 128) - (1 << 104))") == TypedVal(ScalarType.F32, F32_MAX) and env.eval_expr("f64((1 << 1024) - (1 << 971))") == TypedVal(ScalarType.F64, F64_MAX)
    codes = {d.name: d.code for d in ComptimeEnv({"OVF": "u8(300)", "DIV0": "100 / (1e30 - 1e30)", "TRUNC": "int(2.5)", "NEG_SH": "1 << -1", "CYC_A": "CYC_B + 1", "CYC_B": "CYC_A + 1"}).resolve_all().diagnostics}
    assert codes == {"OVF": "HYB-E5001", "DIV0": "HYB-E5002", "TRUNC": "HYB-E5003", "NEG_SH": "HYB-E5004", "CYC_A": "HYB-E5005", "CYC_B": "HYB-E5005"}, codes
    return {"status": "ok", "verified_cases": 11, "codes": codes}
if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true"); ap.add_argument("--json", action="store_true"); args = ap.parse_args(); out = _run_self_check()
    print(json.dumps(out) if args.json else f"W-059 (#1290) OK: {out['verified_cases']}/11 unified comptime scenarios verified."); sys.exit(0)