//! Regression tests for issue #971 (part 2):
//! `Stmt::Defer` targeting value-only builtins (`defer len(s)`, `defer cap(s)`,
//! `defer make(...)`, `defer new(int)`, `defer append(s, 1)`, `defer min(...)`,
//! `defer max(...)`, `defer complex(...)`) or type conversions (`defer int(x)`,
//! `defer string(s)`, `defer []byte(s)`, `defer my_int(x)`) must be rejected
//! with `HYB-E2001` ("defer discards result of ...").

use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn assert_reject_e2001(src: &str) {
    let ast = parse_file("test.hy", src).expect("must parse");
    let res = check_file(&ast);
    assert_eq!(
        res.verdict,
        Verdict::Reject,
        "expected Reject, got Accept: {:?}",
        res.diagnostics
    );
    assert!(
        res.diagnostics.iter().any(|d| d.code == "HYB-E2001"),
        "expected HYB-E2001, got: {:?}",
        res.diagnostics
    );
}

fn assert_accept(src: &str) {
    let ast = parse_file("test.hy", src).expect("must parse");
    let res = check_file(&ast);
    assert_eq!(
        res.verdict,
        Verdict::Accept,
        "expected Accept, got diagnostics: {:?}",
        res.diagnostics
    );
}

#[test]
fn test_defer_value_only_builtins_rejected_with_e2001() {
    for snippet in [
        "package main\nfn main() {\n    s := \"abc\"\n    defer len(s)\n}",
        "package main\nfn main() {\n    s := []int{1}\n    defer cap(s)\n}",
        "package main\nfn main() {\n    defer make([]int, 5)\n}",
        "package main\nfn main() {\n    defer new(int)\n}",
        "package main\nfn main() {\n    s := []int{1}\n    defer append(s, 1)\n}",
        "package main\nfn main() {\n    defer min(1, 2)\n}",
        "package main\nfn main() {\n    defer max(1, 2)\n}",
        "package main\nfn main() {\n    defer complex(1.0, 2.0)\n}",
        "package main\nfn main() {\n    s := \"abc\"\n    defer (len)(s)\n}",
    ] {
        assert_reject_e2001(snippet);
    }
}

#[test]
fn test_defer_type_conversions_rejected_with_e2001() {
    for snippet in [
        "package main\nfn main() {\n    x := 1\n    defer int(x)\n}",
        "package main\nfn main() {\n    s := \"abc\"\n    defer string(s)\n}",
        "package main\nfn main() {\n    s := \"abc\"\n    defer []byte(s)\n}",
        "package main\ntype my_int = int\nfn main() {\n    x := 1\n    defer my_int(x)\n}",
        "package main\nfn main() {\n    x := 1\n    defer any(x)\n}",
    ] {
        assert_reject_e2001(snippet);
    }
}

#[test]
fn test_defer_in_closure_rejected_with_e2001() {
    for snippet in [
        "package main\nfn main() {\n    f := fn() {\n        s := \"abc\"\n        defer len(s)\n    }\n    f()\n}",
        "package main\nfn main() {\n    f := fn() {\n        x := 1\n        defer int(x)\n    }\n    f()\n}",
    ] {
        assert_reject_e2001(snippet);
    }
}

#[test]
fn test_defer_side_effect_builtins_and_user_fns_accepted() {
    assert_accept(
        r#"package main

struct resource {}
fn (r resource) close() {}

fn user_fn() {}

fn min(a int, b int) {
    _ = a
    _ = b
}

fn main() {
    ch := make(chan int, 1)
    m := map[string]int{"k": 1}
    r := resource{}
    defer close(ch)
    defer delete(m, "k")
    defer print("done")
    defer println("done")
    defer user_fn()
    defer r.close()
    defer min(1, 2)
    len := fn() {}
    defer len()
    f := fn() {
        cap := fn() {}
        defer cap()
    }
    f()
}
"#,
    );
}
