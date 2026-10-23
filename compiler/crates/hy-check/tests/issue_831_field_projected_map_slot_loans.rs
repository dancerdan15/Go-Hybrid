use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check_codes(src: &str) -> (Verdict, Vec<String>) {
    let parsed = parse_file("test.hy", src).expect("test snippet must parse");
    let res = check_file(&parsed);
    let codes = res.diagnostics.iter().map(|d| d.code.clone()).collect();
    (res.verdict, codes)
}

#[test]
fn field_projected_map_slot_loan_rejects_live_insert_and_delete() {
    let insert_src = r#"
import "fmt"
struct item { f int }
fn bad_insert(m map[string]*item, next *item) {
    p := &m["a"].f
    m["b"] = next
    fmt.println(p)
}
"#;
    let (verdict, codes) = check_codes(insert_src);
    assert_eq!(verdict, Verdict::Reject, "expected Reject, got: {codes:?}");
    assert!(codes.iter().any(|c| c == "HYB-E3102"), "got: {codes:?}");

    let delete_src = r#"
import "fmt"
struct inner { v int }
struct outer { nested inner }
fn bad_delete(m map[string]*outer) {
    p := &((m["a"]).nested.v)
    delete(m, "a")
    fmt.println(p)
}
"#;
    let (verdict, codes) = check_codes(delete_src);
    assert_eq!(verdict, Verdict::Reject, "expected Reject, got: {codes:?}");
    assert!(codes.iter().any(|c| c == "HYB-E3102"), "got: {codes:?}");
}

#[test]
fn move_wrapped_and_reborrowed_field_projected_slot_loan_rejected() {
    let move_src = r#"
import "fmt"
struct item { f int }
fn bad_move_slot_loan(m map[string]*item, next *item) {
    p := move(&m["a"].f)
    m["b"] = next
    fmt.println(p)
}
"#;
    let (verdict, codes) = check_codes(move_src);
    assert_eq!(verdict, Verdict::Reject, "expected Reject, got: {codes:?}");
    assert!(codes.iter().any(|c| c == "HYB-E3102"), "got: {codes:?}");

    let addr_of_loan_src = r#"
import "fmt"
struct item { f int }
fn bad_reborrow(m map[string]*item) {
    p := &m["a"].f
    q := &p
    fmt.println(q)
}
"#;
    let (verdict, codes) = check_codes(addr_of_loan_src);
    assert_eq!(verdict, Verdict::Reject, "expected Reject, got: {codes:?}");
    assert!(codes.iter().any(|c| c == "HYB-E3104"), "got: {codes:?}");
}

#[test]
fn field_projected_map_slot_loan_positive_control_accepts_after_last_use() {
    let ok_src = r#"
import "fmt"
struct item { f int }
fn valid_use_before_mutation(m map[string]*item, next *item) {
    p := &m["a"].f
    fmt.println(p)
    m["b"] = next
}
"#;
    let (verdict, codes) = check_codes(ok_src);
    assert_eq!(verdict, Verdict::Accept, "expected Accept, got: {codes:?}");
}

// A bare-identifier match pattern is a reference, not a binding (as resolve.rs treats it), so reading the moved-out outer variable must be rejected.
#[test]
fn bare_ident_match_pattern_is_a_reference_so_use_after_move_rejected() {
    let src = r#"
import "fmt"
fn sink(m map[string]int) { fmt.println(len(m)) }
fn f(sel map[string]int) {
    owned := map[string]int{"k": 1}
    sink(move owned)
    match sel {
        owned: { fmt.println(len(owned)) }
        _: { fmt.println(0) }
    }
}
"#;
    let (verdict, codes) = check_codes(src);
    assert_eq!(verdict, Verdict::Reject, "expected Reject, got: {codes:?}");
    assert!(codes.iter().any(|c| c == "HYB-E3003"), "got: {codes:?}");
}
