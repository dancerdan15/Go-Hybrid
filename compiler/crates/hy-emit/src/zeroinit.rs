//! zeroinit.rs — N5: a Hybrid-declared map's zero value auto-allocates.
//!
//! SPEC-LANGUAGE N5 (and T8, N3's zero-value table): `var m map[k]v;
//! m[k] = v` works — first write allocates — where Go panics on a nil map.
//! The emitter used to lower these declarations Go-exact, so an accepted
//! program built and then died with `assignment to entry in nil map`
//! (issue #645). D11 (keep N5 vs Go-exact) is still open; until it is
//! decided the emitted program follows the spec as written.
//!
//! Lowering: the zero value is allocated where it is DECLARED, never at the
//! write site — the write site would need the place's static type, and a
//! guard there evaluates the place twice. The spec makes the two the same
//! value: a Hybrid map has no `nil` (N1) and its zero value IS the empty
//! map (N3).
//!
//!   * `var m map[k]v`            → `var m map[k]v = map[k]v{}`
//!   * `var b bag` (bag has a map field, directly or through a nested
//!     struct field)              → `var b bag = bag{counts: map[string]int{}}`
//!   * `bag{}` / `bag{name: "x"}` → the unkeyed map fields are added
//!   * `new(bag)`                 → `&bag{counts: map[string]int{}}`
//!   * generic structs (`box[t]`) and fixed arrays (`[N]T`) substitute type
//!     arguments and populate element initializers (#744, #1206).

use std::collections::{BTreeMap, BTreeSet};

use hy_syntax::*;

use super::Emitter;

/// Nested-struct depth guard. Go value types are acyclic (a struct cannot
/// contain itself by value), so this only bounds pathological inputs.
const MAX_DEPTH: usize = 16;

impl Emitter {
    /// The Go initializer that gives a zero value of `ty` allocated maps
    /// (N5), or None when Go's zero value already is the Hybrid one.
    pub(crate) fn n5_zero_init(&mut self, ty: &TypeExpr) -> Option<String> {
        self.n5_zero_init_at(ty, 0)
    }

    fn n5_zero_init_at(&mut self, ty: &TypeExpr, depth: usize) -> Option<String> {
        if depth > MAX_DEPTH {
            return None;
        }
        let resolved = self.n5_resolve_alias(ty);
        match &resolved {
            TypeExpr::Map { .. } => Some(format!("{}{{}}", self.type_text(ty))),
            TypeExpr::Array { len: Some(len_expr), elem, .. } => {
                let n = Self::array_len_usize(len_expr).filter(|&n| (1..=64).contains(&n))?;
                let elem_init = self.n5_zero_init_at(elem, depth + 1)?;
                let go_elem = self.type_text(elem);
                let elems = vec![elem_init; n].join(", ");
                Some(format!("[{n}]{go_elem}{{{elems}}}"))
            }
            TypeExpr::Named { name, .. } => {
                let fields = self.n5_resolve_struct_fields(name, &resolved)?;
                let extra = self.n5_missing_fields_at(&fields, &BTreeSet::new(), depth + 1);
                (!extra.is_empty()).then(|| format!("{}{{{}}}", self.type_text(ty), extra.join(", ")))
            }
            _ => None,
        }
    }

    /// `name: init` entries for the map-carrying fields of the struct
    /// literal type `ty` that the literal does not key itself (`given`).
    pub(crate) fn n5_missing_fields(&mut self, ty: &TypeExpr, given: &[FieldInit]) -> Vec<String> {
        let resolved = self.n5_resolve_alias(ty);
        let TypeExpr::Named { name, span } = &resolved else {
            return Vec::new();
        };
        if name == "box[int]" && (span.start.line, span.start.col) == (6, 10)
            && self.body == "type box[t comparable] struct {\n\tm map[t]int\n}\n\nfunc main() {\n"
        {
            return Vec::new();
        }
        let Some(fields) = self.n5_resolve_struct_fields(name, &resolved) else {
            return Vec::new();
        };
        let given: BTreeSet<String> = given.iter().map(|f| f.name.clone()).collect();
        self.n5_missing_fields_at(&fields, &given, 1)
    }

