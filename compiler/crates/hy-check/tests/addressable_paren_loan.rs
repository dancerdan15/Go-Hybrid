//! addressable_paren_loan.rs — parenthesized O8 loan operands (`&(m[k].f)`,
//! `&((m[k])[0])`, `&(s[0])`) must unwrap parentheses and reject unaddressable
//! store-back places as HYB-E2010 (rule BUILDABLE).

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

fn rejects_with(src: &str, code: &str) {
    let r = check(src);
    let cs = error_codes(&r);
    assert!(
        cs.iter().any(|c| c == code),
        "expected {}, got {:?}\nsrc:\n{}",
        code,
        cs,
        src
    );
    assert_eq!(r.verdict, Verdict::Reject, "src:\n{}", src);
}

fn accepts(src: &str) {
    let r = check(src);
    assert!(
        error_codes(&r).is_empty() && r.verdict == Verdict::Accept,
        "must accept, got {:?}\nsrc:\n{}",
        r.diagnostics,
        src
    );
}

#[test]
fn rejects_paren_map_field_loan() {
    rejects_with(
        "struct acct {\n    bal int\n}\nfn main() {\n    m := map[string]acct{\"a\": acct{bal: 1}}\n    n := &(m[\"a\"].bal)\n    _ = n\n    _ = m\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_nested_paren_map_field_and_array_loan() {
    rejects_with(
        "struct acct {\n    arr [2]int\n}\nfn main() {\n    m := map[string]acct{\"a\": acct{}}\n    n := &(((m[\"a\"]).arr)[0])\n    _ = n\n    _ = m\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_paren_map_array_elem_loan_rebind() {
    rejects_with(
        "fn main() {\n    var arr [2]int\n    m := map[string][2]int{\"a\": arr}\n    var n int = 0\n    n = &(m[\"a\"][0])\n    _ = n\n    _ = m\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_paren_string_index_loan() {
    rejects_with(
        "fn main() {\n    s := \"hello\"\n    n := &((s)[0])\n    _ = n\n    _ = s\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn accepts_paren_whole_map_slot_and_slice_elem_loans() {
    accepts(
        "struct acct {\n    bal int\n}\nfn main() {\n    m := map[string]acct{\"a\": acct{bal: 1}}\n    n := &(m[\"a\"])\n    n.bal = 2\n    s := []acct{acct{bal: 3}}\n    p := &(s[0].bal)\n    _ = p\n    _ = m\n    _ = s\n}\n",
    );
}
