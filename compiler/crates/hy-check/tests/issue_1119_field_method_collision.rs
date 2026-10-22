//! issue_1119_field_method_collision.rs — regression tests for #1119:
//! a struct field and a method on that struct cannot share the same non-blank
//! name in Go (`field and method with the same name <x>`, HYB-E1002, Row 13 BUILDABLE).

use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("t.hy", src).expect("snippet parses");
    check_file(&ast)
}

fn error_codes(r: &hy_check::CheckResult) -> Vec<String> {
    r.diagnostics
        .iter()
        .filter(|d| d.severity == hy_diag::Severity::Error)
        .map(|d| d.code.clone())
        .collect()
}

#[test]
fn rejects_value_receiver_field_method_same_name() {
    let src = "\
struct counter { n int }
fn (c counter) n() int { return 1 }
fn main() {
    c := counter{n: 2}
    _ = c
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E1002".to_string()));
    assert!(
        r.diagnostics
            .iter()
            .any(|d| d.message.contains("field and method with the same name n")),
        "expected field/method collision diagnostic, got: {:?}",
        r.diagnostics
    );
}

#[test]
fn rejects_pointer_receiver_field_method_same_name() {
    let src = "\
struct counter { n int }
fn (c *counter) n() int { return 1 }
fn main() {
    c := counter{n: 2}
    _ = c
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E1002".to_string()));
}

#[test]
fn rejects_method_declared_before_struct_definition() {
    let src = "\
fn (c counter) n() int { return 1 }
struct counter {
    ok int
    n int
}
fn main() {}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E1002".to_string()));
}

#[test]
fn accepts_distinct_field_and_method_names() {
    let src = "\
struct counter { n int }
fn (c counter) get() int { return c.n }
fn main() {
    c := counter{n: 2}
    _ = c.get()
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).is_empty());
}

#[test]
fn accepts_same_method_name_on_different_struct() {
    let src = "\
struct counter { n int }
struct other { x int }
fn (o other) n() int { return o.x }
fn main() {
    c := counter{n: 2}
    o := other{x: c.n}
    _ = o.n()
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).is_empty());
}

#[test]
fn accepts_blank_identifier_field_and_method() {
    let src = "\
struct counter {
    _ int
    n int
}
fn (c counter) _() int { return c.n }
fn main() {
    c := counter{n: 2}
    _ = c.n
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).is_empty());
}