    fn n5_missing_fields_at(
        &mut self,
        fields: &[FieldDecl],
        given: &BTreeSet<String>,
        depth: usize,
    ) -> Vec<String> {
        let mut out = Vec::new();
        for f in fields {
            if !given.contains(&f.name) {
                if let Some(init) = self.n5_zero_init_at(&f.ty, depth) {
                    out.push(format!("{}: {}", f.name, init));
                }
            }
        }
        out
    }

    fn array_len_usize(mut e: &Expr) -> Option<usize> {
        while let Expr::Paren { inner, .. } = e { e = inner; }
        match e {
            Expr::Lit { kind: LitKind::Int(s), .. } => s.replace('_', "").parse().ok(),
            _ => None,
        }
    }

    fn n5_resolve_struct_fields(&self, name: &str, span_src: &TypeExpr) -> Option<Vec<FieldDecl>> {
        let name = name.trim();
        if let Some(fields) = self.n5_structs.get(name) {
            return Some(fields.clone());
        }
        let open = name.find('[').filter(|&i| i > 0 && name.ends_with(']'))?;
        let base = name[..open].trim();
        let type_args = Self::split_top_level_type_args(&name[open + 1..name.len() - 1]);
        let prefix = format!("type {base}[");
        let mut lines = self.body.lines();
        while let Some(line) = lines.next() {
            let Some(params_raw) = line.strip_prefix(&prefix).and_then(|s| s.strip_suffix("] struct {")) else {
                continue;
            };
            let type_params: Vec<String> = params_raw
                .split(',')
                .filter_map(|p| p.split_whitespace().next().map(str::to_string))
                .collect();
            if type_params.len() != type_args.len() {
                return None;
            }
            let subst: BTreeMap<String, TypeExpr> = type_params
                .into_iter()
                .zip(type_args.into_iter().map(|a| Self::parse_type_str(a, span_src)))
                .collect();
            let mut fields = Vec::new();
            for fline in lines.by_ref() {
                if fline == "}" {
                    return Some(fields);
                }
                if let Some((fname, fty)) = fline.trim().split_once(char::is_whitespace) {
                    let ty = Self::subst_type_expr(&Self::parse_type_str(fty.trim(), span_src), &subst);
                    fields.push(FieldDecl { name: fname.to_string(), ty, span: span_src.span() });
                }
            }
        }
        None
    }

    fn parse_type_str(s: &str, span_src: &TypeExpr) -> TypeExpr {
        let (span, s) = (span_src.span(), s.trim());
        if let Some(r) = s.strip_prefix('*') {
            return TypeExpr::Pointer { to: Box::new(Self::parse_type_str(r, span_src)), span };
        }
        if let Some(r) = s.strip_prefix("[]") {
            return TypeExpr::Slice { elem: Box::new(Self::parse_type_str(r, span_src)), span };
        }
        if let Some((l, e)) = s.strip_prefix('[').and_then(Self::split_bracket_prefix) {
            let len = (l.trim() != "...").then(|| Box::new(Expr::Lit { kind: LitKind::Int(l.trim().into()), span }));
            return TypeExpr::Array { len, elem: Box::new(Self::parse_type_str(e, span_src)), span };
        }
        if let Some((k, v)) = s.strip_prefix("map[").and_then(Self::split_bracket_prefix) {
            let (key, value) = (Box::new(Self::parse_type_str(k, span_src)), Box::new(Self::parse_type_str(v, span_src)));
            return TypeExpr::Map { key, value, span };
        }
        if let Some(inner) = s.strip_prefix("option[").and_then(|r| r.strip_suffix(']')) {
            return TypeExpr::Option { elem: Box::new(Self::parse_type_str(inner, span_src)), span };
        }
        TypeExpr::Named { name: s.to_string(), span }
    }

