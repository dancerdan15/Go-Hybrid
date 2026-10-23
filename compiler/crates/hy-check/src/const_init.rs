//! const_init.rs — `const` initializers that are not constant expressions
//! (issue #205).
//!
//! `const c = get_val()` passes `hyb check` with 0 diagnostics today, then
//! fails `go build` with `get_val() (value of type int) is not constant`.
//! The emitter keeps the Go `const` keyword for every non-composite
//! initializer (`hy-emit` stmt.rs), so the accepted-should-emit-buildable-Go
//! contract requires a checker diagnostic (HYB-E6003).
//!
//! Composite literals (`[]T{…}`, maps, structs) already lower to Go `var`
//! and are intentionally out of scope here — their runtime-mutable /
//! sendability story is issue #188.

use crate::model::{unwrap_parens, Checker, FnState};
use hy_diag::{Diagnostic, Pos, Span};
use hy_syntax::{
    BinOp, Block, Decl, Expr, ForKind, LitKind, LocalKind, MatchPattern, SelectArmKind, Stmt,
    TypeExpr, UnOp,
};

type LocalConst<'a> = Option<(&'a Expr, Option<&'a TypeExpr>)>;

impl<'a> Checker<'a> {
    /// When `value` would be emitted as a Go `const` initializer but is not
    /// a constant expression, emit HYB-E6003.
    pub(crate) fn check_const_init_is_constant(
        &mut self,
        name: &str,
        value: &Expr,
        st: Option<&FnState>,
    ) {
        if is_composite_lit(value) {
            // Emitter rewrites these to `var` — not the #205 contract break.
            return;
        }
        let decl_ty = if st.is_none() {
            self.file.decls.iter().find_map(|d| match d { Decl::Const(c) if c.name == name && c.value.span() == value.span() => c.ty.as_ref(), _ => None })
        } else {
            self.resolve_local_const(name, value.span().start, Some(value.span())).flatten().and_then(|(_, ty)| ty)
        };
        let mut visiting = Vec::new();
        if st.is_none() {
            visiting.push(name.to_string());
        }
        if decl_ty.is_none_or(|ty| self.is_const_type(ty, false)) && self.expr_is_constant_inner(value, st, &mut visiting, 0) {
            return;
        }
        let span = value.span();
        let msg = format!(
            "const `{}` initializer `{}` is not a constant expression (HYB-E6003)",
            name,
            value.render()
        );
        let notes = vec![
            "why: Go rejects a non-constant `const` initializer (`… is not constant`), so an accepted Hybrid program would emit unbuildable Go (issue #205)".to_string(),
            "fix: use a literal or constant expression (`const c = 42`, `const c = 1 + 2`), reference another `const`, or bind a run-time value with `:=` / `let` / `var`".to_string(),
            format!("note: declared here ({})", span_hint(span)),
        ];
        self.diags.push(
            Diagnostic::error("HYB-E6003", self.path.clone(), span, msg).with_notes(notes),
        );
    }

