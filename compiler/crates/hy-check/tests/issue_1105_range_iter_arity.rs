//! Regression tests for issue #1105 (part 1):
//! - Non-literal `set[T]` (parameter, struct field, call result, var) and
//!   type-aliased `set[T]` (`type int_set = set[int]`) in `ForKind::Iterate`:
//!   1-variable iteration is accepted (`Verdict::Accept`), 2-variable iteration
//!   is rejected with `HYB-E2009` (`Verdict::Reject`).
//! - Send-only channel (`chan<- T`) unwrapped from `option[chan<- T]`
//!   (`.unwrap()`, `.or(...)`, local alias) or projected from generic struct
//!   fields / generic calls is rejected with `HYB-E2009`.

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> (Verdict, Vec<String>) {
    let ast = parse_file("issue_1105_arity.hy", src).expect("must parse");
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
fn non_literal_and_aliased_set_two_var_iteration_rejects_e2009() {
    let cases = [
        "fn walk(s set[int]) {\n    for i, v in s { _ = i\n _ = v }\n}\nfn main() {}\n",
        "struct holder { s set[string] }\nfn walk(h holder) {\n    for i, v in h.s { _ = i\n _ = v }\n}\nfn main() {}\n",
        "fn make_set() set[int] { return set[int]{} }\nfn main() {\n    for i, v in make_set() { _ = i\n _ = v }\n}\n",
        "type int_set = set[int]\nfn main() {\n    s := int_set{}\n    for i, v in s { _ = i\n _ = v }\n}\n",
        "type int_set = set[int]\nfn walk(s int_set) {\n    for i, v in (s) { _ = i\n _ = v }\n}\nfn main() {}\n",
    ];
    for src in cases {
        let (verdict, codes) = check(src);
        assert_eq!(verdict, Verdict::Reject, "expected Reject for:\n{src}");
        assert_eq!(codes, vec!["HYB-E2009"], "expected HYB-E2009 for:\n{src}");
    }
}

#[test]
fn non_literal_and_aliased_set_single_var_iteration_accepts() {
    let src = r#"
type int_set = set[int]

struct holder {
    s set[string]
}

fn make_set() set[int] {
    return set[int]{}
}

fn walk(s set[int], a int_set, h holder) {
    for v in s { _ = v }
    for v in a { _ = v }
    for v in h.s { _ = v }
    for v in make_set() { _ = v }
    lit := int_set{}
    for v in lit { _ = v }
}

fn main() {}
"#;
    let (verdict, codes) = check(src);
    assert!(codes.is_empty(), "expected 0 errors, got {codes:?}");
    assert_eq!(verdict, Verdict::Accept);
}

#[test]
fn option_and_generic_send_only_channel_iteration_rejects_e2009() {
    let cases = [
        "fn f(opt option[chan<- int]) {\n    for v in (opt.unwrap()) { _ = v }\n}\nfn main() {}\n",
        "fn f(opt option[chan<- int], fb chan<- int) {\n    for v in (opt).or(fb) { _ = v }\n}\nfn main() {}\n",
        "fn f(opt option[chan<- int]) {\n    if true { c := opt.unwrap()\n for v in c { _ = v } }\n}\nfn main() {}\n",
        "struct box[t any] { ch t }\nfn f(b box[chan<- int], p *box[chan<- int]) {\n    for v in b.ch { _ = v }\n    for v in p.ch { _ = v }\n}\nfn main() {}\n",
        "fn id[t any](x t) t { return x }\nfn f(ch chan<- int) {\n    for v in id(ch) { _ = v }\n    for v in id[chan<- int](ch) { _ = v }\n}\nfn main() {}\n",
    ];
    for src in cases {
        let (verdict, codes) = check(src);
        assert_eq!(verdict, Verdict::Reject, "expected Reject for:\n{src}");
        assert!(
            codes.iter().all(|c| c == "HYB-E2009") && !codes.is_empty(),
            "expected only HYB-E2009 for:\n{src}, got {codes:?}"
        );
    }
    let cycle = "fn f() {\n    a := b\n    b := a\n    c := c.unwrap()\n    for v in a { _ = v }\n    for v in c { _ = v }\n}\nfn main() {}\n";
    assert_eq!(check(cycle).0, Verdict::Reject);
}

#[test]
fn option_and_generic_readable_channel_iteration_accepts() {
    let src = r#"
struct box[t any] {
    ch t
}

fn id[t any](x t) t {
    return x
}

fn f(ro option[<-chan int], bi option[chan int], fb chan int, b box[<-chan int], ch <-chan int) {
    for v in ro.unwrap() { _ = v }
    for v in bi.or(fb) { _ = v }
    for v in b.ch { _ = v }
    for v in id(ch) { _ = v }
}

fn main() {}
"#;
    let (verdict, codes) = check(src);
    assert!(codes.is_empty(), "expected 0 errors, got {codes:?}");
    assert_eq!(verdict, Verdict::Accept);
}
