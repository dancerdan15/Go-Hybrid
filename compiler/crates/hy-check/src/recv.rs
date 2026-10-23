//! recv.rs — receive-operand channel-ness checks (HYB-E2008 / E3005).
//!
//! Issue #409: the send direction has been guarded since #77 —
//! `check_send_target` rejects a non-channel send target (HYB-E3007) and
//! fail-closes an unresolvable one (HYB-E3005). The receive direction had
//! no counterpart: `<-x` with `x` an int / map / non-channel call result
//! passed `hyb check` with 0 diagnostics and emitted Go that fails
//! `go build` (`cannot receive from non-channel`). This module mirrors the
//! send side one-for-one:
//!   * a receive whose operand resolves to a concrete non-channel shape is
//!     rejected (HYB-E2008);
//!   * an unresolvable operand stays fail-closed (HYB-E3005, O4.9) — the
//!     same treatment `check_send_target` gives an unknown send target.
//!
//! Wiring: every statement in the Tier-0 flat walk (lib.rs, after the
//! builtins pass) and every closure-body statement (closures.rs — FnLit
//! bodies sit outside the flat walk, the A5 perimeter) runs
//! `check_stmt_recv_operands`, which recurses the statement's own
//! expression trees (nested block statements arrive as their own flattened
//! entries) and classifies every `Expr::Receive` operand it finds, plus
//! `SelectArmKind::Recv` channel expressions.

use crate::model::{Checker, FnState, TypeShape};
use hy_diag::{Diagnostic, Span};
use hy_syntax::{Expr, InterpPart, PlaceKind, SelectArmKind, Stmt};

impl<'a> Checker<'a> {
    /// Statement entry: classify every receive operand in this statement's
    /// own expressions. Nested block bodies are NOT recursed — the Tier-0
    /// flat walk already lists those statements (same discipline as
    /// `arith.rs` / `builtins.rs`); the closure walk recurses explicitly.
    pub(crate) fn check_stmt_recv_operands(&mut self, st: &FnState, stmt: &Stmt) {
        let enriched = self.enrich_recv_state(st, stmt.span());
        let st = &enriched;
        match stmt {
            Stmt::Local(l) => {
                if let Some(ty) = &l.ty {
                    self.check_type_recv_operands(st, ty);
                }
                for v in &l.values {
                    self.check_expr_recv_operands(st, v);
                }
            }
            Stmt::Assign { targets, values, .. } => {
                for t in targets {
                    self.check_place_recv_operands(st, t);
                }
                for v in values {
                    self.check_expr_recv_operands(st, v);
                }
            }
            Stmt::Expr { expr, .. } => self.check_expr_recv_operands(st, expr),
            Stmt::Send { chan, value, .. } => {
                // The send TARGET is `check_send_target`'s domain (E3007);
                // here we only look for receives NESTED in either side
                // (`ch <- <-done` and friends).
                self.check_expr_recv_operands(st, chan);
                self.check_expr_recv_operands(st, value);
            }
            // `if`/`for` init statements arrive as their own flattened
            // entries in Pass B (walk.rs walk_one), whereas closures.rs calls
            // `check_stmt_recv_operands` on `Stmt::If` before walking `init`.
            // Bind `init` locals into a temporary scope when checking `cond`
            // so `if ch := make(chan int, 1); (<-ch) > 0` resolves `ch` in
            // both passes without double-checking `init` itself.
            Stmt::If { init, cond, .. } => {
                if let Some(hy_syntax::Stmt::Local(l)) = init.as_deref() {
                    let mut tmp = st.clone();
                    tmp.scope_push();
                    self.apply_recv_local(&mut tmp, l);
                    self.check_expr_recv_operands(&tmp, cond);
                } else {
                    self.check_expr_recv_operands(st, cond);
                }
            }
            Stmt::For { kind, .. } => match kind {
                hy_syntax::ForKind::While { cond } => self.check_expr_recv_operands(st, cond),
                hy_syntax::ForKind::Range { lo, hi, .. } => {
                    self.check_expr_recv_operands(st, lo);
                    self.check_expr_recv_operands(st, hi);
                }
                hy_syntax::ForKind::Iterate { expr, .. } => self.check_expr_recv_operands(st, expr),
                hy_syntax::ForKind::Infinite => {}
            },
            Stmt::Return { values, .. } => {
                for v in values {
                    self.check_expr_recv_operands(st, v);
                }
            }
            Stmt::Task { callee, args, .. } => {
                self.check_expr_recv_operands(st, callee);
                for a in args {
                    self.check_expr_recv_operands(st, &a.value);
                }
            }
            Stmt::Match { subject, arms, .. } => {
                self.check_expr_recv_operands(st, subject);
                for arm in arms {
                    if let Some(g) = &arm.guard {
                        let mut arm_st = st.clone();
                        arm_st.scope_push();
                        self.bind_recv_match_arm(st, &mut arm_st, subject, &arm.pattern);
                        self.check_expr_recv_operands(&arm_st, g);
                    }
                }
            }
            Stmt::Defer { call, .. } => self.check_expr_recv_operands(st, call),
            Stmt::Select { arms, .. } => {
                for arm in arms {
                    match &arm.kind {
                        SelectArmKind::Send { chan, value, .. } => {
                            self.check_expr_recv_operands(st, chan);
                            self.check_expr_recv_operands(st, value);
                        }
                        // The receive ARM's channel is a receive operand
                        // itself (`select { case v := <-x: }` with `x` an
                        // int used to pass with 0 diagnostics).
                        SelectArmKind::Recv { chan, .. } => {
                            let span = chan.span();
                            self.check_recv_operand(st, chan, span);
                            self.check_expr_recv_operands(st, chan);
                        }
                        SelectArmKind::Default => {}
                    }
                }
            }
            Stmt::Break { .. }
            | Stmt::Continue { .. }
            | Stmt::Block { .. }
            | Stmt::Managed { .. } => {}
        }
    }

