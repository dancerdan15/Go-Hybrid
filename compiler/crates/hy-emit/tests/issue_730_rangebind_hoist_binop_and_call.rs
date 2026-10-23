use hy_emit::emit_go;
use hy_syntax::parse_file;

#[test]
fn exclusive_range_hi_ordered_call_before_try_preserves_outer_binding() {
    let src = r#"package main

import "fmt"

fn id(x int) int {
    return x
}

fn step(x int) (int, error) {
    return x, nil
}

fn run_ordered(xs []int) (int, error) {
    for i, v in xs {
        fmt.println(i)
        for v in 0..(id(v) + step(1)?) {
            fmt.println(v)
        }
    }
    return 0, nil
}
"#;
    let file = parse_file("test.hy", src).expect("parse succeeds");
    let go = emit_go(&file).expect("emit succeeds");
    assert!(
        go.contains("for i, v := range xs {"),
        "expected outer `v` to remain bound when `id(v)` is hoisted before `step(1)?`, got:\n{go}"
    );
    assert!(
        !go.contains("for i, _ := range xs"),
        "must not blank outer `v` when `id(v)` is evaluated before inner loop header:\n{go}"
    );
}

#[test]
fn exclusive_range_hi_short_circuit_try_and_inclusive_range_preserve_outer_binding() {
    let src = r#"package main

import "fmt"

fn check(x int) (bool, error) {
    return x > 0, nil
}

fn pick(b bool) int {
    if b {
        return 1
    }
    return 0
}

fn run_short_circuit_and_inclusive(xs []int) (int, error) {
    for i, v in xs {
        fmt.println(i)
        for v in 0..pick(v > 0 && check(v)?) {
            fmt.println(v)
        }
    }
    for i, v in xs {
        fmt.println(i)
        for v in 0..=v {
            fmt.println(v)
        }
    }
    return 0, nil
}
"#;
    let file = parse_file("test.hy", src).expect("parse succeeds");
    let go = emit_go(&file).expect("emit succeeds");
    assert_eq!(
        go.matches("for i, v := range xs {").count(),
        2,
        "expected both short-circuit try in exclusive `hi` and inclusive `..=v` to keep outer `v` bound:\n{go}"
    );
}

#[test]
fn exclusive_range_hi_unhoisted_rhs_after_try_blanks_outer_binding() {
    let src = r#"package main

import "fmt"

fn step(x int) (int, error) {
    return x, nil
}

fn run_unhoisted_rhs(xs []int) (int, error) {
    for i, v in xs {
        fmt.println(i)
        for v in 0..(step(1)? + v) {
            fmt.println(v)
        }
    }
    return 0, nil
}
"#;
    let file = parse_file("test.hy", src).expect("parse succeeds");
    let go = emit_go(&file).expect("emit succeeds");
    assert!(
        go.contains("for i, _ := range xs {"),
        "unhoisted `+ v` on RHS of `step(1)? + v` resolves to inner `v` in Go loop header, so outer `v` must be blanked:\n{go}"
    );
}
