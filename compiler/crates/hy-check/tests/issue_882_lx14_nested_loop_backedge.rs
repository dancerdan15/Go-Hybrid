//! Issue #882 / #739: LX14 `continue loop` inside nested finite `for` loops
//! targets the nearest enclosing infinite `loop` and reaches its back-edge.

use hy_check::{check_file, CheckResult, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("issue_882_lx14_nested_loop_backedge.hy", src).expect("snippet parses");
    check_file(&ast)
}

#[test]
fn continue_loop_in_nested_range_for_reaches_outer_loop_backedge() {
    let r = check(
        "fn worker(m map[string]int, done chan int) { m[\"w\"] = 1\n done <- 1 }\n\
         fn main() {\n\
           done := make(chan int, 2)\n\
           m := map[string]int{\"k\": 0}\n\
           n := 0\n\
           loop {\n\
             task worker(move m, done)\n\
             for i in 0..1 {\n\
               _ = i\n\
               if n == 0 {\n\
                 n = 1\n\
                 continue loop\n\
               }\n\
             }\n\
             break\n\
           }\n\
           <-done\n\
           <-done\n\
         }",
    );
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert!(r.diagnostics.iter().any(|d| d.code == "HYB-E3003"));
}

#[test]
fn continue_loop_across_multi_level_finite_for_reaches_outer_loop_backedge() {
    let r = check(
        "fn worker(m map[string]int, done chan int) { m[\"w\"] = 1\n done <- 1 }\n\
         fn main() {\n\
           done := make(chan int, 2)\n\
           m := map[string]int{\"k\": 0}\n\
           xs := []int{1}\n\
           step := 0\n\
           loop {\n\
             for x in xs {\n\
               _ = x\n\
               for step < 2 {\n\
                 task worker(move m, done)\n\
                 if step == 0 {\n\
                   step = 1\n\
                   continue loop\n\
                 }\n\
                 return\n\
               }\n\
             }\n\
             return\n\
           }\n\
         }",
    );
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert!(r.diagnostics.iter().any(|d| d.code == "HYB-E3003"));
}

#[test]
fn plain_continue_or_inner_infinite_loop_does_not_reach_outer_backedge() {
    let r_plain = check(
        "fn worker(m map[string]int, done chan int) { m[\"w\"] = 1\n done <- 1 }\n\
         fn main() {\n\
           done := make(chan int, 1)\n\
           m := map[string]int{\"k\": 0}\n\
           loop {\n\
             task worker(move m, done)\n\
             for i in 0..1 {\n\
               _ = i\n\
               continue\n\
             }\n\
             break\n\
           }\n\
           <-done\n\
         }",
    );
    assert_eq!(r_plain.verdict, Verdict::Accept, "{:?}", r_plain.diagnostics);

    let r_inner_loop = check(
        "fn worker(m map[string]int, done chan int) { m[\"w\"] = 1\n done <- 1 }\n\
         fn main() {\n\
           done := make(chan int, 1)\n\
           m := map[string]int{\"k\": 0}\n\
           n := 0\n\
           loop {\n\
             task worker(move m, done)\n\
             loop {\n\
               if n == 0 {\n\
                 n = 1\n\
                 continue loop\n\
               }\n\
               return\n\
             }\n\
           }\n\
         }",
    );
    assert_eq!(r_inner_loop.verdict, Verdict::Accept, "{:?}", r_inner_loop.diagnostics);
}
