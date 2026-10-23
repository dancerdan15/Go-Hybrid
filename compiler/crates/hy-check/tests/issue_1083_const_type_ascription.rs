use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("inline.hy", src).unwrap_or_else(|d| panic!("must parse: {:?}", d));
    check_file(&ast)
}

fn error_codes(r: &hy_check::CheckResult) -> Vec<String> {
    r.diagnostics.iter().filter(|d| d.severity == Severity::Error).map(|d| d.code.clone()).collect()
}

fn count_code(r: &hy_check::CheckResult, code: &str) -> usize {
    error_codes(r).iter().filter(|c| c.as_str() == code).count()
}

fn assert_has(r: &hy_check::CheckResult, code: &str) {
    let cs = error_codes(r);
    assert!(cs.iter().any(|c| c == code), "expected {}, got {:?}", code, cs);
    assert_eq!(r.verdict, Verdict::Reject);
}

fn assert_accepts(src: &str) {
    let r = check(src);
    let errs = error_codes(&r);
    assert!(errs.is_empty(), "expected clean accept, got {:?}\nsrc: {}", errs, src);
    assert_eq!(r.verdict, Verdict::Accept);
}

#[test]
fn top_level_and_local_non_constant_type_ascriptions_reject_with_e6003() {
    assert_has(&check("package main\nconst x any = 42\nfn main() { _ = x }\n"), "HYB-E6003");
    assert_has(&check("package main\nconst s any = \"hello\"\nfn main() { _ = s }\n"), "HYB-E6003");
    assert_has(&check("package main\ntype iface = any\nconst c iface = true\nfn main() { _ = c }\n"), "HYB-E6003");
    assert_has(&check("package main\nconst x option[int] = 42\nfn main() { _ = x }\n"), "HYB-E6003");
    assert_has(&check("package main\nconst xs []int = 1\nfn main() { _ = xs }\n"), "HYB-E6003");
    assert_has(&check("package main\nstruct empty {}\nconst c empty = 1\nfn main() { _ = c }\n"), "HYB-E6003");
    assert_has(&check("package main\nstruct box_int { v int }\nconst c box_int = 1\nfn main() { _ = c }\n"), "HYB-E6003");
    let r_opt = check("package main\nconst s option[string] = \"hello\"\nconst n = len(s)\nfn main() { _ = n }\n");
    assert_eq!(count_code(&r_opt, "HYB-E6003"), 2, "{:?}", r_opt.diagnostics);
    let r_local = check("package main\nfn main() {\n    const x any = 42\n    const s option[int] = 7\n    _ = x\n    _ = s\n}\n");
    assert_eq!(count_code(&r_local, "HYB-E6003"), 2, "{:?}", r_local.diagnostics);
    let r_closure = check("package main\nfn main() {\n    f := fn() {\n        const x any = 42\n        const s []int = 1\n        _ = x\n        _ = s\n    }\n    f()\n}\n");
    assert_eq!(count_code(&r_closure, "HYB-E6003"), 2, "{:?}", r_closure.diagnostics);
}

#[test]
fn cyclic_type_alias_ascription_and_conversion_reject_with_e6003() {
    let r_cyc_decl = check("package main\ntype a_ty = b_ty\ntype b_ty = a_ty\nconst x a_ty = 42\nfn main() { _ = x }\n");
    assert_has(&r_cyc_decl, "HYB-E6003");
    let r_cyc_conv = check("package main\ntype int8 = my_int\ntype my_int = int8\ntype string = my_str\ntype my_str = string\nconst c = my_int(42)\nconst n = len(my_str(\"hello\"))\nfn main() { _ = c\n _ = n }\n");
    assert_eq!(count_code(&r_cyc_conv, "HYB-E6003"), 2, "{:?}", r_cyc_conv.diagnostics);
}

#[test]
fn valid_scalar_type_ascriptions_and_aliases_are_accepted() {
    assert_accepts("package main\ntype my_int = int\ntype my_str = string\ntype my_f64 = f64\nconst a int = 42\nconst b string = \"hello\"\nconst c f64 = 3.5\nconst d my_int = a + 1\nconst e my_str = b\nconst f my_f64 = c\nconst n int = len(e)\nfn main() {\n    const la my_int = d + 1\n    const ls my_str = \"world\"\n    const ln int = len(ls)\n    _ = f\n    _ = n\n    _ = la\n    _ = ln\n}\n");
}
