use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("generic_multi_return_73.hy", src).expect("parses");
    check_file(&ast)
}

#[test]
fn generic_multi_return_preserves_shapes_and_types() {
    let r = check(
        "fn pair_id[a any, b any](x a, y b) (a, b) { return x, y }\n\
         fn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n done := make(chan bool, 1)\n m := map[string]int{\"k\": 1}\n _, m2 := pair_id(1, m)\n m3 := m2\n task worker(move m, done)\n m3[\"k\"] = 2\n <-done\n }",
    );
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert_eq!(r.diagnostics[0].code, "HYB-E3003");
}