    fn check_type_recv_operands(&mut self, st: &FnState, ty: &hy_syntax::TypeExpr) {
        match ty {
            hy_syntax::TypeExpr::Named { .. } => {}
            hy_syntax::TypeExpr::Pointer { to, .. } => self.check_type_recv_operands(st, to),
            hy_syntax::TypeExpr::Slice { elem, .. }
            | hy_syntax::TypeExpr::Chan { elem, .. }
            | hy_syntax::TypeExpr::Option { elem, .. } => self.check_type_recv_operands(st, elem),
            hy_syntax::TypeExpr::Array { len, elem, .. } => {
                if let Some(len) = len {
                    self.check_expr_recv_operands(st, len);
                }
                self.check_type_recv_operands(st, elem);
            }
            hy_syntax::TypeExpr::Map { key, value, .. } => {
                self.check_type_recv_operands(st, key);
                self.check_type_recv_operands(st, value);
            }
            hy_syntax::TypeExpr::Fn {
                params, results, ..
            } => {
                for p in params {
                    self.check_type_recv_operands(st, &p.ty);
                }
                for r in results {
                    self.check_type_recv_operands(st, r);
                }
            }
        }
    }

    fn check_place_recv_operands(&mut self, st: &FnState, place: &hy_syntax::Place) {
        match &place.kind {
            PlaceKind::Ident(_) => {}
            PlaceKind::Index { base, index } => {
                self.check_place_recv_operands(st, base);
                self.check_expr_recv_operands(st, index);
            }
            PlaceKind::Field { base, .. } | PlaceKind::AddrOf { base } => {
                self.check_place_recv_operands(st, base);
            }
        }
    }

