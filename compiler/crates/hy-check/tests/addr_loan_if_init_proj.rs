//! addr_loan_if_init_proj.rs — T24 (HYB-E3104) regression tests for `Stmt::If`
//! initializers and conditions taking the address of a slot loan or its
//! field/index projections (`if p := &n.arr[0]; ...`, `if p := &n.val; ...`).

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    check_file(&ast)
}

fn error_codes(r: &hy_check::CheckResult) -> Vec<String> {
    r.diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code.clone())
        .collect()
}

#[test]
fn if_init_addr_of_loan_field_and_index_proj_rejected_once_e3104() {
    let src = "\
struct node {
    arr [2]int
    val int
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}, val: 3}}
    n := &m[\"a\"]
    if p := &n.val; p != nil {
        _ = p
    }
    if q := &n.arr[0]; q != nil {
        _ = q
    }
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject);
    assert_eq!(
        error_codes(&r),
        vec!["HYB-E3104".to_string(), "HYB-E3104".to_string()]
    );
}

#[test]
fn closure_if_init_addr_of_loan_projection_is_rejected_e3104() {
    let src = "\
struct node {
    arr [2]int
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}}}
    n := &m[\"a\"]
    f := fn() {
        if p := &n.arr[0]; p != nil {
            _ = p
        }
    }
    f()
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject);
    assert_eq!(error_codes(&r), vec!["HYB-E3104".to_string()]);
}

#[test]
fn if_init_slot_loan_used_with_addr_of_index_in_cond_is_rejected_e3104() {
    let src = "\
struct node {
    arr [2]int
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}}}
    if n := &m[\"a\"]; (&n.arr[0]) != nil {
        _ = n.arr[0]
    }
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject);
    assert_eq!(error_codes(&r), vec!["HYB-E3104".to_string()]);
}

#[test]
fn if_init_valid_slot_loan_and_plain_local_addr_are_accepted() {
    let src = "\
struct node {
    arr [2]int
    val int
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}, val: 3}}
    if n := &m[\"a\"]; n.val > 0 {
        n.arr[0] = 9
    }
    local_node := node{arr: [2]int{4, 5}, val: 6}
    if p := &local_node.arr[0]; p != nil {
        _ = p
    }
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "unexpected diagnostics: {:?}", r.diagnostics);
    assert!(error_codes(&r).is_empty());
}
