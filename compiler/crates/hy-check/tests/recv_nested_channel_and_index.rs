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
fn nested_receive_and_indexed_channel_expressions_rejected_when_send_only() {
    assert_eq!(check(r#"package main
fn ok_recv(outer chan []chan int, p *[2]chan int, opt option[chan int]) {
    _ = <-(<-outer)[0]
    _ = <-p[0:1][0]
    _ = <-opt?
}
"#).verdict, Verdict::Accept);
    assert_has(r#"package main
fn probe(outer chan []chan<- int, ch chan<- int) {
    var a [<-(<-outer)[0]]int
    _ = a
    _ = <-[ch][0]
}
"#, "HYB-E2008");
    assert_has(r#"package main
fn probe(p *[2]chan<- int, opt option[chan<- int]) {
    var a [<-p[0:1][0]]int
    var b [<-opt?]int
    _ = a
    _ = b
}
"#, "HYB-E2008");
}
