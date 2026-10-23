//! Regression tests for issue #1044: primitive kind and type-alias channel
//! element mismatches (`bool`, `int`, `float64`, `string`, `rune`, aliases).

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse: {:?}", diags));
    check_file(&ast)
}

fn error_codes(r: &hy_check::CheckResult) -> Vec<String> {
    r.diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code.clone())
        .collect()
}

#[test]
fn primitive_and_alias_channel_elem_mismatches_reject_e3011() {
    let cases = [
        "fn main() {\n    ch := make(chan int, 1)\n    ch <- true\n}\n",
        "fn main() {\n    ch := make(chan bool, 1)\n    ch <- 42\n}\n",
        "fn main() {\n    ch := make(chan bool, 1)\n    ch <- 3.14\n}\n",
        "fn main() {\n    ch := make(chan int, 1)\n    var f float64 = 3.14\n    ch <- f\n}\n",
        "fn main() {\n    ch := make(chan string, 1)\n    ch <- 'a'\n}\n",
        "type id_t = int\nfn main() {\n    ch := make(chan id_t, 1)\n    ch <- \"bad\"\n}\n",
        "type id_t = int\nfn main() {\n    ch := make(chan id_t, 1)\n    ch <- false\n}\n",
        "type int_slice = []int\nfn main() {\n    ch := make(chan int_slice, 1)\n    xs := []string{\"a\"}\n    ch <- move xs\n}\n",
    ];
    for src in cases {
        let r = check(src);
        assert_eq!(r.verdict, Verdict::Reject, "expected Reject for:\n{src}");
        assert!(
            error_codes(&r).iter().any(|c| c == "HYB-E3011"),
            "expected HYB-E3011 for:\n{src}\ngot: {:?}",
            r.diagnostics
        );
    }
}

#[test]
fn matching_primitive_and_alias_channel_elems_accept() {
    let r = check(
        "type id_t = int\n\
        fn main() {\n\
            ch_i := make(chan int, 1)\n\
            ch_i <- 42\n\
            ch_i <- 'a'\n\
            i := <-ch_i\n\
            ch_b := make(chan bool, 1)\n\
            ch_b <- true\n\
            b := <-ch_b\n\
            ch_f := make(chan float64, 2)\n\
            ch_f <- 3.14\n\
            ch_f <- 42\n\
            f := <-ch_f\n\
            ch_id := make(chan id_t, 1)\n\
            ch_id <- 7\n\
            id := <-ch_id\n\
            fmt.println(\"{i} {b} {f} {id}\")\n\
        }\n",
    );
    assert_eq!(error_codes(&r), Vec::<String>::new(), "got: {:?}", r.diagnostics);
    assert_eq!(r.verdict, Verdict::Accept);
}

#[test]
fn cyclic_type_aliases_and_consts_terminate_without_overflow() {
    let _ = check("type ta = tb\ntype tb = ta\nfn main() {\n    var ch chan ta\n    var x tb\n    ch <- move x\n}\n");
    let _ = check("type ta = []ta\ntype tb = []tb\nfn main() {\n    var ch chan ta\n    var x tb\n    ch <- move x\n}\n");
    let _ = check("const ca = cb\nconst cb = ca\nfn main() {\n    ch := make(chan int, 1)\n    ch <- ca\n}\n");
}