    /// Compile-time constant expression under the narrow MVP rules that
    /// match what Go's `const` accepts for the shapes Hybrid emits.
    fn expr_is_constant_inner(
        &self,
        e: &Expr,
        st: Option<&FnState>,
        visiting: &mut Vec<String>,
        ref_depth: usize,
    ) -> bool {
        if ref_depth > 32 {
            return false;
        }
        match unwrap_parens(e) {
            Expr::Lit { kind, .. } => !matches!(kind, LitKind::Nil),
            Expr::Unary {
                op: UnOp::Neg | UnOp::Not,
                operand,
                ..
            } => self.expr_is_constant_inner(operand, st, visiting, ref_depth),
            Expr::Unary { op: UnOp::Addr, .. } => false,
            Expr::Binary { lhs, rhs, .. } => {
                self.expr_is_constant_inner(lhs, st, visiting, ref_depth)
                    && self.expr_is_constant_inner(rhs, st, visiting, ref_depth)
            }
            Expr::Ident { name, span } => {
                self.ident_is_constant(name, span.start, st, visiting, ref_depth + 1)
            }
            // Typed conversion of a constant (`int8(42)`, `byte(0xff)`) or
            // `len("const_str")`. A call to a real function (`get_val()`) or
            // shadowed local binding is never constant.
            Expr::Call { callee, args, .. } => {
                let Some(ty_name) = conversion_callee_name(callee) else {
                    return false;
                };
                if self.is_callee_shadowed(ty_name, callee.span().start, st) || args.len() != 1 || args[0].spread {
                    return false;
                }
                if ty_name == "len" {
                    if self.type_aliases.contains_key("len") {
                        return false;
                    }
                    return self.expr_is_constant_inner(&args[0].value, st, visiting, ref_depth)
                        && self.is_const_string_expr(&args[0].value, st, ref_depth);
                }
                if !self.is_const_conversion_target(ty_name, false) {
                    return false;
                }
                self.expr_is_constant_inner(&args[0].value, st, visiting, ref_depth)
            }
            // Everything else (field calls, index, receive, try, interp,
            // move, addr-of, composites nested under non-composite forms…)
            // is run-time.
            _ => false,
        }
    }

    fn is_callee_shadowed(&self, name: &str, pos: Pos, st: Option<&FnState>) -> bool {
        (st.is_some() && self.resolve_local_const(name, pos, None).is_some())
            || self.fns.contains_key(name) || self.structs.contains_key(name) || self.struct_type_params.contains_key(name)
            || self.enums.contains_key(name) || self.enums.values().any(|vs| vs.iter().any(|v| v == name))
            || self.file_const_decl(name).is_some() || self.packages.contains(name)
    }

    fn is_const_name(&self, n: &str, str_only: bool) -> bool {
        !self.type_aliases.contains_key(n) && !self.structs.contains_key(n) && !self.struct_type_params.contains_key(n)
            && !self.enums.contains_key(n) && if str_only { n == "string" } else { is_conversion_type_name(n) }
    }

    fn is_const_type(&self, ty: &TypeExpr, str_only: bool) -> bool {
        matches!(self.resolve_type_alias(ty), TypeExpr::Named { name: t, .. } if self.is_const_name(&t, str_only))
    }

    fn is_const_conversion_target(&self, name: &str, str_only: bool) -> bool {
        self.type_aliases.get(name).map_or_else(|| self.is_const_name(name, str_only), |ty| self.is_const_type(ty, str_only))
    }

    fn is_const_string_expr(&self, e: &Expr, st: Option<&FnState>, ref_depth: usize) -> bool {
        if ref_depth > 32 {
            return false;
        }
        match unwrap_parens(e) {
            Expr::Lit {
                kind: LitKind::Str(_) | LitKind::RawStr(_),
                ..
            } => true,
            Expr::Binary {
                op: BinOp::Add,
                lhs,
                rhs,
                ..
            } => {
                self.is_const_string_expr(lhs, st, ref_depth)
                    && self.is_const_string_expr(rhs, st, ref_depth)
            }
            Expr::Ident { name, span } => {
                if st.is_some() {
                    if let Some(l) = self.resolve_local_const(name, span.start, None) {
                        return l.is_some_and(|(v, ty)| !is_composite_lit(v) && ty.is_none_or(|t| self.is_const_type(t, true)) && self.is_const_string_expr(v, st, ref_depth + 1));
                    }
                }
                if let Some(cdecl) = self.file_const_decl(name) {
                    if is_composite_lit(&cdecl.value) || cdecl.ty.as_ref().is_some_and(|t| !self.is_const_type(t, true)) {
                        return false;
                    }
                    return self.is_const_string_expr(&cdecl.value, None, ref_depth + 1);
                }
                false
            }
            Expr::Call { callee, args, .. } => {
                let Some(ty_name) = conversion_callee_name(callee) else {
                    return false;
                };
                !self.is_callee_shadowed(ty_name, callee.span().start, st) && self.is_const_conversion_target(ty_name, true)
                    && args.len() == 1
                    && !args[0].spread
            }
            _ => false,
        }
    }