    /// Recurse an expression tree, classifying every `Expr::Receive`
    /// operand found at any depth. FnLit bodies are the A5 perimeter —
    /// not walked here (the closure pass enters them).
    fn check_expr_recv_operands(&mut self, st: &FnState, expr: &Expr) {
        match expr {
            Expr::Receive { chan, span } => {
                self.check_recv_operand(st, chan, *span);
                self.check_expr_recv_operands(st, chan);
            }
            Expr::Call { callee, args, .. } => {
                self.check_expr_recv_operands(st, callee);
                for a in args {
                    self.check_expr_recv_operands(st, &a.value);
                }
            }
            Expr::Binary { lhs, rhs, .. } => {
                self.check_expr_recv_operands(st, lhs);
                self.check_expr_recv_operands(st, rhs);
            }
            Expr::Unary { operand, .. }
            | Expr::AddrOf { operand, .. }
            | Expr::Move { operand, .. }
            | Expr::Try { operand, .. }
            | Expr::Paren { inner: operand, .. } => self.check_expr_recv_operands(st, operand),
            Expr::Index { base, index, .. } => {
                self.check_expr_recv_operands(st, base);
                self.check_expr_recv_operands(st, index);
            }
            Expr::Slice { base, lo, hi, .. } => {
                self.check_expr_recv_operands(st, base);
                if let Some(lo) = lo {
                    self.check_expr_recv_operands(st, lo);
                }
                if let Some(hi) = hi {
                    self.check_expr_recv_operands(st, hi);
                }
            }
            Expr::Field { base, .. } => self.check_expr_recv_operands(st, base),
            Expr::StructLit { fields, .. } => {
                for f in fields {
                    self.check_expr_recv_operands(st, &f.value);
                }
            }
            Expr::SliceLit { items, .. } => {
                for it in items {
                    self.check_expr_recv_operands(st, it);
                }
            }
            Expr::MapLit { entries, .. } => {
                for (k, v) in entries {
                    self.check_expr_recv_operands(st, k);
                    self.check_expr_recv_operands(st, v);
                }
            }
            Expr::Interp { parts, .. } => {
                for p in parts {
                    if let InterpPart::Expr(e) = p {
                        self.check_expr_recv_operands(st, e);
                    }
                }
            }
            Expr::FnLit { .. } | Expr::Ident { .. } | Expr::Lit { .. } => {}
        }
    }

    fn resolve_recv_expr_shape(&self, st: &FnState, expr: &Expr) -> TypeShape {
        match self.shape_of_expr(st, expr) {
            TypeShape::Unknown => self.recv_chan_type_expr(st, expr).map(|t| self.shape_of_type(&t)).unwrap_or(TypeShape::Unknown),
            s => s,
        }
    }

    fn recv_chan_type_expr(&self, st: &FnState, expr: &Expr) -> Option<hy_syntax::TypeExpr> {
        use hy_syntax::TypeExpr;
        match expr {
            Expr::Paren { inner, .. } | Expr::Move { operand: inner, .. } => self.recv_chan_type_expr(st, inner),
            Expr::Try { operand, .. } => {
                let inner = self.recv_chan_type_expr(st, operand)?;
                match self.resolve_type_alias(&inner) { TypeExpr::Option { elem, .. } => Some(*elem), _ => Some(inner) }
            }
            Expr::Receive { chan, .. } => match self.resolve_type_alias(&self.recv_chan_type_expr(st, chan)?) { TypeExpr::Chan { elem, .. } => Some(*elem), _ => None },
            Expr::Slice { base, .. } => match self.resolve_type_alias(&self.recv_chan_type_expr(st, base)?) {
                TypeExpr::Slice { elem, span } | TypeExpr::Array { elem, span, .. } => Some(TypeExpr::Slice { elem, span }),
                TypeExpr::Pointer { to, span } => match self.resolve_type_alias(&to) { TypeExpr::Array { elem, .. } => Some(TypeExpr::Slice { elem, span }), _ => None },
                _ => None,
            },
            Expr::Index { base, .. } => match self.resolve_type_alias(&self.recv_chan_type_expr(st, base)?) {
                TypeExpr::Slice { elem, .. } | TypeExpr::Array { elem, .. } | TypeExpr::Map { value: elem, .. } => Some(*elem),
                TypeExpr::Pointer { to, .. } => match self.resolve_type_alias(&to) { TypeExpr::Array { elem, .. } => Some(*elem), _ => None },
                _ => None,
            },
            Expr::Field { base, name, .. } => {
                if let Some(res) = self.recv_chan_type_expr(st, base).or_else(|| self.ty_of_expr(st, base)).map(|t| self.resolve_type_alias(&t)) {
                    if let Some((sname, fty)) = Self::base_named_type(&res).and_then(|s| self.structs.get(&s)?.iter().find(|(n, _)| n == name).map(|(_, t)| (s, t))) {
                        let raw = match &res { TypeExpr::Pointer { to, .. } => &**to, o => o };
                        if let (Some(tps), TypeExpr::Named { name: n, span }) = (self.struct_type_params.get(&sname).filter(|t| !t.is_empty()), raw) {
                            let names: Vec<String> = tps.iter().map(|p| p.name.clone()).collect();
                            return Some(subst_type(fty, &names, &split_type_args(n, *span)));
                        }
                        return Some(fty.clone());
                    }
                }
                self.ty_of_expr(st, expr)
            }
            Expr::SliceLit { elem: None, items, span } => Some(TypeExpr::Slice { elem: Box::new(self.recv_chan_type_expr(st, items.first()?)?), span: *span }),
            Expr::Call { callee, args, span } => {
                if let Expr::Ident { name, .. } = crate::model::unwrap_parens(callee) {
                    if self.lookup_binding(st, name).is_none() {
                        match name.as_str() {
                            "append" => return self.recv_chan_type_expr(st, &args.first()?.value),
                            "some" => return Some(TypeExpr::Option { elem: Box::new(self.recv_chan_type_expr(st, &args.first()?.value)?), span: *span }),
                            "make" => match crate::model::unwrap_parens(&args.first()?.value) {
                                Expr::Ident { name: tn, span: ts } if self.type_aliases.contains_key(tn) => return Some(TypeExpr::Named { name: tn.clone(), span: *ts }),
                                first => return self.recv_chan_type_expr(st, first),
                            },
                            _ => {}
                        }
                    }
                }
                self.ty_of_expr(st, expr)
            }
            Expr::Ident { name, .. } => self.ty_of_expr(st, expr).or_else(|| self.consts.get(name).and_then(|b| b.ty.clone())),
            _ => self.ty_of_expr(st, expr),
        }
    }

