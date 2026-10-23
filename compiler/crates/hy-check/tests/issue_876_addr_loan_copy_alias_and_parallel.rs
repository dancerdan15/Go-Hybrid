//! issue_876_addr_loan_copy_alias_and_parallel.rs — Fail-to-Pass regression
//! tests for #876 & #583 (T24, HYB-E3104): copy aliases (`alias := n`,
//! `alias = n`), conditional branch reassignments (`if cond { n = fallback }`),
//! and same-statement parallel bindings (`n, p := &m["a"], &n`), plus positive
//! control for same-scope non-loan re-assignment.

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
fn addr_of_copy_alias_of_slot_loan_is_rejected_e3104() {
    let r_local = check(
        "fn main() {\n    m := map[string]int{\"a\": 1}\n    n := &m[\"a\"]\n    alias := n\n    p := &alias\n    fmt.println(n, p)\n}\n",
    );
    assert_eq!(codes(&r_local), vec!["HYB-E3104".to_string()]);
    assert_eq!(r_local.verdict, Verdict::Reject);

    let r_assign = check(
        "fn main() {\n    m := map[string]int{\"a\": 1}\n    n := 0\n    n = &m[\"a\"]\n    alias := 0\n    alias = n\n    p := &alias\n    fmt.println(n, p)\n}\n",
    );
    assert_eq!(codes(&r_assign), vec!["HYB-E3104".to_string()]);
    assert_eq!(r_assign.verdict, Verdict::Reject);
}

#[test]
fn conditional_branch_reassign_and_parallel_slot_loan_reject_e3104() {
    let r_branch = check(
        "fn main() {\n    m := map[string]int{\"a\": 1}\n    n := 0\n    n = &m[\"a\"]\n    cond := false\n    fallback := 5\n    if cond {\n        n = fallback\n    }\n    p := &n\n    fmt.println(p)\n}\n",
    );
    assert_eq!(codes(&r_branch), vec!["HYB-E3104".to_string()]);
    assert_eq!(r_branch.verdict, Verdict::Reject);

    let r_par_local = check(
        "fn main() {\n    m := map[string]int{\"a\": 1}\n    n := 0\n    fmt.println(n)\n    n, p := &m[\"a\"], &n\n    fmt.println(n, p)\n}\n",
    );
    assert_eq!(codes(&r_par_local), vec!["HYB-E3104".to_string()]);
    assert_eq!(r_par_local.verdict, Verdict::Reject);

    let r_par_assign = check(
        "fn main() {\n    m := map[string]int{\"a\": 1}\n    n := 0\n    var p *int\n    n, p = &m[\"a\"], &n\n    fmt.println(n, p)\n}\n",
    );
    assert_eq!(codes(&r_par_assign), vec!["HYB-E3104".to_string()]);
    assert_eq!(r_par_assign.verdict, Verdict::Reject);
}

#[test]
fn same_scope_non_loan_rebind_clears_slot_loan_and_is_accepted() {
    let r = check(
        "fn main() {\n    m := map[string]int{\"a\": 1}\n    n := 0\n    n = &m[\"a\"]\n    fmt.println(n)\n    fallback := 5\n    n = fallback\n    p := &n\n    fmt.println(p)\n}\n",
    );
    assert!(codes(&r).is_empty(), "unexpected diagnostics: {:?}", r.diagnostics);
    assert_eq!(r.verdict, Verdict::Accept);
}
