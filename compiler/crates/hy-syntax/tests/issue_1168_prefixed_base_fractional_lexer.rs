//! Issue #1168 — Fail-to-Pass tests for prefixed-base literals with fractional parts.
//!
//! Verifies that `0x1.8`, `0x1.f`, `0x1.`, `0b1.0`, `0o7.5` (and uppercase prefix
//! variants) are rejected at lex/parse time with `HYB-E0002` tagged `LX6`, instead
//! of silently splitting into an integer literal followed by an orphan `.8` float.

use hy_syntax::{lex, parse_file};

#[test]
fn prefixed_base_fractional_literals_fail_closed() {
    for (lit, base) in [
        ("0x1.8", "0x"),
        ("0x1.f", "0x"),
        ("0x1.", "0x"),
        ("0X1.8", "0x"),
        ("0b1.0", "0b"),
        ("0B1.0", "0b"),
        ("0b1.0e-2", "0b"),
        ("0o7.5", "0o"),
        ("0O7.5", "0o"),
        ("0o7.5e+2", "0o"),
    ] {
        let src = format!("fn main() {{\n    val := {lit}\n}}\n");
        let errs = lex("issue_1168_frac.hy", &src)
            .expect_err(&format!("expected lex error for `{lit}`"));
        assert_eq!(
            errs.len(),
            1,
            "expected single diagnostic for `{lit}`, got: {errs:?}"
        );
        let d = &errs[0];
        assert_eq!(d.code, "HYB-E0002");
        assert_eq!(d.tier0_rule.as_deref(), Some("LX6"));
        assert!(
            d.message.contains(&format!("malformed {base} integer literal"))
                && d.message.contains("prefixed base literals cannot have a fractional part"),
            "unexpected diagnostic message for `{lit}`: {}",
            d.message
        );

        let parse_errs = parse_file("issue_1168_frac.hy", &src)
            .expect_err(&format!("expected parse_file failure for `{lit}`"));
        assert!(
            parse_errs
                .iter()
                .any(|e| e.code == "HYB-E0002" && e.tier0_rule.as_deref() == Some("LX6")),
            "expected HYB-E0002 (LX6) from parse_file for `{lit}`, got: {parse_errs:?}"
        );
    }
}