    /// Classify a receive's SOURCE — the mirror of `check_send_target`
    /// (issue #409): a receive is only well-typed when the channel
    /// expression resolves to a channel shape. A concrete non-channel is
    /// HYB-E2008; an unresolvable shape is fail-closed HYB-E3005 (O4.9),
    /// exactly as the send side treats an unknown target. Like the send
    /// side, an ill-typed source is not a sendability doubt — `managed`
    /// does not suppress it.
    pub(crate) fn check_recv_operand(&mut self, st: &FnState, chan: &Expr, span: Span) -> bool {
        match self.resolve_recv_expr_shape(st, chan) {
            TypeShape::Chan(_) => {
                if let Some(ty) = self.recv_chan_type_expr(st, chan) {
                    let resolved = self.resolve_type_alias(&ty);
                    if matches!(
                        resolved,
                        hy_syntax::TypeExpr::Chan {
                            dir: hy_syntax::ChanDir::Send,
                            ..
                        }
                    ) {
                        // HYB-E3008 (chan-direction-mismatch) owns directed-channel
                        // misuse on main; this generic arm only reports when E3008
                        // has not already diagnosed the same site, so a defect is
                        // reported once by its precise code.
                        if !self
                            .diags
                            .iter()
                            .any(|d| d.code == "HYB-E3008" && d.span == span)
                        {
                            let msg = format!(
                                "cannot receive from `{}` (`{}`) — channel is send-only (HYB-E2008)",
                                chan.render(),
                                resolved.render()
                            );
                            let notes = vec![
                                "why: Go rejects `<-ch` on a `chan<- T` send-only channel (`invalid operation: cannot receive from send-only channel`), so an accepted Hybrid program would emit unbuildable Go".to_string(),
                                "fix: declare the parameter or binding as a bidirectional (`chan T`) or receive-only (`<-chan T`) channel".to_string(),
                            ];
                            self.diags.push(
                                Diagnostic::error("HYB-E2008", self.path.clone(), span, msg)
                                    .with_notes(notes),
                            );
                        }
                        return false;
                    }
                }
                true
            }
            TypeShape::Unknown => {
                let msg = format!(
                    "cannot prove `{}` is a channel — a receive source must resolve to `chan T` (O4.9)",
                    chan.render()
                );
                let notes = vec![
                    "why: the checker cannot classify the receive source's type, and a receive from a non-channel is not valid Go (issue #409)".to_string(),
                    "fix: bind the channel to a local (or parameter) with a visible `chan T` type before receiving, so the checker can resolve it".to_string(),
                ];
                self.diags.push(
                    Diagnostic::error("HYB-E3005", self.path.clone(), span, msg).with_notes(notes),
                );
                false
            }
            other => {
                let msg = format!(
                    "cannot receive from `{}` ({}) — a receive source must be a channel (O4)",
                    chan.render(),
                    other.render()
                );
                let notes = vec![
                    "why: `<-` on a non-channel value is not valid Go; the accepted subset only emits buildable Go (issue #409 — the send side of this rule is HYB-E3007, issue #77)".to_string(),
                    "fix: receive from a `chan T` binding (`ch := make(chan T, n)` or a `chan T` parameter); a statement STARTING with `<-expr` is a receive — check the operand's type".to_string(),
                ];
                self.diags.push(
                    Diagnostic::error("HYB-E2008", self.path.clone(), span, msg).with_notes(notes),
                );
                false
            }
        }
    }

