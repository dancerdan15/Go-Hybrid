//! Regression tests for issue #808: platform-word integer targets (`int`,
//! `uint`, `uintptr`) and unary/bitwise subexpressions containing constant
//! conversions in `hy-emit` SPEC X4 constant folding.

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit_src(src: &str) -> String {
    let ast = parse_file("issue_808_word.hy", src).expect("parses");
    emit_go(&ast).expect("emits")
}

#[test]
fn platform_word_and_subexpr_conversions_fold_cleanly() {
    let go = emit_src(
        r#"
fn main() {
    w_int := u8(int(300))
    w_uint := u8(uint(300))
    w_ptr := u8(uintptr(300))
    w_neg_uint := u64(uint(100 - 200))
    w_ptr_trunc := u16(uintptr(70000))
    u_neg := u8(-u16(300))
    s_neg := i8(-u8(200))
    bit_xor := u8(u16(0x1ff) ^ u16(0xd3))
    paren_callee := u8((u16)(300))
    fmt.println("{w_int} {w_uint} {w_ptr} {w_neg_uint} {w_ptr_trunc} {u_neg} {s_neg} {bit_xor} {paren_callee}")
}
"#,
    );
    assert!(
        go.contains("w_int := uint8(44)"),
        "expected u8(int(300)) -> uint8(44), got:\n{go}"
    );
    assert!(
        go.contains("w_uint := uint8(44)"),
        "expected u8(uint(300)) -> uint8(44), got:\n{go}"
    );
    assert!(
        go.contains("w_ptr := uint8(44)"),
        "expected u8(uintptr(300)) -> uint8(44), got:\n{go}"
    );
    assert!(
        go.contains("w_neg_uint := uint64(18446744073709551516)"),
        "expected u64(uint(100 - 200)) -> uint64(18446744073709551516), got:\n{go}"
    );
    assert!(
        go.contains("w_ptr_trunc := uint16(4464)"),
        "expected u16(uintptr(70000)) -> uint16(4464), got:\n{go}"
    );
    assert!(
        go.contains("u_neg := uint8(212)"),
        "expected u8(-u16(300)) -> uint8(212), got:\n{go}"
    );
    assert!(
        go.contains("s_neg := int8(56)"),
        "expected i8(-u8(200)) -> int8(56), got:\n{go}"
    );
    assert!(
        go.contains("bit_xor := uint8(44)"),
        "expected u8(u16(0x1ff) ^ u16(0xd3)) -> uint8(44), got:\n{go}"
    );
    assert!(
        go.contains("paren_callee := uint8(44)"),
        "expected u8((u16)(300)) -> uint8(44), got:\n{go}"
    );
}

#[test]
fn non_constant_and_ordinary_calls_do_not_fold_positive_control() {
    let go = emit_src(
        r#"
fn pass_thru(x_val int) int {
    return x_val
}

fn main() {
    dyn_val := 300
    keep_var := u8(dyn_val)
    keep_call := u8(pass_thru(300))
    fmt.println("{keep_var} {keep_call}")
}
"#,
    );
    assert!(
        go.contains("keep_var := uint8(dyn_val)"),
        "variable operand must not fold as constant, got:\n{go}"
    );
    assert!(
        go.contains("keep_call := uint8(pass_thru(300))"),
        "ordinary function call must not fold as conversion, got:\n{go}"
    );
}
