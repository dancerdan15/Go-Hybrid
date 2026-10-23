use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("generic_call_freeze_73_b.hy", src).expect("parses");
    check_file(&ast)
}

// id(move m) then task worker(move m2) is valid; main falsely rejected it with HYB-E3005.
#[test]
fn generic_identity_owned_move_chain_is_accepted() {
    let r_ok = check(
        "fn id[t any](x t) t { return x }\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n done := make(chan bool, 1)\n m := map[string]int{\"k\": 1}\n m2 := id(move m)\n task worker(move m2, done)\n <-done\n }",
    );
    assert_eq!(r_ok.verdict, Verdict::Accept, "{:?}", r_ok.diagnostics);
}
