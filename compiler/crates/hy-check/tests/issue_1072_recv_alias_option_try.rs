//! Issue #1072 — `recv_chan_type_expr` and `check_recv_operand` resolution
//! across local aliases (`resolve_alias`), `option[chan]` unwrapping (`?`,
//! `.unwrap()`, `.or(...)`, `some(...)`), pointer-to-array indexing
//! (`*[N]chan`), and struct fields.

use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("test.hy", src).expect("snippet parses");
    check_file(&ast)
}

#[test]
fn send_only_chan_through_option_unwrap_or_and_try_rejected_issue_1072() {
    for src in [
        "fn bad(sc chan<- int) {\n  f := fn() {\n    _ = <-some(sc).unwrap()\n  }\n  f()\n}\n",
        "fn bad(sc chan<- int) {\n  _ = <-none.or(sc)\n}\n",
        "fn bad(opt option[chan<- int]) option[int] {\n  f := fn() option[int] {\n    v := <-opt?\n    return some(v)\n  }\n  return f()\n}\n",
        "type send_ch = chan<- int\nfn bad(opt option[send_ch]) option[int] {\n  v := <-some(opt?).unwrap()\n  return some(v)\n}\n",
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
fn send_only_chan_through_alias_ptr_array_and_field_rejected_issue_1072() {
    for src in [
        "fn bad(sc chan<- int) {\n  alias := sc\n  f := fn() {\n    _ = <-some(alias).unwrap()\n  }\n  f()\n}\n",
        "fn bad(pa *[2]chan<- int) {\n  f := fn() {\n    _ = <-pa[0]\n  }\n  f()\n}\n",
        "struct pipe {\n  ch chan<- int\n}\nfn bad(p option[pipe]) {\n  f := fn() {\n    _ = <-p.unwrap().ch\n  }\n  f()\n}\n",
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
fn non_chan_through_option_unwrap_or_rejected_e2008_issue_1072() {
    for src in [
        "fn bad(x int) {\n  _ = <-some(x).unwrap()\n}\n",
        "fn bad(x int) {\n  _ = <-none.or(x)\n}\n",
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
fn readable_chan_through_alias_option_try_ptr_array_accepted_issue_1072() {
    let r = check(
        "struct pipe {\n\
           ch <-chan int\n\
         }\n\
         fn ok(rc <-chan int, opt option[<-chan int], pa *[2]<-chan int, p option[pipe]) option[int] {\n\
           alias := rc\n\
           _ = <-some(alias).unwrap()\n\
           _ = <-none.or(rc)\n\
           _ = <-pa[0]\n\
           _ = <-p.unwrap().ch\n\
           v := <-opt?\n\
           return some(v)\n\
         }\n",
    );
    assert_eq!(r.verdict, Verdict::Accept, "{:?}", r.diagnostics);
}
