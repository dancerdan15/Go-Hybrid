//! Regression tests for Issue #765 (`HYB-E2002` / T23): multi-value return
//! arity checking for single-type-arg generic function and method calls,
//! generic struct `fn` fields, and local method / generic function values.

use hy_check::{check_file, CheckResult, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    check_file(&ast)
}

fn error_codes(r: &CheckResult) -> Vec<String> {
    r.diagnostics.iter().filter(|d| d.severity == Severity::Error).map(|d| d.code.clone()).collect()
}

fn assert_rejects_e2002(src: &str) {
    let r = check(src);
    assert_eq!(error_codes(&r), vec!["HYB-E2002".to_string()], "src:\n{src}");
    assert_eq!(r.verdict, Verdict::Reject);
}

fn assert_accepts(src: &str) {
    let r = check(src);
    assert!(error_codes(&r).is_empty(), "unexpected diagnostics: {:?}\nsrc:\n{src}", r.diagnostics);
    assert_eq!(r.verdict, Verdict::Accept);
}

#[test]
fn single_type_arg_generic_fn_and_method_calls_reject_e2002() {
    assert_rejects_e2002(
        "fn pair[t](a t, b t) (t, t) { return a, b }\n\
         fn main() {\n    x := pair[int](3, 9)\n    fmt.println(x)\n}\n",
    );
    assert_rejects_e2002(
        "struct pair_box[t] { a t\n b t }\n\
         fn (b pair_box[t]) pair() (t, t) { return b.a, b.b }\n\
         fn main() {\n    b := pair_box[int]{a: 3, b: 9}\n    x := b.pair()\n    fmt.println(x)\n}\n",
    );
    assert_rejects_e2002(
        "struct box_val { v int }\n\
         fn box_val.pair[t](x t, y t) (t, t) { return x, y }\n\
         fn main() {\n    x := box_val.pair[int](3, 9)\n    fmt.println(x)\n}\n",
    );
}

#[test]
fn generic_struct_fn_fields_reject_e2002() {
    assert_rejects_e2002(
        "type pair_fn = fn(a int, b int) int int\n\
         struct holder[t] { f t }\n\
         fn minmax(a int, b int) (int, int) { return a, b }\n\
         fn main() {\n    h := holder[pair_fn]{f: minmax}\n    x := h.f(3, 9)\n    fmt.println(x)\n}\n",
    );
    assert_rejects_e2002(
        "struct fn_holder[t] { f fn(a t, b t) t t }\n\
         fn minmax(a int, b int) (int, int) { return a, b }\n\
         fn main() {\n    h := fn_holder[int]{f: minmax}\n    x := h.f(3, 9)\n    fmt.println(x)\n}\n",
    );
}

#[test]
fn bound_method_values_and_indexed_generic_fields_reject_e2002() {
    assert_rejects_e2002(
        "struct pair_box { a int\n b int }\n\
         fn (b pair_box) pair() (int, int) { return b.a, b.b }\n\
         fn main() {\n    b := pair_box{a: 3, b: 9}\n    f := b.pair\n    x := f()\n    fmt.println(x)\n}\n",
    );
    assert_rejects_e2002(
        "struct pair_box[t] { a t\n b t }\n\
         fn (b pair_box[t]) pair() (t, t) { return b.a, b.b }\n\
         fn main() {\n    b := pair_box[int]{a: 3, b: 9}\n    f := b.pair\n    x := f()\n    fmt.println(x)\n}\n",
    );
    assert_rejects_e2002(
        "type pair_fn = fn(a int, b int) int int\n\
         struct holder[t] { f t }\n\
         fn minmax(a int, b int) (int, int) { return a, b }\n\
         fn main() {\n    fns := []pair_fn{minmax}\n    h := holder[[]pair_fn]{f: fns}\n    x := h.f[0](3, 9)\n    fmt.println(x)\n}\n",
    );
}

#[test]
fn generic_and_method_value_destructuring_positive_controls_accept() {
    assert_accepts(
        "type pair_fn = fn(a int, b int) int int\n\
         struct holder[t] { f t }\n\
         struct pair_box { a int\n b int }\n\
         fn (b pair_box) pair() (int, int) { return b.a, b.b }\n\
         fn pair[t](a t, b t) (t, t) { return a, b }\n\
         fn minmax(a int, b int) (int, int) { return a, b }\n\
         fn main() {\n\
             a1, b1 := pair[int](3, 9)\n\
             bx := pair_box{a: a1, b: b1}\n\
             f_meth := bx.pair\n\
             a2, b2 := f_meth()\n\
             h := holder[pair_fn]{f: minmax}\n\
             a3, b3 := h.f(a2, b2)\n\
             fmt.println(\"{a3} {b3}\")\n\
         }\n",
    );
}