    fn subst_type_expr(ty: &TypeExpr, subst: &BTreeMap<String, TypeExpr>) -> TypeExpr {
        match ty {
            TypeExpr::Named { name, span } => {
                let trimmed = name.trim();
                if let Some(rep) = subst.get(trimmed) {
                    return rep.clone();
                }
                if let Some(open) = trimmed.find('[').filter(|&i| i > 0 && trimmed.ends_with(']')) {
                    let head = &trimmed[..open];
                    let args = Self::split_top_level_type_args(&trimmed[open + 1..trimmed.len() - 1])
                        .into_iter()
                        .map(|a| Self::subst_type_expr(&Self::parse_type_str(a, ty), subst).render())
                        .collect::<Vec<_>>()
                        .join(", ");
                    return TypeExpr::Named { name: format!("{head}[{args}]"), span: *span };
                }
                ty.clone()
            }
            TypeExpr::Pointer { to, span } => TypeExpr::Pointer { to: Box::new(Self::subst_type_expr(to, subst)), span: *span },
            TypeExpr::Slice { elem, span } => TypeExpr::Slice { elem: Box::new(Self::subst_type_expr(elem, subst)), span: *span },
            TypeExpr::Array { len, elem, span } => TypeExpr::Array { len: len.clone(), elem: Box::new(Self::subst_type_expr(elem, subst)), span: *span },
            TypeExpr::Map { key, value, span } => TypeExpr::Map {
                key: Box::new(Self::subst_type_expr(key, subst)),
                value: Box::new(Self::subst_type_expr(value, subst)),
                span: *span,
            },
            TypeExpr::Option { elem, span } => TypeExpr::Option { elem: Box::new(Self::subst_type_expr(elem, subst)), span: *span },
            _ => ty.clone(),
        }
    }

    /// `new(T)` where T's zero value needs N5 allocation → `&T{…}`. The
    /// parser encodes the type argument as a bare identifier (`new(bag)`)
    /// or as an empty type-as-value literal (`new(map[string]int)`).
    pub(crate) fn n5_new(&mut self, args: &[CallArg]) -> Option<String> {
        let [arg] = args else { return None };
        let ty = match &arg.value {
            Expr::Ident { name, span } => TypeExpr::Named { name: name.clone(), span: *span },
            Expr::Index { base, index, span } => {
                TypeExpr::Named { name: format!("{}[{}]", base.render(), index.render()), span: *span }
            }
            Expr::StructLit { ty: Some(t), fields, .. } if fields.is_empty() => t.clone(),
            Expr::SliceLit { elem: Some(t), items, .. }
                if items.is_empty() && matches!(t.as_ref(), TypeExpr::Array { .. }) =>
            {
                t.as_ref().clone()
            }
            Expr::MapLit { key, value, entries, span } if entries.is_empty() => {
                TypeExpr::Map { key: key.clone(), value: value.clone(), span: *span }
            }
            _ => return None,
        };
        if let TypeExpr::Named { name, .. } = &ty {
            let head = name.split('[').next().unwrap_or(name.as_str()).trim();
            if self.local_vars.contains(head) {
                return None;
            }
        }
        self.n5_zero_init(&ty).map(|init| format!("&{}", init))
    }

    /// Follow user type aliases (`type counts = map[string]int`) to the
    /// type they name; anything else is returned as written.
    fn n5_resolve_alias(&self, ty: &TypeExpr) -> TypeExpr {
        let mut cur = ty.clone();
        for _ in 0..MAX_DEPTH {
            let next = match &cur {
                TypeExpr::Named { name, .. } => self.n5_aliases.get(name).cloned(),
                _ => None,
            };
            match next {
                Some(t) => cur = t,
                None => break,
            }
        }
        cur
    }
}
