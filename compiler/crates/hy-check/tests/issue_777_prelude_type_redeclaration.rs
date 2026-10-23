//! Issue #777 — package-level declarations named `option`, `some`, `set`, or
//! `hy_pop` that redeclare an active Hybrid prelude identifier (`HYB-E1002`).

use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("issue_777.hy", src).expect("parses");
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
fn package_level_some_and_option_with_active_option_prelude_are_rejected() {
    assert_e1002(
        "fn some(x int) int {\n    return x + 1\n}\n\
         fn has(o option[int]) bool {\n    return o.is_some()\n}\n\
         fn main() {\n    n := some(1)\n    fmt.println(\"{n}\")\n}\n",
    );
    assert_e1002(
        "fn some(x int) int {\n    return x + 1\n}\n\
         fn main() {\n    fmt.println(\"ok\")\n}\n",
    );
    assert_e1002(
        "struct option {\n    n int\n}\n\
         fn has(o option[int]) bool {\n    return o.is_some()\n}\n\
         fn main() {\n    v := option{n: 1}\n    fmt.println(\"{v.n}\")\n}\n",
    );
}

#[test]
fn package_level_const_type_enum_redeclaring_option_or_some_are_rejected() {
    assert_e1002(
        "const option = 1\n\
         fn has(o option[int]) bool {\n    return o.is_some()\n}\n\
         fn main() {\n    fmt.println(\"{option}\")\n}\n",
    );
    assert_e1002(
        "type some = int\n\
         fn has(o option[int]) bool {\n    return o.is_some()\n}\n\
         fn main() {\n    fmt.println(\"ok\")\n}\n",
    );
    assert_e1002(
        "enum option {\n    none_val\n}\n\
         fn has(o option[int]) bool {\n    return o.is_some()\n}\n\
         fn main() {\n    fmt.println(\"ok\")\n}\n",
    );
}

#[test]
fn package_level_set_and_hy_pop_with_active_preludes_are_rejected() {
    assert_e1002(
        "fn set(x int) int {\n    return x\n}\n\
         fn main() {\n    s := set[int]{}\n    s.insert(1)\n    fmt.println(\"{len(s)}\")\n}\n",
    );
    assert_e1002(
        "struct set {\n    n int\n}\n\
         fn has(s set[int]) bool {\n    return s.contains(1)\n}\n\
         fn main() {\n    fmt.println(\"ok\")\n}\n",
    );
    assert_e1002(
        "fn hy_pop(x int) int {\n    return x\n}\n\
         fn main() {\n    fmt.println(\"{hy_pop(1)}\")\n}\n",
    );
}

#[test]
fn positive_controls_dormant_prelude_methods_and_locals_are_accepted() {
    assert_accept(
        "fn set(dst []map[string]int, v map[string]int) {\n    dst[0] = v\n}\n\
         fn main() {\n    dst := make([]map[string]int, 1)\n    set(dst, map[string]int{\"k\": 1})\n    fmt.println(\"{dst[0][\"k\"]}\")\n}\n",
    );
    assert_accept(
        "struct set {\n    n int\n}\n\
         struct option {\n    n int\n}\n\
         fn main() {\n    v := set{n: 1}\n    o := option{n: 2}\n    fmt.println(\"{v.n} {o.n}\")\n}\n",
    );
    assert_accept(
        "struct s {\n    n int\n}\n\
         fn (x s) some() int {\n    return x.n\n}\n\
         fn s.some(x int) int {\n    return x + 1\n}\n\
         fn main() {\n    some := 1\n    v := s{n: some}\n    fmt.println(\"{v.some()} {s.some(2)}\")\n}\n",
    );
}
