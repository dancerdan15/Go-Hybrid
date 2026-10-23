//! Regression tests for Issue #765 (`HYB-E2002` / T23): multi-value return
//! arity checking for channel-received functions (`(<-ch)(...)`, `f := <-ch`,
//! `select { f := <-ch => ... }`) and loop-variable function calls
//! (`for _, f in fns { ... }`).

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
fn channel_received_multi_value_fn_calls_reject_e2002() {
    assert_rejects_e2002(
        "type pair_fn = fn(a int, b int) int int\nconst _anchor = 0\n\
         fn main() {\n    ch := make(chan pair_fn, 1)\n    x := (<-ch)(3, 9)\n    fmt.println(x)\n}\n",
    );
    assert_rejects_e2002(
        "type pair_fn = fn(a int, b int) int int\nconst _anchor = 0\n\
         fn main() {\n    ch := make(chan pair_fn, 1)\n    f := <-ch\n    x := f(3, 9)\n    fmt.println(x)\n}\n",
    );
    assert_rejects_e2002(
        "type pair_fn = fn(a int, b int) int int\nconst _anchor = 0\n\
         fn main() {\n    ch := make(chan pair_fn, 1)\n\
             select {\n        f := <-ch => {\n            x := f(3, 9)\n            fmt.println(x)\n        }\n    }\n}\n",
    );
}

#[test]
fn loop_variable_multi_value_fn_calls_reject_e2002() {
    assert_rejects_e2002(
        "fn minmax(a int, b int) (int, int) { return a, b }\n\
         fn main() {\n    fns := []fn(a int, b int) int int{minmax}\n\
             for _, f in fns {\n        x := f(3, 9)\n        fmt.println(x)\n    }\n}\n",
    );
    assert_rejects_e2002(
        "type pair_fn = fn(a int, b int) int int\nconst _anchor = 0\n\
         fn main() {\n    ch := make(chan pair_fn, 1)\n\
             for f in ch {\n        x := f(3, 9)\n        fmt.println(x)\n    }\n}\n",
    );
}

#[test]
fn channel_and_loop_multi_value_destructuring_positive_controls_accept() {
    assert_accepts(
        "type pair_fn = fn(a int, b int) int int\nconst _anchor = 0\n\
         fn minmax(a int, b int) (int, int) { return a, b }\n\
         fn main() {\n\
             ch := make(chan pair_fn, 2)\n\
             a1, b1 := (<-ch)(3, 9)\n\
             f_recv := <-ch\n\
             a2, b2 := f_recv(a1, b1)\n\
             fns := []pair_fn{minmax}\n\
             for _, f_loop in fns {\n\
                 a3, b3 := f_loop(a2, b2)\n\
                 fmt.println(\"{a3} {b3}\")\n\
             }\n\
         }\n",
    );
}
