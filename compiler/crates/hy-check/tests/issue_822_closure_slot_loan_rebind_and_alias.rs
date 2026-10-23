//! Regression tests for issue #822: closure-body map-slot loans (`&m[k]`) must
//! reject whole-map rebinds (`m = ...`, `h.m = ...`) and mutations through
//! closure-local map aliases (`a := m; a[k2] = ...`) while the loan is live.

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    check_file(&ast)
}

fn codes(r: &hy_check::CheckResult) -> Vec<String> {
    r.diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code.clone())
        .collect()
}

#[test]
fn closure_whole_map_rebind_while_slot_loan_live_is_rejected() {
    let r = check(
        "struct cell { n int }\n\
         fn main() {\n\
         \x20   var m = map[string]cell{\"a\": cell{n: 1}}\n\
         \x20   f := fn() {\n\
         \x20       p := &m[\"a\"]\n\
         \x20       m = map[string]cell{\"b\": cell{n: 2}}\n\
         \x20       p.n = 3\n\
         \x20   }\n\
         \x20   f()\n\
         \x20   fmt.println(m[\"a\"].n)\n\
         }\n",
    );
    assert_eq!(codes(&r), vec!["HYB-E3102".to_string()]);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn closure_field_map_rebind_while_slot_loan_live_is_rejected() {
    let r = check(
        "struct cell { n int }\n\
         struct holder { m map[string]cell }\n\
         fn main() {\n\
         \x20   var h = holder{m: map[string]cell{\"a\": cell{n: 1}}}\n\
         \x20   f := fn() {\n\
         \x20       p := &h.m[\"a\"]\n\
         \x20       h.m = map[string]cell{\"b\": cell{n: 2}}\n\
         \x20       p.n = 3\n\
         \x20   }\n\
         \x20   f()\n\
         \x20   fmt.println(h.m[\"a\"].n)\n\
         }\n",
    );
    assert_eq!(codes(&r), vec!["HYB-E3102".to_string()]);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn closure_local_alias_insert_while_slot_loan_live_is_rejected() {
    let r = check(
        "struct cell { n int }\n\
         fn main() {\n\
         \x20   m := map[string]cell{\"a\": cell{n: 1}}\n\
         \x20   f := fn() {\n\
         \x20       p := &m[\"a\"]\n\
         \x20       a := m\n\
         \x20       a[\"b\"] = cell{n: 2}\n\
         \x20       p.n = 3\n\
         \x20   }\n\
         \x20   f()\n\
         }\n",
    );
    assert_eq!(codes(&r), vec!["HYB-E3102".to_string()]);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn closure_rebind_and_alias_insert_after_slot_loan_expires_is_accepted() {
    let r = check(
        "struct cell { n int }\n\
         fn main() {\n\
         \x20   var m = map[string]cell{\"a\": cell{n: 1}}\n\
         \x20   f := fn() {\n\
         \x20       p := &m[\"a\"]\n\
         \x20       p.n = 3\n\
         \x20       a := m\n\
         \x20       a[\"b\"] = cell{n: 2}\n\
         \x20       m = map[string]cell{\"c\": cell{n: 4}}\n\
         \x20   }\n\
         \x20   f()\n\
         \x20   fmt.println(m[\"c\"].n)\n\
         }\n",
    );
    assert!(codes(&r).is_empty(), "unexpected diagnostics: {:?}", r.diagnostics);
    assert_eq!(r.verdict, Verdict::Accept);
}
