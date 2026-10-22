use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("loop_backedge_range_vars_313.hy", src).expect("parses");
    check_file(&ast)
}

#[test]
fn map_and_slice_and_blank_key_range_var_rebound_to_outer_rejects_on_backedge() {
    for iter_hdr in [
        "m := map[string]map[string]int{\"a\": map[string]int{\"x\": 1}}\n for k, v in m { _ = k",
        "xs := []map[string]int{map[string]int{\"x\": 1}}\n for i, v in xs { _ = i",
        "m := map[string]map[string]int{\"a\": map[string]int{\"x\": 1}}\n for _, v in m {",
    ] {
        let src = format!(
            "package main\nfn consume(s map[string]int) {{ s[\"w\"] = 1 }}\nfn main() {{\n s := map[string]int{{\"e\": 0}}\n {}\n v = s\n task consume(move v)\n }}\n}}",
            iter_hdr
        );
        let r = check(&src);
        assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
        assert!(r.diagnostics.iter().any(|d| d.code == "HYB-E3003" || d.code == "HYB-E3001"));
    }
}

#[test]
fn range_var_without_outer_alias_accepts_move() {
    let r = check(
        "package main\nfn consume(v int) {}\nfn main() {\n m := map[string]int{\"a\": 1, \"b\": 2}\n for k, v in m {\n _ = k\n task consume(move v)\n }\n}",
    );
    assert_eq!(r.verdict, Verdict::Accept, "{:?}", r.diagnostics);
}
