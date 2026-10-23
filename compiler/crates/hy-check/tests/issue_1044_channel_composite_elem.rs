//! Regression tests for issue #1044: composite channel element type checks
//! (`[]T`, `[N]T`, `map[K]V`, `option[T]`, `set[T]`) on send and receive.

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
fn composite_channel_elem_mismatches_reject_e3011() {
    let cases = [
        "fn main() {\n    ch := make(chan []int, 1)\n    xs := []string{\"a\"}\n    ch <- move xs\n}\n",
        "fn main() {\n    ch := make(chan []int, 1)\n    ch <- [2]int{1, 2}\n}\n",
        "fn main() {\n    ch := make(chan [2]int, 1)\n    xs := []int{1, 2}\n    ch <- move xs\n}\n",
        "fn main() {\n    ch := make(chan [2]int, 1)\n    ch <- [3]int{1, 2, 3}\n}\n",
        "fn main() {\n    ch := make(chan [2]int, 1)\n    ch <- [2]int{1, 2}\n    var xs []int = <-ch\n}\n",
        "fn main() {\n    ch := make(chan map[string]int, 1)\n    m := map[string]bool{\"a\": true}\n    ch <- move m\n}\n",
        "fn main() {\n    ch := make(chan map[string]int, 1)\n    m := map[string]string{\"a\": \"b\"}\n    ch <- move m\n}\n",
        "fn main() {\n    ch := make(chan option[int], 1)\n    ch <- some(\"a\")\n}\n",
        "fn main() {\n    ch := make(chan option[int], 1)\n    var o option[string] = none\n    ch <- o\n}\n",
        "fn main() {\n    ch := make(chan set[int], 1)\n    s := set[string]{}\n    ch <- move s\n}\n",
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
fn matching_composite_channel_elems_accept() {
    let r = check(
        "fn main() {\n\
            ch_s := make(chan []int, 1)\n\
            xs := []int{1, 2}\n\
            ch_s <- move xs\n\
            var got_s []int = <-ch_s\n\
            ch_a := make(chan [2]int, 1)\n\
            ch_a <- [2]int{1, 2}\n\
            var got_a [2]int = <-ch_a\n\
            ch_m := make(chan map[string]int, 1)\n\
            m := map[string]int{\"k\": 1}\n\
            ch_m <- move m\n\
            ch_o := make(chan option[int], 1)\n\
            ch_o <- some(7)\n\
            ch_set := make(chan set[int], 1)\n\
            s := set[int]{}\n\
            ch_set <- move s\n\
            fmt.println(\"{len(got_s)} {got_a[0]}\")\n\
        }\n",
    );
    assert_eq!(error_codes(&r), Vec::<String>::new(), "got: {:?}", r.diagnostics);
    assert_eq!(r.verdict, Verdict::Accept);
}
