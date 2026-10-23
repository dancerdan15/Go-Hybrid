//! Issue #1042 — `call_shape` and `call_result_tys` soundness across indirect
//! function-value calls (struct fields, indexed slice/map tables, IIFEs,
//! type-aliased local fn bindings, and multi-return indirect calls).

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
fn struct_fn_field_callback_preserves_slice_shape_and_freezes_alias() {
    let src = "\
package main
struct holder { cb fn(x []int) []int }
fn worker(s []int, done chan<- bool) { s[0] = 1\n done <- true }
fn main() {
    s := []int{1, 2}
    h := holder{cb: fn(x []int) []int { return x }}
    alias := h.cb(s)
    copy_s := alias
    done := make(chan bool, 1)
    task worker(move s, done)
    copy_s[0] = 99
    <-done
}";
    assert!(codes(src).iter().any(|c| c == "HYB-E3003"));
}

#[test]
fn slice_and_map_indexed_fn_elements_preserve_return_shapes() {
    let slice_src = "\
package main
fn worker(s []int, done chan<- bool) { s[0] = 1\n done <- true }
fn main() {
    s := []int{1, 2}
    fns := []fn(x []int) []int{fn(x []int) []int { return x }}
    alias := fns[0](s)
    copy_s := alias
    done := make(chan bool, 1)
    task worker(move s, done)
    copy_s[0] = 99
    <-done
}";
    assert!(codes(slice_src).iter().any(|c| c == "HYB-E3003"));

    let map_src = "\
package main
fn worker(s []int, done chan<- bool) { s[0] = 1\n done <- true }
fn borrow_escape(s []int) {
    fns := map[string]fn(x []int) []int{\"k\": fn(x []int) []int { return x }}
    alias := fns[\"k\"](s)
    var escaped []int\n escaped = alias
    done := make(chan bool, 1)
    task worker(move escaped, done)
    <-done
}";
    assert!(codes(map_src).iter().any(|c| c == "HYB-E3004"));
}

#[test]
fn iife_fn_lit_call_preserves_slice_shape_and_non_owned_origin() {
    let freeze_src = "package main\nfn worker(s []int, done chan<- bool) { s[0] = 1\n done <- true }\nfn main() {\n    s := []int{1, 2}\n    alias := (fn(x []int) []int { return x })(s)\n    copy_s := alias\n    done := make(chan bool, 1)\n    task worker(move s, done)\n    copy_s[0] = 99\n    <-done\n}\n";
    assert!(codes(freeze_src).iter().any(|c| c == "HYB-E3003"));

    let borrow_src = "package main\nfn worker(s []int, done chan<- bool) { s[0] = 1\n done <- true }\nfn borrow_escape(s []int) {\n    alias := (fn(x []int) []int { return x })(s)\n    var escaped []int\n    escaped = alias\n    done := make(chan bool, 1)\n    task worker(move escaped, done)\n    <-done\n}\n";
    assert!(codes(borrow_src).iter().any(|c| c == "HYB-E3004"));
}

#[test]
fn type_aliased_local_fn_binding_call_preserves_return_shape() {
    let src = "package main\ntype mapper = fn(x []int) []int\nfn worker(s []int, done chan<- bool) { s[0] = 1\n done <- true }\nfn main() {\n    s := []int{1, 2}\n    var f mapper = fn(x []int) []int { return x }\n    alias := f(s)\n    copy_s := alias\n    done := make(chan bool, 1)\n    task worker(move s, done)\n    copy_s[0] = 99\n    <-done\n}\n";
    assert!(codes(src).iter().any(|c| c == "HYB-E3003"));
}

#[test]
fn multi_return_indirect_fn_field_calls_preserve_shapes_and_origins() {
    let freeze_src = "package main\nstruct pair_box { cb fn(x map[string]int) map[string]int bool }\nfn pair_id(x map[string]int) (map[string]int, bool) { return x, true }\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\nfn main() {\n    m := map[string]int{\"a\": 1}\n    box := pair_box{cb: pair_id}\n    p := &box\n    alias, ok := p.cb(m)\n    _ = ok\n    copy_m := alias\n    done := make(chan bool, 1)\n    task worker(move m, done)\n    copy_m[\"k\"] = 99\n    <-done\n}\n";
    assert!(codes(freeze_src).iter().any(|c| c == "HYB-E3003"));

    let borrow_src = "package main\nstruct pair_box { cb fn(x map[string]int) map[string]int bool }\nfn pair_id(x map[string]int) (map[string]int, bool) { return x, true }\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\nfn borrow_escape(m map[string]int) {\n    box := pair_box{cb: pair_id}\n    p := &box\n    alias, ok := p.cb(m)\n    _ = ok\n    var escaped map[string]int\n    escaped = alias\n    done := make(chan bool, 1)\n    task worker(move escaped, done)\n    <-done\n}\n";
    assert!(codes(borrow_src).iter().any(|c| c == "HYB-E3004"));
}

#[test]
fn positive_control_indirect_fn_calls_returning_primitives_accept() {
    let src = "package main\ntype mapper = fn(x []int) int\nstruct holder { cb mapper }\nfn main() {\n    s := []int{10, 20}\n    h := holder{cb: fn(x []int) int { return x[0] }}\n    fns := []mapper{h.cb}\n    a := h.cb(s)\n    b := fns[0](s)\n    c := (fn(x []int) int { return x[1] })(s)\n    s[0] = a + b + c\n}\n";
    assert_eq!(check(src).verdict, Verdict::Accept);
}
