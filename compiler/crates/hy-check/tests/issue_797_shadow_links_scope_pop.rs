use hy_check::{check_file, CheckResult, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("t.hy", src).expect("snippet parses");
    check_file(&ast)
}

#[test]
fn inner_share_before_shadow_preserves_outer_may_share_on_scope_pop_e3003() {
    let single_shadow = check(
        "fn worker(x []int) { x[0] = 1 }\n\
         fn bad(flag bool) {\n\
           s1 := []int{1, 2}\n\
           s2 := []int{3, 4}\n\
           fmt.println(len(s2))\n\
           if flag {\n\
             s2 = s1\n\
             s1 := 0\n\
             fmt.println(s1)\n\
           }\n\
           task worker(move s1)\n\
           fmt.println(s2[0])\n\
         }",
    );
    assert_eq!(single_shadow.verdict, Verdict::Reject, "{:?}", single_shadow.diagnostics);
    assert!(single_shadow.diagnostics.iter().any(|d| d.code == "HYB-E3003"), "{:?}", single_shadow.diagnostics);

    let double_shadow = check(
        "fn worker(x []int) { x[0] = 1 }\n\
         fn bad(flag bool) {\n\
           s1 := []int{1, 2}\n\
           s2 := []int{3, 4}\n\
           fmt.println(len(s2))\n\
           if flag {\n\
             s2 = s1\n\
             s1 := 0\n\
             s2 := 0\n\
             fmt.println(s1 + s2)\n\
           }\n\
           task worker(move s1)\n\
           fmt.println(s2[0])\n\
         }",
    );
    assert_eq!(double_shadow.verdict, Verdict::Reject, "{:?}", double_shadow.diagnostics);
    assert!(double_shadow.diagnostics.iter().any(|d| d.code == "HYB-E3003"), "{:?}", double_shadow.diagnostics);
}

#[test]
fn inner_share_with_param_before_shadow_propagates_non_owned_origin_e3004() {
    let r = check(
        "fn worker(x []int) { x[0] = 1 }\n\
         fn bad(p []int, flag bool) {\n\
           s := []int{1, 2}\n\
           fmt.println(len(s))\n\
           if flag {\n\
             s = p[:]\n\
             p := 0\n\
             fmt.println(p)\n\
           }\n\
           task worker(move s)\n\
         }",
    );
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert!(r.diagnostics.iter().any(|d| d.code == "HYB-E3004"), "{:?}", r.diagnostics);
}

#[test]
fn nested_block_shadow_links_survive_multi_level_scope_pop_e3003() {
    let r = check(
        "fn worker(x []int) { x[0] = 1 }\n\
         fn bad(f1 bool, f2 bool) {\n\
           s1 := []int{1, 2}\n\
           s2 := []int{3, 4}\n\
           fmt.println(len(s2))\n\
           if f1 {\n\
             s2 = s1\n\
             s1 := 0\n\
             if f2 {\n\
               s2 := 0\n\
               fmt.println(s2)\n\
             }\n\
             fmt.println(s1)\n\
           }\n\
           task worker(move s1)\n\
           fmt.println(s2[0])\n\
         }",
    );
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    assert!(r.diagnostics.iter().any(|d| d.code == "HYB-E3003"), "{:?}", r.diagnostics);
}

#[test]
fn disjoint_slices_with_inner_shadows_positive_control_accepts() {
    let ok = check(
        "fn worker(x []int) { x[0] = 1 }\n\
         fn good(flag bool) {\n\
           s1 := []int{1, 2}\n\
           s2 := []int{3, 4}\n\
           if flag {\n\
             s1 := 0\n\
             fmt.println(s1)\n\
           }\n\
           task worker(move s1)\n\
           fmt.println(s2[0])\n\
         }",
    );
    assert_eq!(ok.verdict, Verdict::Accept, "{:?}", ok.diagnostics);
}
