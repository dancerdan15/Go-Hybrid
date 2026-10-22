use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("generic_call_freeze_73_a.hy", src).expect("parses");
    check_file(&ast)
}

#[test]
fn generic_identity_preserves_shape_for_alias_chain_and_struct_projection() {
    let r1 = check(
        "fn id[t any](x t) t { return x }\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n done := make(chan bool, 1)\n m := map[string]int{\"k\": 1}\n m2 := id(m)\n m3 := m2\n task worker(move m, done)\n m3[\"k\"] = 2\n <-done\n }",
    );
    assert_eq!(r1.verdict, Verdict::Reject, "{:?}", r1.diagnostics);
    assert_eq!(r1.diagnostics[0].code, "HYB-E3003");

    let r2 = check(
        "struct holder { m map[string]int }\nfn id[t any](x t) t { return x }\nfn worker(h holder, done chan<- bool) { h.m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n done := make(chan bool, 1)\n h1 := holder{m: map[string]int{\"k\": 1}}\n h2 := id(h1)\n inner := h2.m\n task worker(move h1, done)\n inner[\"k\"] = 2\n <-done\n }",
    );
    assert_eq!(r2.verdict, Verdict::Reject, "{:?}", r2.diagnostics);
    assert_eq!(r2.diagnostics[0].code, "HYB-E3003");

    let r3 = check(
        "struct box[t any] { val t }\nfn unbox[t any](b box[t]) t { return b.val }\nfn worker(m map[string]int, done chan<- bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n done := make(chan bool, 1)\n m := map[string]int{\"k\": 1}\n b := box[map[string]int]{val: m}\n m2 := unbox(b)\n task worker(move m, done)\n m2[\"k\"] = 2\n <-done\n }",
    );
    assert_eq!(r3.verdict, Verdict::Reject, "{:?}", r3.diagnostics);
    assert_eq!(r3.diagnostics[0].code, "HYB-E3003");
}
