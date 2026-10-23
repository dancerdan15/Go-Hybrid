//! issue_583_auto_addr_call_on_slot_loan.rs — Fail-to-Pass regression tests
//! for #583 & #876 (T24, HYB-E3104): auto-addressed `*T` function call
//! arguments on projections of an O8 map-slot loan (`touch(n.arr[0])`,
//! `touch(n.val)`), plus positive controls for passing the loan root (`touch_node(n)`)
//! and invoking a pointer-receiver method on the loan root (`n.bump()`).

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
fn auto_addressed_pointer_param_on_slot_loan_projections_is_rejected_e3104() {
    let src_index = "struct node { arr [2]int, val int }\nfn touch(p *int) { fmt.println(p) }\n\nfn main() {\n    m := map[string]node{\"a\": node{arr: [2]int{10, 20}, val: 3}}\n    n := &m[\"a\"]\n    touch(n.arr[0])\n}\n";
    let r_index = check(src_index);
    assert_eq!(codes(&r_index), vec!["HYB-E3104".to_string()]);
    assert_eq!(r_index.verdict, Verdict::Reject);

    let src_field = "struct node { arr [2]int, val int }\nfn touch(p *int) { fmt.println(p) }\n\nfn main() {\n    m := map[string]node{\"a\": node{arr: [2]int{10, 20}, val: 3}}\n    n := &m[\"a\"]\n    touch(n.val)\n}\n";
    let r_field = check(src_field);
    assert_eq!(codes(&r_field), vec!["HYB-E3104".to_string()]);
    assert_eq!(r_field.verdict, Verdict::Reject);
}

#[test]
fn direct_loan_pass_and_pointer_receiver_method_on_loan_root_are_accepted() {
    let src = "struct node { val int }\nfn touch_node(p *node) { p.val = p.val + 1 }\nfn (self *node) bump() { self.val = self.val + 1 }\n\nfn main() {\n    m := map[string]node{\"a\": node{val: 3}}\n    n := &m[\"a\"]\n    touch_node(n)\n    n.bump()\n    fmt.println(n.val)\n}\n";
    let r = check(src);
    assert!(codes(&r).is_empty(), "unexpected diagnostics: {:?}", r.diagnostics);
    assert_eq!(r.verdict, Verdict::Accept);
}
