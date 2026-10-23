use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn check_codes(src: &str) -> (Verdict, Vec<String>) {
    let parsed = parse_file("test.hy", src).expect("test snippet must parse");
    let res = check_file(&parsed);
    let codes = res.diagnostics.iter().map(|d| d.code.clone()).collect();
    (res.verdict, codes)
}

#[test]
fn addr_of_field_and_index_projections_on_slot_loan_rejected() {
    let field_proj_src = r#"
import "fmt"
struct pair { x int }
fn bad_field_proj(m map[string]pair) {
    p := &m["a"]
    q := &p.x
    fmt.println(q)
}
"#;
    let (verdict, codes) = check_codes(field_proj_src);
    assert_eq!(verdict, Verdict::Reject, "expected Reject, got: {codes:?}");
    assert_eq!(codes, vec!["HYB-E3104"], "expected 1 HYB-E3104, got: {codes:?}");

    let index_proj_src = r#"
import "fmt"
fn bad_index_proj(m map[string][2]int, idx int) {
    p := &m["a"]
    q := &p[idx]
    fmt.println(q)
}
"#;
    let (verdict, codes) = check_codes(index_proj_src);
    assert_eq!(verdict, Verdict::Reject, "expected Reject, got: {codes:?}");
    assert_eq!(codes, vec!["HYB-E3104"], "expected 1 HYB-E3104, got: {codes:?}");
}

#[test]
fn addr_of_projection_positive_control_accepts_non_loan_locals() {
    let ok_src = r#"
import "fmt"
struct pair { x int }
fn ok_local_projections(idx int) {
    s := pair{x: 10}
    arr := [2]int{1, 2}
    pf := &s.x
    pi := &arr[idx]
    fmt.println(pf, pi)
}
"#;
    let (verdict, codes) = check_codes(ok_src);
    assert_eq!(verdict, Verdict::Accept, "expected Accept, got: {codes:?}");
}

#[test]
fn bare_ident_match_pattern_tracks_match_bind_origin_and_positive_control() {
    let reject_src = r#"
import "fmt"
fn worker(m map[string]int) { m["k"] = 1 }
fn bad_match_bare_ident(shared_map map[string]int) {
    captured := 0
    fmt.println(captured)
    match shared_map {
        captured: {
            task worker(move captured)
        }
    }
}
"#;
    let (verdict, codes) = check_codes(reject_src);
    assert_eq!(verdict, Verdict::Reject, "expected Reject, got: {codes:?}");
    assert!(codes.iter().any(|c| c == "HYB-E3004"), "got: {codes:?}");

    let accept_src = r#"
import "fmt"
fn ok_match_bare_ident(x int) {
    captured := 0
    fmt.println(captured)
    match x {
        captured if captured > 0: { fmt.println(captured) }
        _: { fmt.println(0) }
    }
}
"#;
    let (verdict, codes) = check_codes(accept_src);
    assert_eq!(verdict, Verdict::Accept, "expected Accept, got: {codes:?}");
}