    fn bind_recv_match_arm(&self, eval_st: &FnState, arm_st: &mut FnState, subject: &Expr, pattern: &hy_syntax::MatchPattern) {
        let hy_syntax::MatchPattern::Path { segments, binding: Some(b) } = pattern else { return };
        if segments == &["some"] { self.bind_match_arm(arm_st, b, subject, false); }
        if let Some(ty) = self.resolve_match_arm_payload_type(eval_st, subject, segments) {
            self.upsert_recv_binding(arm_st, b, self.shape_of_type(&ty), Some(ty));
        }
    }

    fn resolve_match_arm_payload_type(&self, st: &FnState, subject: &Expr, segments: &[String]) -> Option<hy_syntax::TypeExpr> {
        let resolved = self.resolve_type_alias(&self.recv_chan_type_expr(st, subject)?);
        if segments == &["some"] {
            if let hy_syntax::TypeExpr::Option { elem, .. } = resolved { return Some(*elem); }
        }
        let vname = segments.last()?.as_str();
        let hy_syntax::TypeExpr::Named { name, span } = &resolved else { return None };
        let base = Self::base_named_type_str(name);
        if segments.len() == 2 && segments[0] != base { return None; }
        let type_args = split_type_args(name, *span);
        for d in &self.file.decls {
            if let hy_syntax::Decl::Enum(ed) = d {
                if ed.name == base {
                    let payload = ed.variants.iter().find(|v| v.name == vname)?.payload.as_ref()?;
                    let mut tps = Vec::new();
                    for p in ed.variants.iter().filter_map(|v| v.payload.as_ref()) { self.collect_unresolved_type_names(p, &mut tps); }
                    return Some(subst_type(payload, &tps, &type_args));
                }
            }
        }
        match (base.eq_ignore_ascii_case("result"), vname) { (true, "ok") => type_args.first().cloned(), (true, "err") => type_args.get(1).cloned(), _ => None }
    }

    fn collect_unresolved_type_names(&self, ty: &hy_syntax::TypeExpr, out: &mut Vec<String>) {
        use hy_syntax::TypeExpr::*;
        match ty {
            Named { name, .. } => {
                let b = Self::base_named_type_str(name);
                let known = matches!(b, "int" | "int8" | "int16" | "int32" | "int64" | "uint" | "uint8" | "uint16" | "uint32" | "uint64" | "uintptr" | "float32" | "float64" | "bool" | "string" | "byte" | "rune" | "any" | "error")
                    || self.structs.contains_key(b) || self.enums.contains_key(b) || self.type_aliases.contains_key(b);
                if !known && !out.iter().any(|p| p == b) { out.push(b.to_string()); }
            }
            Pointer { to, .. } | Slice { elem: to, .. } | Array { elem: to, .. } | Chan { elem: to, .. } | Option { elem: to, .. } => self.collect_unresolved_type_names(to, out),
            Map { key, value, .. } => { self.collect_unresolved_type_names(key, out); self.collect_unresolved_type_names(value, out); }
            Fn { .. } => {}
        }
    }

    fn enrich_recv_state(&self, st: &FnState, target: Span) -> FnState {
        let mut out = st.clone();
        if let Some(body) = self.file.fns().find(|f| span_contains(f.span, target)).and_then(|f| f.body.as_ref()) { self.enrich_recv_block(&mut out, body, target); }
        out
    }

