//! addr_loan_index_proj.rs — T24 (HYB-E3104) regression tests for address-of
//! on index projections rooted at an O8 slot loan (`&n.arr[0]`, `&n[0]`).

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
fn addr_of_struct_field_array_index_on_map_slot_loan_is_rejected_e3104() {
    let src = "\
struct node {
    arr [2]int
    val int
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}, val: 3}}
    n := &m[\"a\"]
    p := &n.arr[0]
    _ = p
    _ = n.val
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject);
    assert_eq!(error_codes(&r), vec!["HYB-E3104".to_string()]);
}

#[test]
fn addr_of_direct_index_on_array_map_slot_loan_is_rejected_e3104() {
    let src = "\
fn main() {
    m := map[string][2]int{\"a\": [2]int{10, 20}}
    n := &m[\"a\"]
    p := &n[0]
    _ = p
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject);
    assert_eq!(error_codes(&r), vec!["HYB-E3104".to_string()]);
}

#[test]
fn addr_of_loan_index_projection_in_call_and_return_is_rejected_e3104() {
    let call_src = "\
struct node {
    arr [2]int
}

fn touch(p *int) {
    _ = p
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}}}
    n := &m[\"a\"]
    touch(&n.arr[0])
}
";
    let r_call = check(call_src);
    assert_eq!(r_call.verdict, Verdict::Reject);
    assert_eq!(error_codes(&r_call), vec!["HYB-E3104".to_string()]);

    let ret_src = "\
struct node {
    arr [2]int
}

fn leak(m map[string]node) *int {
    n := &m[\"a\"]
    return &n.arr[0]
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}}}
    p := leak(m)
    _ = p
}
";
    let r_ret = check(ret_src);
    assert_eq!(r_ret.verdict, Verdict::Reject);
    assert_eq!(error_codes(&r_ret), vec!["HYB-E3104".to_string()]);
}

#[test]
fn direct_loan_index_mutation_and_plain_local_index_addr_are_accepted() {
    let src = "\
struct node {
    arr [2]int
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}}}
    n := &m[\"a\"]
    n.arr[0] = 9
    local_node := node{arr: [2]int{3, 4}}
    p := &local_node.arr[0]
    _ = p
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "unexpected diagnostics: {:?}", r.diagnostics);
    assert!(error_codes(&r).is_empty());
}
