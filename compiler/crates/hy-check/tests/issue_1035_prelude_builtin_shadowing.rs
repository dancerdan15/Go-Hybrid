//! Issue #1035 — package-level declarations shadowing Go builtins (`panic`,
//! `make`, `delete`, `len`) called by an active Hybrid prelude (`HYB-E1002`).

use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("issue_1035.hy", src).expect("parses");
    check_file(&ast)
}

fn assert_e1002(src: &str) {
    let out = check(src);
    assert_eq!(out.verdict, Verdict::Reject, "expected Reject for:\n{src}");
    assert!(
        !out.diagnostics.is_empty() && out.diagnostics.iter().all(|d| d.code == "HYB-E1002"),
        "expected only HYB-E1002, got {:?} for:\n{src}",
        out.diagnostics.iter().map(|d| &d.code).collect::<Vec<_>>()
    );
}

fn assert_accept(src: &str) {
    let out = check(src);
    assert_eq!(
        out.verdict,
        Verdict::Accept,
        "expected Accept (0 diagnostics), got {:?} for:\n{src}",
        out.diagnostics
    );
}

#[test]
fn package_level_panic_with_active_option_prelude_is_rejected() {
    assert_e1002(
        "fn panic(msg string) {\n    fmt.println(msg)\n}\n\
         fn main() {\n    var o option[int]\n    fmt.println(\"{o.unwrap()}\")\n}\n",
    );
    assert_e1002(
        "const panic = 1\n\
         fn main() {\n    o := some(panic)\n    fmt.println(\"{o.is_some()}\")\n}\n",
    );
    assert_e1002(
        "struct panic {\n    n int\n}\n\
         fn has(o option[int]) bool {\n    return o.is_some()\n}\n\
         fn main() {\n    v := panic{n: 1}\n    fmt.println(\"{v.n}\")\n}\n",
    );
    assert_e1002(
        "type panic = int\n\
         fn has(o option[int]) bool {\n    return o.is_some()\n}\n\
         fn main() {\n    fmt.println(\"ok\")\n}\n",
    );
}

#[test]
fn package_level_panic_when_fn_some_alone_activates_option_prelude_is_rejected() {
    let out = check(
        "fn some(x int) int {\n    return x\n}\n\
         fn panic(msg string) {\n    fmt.println(msg)\n}\n\
         fn main() {\n    fmt.println(\"{some(1)}\")\n}\n",
    );
    assert_eq!(out.verdict, Verdict::Reject);
    assert_eq!(out.diagnostics.len(), 2);
    assert!(out.diagnostics.iter().all(|d| d.code == "HYB-E1002"));
}

#[test]
fn package_level_make_delete_len_with_active_set_prelude_are_rejected() {
    assert_e1002(
        "fn make(x int) int {\n    return x\n}\n\
         fn main() {\n    s := set[int]{}\n    s.insert(1)\n    fmt.println(\"{s.contains(1)}\")\n}\n",
    );
    assert_e1002(
        "fn delete(x int) int {\n    return x\n}\n\
         fn main() {\n    s := set[int]{}\n    s.insert(1)\n    fmt.println(\"{s.contains(1)}\")\n}\n",
    );
    assert_e1002(
        "fn len(xs []int) int {\n    return 0\n}\n\
         fn main() {\n    s := set[int]{}\n    s.insert(1)\n    fmt.println(\"{s.contains(1)}\")\n}\n",
    );
}

#[test]
fn package_level_len_with_active_slice_pop_prelude_is_rejected() {
    assert_e1002(
        "fn len(xs []int) int {\n    return 0\n}\n\
         fn main() {\n    xs := []int{1, 2}\n    n := xs.pop()\n    fmt.println(\"{n}\")\n}\n",
    );
    assert_e1002(
        "const len = 0\n\
         fn main() {\n    s := set[int]{}\n    s.insert(1)\n    xs := []int{1}\n    fmt.println(\"{xs.pop()} {len}\")\n}\n",
    );
}

#[test]
fn positive_controls_builtins_with_dormant_prelude_or_local_scope_are_accepted() {
    assert_accept(
        "fn panic(msg string) {\n    fmt.println(msg)\n}\n\
         fn main() {\n    panic(\"hello\")\n}\n",
    );
    assert_accept(
        "fn make(x int) int {\n    return x\n}\n\
         fn delete(x int) int {\n    return x\n}\n\
         fn len(x int) int {\n    return x\n}\n\
         fn main() {\n    var o option[int]\n    fmt.println(\"{make(1)} {delete(2)} {len(3)} {o.is_some()}\")\n}\n",
    );
    assert_accept(
        "struct s {\n    n int\n}\n\
         fn (x s) panic() int {\n    return x.n\n}\n\
         fn main() {\n\
             var o option[int]\n\
             panic := 1\n\
             len := 2\n\
             v := s{n: 3}\n\
             fmt.println(\"{panic} {len} {v.panic()} {o.is_some()}\")\n\
         }\n",
    );
}
