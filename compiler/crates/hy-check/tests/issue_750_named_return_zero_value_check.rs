//! Regression tests for issue #750:
//! Named return parameters on top-level functions, methods, and function
//! literals (`Expr::FnLit`) are zero-initialized at function entry in Go and
//! must enforce N3 zero-value checks (`HYB-E4002`).

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("issue_750.hy", src).expect("snippet must parse");
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
fn named_return_enum_and_struct_without_zero_value_reject_e4002() {
    let src = "\
enum msg {
    data(int)
    ping
}
struct envelope { inner msg }
fn bad_enum() (m msg) {
    m = msg.data(1)
    return
}
fn bad_struct() (e envelope) {
    e = envelope{inner: msg.data(2)}
    return
}";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert_eq!(error_codes(&r).iter().filter(|c| *c == "HYB-E4002").count(), 2);
}

#[test]
fn closure_named_return_without_zero_value_rejects_e4002() {
    let src = "\
import \"fmt\"
enum msg { data(int) }
struct envelope { inner msg }
fn main() {
    make_m := fn() (m msg) {
        m = msg.data(5)
        return
    }
    make_e := fn() (e envelope) {
        e = envelope{inner: msg.data(6)}
        return
    }
    fmt.println(make_m(), make_e())
}";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert_eq!(error_codes(&r).iter().filter(|c| *c == "HYB-E4002").count(), 2);
}

#[test]
fn named_return_with_valid_zero_value_and_unnamed_return_are_accepted() {
    let src = "\
import \"fmt\"
enum msg { data(int) }
enum status {
    idle
    busy(int)
}
fn ok_int() (r int) {
    r = 7
    return
}
fn ok_status() (s status) {
    s = status.idle
    return
}
fn ok_unnamed() msg { return msg.data(9) }
fn main() { fmt.println(ok_int(), ok_status(), ok_unnamed()) }";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "{:?}", r.diagnostics);
}
