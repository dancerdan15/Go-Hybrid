//! Regression tests for #1115 (part 1): `serde_json.new_array` (`OwnHandle`)
//! and `serde_json.push_string` (`CallMode::Mutate` + T22 `const` write).

use hy_check::{check_file, CheckResult, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("test.hy", src).expect("test snippet must parse");
    check_file(&ast)
}

fn check_codes(src: &str) -> Vec<String> {
    check(src)
        .diagnostics
        .into_iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code)
        .collect()
}

#[test]
fn new_array_is_own_handle_for_a2_a6_and_use_after_move() {
    let src_a2 = r#"import "rust/serde_json"
fn main() {
    arr := serde_json.new_array()
    elem := arr.get("0")?
    arr.set("0", "x")
    fmt.println(elem.string("0")?)
}
"#;
    assert!(check_codes(src_a2).iter().any(|c| c == "HYB-E3101"));

    let src_a6 = r#"import "rust/serde_json"
struct holder { v serde_json.view }
fn main() {
    arr := serde_json.new_array()
    elem := arr.get("0")?
    h := holder{v: elem}
    _ = h
}
"#;
    assert!(check_codes(src_a6).iter().any(|c| c == "HYB-E3103"));

    let src_move = r#"import "rust/serde_json"
fn main() {
    arr := serde_json.new_array()
    moved := move arr
    _ = arr.get("0")?
    _ = moved
}
"#;
    assert!(check_codes(src_move).iter().any(|c| c == "HYB-E3003"));
}

#[test]
fn push_string_enforces_a2_borrow_conflict_and_t22_const_immutability() {
    let method_form = r#"import "rust/serde_json"
fn main() {
    doc := serde_json.parse(`{"items":["a"]}`)?
    v := doc.array("items")?
    doc.push_string("b")
    fmt.println(v.string("0")?)
}
"#;
    assert!(check_codes(method_form).iter().any(|c| c == "HYB-E3101"));

    let package_form = r#"import "rust/serde_json"
fn main() {
    doc := serde_json.parse(`{"items":["a"]}`)?
    v := serde_json.get(doc, "items")?
    serde_json.push_string(doc, "c")
    fmt.println(v.string("0")?)
}
"#;
    assert!(check_codes(package_form).iter().any(|c| c == "HYB-E3101"));

    let frozen_handle = r#"import "rust/serde_json"
const frozen_arr = serde_json.new_array()
fn main() {
    serde_json.push_string(frozen_arr, "x")
}
"#;
    assert!(check_codes(frozen_handle).iter().any(|c| c == "HYB-E3006"));
}

#[test]
fn positive_control_new_array_and_push_string_after_loan_end_accept() {
    let r = check(
        r#"import "rust/serde_json"
fn main() {
    arr := serde_json.new_array()
    arr.push_string("alpha")
    serde_json.push_string(arr, "beta")
    elem := arr.get("0")?
    fmt.println(elem.string("0")?)
    arr.push_string("gamma")
}
"#,
    );
    assert_eq!(r.verdict, Verdict::Accept, "got {:?}", r.diagnostics);
}
