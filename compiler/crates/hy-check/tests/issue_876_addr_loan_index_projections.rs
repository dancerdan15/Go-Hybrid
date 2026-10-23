//! issue_876_addr_loan_index_projections.rs — Fail-to-Pass regression tests
//! for #876 (T24, HYB-E3104): taking the address of index and mixed
//! field/index projections rooted at an O8 map-slot loan (`&n.arr[0]`,
//! `&n[0]`, `&n.arr[0].x`), plus positive control for nested map-slot loans.

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse: {:?}", diags));
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
fn addr_of_field_array_index_projection_on_slot_loan_is_rejected_e3104() {
    let src = "struct node { arr [2]int }\n\nfn main() {\n    m := map[string]node{\"a\": node{arr: [2]int{10, 20}}}\n    n := &m[\"a\"]\n    p := &n.arr[0]\n    fmt.println(p)\n}\n";
    let r = check(src);
    assert_eq!(codes(&r), vec!["HYB-E3104".to_string()]);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn addr_of_direct_array_index_projection_on_slot_loan_is_rejected_e3104() {
    let src = "fn main() {\n    m := map[string][2]int{\"a\": [2]int{10, 20}}\n    n := &m[\"a\"]\n    p := &n[0]\n    fmt.println(p)\n}\n";
    let r = check(src);
    assert_eq!(codes(&r), vec!["HYB-E3104".to_string()]);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn addr_of_mixed_index_and_field_projection_on_slot_loan_is_rejected_e3104() {
    let src = "struct item { x int }\nstruct node { arr [2]item }\n\nfn main() {\n    m := map[string]node{\"a\": node{arr: [2]item{item{x: 1}, item{x: 2}}}}\n    n := &m[\"a\"]\n    p := &n.arr[0].x\n    fmt.println(p)\n}\n";
    let r = check(src);
    assert_eq!(codes(&r), vec!["HYB-E3104".to_string()]);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn nested_map_slot_loan_through_loan_field_is_accepted() {
    let src = "struct bucket { sub map[string]int }\n\nfn main() {\n    m := map[string]bucket{\"a\": bucket{sub: map[string]int{\"k\": 7}}}\n    n := &m[\"a\"]\n    sub := &n.sub[\"k\"]\n    fmt.println(sub)\n}\n";
    let r = check(src);
    assert!(codes(&r).is_empty(), "unexpected diagnostics: {:?}", r.diagnostics);
    assert_eq!(r.verdict, Verdict::Accept);
}
