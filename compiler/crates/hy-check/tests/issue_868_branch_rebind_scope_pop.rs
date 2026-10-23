use hy_check::{check_file, CheckResult, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("t.hy", src).expect("snippet parses");
    check_file(&ast)
}

#[test]
fn conditional_rebind_of_non_owned_param_still_rejects_after_scope_pop_e3004() {
    let if_branch = check(
        "fn worker(x []int) { x[0] = 1 }\n\
         fn bad(p []int, flag bool) {\n\
           if flag {\n\
             p = []int{1, 2}\n\
           }\n\
           task worker(move p)\n\
         }",
    );
    assert_eq!(if_branch.verdict, Verdict::Reject, "{:?}", if_branch.diagnostics);
    assert!(if_branch.diagnostics.iter().any(|d| d.code == "HYB-E3004"), "{:?}", if_branch.diagnostics);

    let else_branch = check(
        "fn worker(x map[string]int) { x[\"k\"] = 1 }\n\
         fn bad(m map[string]int, flag bool) {\n\
           if flag {\n\
             fmt.println(flag)\n\
           } else {\n\
             m = map[string]int{\"k\": 2}\n\
           }\n\
           task worker(move m)\n\
         }",
    );
    assert_eq!(else_branch.verdict, Verdict::Reject, "{:?}", else_branch.diagnostics);
    assert!(else_branch.diagnostics.iter().any(|d| d.code == "HYB-E3004"), "{:?}", else_branch.diagnostics);
}

#[test]
fn conditional_rebind_of_moved_binding_still_rejects_after_scope_pop_e3003() {
    let r = check(
        "fn worker(x map[string]int) { x[\"k\"] = 1 }\n\
         fn bad(flag bool) {\n\
           m := map[string]int{\"k\": 0}\n\
           task worker(move m)\n\
           if flag {\n\
             m = map[string]int{\"k\": 2}\n\
           }\n\
           fmt.println(m[\"k\"])\n\
         }",
    );
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert!(r.diagnostics.iter().any(|d| d.code == "HYB-E3003"), "{:?}", r.diagnostics);
}

#[test]
fn conditional_rebind_of_borrow_and_slot_loan_still_rejects_after_scope_pop() {
    let loan_escape = check(
        "fn bad(flag bool) *int {\n\
           x := 1\n\
           y := 2\n\
           r := &x\n\
           if flag {\n\
             r = &y\n\
           }\n\
           return r\n\
         }",
    );
    assert_eq!(loan_escape.verdict, Verdict::Reject, "{:?}", loan_escape.diagnostics);
    assert!(loan_escape.diagnostics.iter().any(|d| d.code == "HYB-E3104"), "{:?}", loan_escape.diagnostics);

    let slot_loan = check(
        "fn bad(flag bool) {\n\
           m := map[string]int{\"k\": 1}\n\
           n := map[string]int{\"k\": 2}\n\
           p := &m[\"k\"]\n\
           if flag {\n\
             p = &n[\"k\"]\n\
           }\n\
           m[\"other\"] = 3\n\
           fmt.println(*p)\n\
         }",
    );
    assert_eq!(slot_loan.verdict, Verdict::Reject, "{:?}", slot_loan.diagnostics);
    assert!(slot_loan.diagnostics.iter().any(|d| d.code == "HYB-E3102"), "{:?}", slot_loan.diagnostics);
}

#[test]
fn outer_unconditional_rebind_positive_control_accepts() {
    let ok = check(
        "fn worker(x []int) { x[0] = 1 }\n\
         fn good(p []int) {\n\
           fmt.println(len(p))\n\
           p = []int{1, 2}\n\
           task worker(move p)\n\
         }",
    );
    assert_eq!(ok.verdict, Verdict::Accept, "{:?}", ok.diagnostics);
}
