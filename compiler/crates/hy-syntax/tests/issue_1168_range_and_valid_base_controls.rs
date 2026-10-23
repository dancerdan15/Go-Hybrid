//! Issue #1168 — Positive controls for range operators, hex arithmetic, and valid literals.
//!
//! Ensures the prefixed-base fractional guard does not over-reach onto `..` / `..=`
//! ranges (`0x1..0x5`, `0x1..=0x5`), hex `e`/`E` subtraction (`0x1e-2`), or valid
//! prefixed integers and decimal floats (`0` diagnostics).

use hy_syntax::{lex, parse_file, Tok};

fn toks(src: &str) -> Vec<Tok> {
    lex("issue_1168_control.hy", src)
        .expect("expected clean lex for positive control")
        .into_iter()
        .map(|t| t.tok)
        .collect()
}

#[test]
fn prefixed_base_ranges_and_hex_subtraction_lex_cleanly() {
    assert_eq!(
        toks("0x1..0x5"),
        vec![
            Tok::Int("0x1".into()),
            Tok::Punct(".."),
            Tok::Int("0x5".into()),
            Tok::Eof,
        ]
    );
    assert_eq!(
        toks("0x1..=0x5"),
        vec![
            Tok::Int("0x1".into()),
            Tok::Punct("..="),
            Tok::Int("0x5".into()),
            Tok::Eof,
        ]
    );
    assert_eq!(
        toks("0x1e-2"),
        vec![
            Tok::Int("0x1e".into()),
            Tok::Punct("-"),
            Tok::Int("2".into()),
            Tok::Eof,
        ]
    );
    assert_eq!(
        toks("0x1e+2"),
        vec![
            Tok::Int("0x1e".into()),
            Tok::Punct("+"),
            Tok::Int("2".into()),
            Tok::Eof,
        ]
    );
}

#[test]
fn valid_prefixed_integers_ranges_and_decimal_floats_parse_cleanly() {
    let src = r#"
fn main() {
    hex_mask := 0xff_00
    bin_mask := 0b1010_0101
    oct_perm := 0o755
    hex_diff := 0x1e-2
    dec_float := 1.5e-2
    lead_dot := .5
    trail_dot := 2.
    for idx in 0x1..0x5 {
        _ = idx
    }
    for step in 0x1..=0x5 {
        _ = step
    }
    _ = hex_mask + bin_mask + oct_perm + hex_diff
    _ = dec_float + lead_dot + trail_dot
}
"#;
    let file = parse_file("issue_1168_control.hy", src)
        .expect("expected 0 diagnostics for valid prefixed integers, ranges, and decimal floats");
    assert_eq!(file.decls.len(), 1);
}
