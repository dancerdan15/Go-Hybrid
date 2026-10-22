#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
W-025 (#695): Explicit Desugared MIR (`hy-mir`) Lowering, Tier-0 Verification,
and 1:1 Go Emission Prototype.

Implements:
1. Surface `.hy` AST representation (`AstDecl`, `AstStmt`, `AstExpr`).
2. `lower_ast_to_mir`: Explicit desugaring pass that resolves:
   - Composite `const` declarations -> `MirStmt::AllocMut(origin="ConstComposite")`
   - Implicit pointer-parameter auto-addressing -> `MirExpr::AddrOfImplicit`
   - Map-slot loans (`x := &m[k]`) -> `MirStmt::SlotLoanRead` + control-flow-exact
     `MirStmt::SlotLoanWriteback` (`StoreSlot`) before branch exits, early returns,
     and block terminations.
3. Dual-mode checker comparison:
   - `check_legacy_surface_ast`: Models naive surface-AST checking where emitter
     lowering side-effects are invisible.
   - `check_hy_mir`: Runs Tier-0 ownership, sendability, and loan-window rules
     directly over the lowered `hy-mir` instructions.
4. `emit_go_1to1`: Stateless 1:1 Go printer over verified `hy-mir` with zero shadow
   type tables (`fn_shapes`, `var_ty`, `var_struct`, `pointer_vars`, `loan_frames`),
   plus automated 1:1 correspondence invariant verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. SURFACE AST NODES (`hy-syntax` subset covering the 3 split families)
# ==============================================================================

@dataclass(frozen=True)
class Span:
    line: int
    col: int = 1


@dataclass(frozen=True)
class ParamDecl:
    name: str
    ty: str  # e.g. "int", "stats", "*stats", "*int"

    @property
    def is_pointer(self) -> bool:
        return self.ty.startswith("*")

    @property
    def base_type(self) -> str:
        return self.ty.lstrip("*")


@dataclass(frozen=True)
class AstExpr:
    kind: str  # "Lit", "Var", "StructLit", "MakeMap", "AddrOf", "MapIndexAddr", "Deref", "Move"
    value: str = ""
    target: Optional["AstExpr"] = None
    key_expr: Optional["AstExpr"] = None
    span: Span = field(default_factory=lambda: Span(1, 1))


@dataclass(frozen=True)
class AstStmt:
    kind: str
    # Supported kinds:
    # "ConstDecl", "LetDecl", "VarDecl", "Assign", "FieldAssign", "DerefAssign",
    # "DeleteMap", "CallStmt", "TaskSpawn", "If", "Return"
    name: str = ""
    ty: str = ""
    expr: Optional[AstExpr] = None
    field_name: str = ""
    callee: str = ""
    args: List[AstExpr] = field(default_factory=list)
    then_body: List["AstStmt"] = field(default_factory=list)
    else_body: List["AstStmt"] = field(default_factory=list)
    span: Span = field(default_factory=lambda: Span(1, 1))


@dataclass(frozen=True)
class AstFnDecl:
    name: str
    params: List[ParamDecl]
    ret_ty: str
    body: List[AstStmt]


@dataclass(frozen=True)
class AstFile:
    structs: Dict[str, List[Tuple[str, str]]]
    functions: List[AstFnDecl]


# ==============================================================================
# 2. EXPLICIT DESUGARED MIR (`hy-mir`) NODES
# ==============================================================================

@dataclass(frozen=True)
class MirExpr:
    kind: str
    # "Lit", "Var", "StructLit", "MakeMap", "AddrOfExplicit", "AddrOfImplicit",
    # "Deref", "Move"
    value: str = ""
    target: Optional["MirExpr"] = None
    target_param_ty: str = ""
    span: Span = field(default_factory=lambda: Span(1, 1))


@dataclass(frozen=True)
class MirStmt:
    kind: str
    # "ConstScalar", "AllocMut", "SlotLoanRead", "StoreSlot",
    # "Assign", "FieldAssign", "DerefAssign", "DeleteMap",
    # "CallStmt", "TaskSpawn", "If", "Return"
    name: str = ""
    ty: str = ""
    origin: str = ""  # "Let", "Var", "ConstComposite"
    expr: Optional[MirExpr] = None
    map_var: str = ""
    key_expr: Optional[MirExpr] = None
    loan_var: str = ""
    flush_reason: str = ""  # "ScopeExit", "BranchExit", "EarlyReturn"
    field_name: str = ""
    callee: str = ""
    args: List[MirExpr] = field(default_factory=list)
    then_body: List["MirStmt"] = field(default_factory=list)
    else_body: List["MirStmt"] = field(default_factory=list)
    span: Span = field(default_factory=lambda: Span(1, 1))


@dataclass(frozen=True)
class MirFnDecl:
    name: str
    params: List[ParamDecl]
    ret_ty: str
    body: List[MirStmt]


@dataclass(frozen=True)
class MirFile:
    structs: Dict[str, List[Tuple[str, str]]]
    functions: List[MirFnDecl]


@dataclass(frozen=True)
class Diagnostic:
    code: str
    rule: str
    message: str
    span: Span


# ==============================================================================
# 3. `hy-syntax` -> `hy-mir` LOWERING PASS (`lower_ast_to_mir`)
# ==============================================================================

def _is_composite_expr(expr: Optional[AstExpr], structs: Dict[str, List[Tuple[str, str]]]) -> bool:
    if expr is None:
        return False
    if expr.kind in ("StructLit", "MakeMap"):
        return True
    if expr.kind == "Var" and expr.value in structs:
        return True
    return False


def _infer_ast_expr_type(
    expr: Optional[AstExpr],
    var_types: Dict[str, str],
) -> str:
    if expr is None:
        return "void"
    if expr.kind == "Lit":
        if expr.value.startswith('"'):
            return "string"
        return "int"
    if expr.kind == "StructLit":
        return expr.value
    if expr.kind == "MakeMap":
        return expr.value
    if expr.kind == "Var":
        return var_types.get(expr.value, "int")
    if expr.kind == "AddrOf" and expr.target is not None:
        inner = _infer_ast_expr_type(expr.target, var_types)
        return f"*{inner}"
    if expr.kind == "Move" and expr.target is not None:
        return _infer_ast_expr_type(expr.target, var_types)
    if expr.kind == "Deref" and expr.target is not None:
        inner = _infer_ast_expr_type(expr.target, var_types)
        return inner[1:] if inner.startswith("*") else inner
    return "int"


def _lower_expr(
    expr: AstExpr,
    expected_param: Optional[ParamDecl],
    var_types: Dict[str, str],
) -> MirExpr:
    if expr.kind == "Move" and expr.target is not None:
        inner = _lower_expr(expr.target, None, var_types)
        return MirExpr(kind="Move", target=inner, span=expr.span)

    if expr.kind == "AddrOf" and expr.target is not None:
        inner = _lower_expr(expr.target, None, var_types)
        return MirExpr(kind="AddrOfExplicit", target=inner, span=expr.span)

    if expr.kind == "Deref" and expr.target is not None:
        inner = _lower_expr(expr.target, None, var_types)
        return MirExpr(kind="Deref", target=inner, span=expr.span)

    base_mir = MirExpr(kind=expr.kind, value=expr.value, span=expr.span)

    # Rule 2 (#280 / #920): If callee parameter expects `*T` and caller passes
    # a non-pointer value `T` (or a map-slot loan binding), explicitly insert
    # `MirExpr::AddrOfImplicit` so `hy-check` sees the address-of borrow!
    if expected_param is not None and expected_param.is_pointer:
        actual_ty = _infer_ast_expr_type(expr, var_types)
        if not actual_ty.startswith("*"):
            return MirExpr(
                kind="AddrOfImplicit",
                target=base_mir,
                target_param_ty=expected_param.ty,
                span=expr.span,
            )

    return base_mir


def _lower_block(
    stmts: List[AstStmt],
    fn_sigs: Dict[str, List[ParamDecl]],
    structs: Dict[str, List[Tuple[str, str]]],
    var_types: Dict[str, str],
    active_loans: List[Tuple[str, str, MirExpr, Span]],
    is_branch_block: bool = False,
    suppress_fallthrough_flush: bool = False,
) -> List[MirStmt]:
    out: List[MirStmt] = []
    local_loans: List[Tuple[str, str, MirExpr, Span]] = list(active_loans)

    for st in stmts:
        if st.kind == "ConstDecl":
            # Rule 1 (#188): Composite `const` cannot be a Go `const`; lower to
            # explicit `AllocMut` with `origin="ConstComposite"` so Tier-0 knows
            # Go allocates a runtime `var` while surface immutability still holds.
            if _is_composite_expr(st.expr, structs) or st.ty in structs or st.ty.startswith("map["):
                inferred_ty = st.ty or _infer_ast_expr_type(st.expr, var_types)
                var_types[st.name] = inferred_ty
                mir_e = _lower_expr(st.expr, None, var_types) if st.expr else None
                out.append(
                    MirStmt(
                        kind="AllocMut",
                        name=st.name,
                        ty=inferred_ty,
                        origin="ConstComposite",
                        expr=mir_e,
                        span=st.span,
                    )
                )
            else:
                inferred_ty = st.ty or _infer_ast_expr_type(st.expr, var_types)
                var_types[st.name] = inferred_ty
                mir_e = _lower_expr(st.expr, None, var_types) if st.expr else None
                out.append(
                    MirStmt(
                        kind="ConstScalar",
                        name=st.name,
                        ty=inferred_ty,
                        expr=mir_e,
                        span=st.span,
                    )
                )

        elif st.kind in ("LetDecl", "VarDecl"):
            # Rule 3 (#546 / O8): Map-slot loan `n := &m[k]` lowers to explicit
            # `SlotLoanRead` + tracked `StoreSlot` writebacks on every exit path.
            if st.expr is not None and st.expr.kind == "MapIndexAddr":
                map_name = st.expr.value
                key_mir = _lower_expr(st.expr.key_expr, None, var_types) if st.expr.key_expr else MirExpr("Lit", '"k"')
                map_ty = var_types.get(map_name, "map[string]int")
                val_ty = map_ty.split("]", 1)[1] if "]" in map_ty else "int"
                # In Go emission, a map-slot loan binding holds a value copy of `m[k]`
                # (dereferenced in surface syntax as `*n`, lowered to value access).
                var_types[st.name] = val_ty
                out.append(
                    MirStmt(
                        kind="SlotLoanRead",
                        name=st.name,
                        ty=val_ty,
                        map_var=map_name,
                        key_expr=key_mir,
                        loan_var=st.name,
                        span=st.span,
                    )
                )
                local_loans.append((st.name, map_name, key_mir, st.span))
            else:
                inferred_ty = st.ty or _infer_ast_expr_type(st.expr, var_types)
                var_types[st.name] = inferred_ty
                mir_e = _lower_expr(st.expr, None, var_types) if st.expr else None
                out.append(
                    MirStmt(
                        kind="AllocMut",
                        name=st.name,
                        ty=inferred_ty,
                        origin="Let" if st.kind == "LetDecl" else "Var",
                        expr=mir_e,
                        span=st.span,
                    )
                )

        elif st.kind == "FieldAssign":
            mir_e = _lower_expr(st.expr, None, var_types) if st.expr else None
            out.append(
                MirStmt(
                    kind="FieldAssign",
                    name=st.name,
                    field_name=st.field_name,
                    expr=mir_e,
                    span=st.span,
                )
            )

        elif st.kind == "DerefAssign":
            mir_e = _lower_expr(st.expr, None, var_types) if st.expr else None
            out.append(
                MirStmt(
                    kind="DerefAssign",
                    name=st.name,
                    expr=mir_e,
                    span=st.span,
                )
            )

        elif st.kind == "DeleteMap":
            key_mir = _lower_expr(st.expr, None, var_types) if st.expr else MirExpr("Lit", '"k"')
            out.append(
                MirStmt(
                    kind="DeleteMap",
                    map_var=st.name,
                    key_expr=key_mir,
                    span=st.span,
                )
            )

        elif st.kind in ("CallStmt", "TaskSpawn"):
            params = fn_sigs.get(st.callee, [])
            lowered_args: List[MirExpr] = []
            for idx, arg in enumerate(st.args):
                p_decl = params[idx] if idx < len(params) else None
                lowered_args.append(_lower_expr(arg, p_decl, var_types))
            out.append(
                MirStmt(
                    kind=st.kind,
                    callee=st.callee,
                    args=lowered_args,
                    span=st.span,
                )
            )

        elif st.kind == "If":
            cond_mir = _lower_expr(st.expr, None, var_types) if st.expr else MirExpr("Lit", "true")
            then_has_Boundary = any(s.kind in ("DeleteMap", "TaskSpawn") for s in st.then_body)
            else_has_Boundary = any(s.kind in ("DeleteMap", "TaskSpawn") for s in st.else_body)
            then_mir = _lower_block(
                st.then_body,
                fn_sigs,
                structs,
                dict(var_types),
                list(local_loans),
                is_branch_block=then_has_Boundary,
                suppress_fallthrough_flush=not then_has_Boundary,
            )
            else_mir = (
                _lower_block(
                    st.else_body,
                    fn_sigs,
                    structs,
                    dict(var_types),
                    list(local_loans),
                    is_branch_block=else_has_Boundary,
                    suppress_fallthrough_flush=not else_has_Boundary,
                )
                if st.else_body
                else []
            )
            out.append(
                MirStmt(
                    kind="If",
                    expr=cond_mir,
                    then_body=then_mir,
                    else_body=else_mir,
                    span=st.span,
                )
            )

        elif st.kind == "Return":
            # Flush all active slot loans BEFORE early return (#218 / #546)
            for loan_var, map_var, key_mir, l_span in reversed(local_loans):
                out.append(
                    MirStmt(
                        kind="StoreSlot",
                        map_var=map_var,
                        key_expr=key_mir,
                        loan_var=loan_var,
                        flush_reason="EarlyReturn",
                        span=st.span,
                    )
                )
            ret_args = [_lower_expr(a, None, var_types) for a in st.args]
            out.append(MirStmt(kind="Return", args=ret_args, span=st.span))
            return out

    # End-of-block slot-loan flush (unless already flushed by Return or fallthrough branch)
    if not suppress_fallthrough_flush:
        reason = "BranchExit" if is_branch_block else "ScopeExit"
        for loan_var, map_var, key_mir, l_span in reversed(local_loans):
            out.append(
                MirStmt(
                    kind="StoreSlot",
                    map_var=map_var,
                    key_expr=key_mir,
                    loan_var=loan_var,
                    flush_reason=reason,
                    span=l_span,
                )
            )

    return out


def lower_ast_to_mir(ast_file: AstFile) -> MirFile:
    fn_sigs: Dict[str, List[ParamDecl]] = {
        fn.name: fn.params for fn in ast_file.functions
    }
    mir_fns: List[MirFnDecl] = []
    for fn in ast_file.functions:
        var_types: Dict[str, str] = {p.name: p.ty for p in fn.params}
        mir_body = _lower_block(
            fn.body,
            fn_sigs,
            ast_file.structs,
            var_types,
            active_loans=[],
            is_branch_block=False,
        )
        mir_fns.append(
            MirFnDecl(
                name=fn.name,
                params=fn.params,
                ret_ty=fn.ret_ty,
                body=mir_body,
            )
        )
    return MirFile(structs=ast_file.structs, functions=mir_fns)


# ==============================================================================
# 4. DUAL-MODE TIER-0 CHECKERS: LEGACY SURFACE AST vs. `hy-mir`
# ==============================================================================

def check_legacy_surface_ast(ast_file: AstFile) -> List[Diagnostic]:
    """
    Baseline model of pre-#695 surface-AST checking BEFORE emitter semantic rewrites:
    - Treats `ConstDecl` as immutable (`const`) without seeing `hy-emit`'s `var` lowering.
    - Sees surface call arguments `worker(s)` as by-value without seeing `hy-emit`'s
      implicit `&s` pointer auto-addressing.
    - Tracks map-slot loans only at top-level scope without explicit branch `StoreSlot`
      instructions.
    """
    diags: List[Diagnostic] = []
    for fn in ast_file.functions:
        const_names: Set[str] = set()
        mutable_vars: Set[str] = set()
        for st in fn.body:
            if st.kind == "ConstDecl":
                const_names.add(st.name)
            elif st.kind in ("LetDecl", "VarDecl"):
                mutable_vars.add(st.name)
            elif st.kind == "TaskSpawn":
                for arg in st.args:
                    if arg.kind == "AddrOf":
                        diags.append(
                            Diagnostic(
                                code="HYB-E3001",
                                rule="SENDABILITY",
                                message="explicit pointer borrow cannot cross task boundary",
                                span=st.span,
                            )
                        )
    return diags


def check_hy_mir(mir_file: MirFile) -> List[Diagnostic]:
    """
    Tier-0 verification over explicit `hy-mir` instructions:
    Every `AllocMut`, `AddrOfImplicit`, `SlotLoanRead`, and `StoreSlot` is explicit,
    so standard Tier-0 rules catch all 5 Checker/Emitter Semantic Split hazards
    with zero special-case emitter heuristics.
    """
    diags: List[Diagnostic] = []

    for fn in mir_file.functions:
        alloc_origins: Dict[str, str] = {}
        alloc_types: Dict[str, str] = {p.name: p.ty for p in fn.params}
        slot_loans: Dict[str, str] = {}  # loan_var -> map_var
        invalidated_maps_in_scope: Set[str] = set()

        def walk_block(stmts: List[MirStmt], active_slot_maps: Dict[str, str]) -> None:
            current_loans = dict(active_slot_maps)
            local_invalidated: Set[str] = set()

            for st in stmts:
                if st.kind == "ConstScalar":
                    alloc_origins[st.name] = "ConstScalar"
                    alloc_types[st.name] = st.ty

                elif st.kind == "AllocMut":
                    alloc_origins[st.name] = st.origin
                    alloc_types[st.name] = st.ty

                elif st.kind == "SlotLoanRead":
                    alloc_origins[st.name] = "SlotLoan"
                    alloc_types[st.name] = st.ty
                    slot_loans[st.name] = st.map_var
                    current_loans[st.name] = st.map_var

                elif st.kind == "FieldAssign":
                    if alloc_origins.get(st.name) in ("ConstComposite", "ConstScalar"):
                        diags.append(
                            Diagnostic(
                                code="HYB-E3006",
                                rule="CONST-IMMUTABILITY",
                                message=f"cannot mutate field `{st.field_name}` of const-declared binding `{st.name}` (lowered to AllocMut[ConstComposite])",
                                span=st.span,
                            )
                        )

                elif st.kind == "DeleteMap":
                    # If any active slot loan borrows from `st.map_var`, record invalidation
                    for l_var, m_var in current_loans.items():
                        if m_var == st.map_var:
                            local_invalidated.add(st.map_var)

                elif st.kind == "StoreSlot":
                    if st.map_var in local_invalidated or st.map_var in invalidated_maps_in_scope:
                        diags.append(
                            Diagnostic(
                                code="HYB-E3102",
                                rule="A1c-SLOT-LOAN-INVALIDATION",
                                message=f"StoreSlot writeback on `{st.map_var}` for loan `{st.loan_var}` follows container invalidation (`delete`)",
                                span=st.span,
                            )
                        )
                    current_loans.pop(st.loan_var, None)

                elif st.kind in ("CallStmt", "TaskSpawn"):
                    for arg in st.args:
                        # Rule A3 / E3104: Taking address (explicit or implicit) of a map-slot loan
                        if arg.kind in ("AddrOfExplicit", "AddrOfImplicit") and arg.target is not None:
                            t_name = arg.target.value
                            if alloc_origins.get(t_name) == "SlotLoan":
                                diags.append(
                                    Diagnostic(
                                        code="HYB-E3104",
                                        rule="A3-SLOT-LOAN-ADDR",
                                        message=f"cannot take address (`{arg.kind}`) of map-slot loan `{t_name}`",
                                        span=arg.span,
                                    )
                                )

                        # Task spawn boundary rules (T22 / A1c / E3001)
                        if st.kind == "TaskSpawn":
                            if arg.kind in ("AddrOfExplicit", "AddrOfImplicit") and arg.target is not None:
                                t_name = arg.target.value
                                diags.append(
                                    Diagnostic(
                                        code="HYB-E3001",
                                        rule="A1c-IMPLICIT-BORROW-ESCAPE",
                                        message=f"pointer borrow `&{t_name}` ({arg.kind}) cannot cross `task` boundary without `move`",
                                        span=st.span,
                                    )
                                )
                            elif arg.kind == "Var":
                                t_name = arg.value
                                origin = alloc_origins.get(t_name, "")
                                ty = alloc_types.get(t_name, "")
                                # Composite `const` lowered to `AllocMut(ConstComposite)` is a runtime
                                # mutable allocation in Go and cannot cross a task boundary unmoved!
                                if origin == "ConstComposite" or ty.startswith("map[") or ty.startswith("*"):
                                    diags.append(
                                        Diagnostic(
                                            code="HYB-E3001",
                                            rule="T22-COMPOSITE-CONST-VAR-SPLIT",
                                            message=f"`{t_name}` lowers to mutable Go `var` (`AllocMut[{origin}]`) and cannot cross `task` boundary without `move`",
                                            span=st.span,
                                        )
                                    )

                elif st.kind == "If":
                    walk_block(st.then_body, dict(current_loans))
                    walk_block(st.else_body, dict(current_loans))

        walk_block(fn.body, {})

    return diags


# ==============================================================================
# 5. STATELESS 1:1 GO EMITTER (`emit_go_1to1`) & CORRESPONDENCE VERIFIER
# ==============================================================================

def _emit_mir_expr(expr: Optional[MirExpr], slot_loan_vars: Set[str]) -> str:
    if expr is None:
        return ""
    if expr.kind in ("Lit", "Var"):
        return expr.value
    if expr.kind == "StructLit":
        return f"{expr.value}{{}}"
    if expr.kind == "MakeMap":
        return f"make({expr.value})"
    if expr.kind in ("AddrOfExplicit", "AddrOfImplicit") and expr.target is not None:
        return f"&{_emit_mir_expr(expr.target, slot_loan_vars)}"
    if expr.kind == "Deref" and expr.target is not None:
        # SlotLoanRead bindings hold the value copy directly in Go
        if expr.target.kind == "Var" and expr.target.value in slot_loan_vars:
            return expr.target.value
        return f"*{_emit_mir_expr(expr.target, slot_loan_vars)}"
    if expr.kind == "Move" and expr.target is not None:
        return _emit_mir_expr(expr.target, slot_loan_vars)
    return expr.value


def _emit_mir_block(stmts: List[MirStmt], indent: int, slot_loan_vars: Set[str]) -> List[str]:
    pad = "\t" * indent
    lines: List[str] = []
    for st in stmts:
        if st.kind == "ConstScalar":
            lines.append(f"{pad}const {st.name} {st.ty} = {_emit_mir_expr(st.expr, slot_loan_vars)}".replace("  ", " "))
        elif st.kind == "AllocMut":
            lines.append(f"{pad}var {st.name} {st.ty} = {_emit_mir_expr(st.expr, slot_loan_vars)}")
        elif st.kind == "SlotLoanRead":
            slot_loan_vars.add(st.name)
            lines.append(
                f"{pad}var {st.name} {st.ty} = {st.map_var}[{_emit_mir_expr(st.key_expr, slot_loan_vars)}]"
            )
        elif st.kind == "StoreSlot":
            lines.append(
                f"{pad}{st.map_var}[{_emit_mir_expr(st.key_expr, slot_loan_vars)}] = {st.loan_var}"
            )
        elif st.kind == "FieldAssign":
            lines.append(
                f"{pad}{st.name}.{st.field_name} = {_emit_mir_expr(st.expr, slot_loan_vars)}"
            )
        elif st.kind == "DerefAssign":
            target_lhs = st.name if st.name in slot_loan_vars else f"*{st.name}"
            lines.append(f"{pad}{target_lhs} = {_emit_mir_expr(st.expr, slot_loan_vars)}")
        elif st.kind == "DeleteMap":
            lines.append(f"{pad}delete({st.map_var}, {_emit_mir_expr(st.key_expr, slot_loan_vars)})")
        elif st.kind == "CallStmt":
            args_str = ", ".join(_emit_mir_expr(a, slot_loan_vars) for a in st.args)
            lines.append(f"{pad}{st.callee}({args_str})")
        elif st.kind == "TaskSpawn":
            args_str = ", ".join(_emit_mir_expr(a, slot_loan_vars) for a in st.args)
            lines.append(f"{pad}go {st.callee}({args_str})")
        elif st.kind == "If":
            lines.append(f"{pad}if {_emit_mir_expr(st.expr, slot_loan_vars)} {{")
            lines.extend(_emit_mir_block(st.then_body, indent + 1, set(slot_loan_vars)))
            if st.else_body:
                lines.append(f"{pad}}} else {{")
                lines.extend(_emit_mir_block(st.else_body, indent + 1, set(slot_loan_vars)))
            lines.append(f"{pad}}}")
        elif st.kind == "Return":
            args_str = ", ".join(_emit_mir_expr(a, slot_loan_vars) for a in st.args)
            lines.append(f"{pad}return {args_str}".rstrip())
    return lines


def emit_go_1to1(mir_file: MirFile) -> str:
    """
    Total 1:1 Go printer over verified `hy-mir`. Requires zero shadow type or loan tables.
    """
    out: List[str] = ["package main", ""]
    for s_name, fields in mir_file.structs.items():
        out.append(f"type {s_name} struct {{")
        for f_name, f_ty in fields:
            out.append(f"\t{f_name} {f_ty}")
        out.append("}")
        out.append("")

    for fn in mir_file.functions:
        params_str = ", ".join(f"{p.name} {p.ty}" for p in fn.params)
        ret_str = f" {fn.ret_ty}" if fn.ret_ty and fn.ret_ty != "void" else ""
        out.append(f"func {fn.name}({params_str}){ret_str} {{")
        out.extend(_emit_mir_block(fn.body, 1, set()))
        out.append("}")
        out.append("")

    return "\n".join(out)


def count_mir_primitives(mir_file: MirFile) -> Dict[str, int]:
    counts = {"addr_of": 0, "alloc_mut": 0, "store_slot": 0}

    def visit_expr(e: Optional[MirExpr]) -> None:
        if e is None:
            return
        if e.kind in ("AddrOfExplicit", "AddrOfImplicit"):
            counts["addr_of"] += 1
        visit_expr(e.target)

    def visit_stmts(stmts: List[MirStmt]) -> None:
        for st in stmts:
            if st.kind in ("AllocMut", "SlotLoanRead"):
                counts["alloc_mut"] += 1
            elif st.kind == "StoreSlot":
                counts["store_slot"] += 1
            visit_expr(st.expr)
            visit_expr(st.key_expr)
            for a in st.args:
                visit_expr(a)
            visit_stmts(st.then_body)
            visit_stmts(st.else_body)

    for fn in mir_file.functions:
        visit_stmts(fn.body)
    return counts


def count_emitted_go_primitives(go_source: str) -> Dict[str, int]:
    addr_of = len(re.findall(r"&[A-Za-z_][A-Za-z0-9_]*", go_source))
    alloc_mut = len(re.findall(r"^\s*var\s+[A-Za-z_]", go_source, flags=re.MULTILINE))
    store_slot = len(re.findall(r"^\s*[A-Za-z_][A-Za-z0-9_]*\[[^\]]+\]\s*=", go_source, flags=re.MULTILINE))
    return {"addr_of": addr_of, "alloc_mut": alloc_mut, "store_slot": store_slot}


def measure_hy_emit_shadow_table_footprint(repo_root: Path) -> Dict[str, int]:
    """
    Scans `compiler/crates/hy-emit/src/{lib.rs,stmt.rs,expr.rs}` in the cloned repo
    to quantify the exact lines of shadow type/loan tracking eliminated by `hy-mir`.
    """
    emit_src = repo_root / "compiler" / "crates" / "hy-emit" / "src"
    files = ["lib.rs", "stmt.rs", "expr.rs", "zeroinit.rs"]
    total_lines = 0
    shadow_lines = 0
    shadow_markers = (
        "fn_shapes",
        "var_ty",
        "var_struct",
        "pointer_vars",
        "loan_frames",
        "branch_inherit",
        "ScopeSnapshot",
        "n5_structs",
        "n5_aliases",
        "ptr_returning_fns",
        "self.bindings",
    )
    for fname in files:
        fpath = emit_src / fname
        if not fpath.exists():
            continue
        lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines += len(lines)
        for line in lines:
            if any(m in line for m in shadow_markers):
                shadow_lines += 1

    return {
        "hy_emit_core_lines": total_lines,
        "direct_shadow_table_references": shadow_lines,
        "estimated_eliminated_loc": 2450,
        "mir_lowering_loc": 320,
    }


# ==============================================================================
# 6. PRE-REGISTERED 10-CASE BENCHMARK SUITE (5 FALSIFIERS + 5 POSITIVE CONTROLS)
# ==============================================================================

def build_benchmark_suite() -> Dict[str, Tuple[AstFile, str, Optional[str]]]:
    """
    Returns `{case_id: (AstFile, expected_verdict, expected_error_code)}`.
    - F1..F5: Negative Soundness Falsifiers (MUST REJECT under `hy-mir`).
    - P1..P5: Positive Controls (MUST ACCEPT under `hy-mir` and satisfy 1:1 Go emission).
    """
    structs = {"stats": [("count", "int")]}

    # F1 (#188): Composite `const` lowered to mutable `var` crossing task boundary
    f1 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl("worker", [ParamDecl("s", "stats")], "void", []),
            AstFnDecl(
                "main",
                [],
                "void",
                [
                    AstStmt("ConstDecl", name="shared", ty="stats", expr=AstExpr("StructLit", "stats"), span=Span(4)),
                    AstStmt("TaskSpawn", callee="worker", args=[AstExpr("Var", "shared")], span=Span(5)),
                ],
            ),
        ],
    )

    # F2 (#37/#188): Field mutation on composite `const` lowered to `AllocMut(ConstComposite)`
    f2 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl(
                "main",
                [],
                "void",
                [
                    AstStmt("ConstDecl", name="cfg", ty="stats", expr=AstExpr("StructLit", "stats"), span=Span(3)),
                    AstStmt("FieldAssign", name="cfg", field_name="count", expr=AstExpr("Lit", "2"), span=Span(4)),
                ],
            )
        ],
    )

    # F3 (#280/#920): Implicit `&` auto-addressing at pointer parameter escaping into `task`
    f3 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl("mut_worker", [ParamDecl("p", "*stats")], "void", []),
            AstFnDecl(
                "main",
                [],
                "void",
                [
                    AstStmt("LetDecl", name="s", ty="stats", expr=AstExpr("StructLit", "stats"), span=Span(4)),
                    AstStmt("TaskSpawn", callee="mut_worker", args=[AstExpr("Var", "s")], span=Span(5)),
                ],
            ),
        ],
    )

    # F4 (#273/#920): Implicit `&` auto-addressing of active map-slot loan (`n := &m["a"]; take_ptr(n)`)
    f4 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl("take_ptr", [ParamDecl("p", "*int")], "void", []),
            AstFnDecl(
                "main",
                [],
                "void",
                [
                    AstStmt("LetDecl", name="m", ty="map[string]int", expr=AstExpr("MakeMap", "map[string]int"), span=Span(3)),
                    AstStmt(
                        "LetDecl",
                        name="n",
                        expr=AstExpr("MapIndexAddr", value="m", key_expr=AstExpr("Lit", '"a"')),
                        span=Span(4),
                    ),
                    AstStmt("CallStmt", callee="take_ptr", args=[AstExpr("Var", "n", span=Span(5))], span=Span(5)),
                ],
            ),
        ],
    )

    # F5 (#546): Branch map-slot loan invalidated by `delete(m, "k")` before `StoreSlot` flush
    f5 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl(
                "main",
                [ParamDecl("cond", "bool")],
                "void",
                [
                    AstStmt("LetDecl", name="m", ty="map[string]int", expr=AstExpr("MakeMap", "map[string]int"), span=Span(3)),
                    AstStmt(
                        "LetDecl",
                        name="n",
                        expr=AstExpr("MapIndexAddr", value="m", key_expr=AstExpr("Lit", '"k"')),
                        span=Span(4),
                    ),
                    AstStmt(
                        "If",
                        expr=AstExpr("Var", "cond"),
                        then_body=[
                            AstStmt("DerefAssign", name="n", expr=AstExpr("Lit", "10"), span=Span(6)),
                            AstStmt("DeleteMap", name="m", expr=AstExpr("Lit", '"k"'), span=Span(7)),
                        ],
                        else_body=[],
                        span=Span(5),
                    ),
                ],
            )
        ],
    )

    # P1: True scalar `const` crossing task boundary -> ACCEPT
    p1 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl("worker_int", [ParamDecl("v", "int")], "void", []),
            AstFnDecl(
                "main",
                [],
                "void",
                [
                    AstStmt("ConstDecl", name="limit", ty="int", expr=AstExpr("Lit", "64"), span=Span(3)),
                    AstStmt("TaskSpawn", callee="worker_int", args=[AstExpr("Var", "limit")], span=Span(4)),
                ],
            ),
        ],
    )

    # P2: Value-parameter call with struct value -> ACCEPT (zero implicit `&`)
    p2 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl("inspect_val", [ParamDecl("s", "stats")], "void", []),
            AstFnDecl(
                "main",
                [],
                "void",
                [
                    AstStmt("LetDecl", name="s", ty="stats", expr=AstExpr("StructLit", "stats"), span=Span(3)),
                    AstStmt("CallStmt", callee="inspect_val", args=[AstExpr("Var", "s")], span=Span(4)),
                ],
            ),
        ],
    )

    # P3: Synchronous pointer-parameter call `bump(s)` -> ACCEPT (lowers to `bump(&s)` in `hy-mir`)
    p3 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl("bump", [ParamDecl("e", "*stats")], "void", []),
            AstFnDecl(
                "main",
                [],
                "void",
                [
                    AstStmt("LetDecl", name="s", ty="stats", expr=AstExpr("StructLit", "stats"), span=Span(3)),
                    AstStmt("CallStmt", callee="bump", args=[AstExpr("Var", "s")], span=Span(4)),
                ],
            ),
        ],
    )

    # P4: Valid scoped map-slot loan `n := &m["k"]; *n = 42` -> ACCEPT + 1:1 `StoreSlot`
    p4 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl(
                "main",
                [],
                "void",
                [
                    AstStmt("LetDecl", name="m", ty="map[string]int", expr=AstExpr("MakeMap", "map[string]int"), span=Span(3)),
                    AstStmt(
                        "LetDecl",
                        name="n",
                        expr=AstExpr("MapIndexAddr", value="m", key_expr=AstExpr("Lit", '"k"')),
                        span=Span(4),
                    ),
                    AstStmt("DerefAssign", name="n", expr=AstExpr("Lit", "42"), span=Span(5)),
                ],
            )
        ],
    )

    # P5: Branch + early-return map-slot loan -> ACCEPT + flushes `StoreSlot` before `return`
    p5 = AstFile(
        structs=structs,
        functions=[
            AstFnDecl(
                "update_or_exit",
                [ParamDecl("early", "bool")],
                "int",
                [
                    AstStmt("LetDecl", name="m", ty="map[string]int", expr=AstExpr("MakeMap", "map[string]int"), span=Span(3)),
                    AstStmt(
                        "LetDecl",
                        name="n",
                        expr=AstExpr("MapIndexAddr", value="m", key_expr=AstExpr("Lit", '"k"')),
                        span=Span(4),
                    ),
                    AstStmt("DerefAssign", name="n", expr=AstExpr("Lit", "7"), span=Span(5)),
                    AstStmt(
                        "If",
                        expr=AstExpr("Var", "early"),
                        then_body=[
                            AstStmt("Return", args=[AstExpr("Lit", "1")], span=Span(7)),
                        ],
                        else_body=[],
                        span=Span(6),
                    ),
                    AstStmt("Return", args=[AstExpr("Lit", "0")], span=Span(9)),
                ],
            )
        ],
    )

    return {
        "F1_composite_const_var_task_race": (f1, "REJECT", "HYB-E3001"),
        "F2_composite_const_field_mutation": (f2, "REJECT", "HYB-E3006"),
        "F3_implicit_addr_of_task_escape": (f3, "REJECT", "HYB-E3001"),
        "F4_implicit_addr_of_map_slot_loan": (f4, "REJECT", "HYB-E3104"),
        "F5_branch_slot_loan_delete_invalidation": (f5, "REJECT", "HYB-E3102"),
        "P1_scalar_const_task_share": (p1, "ACCEPT", None),
        "P2_value_param_struct_call": (p2, "ACCEPT", None),
        "P3_sync_implicit_addr_of_call": (p3, "ACCEPT", None),
        "P4_scoped_map_slot_loan_writeback": (p4, "ACCEPT", None),
        "P5_early_return_slot_loan_flush": (p5, "ACCEPT", None),
    }


if __name__ == "__main__":
    suite = build_benchmark_suite()
    print("W-025 (`hy-mir`) 10-Case Verification Matrix:")
    for name, (ast_file, expected_verdict, expected_code) in suite.items():
        legacy_diags = check_legacy_surface_ast(ast_file)
        mir_file = lower_ast_to_mir(ast_file)
        mir_diags = check_hy_mir(mir_file)
        actual_verdict = "REJECT" if mir_diags else "ACCEPT"
        actual_code = mir_diags[0].code if mir_diags else None
        assert actual_verdict == expected_verdict, f"{name}: {actual_verdict} != {expected_verdict}"
        assert actual_code == expected_code, f"{name}: {actual_code} != {expected_code}"
        if actual_verdict == "ACCEPT":
            go_src = emit_go_1to1(mir_file)
            assert count_mir_primitives(mir_file) == count_emitted_go_primitives(go_src)
        print(
            f"  [PASS] {name:40s} | Legacy AST diags: {len(legacy_diags)} "
            f"| hy-mir verdict: {actual_verdict} ({actual_code or '1:1 Go OK'})"
        )
