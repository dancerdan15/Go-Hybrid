//! addr_loan_nested_arr_proj.rs — T24 (HYB-E3104) regression tests for nested
//! array/field projections (`&n.grid[0][1]`, `&n.cells[0].val`, `&(n.cells[1])`)
//! and closure-body index projections on O8 slot loans.

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
fn addr_of_nested_2d_array_projection_on_slot_loan_is_rejected_e3104() {
    let src = "\
struct board {
    grid [2][2]int
}

fn main() {
    m := map[string]board{\"a\": board{grid: [2][2]int{[2]int{1, 2}, [2]int{3, 4}}}}
    n := &m[\"a\"]
    p := &n.grid[0][1]
    _ = p
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject);
    assert_eq!(error_codes(&r), vec!["HYB-E3104".to_string()]);
}

#[test]
fn addr_of_index_then_field_and_parenthesized_proj_on_slot_loan_is_rejected_e3104() {
    let src = "\
struct cell {
    val int
}

struct row {
    cells [2]cell
}

fn main() {
    m := map[string]row{\"a\": row{cells: [2]cell{cell{val: 1}, cell{val: 2}}}}
    n := &m[\"a\"]
    p1 := &n.cells[0].val
    p2 := &(n.cells[1])
    _ = p1
    _ = p2
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
fn addr_of_loan_index_projection_inside_closure_is_rejected_e3104() {
    let src = "\
struct node {
    arr [2]int
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}}}
    n := &m[\"a\"]
    f := fn() {
        p := &n.arr[0]
        _ = p
    }
    f()
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject);
    assert_eq!(error_codes(&r), vec!["HYB-E3104".to_string()]);
}

#[test]
fn shadowed_non_loan_binding_nested_index_addr_is_accepted() {
    let src = "\
struct node {
    arr [2]int
}

fn main() {
    m := map[string]node{\"a\": node{arr: [2]int{1, 2}}}
    n := &m[\"a\"]
    n.arr[0] = 5
    if true {
        n := node{arr: [2]int{7, 8}}
        p := &n.arr[1]
        _ = p
    }
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "unexpected diagnostics: {:?}", r.diagnostics);
    assert!(error_codes(&r).is_empty());
}
