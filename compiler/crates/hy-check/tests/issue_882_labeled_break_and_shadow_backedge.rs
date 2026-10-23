//! Issue #882 / #739: LX14 `break loop` inside nested finite `for` loops and
//! shadowed/rebound `pre_moved` bindings moved inside repeating loops.

use hy_check::{check_file, CheckResult, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("issue_882_labeled_break_and_shadow_backedge.hy", src)
        .expect("snippet parses");
    check_file(&ast)
}

#[test]
fn break_loop_in_nested_for_exits_inner_loop_to_outer_backedge() {
    let r = check(
        "fn worker(m map[string]int, done chan bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n\
           m := map[string]int{\"init\": 0}\n\
           done := make(chan bool, 2)\n\
           step := 0\n\
           loop {\n\
             m[\"main\"] = step\n\
             task worker(move m, done)\n\
             loop {\n\
               for j in 0..1 {\n\
                 _ = j\n\
                 if step == 0 {\n\
                   step = 1\n\
                   break loop\n\
                 }\n\
               }\n\
               return\n\
             }\n\
             if step == 1 {\n\
               continue\n\
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
fn shadowed_and_rebound_pre_moved_bindings_rejected_on_loop_backedge() {
    let r_shadow = check(
        "fn worker(m map[string]int, done chan bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n\
           m := map[string]int{\"outer\": 0}\n\
           done := make(chan bool, 3)\n\
           task worker(move m, done)\n\
           if true {\n\
             m := map[string]int{\"inner\": 1}\n\
             for i in 0..2 {\n\
               m[\"main\"] = i\n\
               task worker(move m, done)\n\
             }\n\
           }\n\
           <-done\n\
           <-done\n\
           <-done\n\
         }",
    );
    assert_eq!(r_shadow.verdict, Verdict::Reject, "{:?}", r_shadow.diagnostics);
    assert!(r_shadow.diagnostics.iter().any(|d| d.code == "HYB-E3003"));

    let r_rebind = check(
        "fn worker(m map[string]int, done chan bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n\
           m := map[string]int{\"outer\": 0}\n\
           done := make(chan bool, 2)\n\
           task worker(move m, done)\n\
           for i in 0..2 {\n\
             if i == 0 {\n\
               m = map[string]int{\"rebound\": 1}\n\
             }\n\
             task worker(move m, done)\n\
           }\n\
           <-done\n\
           <-done\n\
         }",
    );
    assert_eq!(r_rebind.verdict, Verdict::Reject, "{:?}", r_rebind.diagnostics);
    assert!(r_rebind.diagnostics.iter().any(|d| d.code == "HYB-E3003"));
}

#[test]
fn plain_break_in_nested_for_and_fresh_per_iteration_shadow_accepted() {
    let r_ok = check(
        "fn worker(m map[string]int, done chan bool) { m[\"w\"] = 1\n done <- true }\n\
         fn main() {\n\
           m := map[string]int{\"outer\": 0}\n\
           done := make(chan bool, 3)\n\
           task worker(move m, done)\n\
           for i in 0..2 {\n\
             _ = i\n\
             m := map[string]int{\"fresh\": 1}\n\
             task worker(move m, done)\n\
           }\n\
           <-done\n\
           <-done\n\
           <-done\n\
         }",
    );
    assert_eq!(r_ok.verdict, Verdict::Accept, "{:?}", r_ok.diagnostics);
}
