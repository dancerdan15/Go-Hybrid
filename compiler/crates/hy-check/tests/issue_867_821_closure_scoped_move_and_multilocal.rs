//! Regression tests for issues #867 and #821:
//! - #867: restoring `closure_scoped_envs` for a block-scoped closure must
//!   preserve post-block `moved` state on outer captures and block-local aliases.
//! - #821: multi-value `Stmt::Local` self-shadowing (`a, b := pair(a)`) inside a
//!   closure must preserve `shadow_links`, `aliases`, and `may_share` edges.

use hy_check::{check_file, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> hy_check::CheckResult {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    check_file(&ast)
}

fn codes(r: &hy_check::CheckResult) -> Vec<String> {
    r.diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code.clone())
        .collect()
}

#[test]
fn issue_867_block_scoped_closure_observes_post_block_move_of_outer_capture() {
    let r = check(
        "fn consume(owned map[string]int) { owned[\"a\"] = 2 }\n\
         fn main() {\n\
         \x20   x := map[string]int{\"a\": 1}\n\
         \x20   if true {\n\
         \x20       f := fn() { fmt.println(x[\"a\"]) }\n\
         \x20       f()\n\
         \x20   }\n\
         \x20   task consume(move x)\n\
         }\n",
    );
    assert_eq!(codes(&r), vec!["HYB-E3003".to_string()]);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn issue_867_block_scoped_closure_observes_post_block_move_via_block_alias() {
    let r = check(
        "fn consume(owned map[string]int) { owned[\"a\"] = 2 }\n\
         fn main() {\n\
         \x20   x := map[string]int{\"a\": 1}\n\
         \x20   if true {\n\
         \x20       a := x\n\
         \x20       f := fn() { fmt.println(a[\"a\"]) }\n\
         \x20       f()\n\
         \x20   }\n\
         \x20   task consume(move x)\n\
         }\n",
    );
    assert_eq!(codes(&r), vec!["HYB-E3003".to_string()]);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn issue_821_closure_multivalue_self_shadow_preserves_shadow_links_and_shares() {
    let r = check(
        "fn consume(owned map[string]int) { owned[\"k\"] = 9 }\n\
         fn pair(m map[string]int) (map[string]int, map[string]int) { return m, m }\n\
         fn main() {\n\
         \x20   f := fn() {\n\
         \x20       a := map[string]int{\"k\": 1}\n\
         \x20       p := a\n\
         \x20       if true {\n\
         \x20           a, b := pair(a)\n\
         \x20           fmt.println(b[\"k\"])\n\
         \x20           task consume(move a)\n\
         \x20       }\n\
         \x20       fmt.println(a[\"k\"], p[\"k\"])\n\
         \x20   }\n\
         \x20   f()\n\
         }\n",
    );
    assert_eq!(codes(&r), vec!["HYB-E3003".to_string(), "HYB-E3003".to_string()]);
    assert_eq!(r.verdict, Verdict::Reject);
}

#[test]
fn issue_867_821_positive_control_unmoved_captures_and_multivalue_accepted() {
    let r = check(
        "fn pair(m map[string]int) (map[string]int, map[string]int) { return m, m }\n\
         fn main() {\n\
         \x20   x := map[string]int{\"a\": 1}\n\
         \x20   if true {\n\
         \x20       a := x\n\
         \x20       f := fn() {\n\
         \x20           m := map[string]int{\"k\": 1}\n\
         \x20           if true {\n\
         \x20               m, b := pair(m)\n\
         \x20               fmt.println(m[\"k\"], b[\"k\"])\n\
         \x20           }\n\
         \x20           fmt.println(a[\"a\"], m[\"k\"])\n\
         \x20       }\n\
         \x20       f()\n\
         \x20   }\n\
         }\n",
    );
    assert!(codes(&r).is_empty(), "unexpected diagnostics: {:?}", r.diagnostics);
    assert_eq!(r.verdict, Verdict::Accept);
}
