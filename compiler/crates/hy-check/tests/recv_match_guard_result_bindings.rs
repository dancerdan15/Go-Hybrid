use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    check_file(&parse_file("inline.hy", src).expect("must parse"))
}

fn assert_has(src: &str, code: &str) {
    let r = check(src);
    assert!(r.diagnostics.iter().any(|d| d.severity == Severity::Error && d.code == code), "expected {code}, got {:?}", r.diagnostics);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn match_guard_and_body_resolve_result_ok_err_channel_bindings() {
    assert_eq!(check(r#"package main
enum result[t any, e any] { ok(t), err(e) }
fn probe(res result[chan int, string]) {
    match res {
        ok(ch) if (<-ch) > 0: {}
        ok(_): {}
        err(msg): { _ = msg }
    }
}
"#).verdict, Verdict::Accept);
    assert_has(r#"package main
enum result[t any, e any] { ok(t), err(e) }
fn probe(r1 result[chan<- int, string], r2 result[int, chan<- int]) {
    match r1 {
        ok(ch) if (<-ch) > 0: {}
        ok(_): {}
        err(msg): { _ = msg }
    }
    match r2 {
        ok(v): { _ = v }
        err(ch) if (<-ch) > 0: {}
        err(_): {}
    }
}
"#, "HYB-E2008");
}
