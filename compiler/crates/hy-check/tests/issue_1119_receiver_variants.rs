//! issue_1119_receiver_variants.rs — regression tests for #1119 across generic
//! struct receivers (`box[t]`, `*box[t]`) and type-alias receivers (`type Alias = counter`).

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
fn rejects_generic_struct_value_receiver_field_method_collision() {
    let src = "\
struct box[t] { val t }
fn (b box[t]) val() int { return 1 }
fn main() {}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E1002".to_string()));
}

#[test]
fn rejects_generic_struct_pointer_receiver_field_method_collision() {
    let src = "\
struct box[t] { val t }
fn (b *box[t]) val() int { return 1 }
fn main() {}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E1002".to_string()));
}

#[test]
fn rejects_type_alias_value_receiver_field_method_collision() {
    let src = "\
struct counter { n int }
type alias_t = counter
fn (c alias_t) n() int { return 1 }
fn main() {}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E1002".to_string()));
}

#[test]
fn rejects_type_alias_pointer_receiver_field_method_collision() {
    let src = "\
struct counter { n int }
type alias_t = counter
fn (c *alias_t) n() int { return 1 }
fn main() {}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E1002".to_string()));
}

#[test]
fn rejects_aliased_pointer_receiver_field_method_collision() {
    let src = "\
struct counter { n int }
type ptr_t = *counter
fn (c ptr_t) n() int { return 1 }
fn main() {}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E1002".to_string()));
}

#[test]
fn rejects_bodiless_method_field_method_collision() {
    let src = "\
struct counter { n int }
fn (c counter) n() int
fn main() {}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).contains(&"HYB-E1002".to_string()));
}

#[test]
fn accepts_generic_value_and_pointer_receivers_with_distinct_method_names() {
    let src = "\
struct box[t] { val t }
fn (b box[t]) get_val() t { return b.val }
fn (b *box[t]) set_val(v t) { b.val = v }
fn main() {
    mut b := box[int]{val: 7}
    b.set_val(9)
    _ = b.get_val()
}
";
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Accept, "diags: {:?}", r.diagnostics);
    assert!(error_codes(&r).is_empty());
}
