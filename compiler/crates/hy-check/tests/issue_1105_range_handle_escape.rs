//! Regression tests for issue #1105 (part 2):
//! - Two-variable `for i, u in ...` over `BorrowHandle` (`serde_json.view`)
//!   and iterable `OwnHandle` (`serde_json.value`) must reject with `HYB-E2009`
//!   instead of silently dropping the 2nd variable's handle shape (O7/A6 escape).
//! - Generic struct fields (`b.val`) and single/multi-type-parameter generic
//!   calls (`id(x)`, `pick_second(a, b)`) must substitute concrete type args
//!   in `ForKind::Iterate`.

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> (Verdict, Vec<String>) {
    let ast = parse_file("issue_1105_handle.hy", src).expect("must parse");
    let res = check_file(&ast);
    let codes = res
        .diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code.clone())
        .collect();
    (res.verdict, codes)
}

#[test]
fn borrow_view_and_own_handle_two_var_iteration_rejects_e2009() {
    let borrow_escape = r#"
import "rust/serde_json"

fn worker(x int) {
    _ = x
}

fn main() {
    doc := serde_json.parse(`{"users":["alice"]}`)?
    u := 0
    for i, u in doc.array("users")? {
        _ = i
        task worker(u)
    }
}
"#;
    let (v1, c1) = check(borrow_escape);
    assert_eq!(v1, Verdict::Reject);
    assert_eq!(c1, vec!["HYB-E2009"], "got {c1:?}");

    let own_handle_two_var = r#"
import "rust/serde_json"

fn main() {
    arr := serde_json.parse(`[1, 2, 3]`)?
    for i, u in arr {
        _ = i
        _ = u
    }
}
"#;
    let (v2, c2) = check(own_handle_two_var);
    assert_eq!(v2, Verdict::Reject);
    assert_eq!(c2, vec!["HYB-E2009"]);
}

#[test]
fn generic_field_and_call_invalid_iterables_reject_e2009() {
    let cases = [
        "struct box[t any] { val t }\nfn main() {\n    b := box[int]{val: 5}\n    for v in b.val { _ = v }\n}\n",
        "struct box[t any] { val t }\nfn f(b box[chan int], s box[set[int]]) {\n    for i, v in b.val { _ = i\n _ = v }\n    for i, v in s.val { _ = i\n _ = v }\n}\nfn main() {}\n",
        "struct pair[k comparable, v any] { first k\n second v }\nfn f(p pair[int, []string]) {\n    for x in p.first { _ = x }\n}\nfn main() {}\n",
        "fn id[t any](x t) t { return x }\nfn main() {\n    for v in id(42) { _ = v }\n}\n",
        "fn pick2[a any, b any](x a, y b) b { return y }\nfn f(ch chan int) {\n    for i, v in pick2[int, chan int](1, ch) { _ = i\n _ = v }\n}\nfn main() {}\n",
    ];
    for src in cases {
        let (verdict, codes) = check(src);
        assert_eq!(verdict, Verdict::Reject, "expected Reject for:\n{src}");
        assert!(
            codes.iter().all(|c| c == "HYB-E2009") && !codes.is_empty(),
            "expected HYB-E2009 for:\n{src}, got {codes:?}"
        );
    }
}

#[test]
fn borrow_handle_and_generic_valid_iterables_accept() {
    let src = r#"
import "rust/serde_json"

struct box[t any] {
    val t
}

struct pair[k comparable, v any] {
    first k
    second v
}

fn id[t any](x t) t {
    return x
}

fn pick2[a any, b any](x a, y b) b {
    return y
}

fn walk(bs box[[]int], bc box[chan int], bset box[set[int]], p pair[int, []string]) {
    for i, v in bs.val { _ = i
        _ = v }
    for v in bc.val { _ = v }
    for v in bset.val { _ = v }
    for i, s in p.second { _ = i
        _ = s }
    for i, v in id([]int{1, 2}) { _ = i
        _ = v }
    for i, s in pick2[int, []string](1, []string{"a"}) { _ = i
        _ = s }
}

fn main() {
    doc := serde_json.parse(`{"users":["alice"]}`)?
    for u in doc.array("users")? { _ = u }
    arr := serde_json.parse(`[1, 2]`)?
    for u in arr { _ = u }
}
"#;
    let (verdict, codes) = check(src);
    assert!(codes.is_empty(), "expected 0 errors, got {codes:?}");
    assert_eq!(verdict, Verdict::Accept);
}
