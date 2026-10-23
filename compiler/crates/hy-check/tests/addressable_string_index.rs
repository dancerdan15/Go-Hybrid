//! addressable_string_index.rs — string index expressions (`s[i]`) are neither
//! addressable nor assignable in Go and must reject as HYB-E2010 (rule BUILDABLE).

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    check_file(&ast)
}

fn error_codes(r: &hy_check::CheckResult) -> Vec<String> {
    r.diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code.clone())
        .collect()
}

fn rejects_with(src: &str, code: &str) {
    let r = check(src);
    let cs = error_codes(&r);
    assert!(
        cs.iter().any(|c| c == code),
        "expected {}, got {:?}\nsrc:\n{}",
        code,
        cs,
        src
    );
    assert_eq!(r.verdict, Verdict::Reject, "src:\n{}", src);
}

fn accepts(src: &str) {
    let r = check(src);
    assert!(
        error_codes(&r).is_empty() && r.verdict == Verdict::Accept,
        "must accept, got {:?}\nsrc:\n{}",
        r.diagnostics,
        src
    );
}

#[test]
fn rejects_string_index_assign() {
    rejects_with(
        "fn main() {\n    s := \"hello\"\n    s[0] = 65\n    _ = s\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_string_index_compound_and_inc() {
    rejects_with(
        "fn main() {\n    s := \"hello\"\n    s[0] += 1\n    s[0]++\n    _ = s\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_string_index_loan() {
    rejects_with(
        "fn main() {\n    s := \"hello\"\n    n := &s[0]\n    _ = n\n    _ = s\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_map_and_struct_string_field_index_store_and_loan() {
    rejects_with(
        "struct user {\n    name string\n}\nfn main() {\n    m := map[string]user{\"a\": user{name: \"alice\"}}\n    m[\"a\"].name[0] = 65\n    n := &m[\"a\"].name[0]\n    _ = n\n    _ = m\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn rejects_match_option_string_index_store_and_loan() {
    rejects_with(
        "fn main() {\n    var opt option[string] = some(\"hello\")\n    match opt {\n        some(s): {\n            s[0] = 65\n            n := &s[0]\n            _ = n\n            _ = s\n        }\n        none: {}\n    }\n}\n",
        "HYB-E2010",
    );
}

#[test]
fn accepts_whole_string_assign_and_map_slot_loan() {
    accepts(
        "struct user {\n    name string\n}\nfn main() {\n    var s string = \"hello\"\n    s = \"world\"\n    u := user{name: s}\n    u.name = \"bob\"\n    m := map[string]user{\"a\": u}\n    n := &m[\"a\"]\n    n.name = \"carol\"\n    _ = m\n}\n",
    );
}