    fn enrich_recv_block(&self, st: &mut FnState, block: &hy_syntax::Block, target: Span) {
        for s in &block.stmts {
            let sp = s.span();
            if sp == target || (sp.start.line, sp.start.col) > (target.start.line, target.start.col) { break; }
            if !span_contains(sp, target) {
                match s {
                    Stmt::Local(l) => self.apply_recv_local(st, l),
                    Stmt::Assign { targets, op: hy_syntax::AssignOp::Assign, values, .. } if targets.len() == values.len() => {
                        let up: Vec<_> = targets.iter().zip(values).filter_map(|(t, v)| match &t.kind { PlaceKind::Ident(n) => Some((n.as_str(), self.resolve_recv_expr_shape(st, v), self.recv_chan_type_expr(st, v))), _ => None }).collect();
                        for (n, sh, ty) in up { self.upsert_recv_binding(st, n, sh, ty); }
                    }
                    _ => {}
                }
                continue;
            }
            let mut fnlits = Vec::new();
            crate::walk::own_fnlits(s, &mut fnlits);
            for fl in fnlits {
                if let Expr::FnLit { params, body, span, .. } = fl {
                    if span_contains(*span, target) && span_contains(body.span, target) {
                        for p in params { self.upsert_recv_binding(st, &p.name, self.shape_of_type(&p.ty), Some(p.ty.clone())); }
                        return self.enrich_recv_block(st, body, target);
                    }
                }
            }
            match s {
                Stmt::Block { block: b, .. } | Stmt::Managed { body: b, .. } => self.enrich_recv_block(st, b, target),
                Stmt::If { init, then, otherwise, .. } => {
                    if span_contains(then.span, target) {
                        if let Some(Stmt::Local(l)) = init.as_deref() { self.apply_recv_local(st, l); }
                        self.enrich_recv_block(st, then, target);
                    } else if let Some(other) = otherwise.as_deref().filter(|o| span_contains(o.span(), target)) {
                        if let Some(Stmt::Local(l)) = init.as_deref() { self.apply_recv_local(st, l); }
                        self.enrich_recv_block(st, &hy_syntax::Block { stmts: vec![other.clone()], span: other.span() }, target);
                    }
                }
                Stmt::For { kind: hy_syntax::ForKind::Iterate { vars, expr }, body, .. } if span_contains(body.span, target) => {
                    if let Some(ty) = self.recv_chan_type_expr(st, expr).map(|t| self.resolve_type_alias(&t)) {
                        match ty {
                            hy_syntax::TypeExpr::Slice { elem, .. } | hy_syntax::TypeExpr::Array { elem, .. } | hy_syntax::TypeExpr::Chan { elem, .. } => {
                                if let Some(v) = vars.get(usize::from(vars.len() >= 2)) { self.upsert_recv_binding(st, v, self.shape_of_type(&elem), Some(*elem)); }
                            }
                            hy_syntax::TypeExpr::Map { key, value, .. } => {
                                if vars.len() >= 2 { self.upsert_recv_binding(st, &vars[0], self.shape_of_type(&key), Some(*key)); }
                                if let Some(v) = vars.get(usize::from(vars.len() >= 2)) { self.upsert_recv_binding(st, v, self.shape_of_type(&value), Some(*value)); }
                            }
                            _ => {}
                        }
                    }
                    self.enrich_recv_block(st, body, target);
                }
                Stmt::For { body, .. } if span_contains(body.span, target) => self.enrich_recv_block(st, body, target),
                Stmt::Match { subject, arms, .. } => if let Some(arm) = arms.iter().find(|a| span_contains(a.body.span, target)) {
                    let pre = st.clone();
                    self.bind_recv_match_arm(&pre, st, subject, &arm.pattern);
                    self.enrich_recv_block(st, &arm.body, target);
                },
                Stmt::Select { arms, .. } => if let Some(arm) = arms.iter().find(|a| span_contains(a.body.span, target)) {
                    if let SelectArmKind::Recv { names, chan, .. } = &arm.kind {
                        if let (Some(first), Some(hy_syntax::TypeExpr::Chan { elem, .. })) = (names.first(), self.recv_chan_type_expr(st, chan).map(|t| self.resolve_type_alias(&t))) {
                            self.upsert_recv_binding(st, first, self.shape_of_type(&elem), Some(*elem));
                        }
                    }
                    self.enrich_recv_block(st, &arm.body, target);
                },
                _ => {}
            }
            break;
        }
    }

