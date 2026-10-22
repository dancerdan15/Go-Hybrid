use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("generic_call_freeze_73_b.hy", src).expect("parses");
    check_file(&ast)
}

#[test]
fn generic_slice_and_owned_move_preserve_shapes() {
    let r_copy = check(
        "fn id_slice[t any](s []t) []t { return s }\nfn worker(s []map[string]int, done chan<- bool) { s[0][\"w\"] = 1\n done <- true }\n\
         fn main() {\n done := make(chan bool, 1)\n src := []map[string]int{map[string]int{\"k\": 1}}\n src2 := id_slice(src)\n dst := make([]map[string]int, 1)\n copy(dst, src2)\n task worker(move dst, done)\n src[0][\"k\"] = 2\n <-done\n }",
    );
    assert_eq!(r_copy.verdict, Verdict::Reject, "{:?}", r_copy.diagnostics);
    assert_eq!(r_copy.diagnostics[0].code, "HYB-E3003");

    let r_closure = check(
        "fn wrap_slice[t any](x t) []t { return []t{x} }\nfn run_all(fs []fn(), done chan<- bool) { fs[0]()\n done <- true }\n\
         fn main() {\n done := make(chan bool, 1)\n m := map[string]int{\"k\": 1}\n f := fn() { m[\"k\"] = 2 }\n fs := wrap_slice(f)\n task run_all(move fs, done)\n m[\"k\"] = 3\n <-done\n }",
    );
    assert_eq!(r_closure.verdict, Verdict::Reject, "{:?}", r_closure.diagnostics);
    assert_eq!(r_closure.diagnostics[0].code, "HYB-E3010");

    let r_ok = check(
        "fn id[t any](x t) t { return x }\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n done := make(chan bool, 1)\n m := map[string]int{\"k\": 1}\n m2 := id(move m)\n task worker(move m2, done)\n <-done\n }",
    );
    assert_eq!(r_ok.verdict, Verdict::Accept, "{:?}", r_ok.diagnostics);
}
