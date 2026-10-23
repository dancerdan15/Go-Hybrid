//! Issue #1168 — Fail-to-Pass tests for hex-float exponent forms (`p`/`P`).
//!
//! Verifies that hex-float literals with binary exponents (`0x1.8p2`, `0x1.8p-2`,
//! `0x1.8P+4`, `0x1p2`, `0x1p-2`, `0x1P+4`) are consumed as a single malformed
//! token and rejected with `HYB-E0002` tagged `LX6`.

use hy_syntax::{lex, parse_file};

#[test]
fn hex_float_with_dot_and_exponent_rejected_as_single_token() {
    for lit in ["0x1.8p2", "0x1.8p-2", "0x1.8P+4", "0X1.fP-3"] {
        let src = format!("fn main() {{\n    hex_val := {lit}\n}}\n");
        let errs = lex("issue_1168_hex_exp.hy", &src)
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
            d.message.contains("malformed 0x integer literal")
                && d.message.contains("prefixed base literals cannot have a fractional part"),
            "unexpected message for `{lit}`: {}",
            d.message
        );
        assert!(parse_file("issue_1168_hex_exp.hy", &src).is_err());
    }
}

#[test]
fn dotless_hex_float_exponent_rejected_as_single_token() {
    for (lit, base) in [
        ("0x1p2", "0x"),
        ("0x1p-2", "0x"),
        ("0x1P+4", "0x"),
        ("0XffP-1", "0x"),
        ("0b1e-2", "0b"),
        ("0o7e-2", "0o"),
    ] {
        let src = format!("fn main() {{\n    hex_val := {lit}\n}}\n");
        let errs = lex("issue_1168_dotless_exp.hy", &src)
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
            d.message.contains(&format!("malformed {base} integer literal")),
            "unexpected message for `{lit}`: {}",
            d.message
        );
        assert!(parse_file("issue_1168_dotless_exp.hy", &src).is_err());
    }
}
