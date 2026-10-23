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
fn slice_and_append_of_send_only_channels_rejected_on_receive() {
    assert_eq!(check(r#"package main
fn ok_recv(chs []chan int, ch chan int) { _ = <-append(chs[0:1], ch)[0] }
"#).verdict, Verdict::Accept);
    assert_has(r#"package main
fn probe(chs []chan<- int, ch chan<- int) {
    var a [<-chs[0:1][0]]int
    var b [<-append(chs, ch)[0]]int
    _ = a
    _ = b
}
"#, "HYB-E2008");
    assert_has(r#"package main
fn probe(chs []chan<- int, ch chan<- int) {
    sub := append(chs[0:1], ch)
    var a [<-sub[0]]int
    _ = a
    {
        chs, sub2 := 1, chs[0:1]
        _ = chs
        _ = <-sub2[0]
    }
}
"#, "HYB-E2008");
}
