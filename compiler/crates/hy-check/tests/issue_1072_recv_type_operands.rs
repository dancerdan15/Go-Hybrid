//! Issue #1072 — receive operands inside composite-literal and closure type
//! headers (`Expr::StructLit.ty`, `Expr::SliceLit.elem`, `Expr::MapLit.key`/
//! `value`, `Expr::FnLit.params`/`results`) must run `check_type_recv_operands`
//! so non-channel or send-only `<-x` in `[<-x]T{}` / `fn(a [<-x]T) {}` emits
//! `HYB-E2008` instead of passing with 0 diagnostics.

use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("test.hy", src).expect("snippet parses");
    check_file(&ast)
}

#[test]
fn slice_and_array_lit_type_header_non_chan_recv_rejected_issue_1072() {
    for src in [
        "fn main() {\n  x := 5\n  a := [<-x]int{1}\n  _ = a\n}\n",
        "fn main() {\n  x := \"nope\"\n  s := [][<-x]int{}\n  _ = s\n}\n",
        "fn main() {\n  x := 5\n  ch := make(chan [<-x]int, 1)\n  _ = ch\n}\n",
    ] {
        let r = check(src);
        assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
        assert!(
            r.diagnostics.iter().any(|d| d.code == "HYB-E2008"),
            "expected HYB-E2008, got {:?}",
            r.diagnostics
        );
    }
}

#[test]
fn map_lit_key_and_value_type_header_non_chan_recv_rejected_issue_1072() {
    for src in [
        "fn main() {\n  x := 5\n  m := map[[<-x]int]string{}\n  _ = m\n}\n",
        "fn main() {\n  x := 5\n  m := map[string][<-x]int{}\n  _ = m\n}\n",
    ] {
        let r = check(src);
        assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
        assert!(
            r.diagnostics.iter().any(|d| d.code == "HYB-E2008"),
            "expected HYB-E2008, got {:?}",
            r.diagnostics
        );
    }
}

#[test]
fn fn_lit_param_and_result_type_header_recv_rejected_issue_1072() {
    for src in [
        "fn main() {\n  x := 5\n  f := fn(a [<-x]int) { _ = a }\n  _ = f\n}\n",
        "fn main() {\n  x := 5\n  f := fn() [][<-x]int { return [][1]int{} }\n  _ = f\n}\n",
    ] {
        let r = check(src);
        assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
        assert!(
            r.diagnostics.iter().any(|d| d.code == "HYB-E2008"),
            "expected HYB-E2008, got {:?}",
            r.diagnostics
        );
    }
}

#[test]
fn send_only_chan_in_composite_and_fn_lit_type_headers_rejected_issue_1072() {
    for src in [
        "fn bad(sc chan<- int) {\n  s := [][<-sc]int{}\n  _ = s\n}\n",
        "fn bad(sc chan<- int) {\n  m := map[string][<-sc]int{}\n  _ = m\n}\n",
        "fn bad(sc chan<- int) {\n  f := fn(a [<-sc]int) { _ = a }\n  _ = f\n}\n",
    ] {
        let r = check(src);
        assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
        assert!(
            r.diagnostics
                .iter()
                .any(|d| d.code == "HYB-E2008" || d.code == "HYB-E3008"),
            "expected HYB-E2008/HYB-E3008, got {:?}",
            r.diagnostics
        );
    }
}

#[test]
fn composite_and_fn_lit_positive_controls_accepted_issue_1072() {
    let r = check(
        "fn ok(rc <-chan int) {\n\
           a := [2]int{<-rc, 1}\n\
           s := [][2]int{a}\n\
           m := map[string][2]int{\"k\": a}\n\
           f := fn(v [2]int) int { return v[0] }\n\
           _ = s\n\
           _ = m\n\
           _ = f(a)\n\
         }\n",
    );
    assert_eq!(r.verdict, Verdict::Accept, "{:?}", r.diagnostics);
}
