//! Issue #1131 — fail-to-pass regression tests for bare unknown dotted roots
//! on assignment left-hand sides (`PlaceKind::Field`: `widgets.count = 1`,
//! `ghost.inner.val += 2`) and address-of / indexed field targets (`&ghost.field`).

use hy_check::{check_file, CheckResult, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    check_file(&ast)
}

#[test]
fn direct_place_field_assign_on_unknown_root_emits_single_e1001() {
    let r = check("fn main() {\n    widgets.count = 1\n}\n");
    assert_eq!(r.verdict, Verdict::Reject, "{:?}", r.diagnostics);
    let e1001: Vec<_> = r
        .diagnostics
        .iter()
        .filter(|d| d.severity == Severity::Error && d.code == "HYB-E1001")
        .collect();
    assert_eq!(
        e1001.len(),
        1,
        "expected exactly 1 HYB-E1001 (no double-fire on base ident), got: {:?}",
        r.diagnostics
    );
    assert!(
        e1001[0].message.contains("widgets.count")
            && e1001[0].message.contains("curated allowlist"),
        "expected HYB-E1001 naming `widgets.count` and curated allowlist, got: {:?}",
        e1001[0]
    );
}

#[test]
fn nested_place_field_compound_assign_and_addr_of_reject_e1001() {
    let r_chain = check("fn main() {\n    ghost.inner.val += 2\n}\n");
    assert_eq!(r_chain.verdict, Verdict::Reject, "{:?}", r_chain.diagnostics);
    assert!(
        r_chain.diagnostics.iter().any(|d| {
            d.code == "HYB-E1001"
                && d.message.contains("ghost.inner")
                && d.message.contains("curated allowlist")
        }),
        "expected HYB-E1001 on `ghost.inner` in compound place assignment, got: {:?}",
        r_chain.diagnostics
    );

    let r_addr = check("fn main() {\n    p := &ghost.field\n    _ = p\n}\n");
    assert_eq!(r_addr.verdict, Verdict::Reject, "{:?}", r_addr.diagnostics);
    assert!(
        r_addr.diagnostics.iter().any(|d| {
            d.code == "HYB-E1001"
                && d.message.contains("ghost.field")
                && d.message.contains("curated allowlist")
        }),
        "expected HYB-E1001 on `&ghost.field`, got: {:?}",
        r_addr.diagnostics
    );

    let r_idx_place = check("fn main() {\n    ghost.field[0] = 1\n}\n");
    assert_eq!(r_idx_place.verdict, Verdict::Reject, "{:?}", r_idx_place.diagnostics);
    assert!(
        r_idx_place.diagnostics.iter().any(|d| {
            d.code == "HYB-E1001"
                && d.message.contains("ghost.field")
                && d.message.contains("curated allowlist")
        }),
        "expected HYB-E1001 on `ghost.field[0] = 1`, got: {:?}",
        r_idx_place.diagnostics
    );
}
