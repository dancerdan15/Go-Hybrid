//! Regression tests for issue #811:
//! `for ... in` loop variable shadowing (`for a, b in a`, `for xs in xs`, and
//! `for table, row in table` over `const table`) must preserve O5.2 `may_share`
//! edges, `shadow_links` across scope exit, `non_owned_origin`, and T22 `const`
//! deep immutability.

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("issue_811.hy", src).expect("snippet must parse");
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
fn for_index_shadowing_collection_preserves_elem_share_and_range_origin() {
    let src = "\
import \"fmt\"
fn worker(x [][]int) { fmt.println(len(x)) }
fn worker_row(r []int) { fmt.println(len(r)) }
fn leak_via_shadowed_index() {
    a := [][]int{[]int{1, 2}}
    var saved []int = []int{0}
    for a, b in a {
        fmt.println(a)
        saved = b
    }
    task worker(move a)
    saved[0] = 99
}
fn move_range_elem_when_index_shadows() {
    a := [][]int{[]int{1, 2}}
    for a, b in a {
        fmt.println(a)
        task worker_row(move b)
    }
}";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    let codes = error_codes(&r);
    assert!(codes.contains(&"HYB-E3003".to_string()), "{:?}", r.diagnostics);
    assert!(codes.contains(&"HYB-E3004".to_string()), "{:?}", r.diagnostics);
}

#[test]
fn for_single_var_self_shadowing_collection_preserves_outer_share_peers() {
    let src = "\
import \"fmt\"
fn worker(x [][]int) { fmt.println(len(x)) }
fn main() {
    xs := [][]int{[]int{1, 2}}
    alias := xs
    for xs in xs {
        task worker(move alias)
        xs[0] = 99
    }
}";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E3003".to_string()), "{:?}", r.diagnostics);
}

#[test]
fn for_shadowing_const_collection_preserves_const_immutability_e3006() {
    let src = "\
import \"fmt\"
const table = [][]int{[]int{1, 2}}
fn mutate_self_shadow() {
    for table in table { table[0] = 99 }
}
fn mutate_index_shadow() {
    for table, row in table {
        fmt.println(table)
        row[0] = 99
    }
}";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert_eq!(error_codes(&r).iter().filter(|c| *c == "HYB-E3006").count(), 2);
}

#[test]
fn for_iterate_without_shadowing_or_move_is_accepted() {
    let src = "\
import \"fmt\"
fn main() {
    xs := [][]int{[]int{1, 2}}
    for i, v in xs { fmt.println(i, len(v)) }
}";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "{:?}", r.diagnostics);
}
