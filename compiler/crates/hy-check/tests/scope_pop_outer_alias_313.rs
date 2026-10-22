use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("scope_pop_outer_alias_313.hy", src).expect("parses");
    check_file(&ast)
}

#[test]
fn loop_backedge_propagates_nested_block_outer_alias_move() {
    let r = check(
        "fn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n\
           done := make(chan bool, 2)\n m := map[string]int{\"k\": 1}\n var alias map[string]int\n\
           for i in 0..2 {\n\
             if i > 0 { alias[\"k\"] = i }\n\
             if true { alias = m }\n\
             task worker(move m, done)\n m = map[string]int{\"k\": i}\n\
           }\n <-done\n <-done\n }",
    );
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert_eq!(r.diagnostics[0].code, "HYB-E3003");

    let r_shadow = check(
        "fn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n\
           done := make(chan bool, 1)\n m := map[string]int{\"k\": 1}\n var alias map[string]int\n\
           if true {\n m := m\n alias = m\n m = map[string]int{\"fresh\": 2}\n }\n\
           task worker(move m, done)\n alias[\"k\"] = 3\n <-done\n }",
    );
    assert_eq!(r_shadow.verdict, Verdict::Reject, "{:?}", r_shadow.diagnostics);
    assert_eq!(r_shadow.diagnostics[0].code, "HYB-E3003");
}