    fn apply_recv_local(&self, st: &mut FnState, l: &hy_syntax::LocalDecl) {
        if l.values.is_empty() {
            let sh = l.ty.as_ref().map(|t| self.shape_of_type(t)).unwrap_or(TypeShape::Unknown);
            for name in &l.names { self.upsert_recv_binding(st, name, sh.clone(), l.ty.clone()); }
        } else if l.names.len() == l.values.len() {
            let up: Vec<_> = l.names.iter().zip(&l.values).map(|(n, v)| (n.as_str(), self.resolve_recv_expr_shape(st, v), l.ty.clone().or_else(|| self.recv_chan_type_expr(st, v)))).collect();
            for (n, sh, ty) in up { self.upsert_recv_binding(st, n, sh, ty); }
        } else if l.values.len() == 1 && l.names.len() > 1 {
            let shapes = self.multi_value_shapes(st, &l.values[0], l.names.len());
            let mut tys = self.multi_value_tys(st, &l.values[0], l.names.len());
            if let Some(slot @ None) = tys.first_mut() { *slot = self.recv_chan_type_expr(st, &l.values[0]); }
            for ((name, sh), ty) in l.names.iter().zip(shapes).zip(tys) { self.upsert_recv_binding(st, name, sh, l.ty.clone().or(ty)); }
        }
    }

    fn upsert_recv_binding(&self, st: &mut FnState, name: &str, shape: TypeShape, ty: Option<hy_syntax::TypeExpr>) {
        if name != "_" {
            let eff = match (&ty, shape) { (Some(t), TypeShape::Unknown) => self.shape_of_type(t), (_, s) => s };
            self.define_binding(st, name, eff, ty, false, false, false);
        }
    }
}

fn span_contains(outer: Span, inner: Span) -> bool {
    (outer.start.line, outer.start.col) <= (inner.start.line, inner.start.col) && (inner.end.line, inner.end.col) <= (outer.end.line, outer.end.col)
}

fn split_type_args(name: &str, span: Span) -> Vec<hy_syntax::TypeExpr> {
    let Some(inner) = name.split_once('[').and_then(|(_, r)| r.strip_suffix(']')) else { return Vec::new() };
    let pt = |s: &str| {
        if let Ok(f) = hy_syntax::parse_file("<type>", &format!("type a = {}", s.trim())) {
            if let Some(hy_syntax::Decl::TypeAlias(ta)) = f.decls.into_iter().next() { return ta.ty; }
        }
        hy_syntax::TypeExpr::Named { name: s.trim().to_string(), span }
    };
    let (mut out, mut depth, mut start) = (Vec::new(), 0usize, 0usize);
    for (i, ch) in inner.char_indices() {
        match ch {
            '[' | '(' => depth += 1,
            ']' | ')' => depth = depth.saturating_sub(1),
            ',' if depth == 0 => { out.push(pt(&inner[start..i])); start = i + 1; }
            _ => {}
        }
    }
    if start <= inner.len() && !inner[start..].trim().is_empty() { out.push(pt(&inner[start..])); }
    out
}

fn subst_type(ty: &hy_syntax::TypeExpr, params: &[String], args: &[hy_syntax::TypeExpr]) -> hy_syntax::TypeExpr {
    use hy_syntax::TypeExpr::*;
    match ty {
        Named { name, .. } => params.iter().position(|p| p == name).and_then(|i| args.get(i).cloned()).unwrap_or_else(|| ty.clone()),
        Pointer { to, span } => Pointer { to: Box::new(subst_type(to, params, args)), span: *span },
        Slice { elem, span } => Slice { elem: Box::new(subst_type(elem, params, args)), span: *span },
        Array { len, elem, span } => Array { len: len.clone(), elem: Box::new(subst_type(elem, params, args)), span: *span },
        Map { key, value, span } => Map { key: Box::new(subst_type(key, params, args)), value: Box::new(subst_type(value, params, args)), span: *span },
        Chan { dir, elem, span } => Chan { dir: *dir, elem: Box::new(subst_type(elem, params, args)), span: *span },
        Option { elem, span } => Option { elem: Box::new(subst_type(elem, params, args)), span: *span },
        Fn { .. } => ty.clone(),
    }
}