    fn file_const_decl(&self, name: &str) -> Option<&hy_syntax::ConstDecl> {
        self.file.decls.iter().find_map(|d| match d {
            Decl::Const(c) if c.name == name => Some(c),
            _ => None,
        })
    }

    fn ident_is_constant(
        &self,
        name: &str,
        use_pos: Pos,
        st: Option<&FnState>,
        visiting: &mut Vec<String>,
        ref_depth: usize,
    ) -> bool {
        if ref_depth > 32 {
            return false;
        }
        // Local bindings and parameters shadow file-level constants.
        if st.is_some() {
            if let Some(l) = self.resolve_local_const(name, use_pos, None) {
                return l.is_some_and(|(v, ty)| !is_composite_lit(v) && ty.is_none_or(|t| self.is_const_type(t, false)) && self.expr_is_constant_inner(v, st, visiting, ref_depth));
            }
        }
        if visiting.iter().any(|v| v == name) {
            return false;
        }
        if let Some(cdecl) = self.file_const_decl(name) {
            if is_composite_lit(&cdecl.value) || cdecl.ty.as_ref().is_some_and(|t| !self.is_const_type(t, false)) {
                return false;
            }
            visiting.push(name.to_string());
            let ok = self.expr_is_constant_inner(&cdecl.value, None, visiting, ref_depth);
            visiting.pop();
            return ok;
        }
        false
    }

    fn resolve_local_const(&self, name: &str, use_pos: Pos, self_sp: Option<Span>) -> Option<LocalConst<'a>> {
        let f = self.file.fns().find(|f| span_contains(f.span, use_pos))?;
        let mut found = (f.receiver.as_ref().is_some_and(|r| r.name == name)
            || f.params.iter().any(|p| p.name == name)
            || f.results.iter().any(|r| r.name.as_deref() == Some(name))).then_some(None);
        if let Some(body) = &f.body { Self::scan_block_local_const(body, name, use_pos, self_sp, &mut found); }
        found
    }

    fn scan_block_local_const(block: &'a Block, name: &str, use_pos: Pos, self_sp: Option<Span>, found: &mut Option<LocalConst<'a>>) {
        for stmt in block.stmts.iter().take_while(|_| span_contains(block.span, use_pos)) {
            if span_contains(stmt.span(), use_pos) { return Self::scan_stmt_local_const(stmt, name, use_pos, self_sp, found); }
            if pos_le(stmt.span().end, use_pos) { Self::apply_local_const_stmt(stmt, name, found); }
        }
    }

    fn apply_local_const_stmt(stmt: &'a Stmt, name: &str, found: &mut Option<LocalConst<'a>>) {
        if let Stmt::Local(l) = stmt {
            if let Some(i) = l.names.iter().position(|n| n == name) {
                *found = Some(l.values.get(i).filter(|_| l.kind == LocalKind::Const).map(|v| (v, l.ty.as_ref())));
            }
        }
    }

    fn scan_stmt_local_const(stmt: &'a Stmt, name: &str, use_pos: Pos, self_sp: Option<Span>, found: &mut Option<LocalConst<'a>>) {
        match stmt {
            Stmt::Local(l) if l.kind == LocalKind::Const && self_sp.is_some() => {
                if let Some((_, v)) = l.names.iter().zip(&l.values).find(|(n, v)| n.as_str() == name && Some(v.span()) == self_sp) {
                    *found = Some(Some((v, l.ty.as_ref())));
                    return;
                }
            }
            Stmt::If { init, then, otherwise, .. } => {
                if let Some(s) = init.as_deref() {
                    if span_contains(s.span(), use_pos) { return Self::scan_stmt_local_const(s, name, use_pos, self_sp, found); }
                    if pos_le(s.span().end, use_pos) { Self::apply_local_const_stmt(s, name, found); }
                }
                Self::scan_block_local_const(then, name, use_pos, self_sp, found);
                if let Some(o) = otherwise.as_deref().filter(|o| span_contains(o.span(), use_pos)) {
                    return Self::scan_stmt_local_const(o, name, use_pos, self_sp, found);
                }
            }
            Stmt::For { kind, body, .. } if span_contains(body.span, use_pos) => {
                if matches!(kind, ForKind::Range { var, .. } if var == name) || matches!(kind, ForKind::Iterate { vars, .. } if vars.iter().any(|v| v == name)) {
                    *found = Some(None);
                }
                return Self::scan_block_local_const(body, name, use_pos, self_sp, found);
            }
            Stmt::Managed { body, .. } | Stmt::Block { block: body, .. } => return Self::scan_block_local_const(body, name, use_pos, self_sp, found),
            Stmt::Match { arms, .. } => {
                for a in arms.iter().filter(|a| a.guard.as_ref().is_some_and(|g| span_contains(g.span(), use_pos)) || span_contains(a.body.span, use_pos)) {
                    if matches!(&a.pattern, MatchPattern::Path { binding: Some(b), .. } if b == name) { *found = Some(None); }
                    if span_contains(a.body.span, use_pos) { return Self::scan_block_local_const(&a.body, name, use_pos, self_sp, found); }
                }
            }
            Stmt::Select { arms, .. } => {
                for a in arms.iter().filter(|a| span_contains(a.body.span, use_pos)) {
                    if matches!(&a.kind, SelectArmKind::Recv { names, .. } if names.iter().any(|n| n == name)) { *found = Some(None); }
                    return Self::scan_block_local_const(&a.body, name, use_pos, self_sp, found);
                }
            }
            _ => {}
        }
        let mut fnlits = Vec::new();
        crate::walk::own_fnlits(stmt, &mut fnlits);
        for fl in fnlits {
            if let Expr::FnLit { params, results, body, .. } = fl {
                if span_contains(body.span, use_pos) {
                    if params.iter().any(|p| p.name == name) || results.iter().any(|r| r.name.as_deref() == Some(name)) {
                        *found = Some(None);
                    }
                    return Self::scan_block_local_const(body, name, use_pos, self_sp, found);
                }
            }
        }
    }
}

