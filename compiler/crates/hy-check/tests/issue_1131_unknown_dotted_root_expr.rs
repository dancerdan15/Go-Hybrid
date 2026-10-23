//! Issue #1131 — fail-to-pass regression tests for bare unknown dotted roots
//! in expression positions (`widgets.count`, `widgets.do_thing()`, `a.b.c`,
//! control-flow headers/bodies, `return`/`defer`/`task`, and closures).

use hy_check::{check_file, CheckResult, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    check_file(&ast)
}

fn assert_rejects_unknown_dotted_root(src: &str, expected_member: &str) {
    let r = check(src);
    assert_eq!(r.verdict, Verdict::Reject, "expected Reject for:\n{src}");
    assert!(
        r.diagnostics.iter().any(|d| {
            d.severity == Severity::Error
                && d.code == "HYB-E1001"
                && d.message.contains(expected_member)
                && d.message.contains("curated allowlist")
        }),
        "expected HYB-E1001 naming `{expected_member}` and curated allowlist, got: {:?}",
        r.diagnostics
    );
}

#[test]
fn bare_unknown_dotted_field_call_and_chain_reject_e1001() {
    assert_rejects_unknown_dotted_root(
        "fn main() {\n    fmt.println(widgets.count)\n}\n",
        "widgets.count",
    );
    assert_rejects_unknown_dotted_root(
        "fn main() {\n    widgets.do_thing()\n}\n",
        "widgets.do_thing",
    );
    assert_rejects_unknown_dotted_root(
        "fn main() {\n    _ = a.b.c\n}\n",
        "a.b",
    );
}

#[test]
fn unknown_dotted_root_in_if_match_and_for_reject_e1001() {
    assert_rejects_unknown_dotted_root(
        "fn main() {\n    if widgets.ready {\n        fmt.println(1)\n    }\n}\n",
        "widgets.ready",
    );
    assert_rejects_unknown_dotted_root(
        "fn main() {\n    x := 1\n    match x {\n        1 => {\n            fmt.println(pkg.member)\n        }\n        _ => {}\n    }\n}\n",
        "pkg.member",
    );
    assert_rejects_unknown_dotted_root(
        "fn main() {\n    xs := []int{1, 2}\n    for v in xs {\n        fmt.println(v + pkg.offset)\n    }\n}\n",
        "pkg.offset",
    );
}

#[test]
fn unknown_dotted_root_in_return_defer_task_and_closure_reject_e1001() {
    assert_rejects_unknown_dotted_root(
        "fn compute() int {\n    return widgets.count\n}\nfn main() {\n    _ = compute()\n}\n",
        "widgets.count",
    );
    assert_rejects_unknown_dotted_root(
        "fn main() {\n    defer widgets.cleanup()\n}\n",
        "widgets.cleanup",
    );
    assert_rejects_unknown_dotted_root(
        "fn worker(n int) {\n    _ = n\n}\nfn main() {\n    task worker(widgets.count)\n}\n",
        "widgets.count",
    );
    assert_rejects_unknown_dotted_root(
        "fn main() {\n    f := fn() int {\n        return pkg.member\n    }\n    _ = f()\n}\n",
        "pkg.member",
    );
}
