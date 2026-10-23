use hy_emit::emit_go;
use hy_syntax::parse_file;

#[test]
fn exclusive_range_hi_try_preserves_outer_for_binding() {
    let src = r#"package main

import "fmt"

fn step(x int) (int, error) {
    return x, nil
}

fn run_loop(xs []int) (int, error) {
    for i, v in xs {
        fmt.println(i)
        for v in 0..step(v)? {
            fmt.println(v)
        }
    }
    for i, v in xs {
        fmt.println(i)
        for v in 0..(step(v)? + 1) {
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
        "expected outer `v` to remain bound when exclusive range `hi` hoists `step(v)?`, got:\n{go}"
    );
    assert!(
        !go.contains("for i, _ := range xs"),
        "must not blank outer `v` when `step(v)?` is hoisted before inner `for v := 0`:\n{go}"
    );
}

#[test]
fn exclusive_range_hi_try_preserves_outer_select_and_match_bindings() {
    let src = r#"package main

import "fmt"

fn step(x int) (int, error) {
    return x, nil
}

fn run_select_and_match(ch chan int, opt option[int]) (int, error) {
    select {
        v := <-ch: {
            for v in 0..step(v)? {
                fmt.println(v)
            }
        }
        default: {}
    }
    match opt {
        some(v): {
            for v in 0..(step(v)? + 1) {
                fmt.println(v)
            }
        }
        none: {}
    }
    return 0, nil
}
"#;
    let file = parse_file("test.hy", src).expect("parse succeeds");
    let go = emit_go(&file).expect("emit succeeds");
    assert!(
        go.contains("case v := <-ch:"),
        "expected select arm `v` to stay bound for hoisted `step(v)?`, got:\n{go}"
    );
    assert!(
        go.contains("v := hyMatch"),
        "expected match arm `some(v)` to stay bound for hoisted `step(v)?`, got:\n{go}"
    );
}

#[test]
fn exclusive_range_plain_same_name_hi_still_blanks_outer_binding() {
    let src = r#"package main

import "fmt"

fn main() {
    xs := []int{10, 20}
    for i, v in xs {
        fmt.println(i)
        for v in 0..v {
            fmt.println(v)
        }
    }
}
"#;
    let file = parse_file("test.hy", src).expect("parse succeeds");
    let go = emit_go(&file).expect("emit succeeds");
    assert!(
        go.contains("for i, _ := range xs {"),
        "plain `for v in 0..v` without hoisted try must still blank unread outer `v`:\n{go}"
    );
}