fn pos_le(a: Pos, b: Pos) -> bool { (a.line, a.col) <= (b.line, b.col) }
fn span_contains(span: Span, pos: Pos) -> bool { pos_le(span.start, pos) && pos_le(pos, span.end) }

fn is_composite_lit(e: &Expr) -> bool {
    matches!(
        unwrap_parens(e),
        Expr::SliceLit { .. } | Expr::MapLit { .. } | Expr::StructLit { .. }
    )
}

fn conversion_callee_name(callee: &Expr) -> Option<&str> {
    match unwrap_parens(callee) {
        Expr::Ident { name, .. } => Some(name.as_str()),
        _ => None,
    }
}

/// Type names Hybrid spells that Go accepts as conversion targets in a
/// `const` initializer (`const x = int8(42)`).
pub(crate) fn is_conversion_type_name(name: &str) -> bool {
    matches!(
        name,
        "bool"
            | "string"
            | "byte"
            | "rune"
            | "char"
            | "int"
            | "int8"
            | "int16"
            | "int32"
            | "int64"
            | "uint"
            | "uint8"
            | "uint16"
            | "uint32"
            | "uint64"
            | "uintptr"
            | "float32"
            | "float64"
            | "f32"
            | "f64"
            | "i8"
            | "i16"
            | "i32"
            | "i64"
            | "u8"
            | "u16"
            | "u32"
            | "u64"
            | "isize"
            | "usize"
    )
}

fn span_hint(span: Span) -> String {
    format!("{}:{}", span.start.line, span.start.col)
}

#[cfg(test)]
mod tests {
    use super::is_conversion_type_name;

    #[test]
    fn conversion_type_names_cover_go_builtins() {
        assert!(is_conversion_type_name("int8"));
        assert!(is_conversion_type_name("byte"));
        assert!(!is_conversion_type_name("get_val"));
        assert!(!is_conversion_type_name("fmt"));
    }
}
