//! Regression tests for issue #808: nested fixed-width integer constant
//! conversions (`u8(u16(300))`, `u8(i16(-1))`, `i8(u8(200))`, `i16(u8(300))`,
//! `u16(u8(200) + u8(100))`, `u8(u8(200) + u8(100))`) must evaluate inner
//! two's-complement truncations per SPEC X4 and emit valid Go constants.

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit_src(src: &str) -> String {
    let ast = parse_file("issue_808_nested.hy", src).expect("parses");
    emit_go(&ast).expect("emits")
}

#[test]
fn nested_fixed_width_conversions_fold_twos_complement() {
    let go = emit_src(
        r#"
fn main() {
    a_val := u8(u16(300))
    b_val := u8(i16(-1))
    c_val := i8(u8(200))
    d_val := i16(u8(300))
    e_val := u16(u8(200) + u8(100))
    f_val := u8(u8(200) + u8(100))
    fmt.println("{a_val} {b_val} {c_val} {d_val} {e_val} {f_val}")
}
"#,
    );
    assert!(
        go.contains("a_val := uint8(44)"),
        "expected u8(u16(300)) -> uint8(44), got:\n{go}"
    );
    assert!(
        go.contains("b_val := uint8(255)"),
        "expected u8(i16(-1)) -> uint8(255), got:\n{go}"
    );
    assert!(
        go.contains("c_val := int8(-56)"),
        "expected i8(u8(200)) -> int8(-56), got:\n{go}"
    );
    assert!(
        go.contains("d_val := int16(44)"),
        "expected i16(u8(300)) -> int16(44) via inner u8 truncation, got:\n{go}"
    );
    assert!(
        go.contains("e_val := uint16(300)"),
        "expected u16(u8(200) + u8(100)) -> uint16(300), got:\n{go}"
    );
    assert!(
        go.contains("f_val := uint8(44)"),
        "expected u8(u8(200) + u8(100)) -> uint8(44), got:\n{go}"
    );
}

#[test]
fn in_range_nested_conversions_stay_untouched_positive_control() {
    let go = emit_src(
        r#"
fn main() {
    ok_one := i16(u8(200))
    ok_two := u16(u8(44))
    fmt.println("{ok_one} {ok_two}")
}
"#,
    );
    assert!(
        go.contains("ok_one := int16(uint8(200))"),
        "in-range i16(u8(200)) must not fold, got:\n{go}"
    );
    assert!(
        go.contains("ok_two := uint16(uint8(44))"),
        "in-range u16(u8(44)) must not fold, got:\n{go}"
    );
}
