//! Issue #1042 — `ty_of_expr` and `multi_value_tys` soundness across container,
//! slice, channel, address-of, `append`, and type-aliased `make` projections.

use hy_check::{check_file, CheckResult, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("test.hy", src).expect("parse ok");
    check_file(&ast)
}

fn codes(src: &str) -> Vec<String> {
    check(src).diagnostics.into_iter().map(|d| d.code).collect()
}

#[test]
fn sliced_fn_table_index_call_preserves_shape_and_origin() {
    let freeze_src = "\
package main
fn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }
fn main() {
    m := map[string]int{\"a\": 1}
    s := []fn(x map[string]int) map[string]int{fn(x map[string]int) map[string]int { return x }}
    alias := s[:][0](m)
    copy_m := alias
    done := make(chan bool, 1)
    task worker(move m, done)
    copy_m[\"k\"] = 99
    <-done
}";
    assert!(codes(freeze_src).iter().any(|c| c == "HYB-E3003"));

    let borrow_src = "\
package main
fn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }
fn borrow_escape(m map[string]int) {
    s := []fn(x map[string]int) map[string]int{fn(x map[string]int) map[string]int { return x }}
    alias := s[:][0](m)
    var escaped map[string]int\n escaped = alias
    done := make(chan bool, 1)
    task worker(move escaped, done)
    <-done
}";
    assert!(codes(borrow_src).iter().any(|c| c == "HYB-E3004"));
}

#[test]
fn channel_received_fn_direct_and_comma_ok_calls_preserve_shape() {
    let direct_src = "\
package main
fn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }
fn direct_recv(ch <-chan fn(x map[string]int) map[string]int) {
    m := map[string]int{\"a\": 1}
    alias := (<-ch)(m)
    copy_m := alias
    done := make(chan bool, 1)
    task worker(move m, done)
    copy_m[\"k\"] = 99
    <-done
}";
    assert!(codes(direct_src).iter().any(|c| c == "HYB-E3003"));

    let comma_ok_src = "package main\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\nfn comma_ok_recv(ch <-chan fn(x map[string]int) map[string]int, m map[string]int) {\n    f, ok := <-ch\n    _ = ok\n    alias := f(m)\n    var escaped map[string]int\n    escaped = alias\n    done := make(chan bool, 1)\n    task worker(move escaped, done)\n    <-done\n}\n";
    assert!(codes(comma_ok_src).iter().any(|c| c == "HYB-E3004"));
}

#[test]
fn addr_of_struct_field_call_preserves_shape_and_freezes_alias() {
    let src = "package main\nstruct fn_box { cb fn(x map[string]int) map[string]int }\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\nfn main() {\n    m := map[string]int{\"a\": 1}\n    box := fn_box{cb: fn(x map[string]int) map[string]int { return x }}\n    alias := (&box).cb(m)\n    copy_m := alias\n    done := make(chan bool, 1)\n    task worker(move m, done)\n    copy_m[\"k\"] = 99\n    <-done\n}\n";
    assert!(codes(src).iter().any(|c| c == "HYB-E3003"));
}

#[test]
fn appended_fn_slice_index_call_preserves_non_owned_origin() {
    let src = "package main\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\nfn borrow_escape(m map[string]int) {\n    fns := []fn(x map[string]int) map[string]int{fn(x map[string]int) map[string]int { return x }}\n    alias := append(fns, fns[0])[0](m)\n    var escaped map[string]int\n    escaped = alias\n    done := make(chan bool, 1)\n    task worker(move escaped, done)\n    <-done\n}\n";
    assert!(codes(src).iter().any(|c| c == "HYB-E3004"));
}

#[test]
fn make_type_aliased_fn_map_direct_and_comma_ok_preserve_shape() {
    let direct_src = "package main\ntype fn_map = map[string]fn(x map[string]int) map[string]int\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\nfn main() {\n    m := map[string]int{\"a\": 1}\n    alias := make(fn_map)[\"k\"](m)\n    copy_m := alias\n    done := make(chan bool, 1)\n    task worker(move m, done)\n    copy_m[\"k\"] = 99\n    <-done\n}\n";
    assert!(codes(direct_src).iter().any(|c| c == "HYB-E3003"));

    let comma_ok_src = "package main\ntype fn_map = map[string]fn(x map[string]int) map[string]int\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\nfn borrow_escape(m map[string]int) {\n    tbl := make(fn_map)\n    cb, ok := tbl[\"k\"]\n    _ = ok\n    alias := cb(m)\n    var escaped map[string]int\n    escaped = alias\n    done := make(chan bool, 1)\n    task worker(move escaped, done)\n    <-done\n}\n";
    assert!(codes(comma_ok_src).iter().any(|c| c == "HYB-E3004"));
}

#[test]
fn positive_control_projected_fn_calls_without_aliasing_race_accept() {
    let src = "package main\ntype fn_map = map[string]fn(x map[string]int) int\nstruct fn_box { cb fn(x map[string]int) int }\nfn run(ch <-chan fn(x map[string]int) int) {\n    m := map[string]int{\"a\": 1}\n    box := fn_box{cb: fn(x map[string]int) int { return x[\"a\"] }}\n    s := []fn(x map[string]int) int{box.cb}\n    n1 := s[:][0](m)\n    n2 := (&box).cb(m)\n    n3 := append(s, box.cb)[0](m)\n    n4 := make(fn_map)[\"k\"](m)\n    n5 := (<-ch)(m)\n    m[\"b\"] = n1 + n2 + n3 + n4 + n5\n}\n";
    assert_eq!(check(src).verdict, Verdict::Accept);
}
