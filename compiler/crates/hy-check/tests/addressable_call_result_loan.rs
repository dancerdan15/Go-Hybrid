//! addressable_call_result_loan.rs — O8 loans on field or array-element
//! projections of value-returning calls (`&get_pair().x`, `&get_arr()[0]`)
//! emit unbuildable Go store-backs (`get_pair().x = n`) and must reject as
//! HYB-E2010 (rule BUILDABLE).

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
fn rejects_struct_call_field_loan() {
    rejects_with(
        "struct pair {\n    x int\n}\nfn get_pair() pair {\n    return pair{x: 1}\n}\nfn main() {\n    n := &get_pair().x\n    _ = n\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_array_call_index_loan() {
    rejects_with(
        "fn get_arr() [2]int {\n    var arr [2]int\n    return arr\n}\nfn main() {\n    n := &get_arr()[0]\n    _ = n\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_nested_struct_and_array_call_projection_loan() {
    rejects_with(
        "struct holder {\n    arr [2]int\n}\nfn get_holder() holder {\n    return holder{}\n}\nfn main() {\n    n := &(get_holder().arr[0])\n    _ = n\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_method_and_closure_value_call_field_loans() {
    rejects_with(
        "struct pair {\n    x int\n}\nstruct box {\n    p pair\n}\nfn (b *box) copy_pair() pair {\n    return b.p\n}\nfn main() {\n    b := box{p: pair{x: 1}}\n    n1 := &b.copy_pair().x\n    mk := fn() pair {\n        return pair{x: 2}\n    }\n    n2 := &mk().x\n    _ = n1\n    _ = n2\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn accepts_pointer_slice_and_map_call_result_loans() {
    accepts(
        "struct pair {\n    x int\n}\nfn get_slice() []pair {\n    return []pair{pair{x: 1}}\n}\nfn get_map() map[string]pair {\n    return map[string]pair{\"a\": pair{x: 2}}\n}\nfn main() {\n    n1 := &new(pair).x\n    n2 := &get_slice()[0].x\n    n3 := &get_map()[\"a\"]\n    local_pair := pair{x: 3}\n    n4 := &local_pair.x\n    _ = n1\n    _ = n2\n    _ = n3\n    _ = n4\n}\n",
    );
}
