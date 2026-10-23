//! Regression tests for issue #971 (part 1):
//! Bare expression statements (`Stmt::Expr`) that discard values without
//! side-effects (value-only builtins `len`, `cap`, `make`, `new`, `append`,
//! `min`, `max`, `complex`, `real`, `imag`; type conversions `int(x)`,
//! `string(s)`, `[]byte(s)`, aliases; bare variables, literals, operators,
//! projections, composite/fn literals) must be rejected with `HYB-E1002`.

use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn assert_reject_e1002(src: &str) {
    let ast = parse_file("test.hy", src).expect("must parse");
    let res = check_file(&ast);
    assert_eq!(
        res.verdict,
        Verdict::Reject,
        "expected Reject, got Accept: {:?}",
        res.diagnostics
    );
    assert!(
        res.diagnostics.iter().any(|d| d.code == "HYB-E1002"),
        "expected HYB-E1002, got: {:?}",
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
fn test_bare_value_only_builtins_rejected_with_e1002() {
    for snippet in [
        "package main\nfn main() {\n    s := \"abc\"\n    len(s)\n}",
        "package main\nfn main() {\n    s := []int{1}\n    cap(s)\n}",
        "package main\nfn main() {\n    make([]int, 5)\n}",
        "package main\nfn main() {\n    new(int)\n}",
        "package main\nfn main() {\n    s := []int{1}\n    append(s, 1)\n}",
        "package main\nfn main() {\n    min(1, 2)\n}",
        "package main\nfn main() {\n    max(1, 2)\n}",
        "package main\nfn main() {\n    complex(1.0, 2.0)\n}",
        "package main\nfn main() {\n    s := \"abc\"\n    (len)(s)\n}",
    ] {
        assert_reject_e1002(snippet);
    }
}

#[test]
fn test_bare_type_conversions_rejected_with_e1002() {
    for snippet in [
        "package main\nfn main() {\n    x := 1\n    int(x)\n}",
        "package main\nfn main() {\n    s := \"abc\"\n    string(s)\n}",
        "package main\nfn main() {\n    s := \"abc\"\n    []byte(s)\n}",
        "package main\ntype my_int = int\nfn main() {\n    x := 1\n    my_int(x)\n}",
        "package main\nfn main() {\n    x := 1\n    any(x)\n}",
    ] {
        assert_reject_e1002(snippet);
    }
}

#[test]
fn test_bare_literals_vars_ops_and_closure_stmts_rejected_with_e1002() {
    for snippet in [
        "package main\nfn main() {\n    x := 1\n    x\n}",
        "package main\nfn main() {\n    42\n}",
        "package main\nfn main() {\n    1 + 2\n}",
        "package main\nfn main() {\n    s := []int{1, 2}\n    s[0]\n}",
        "package main\nfn main() {\n    s := []int{1, 2}\n    s[0..1]\n}",
        "package main\nstruct box { v int }\nfn main() {\n    b := box{v: 1}\n    b.v\n}",
        "package main\nfn main() {\n    []int{1, 2}\n}",
        "package main\nfn main() {\n    fn() {}\n}",
        "package main\nfn main() {\n    f := fn() {\n        s := \"abc\"\n        len(s)\n    }\n    f()\n}",
        "package main\nfn main() {\n    f := fn() {\n        x := 1\n        int(x)\n    }\n    f()\n}",
    ] {
        assert_reject_e1002(snippet);
    }
}

#[test]
fn test_side_effect_expr_stmts_and_shadowed_names_accepted() {
    assert_accept(
        r#"package main

fn len(s string) {
    _ = s
}

fn main() {
    print("ok")
    println("ok")
    m := map[string]int{"k": 1}
    delete(m, "k")
    dst := []int{0}
    src := []int{1}
    copy(dst, src)
    ch := make(chan int, 1)
    ch <- 7
    <-ch
    close(ch)
    len("shadowed")
}
"#,
    );
}
