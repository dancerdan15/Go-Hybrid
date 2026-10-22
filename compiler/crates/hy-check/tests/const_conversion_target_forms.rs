//! #823 — invalid constant conversions in disguise (HYB-E6006).
//!
//! Go rejects `(int)("abc")`, `bool(1)`, `my_int("abc")` (a type alias),
//! `int(f)` on `const f = 2.5`, and file-level `const a = int(2.5)` at
//! compile time. `hyb check` accepted all of them: the conversion guard
//! matched the callee without unwrapping parens, omitted `bool` from the
//! target set, never resolved `type_aliases`, folded only bare literal
//! operands, and the file-const pass never invoked the conversion check.
//! Runtime conversions and the SPEC X4 literal fold (`u8(300) == 44`)
//! stay accepted.

use hy_check::check_file;
use hy_syntax::parse_file;

fn codes(src: &str) -> Vec<String> {
    let ast = parse_file("test.hy", src).expect("parses");
    check_file(&ast)
        .diagnostics
        .into_iter()
        .map(|d| d.code)
        .collect()
}

fn assert_code(src: &str, want: &str) {
    let cs = codes(src);
    assert!(
        !cs.is_empty() && cs.iter().all(|c| c == want),
        "expected only [{want}], got {cs:?} for:\n{src}"
    );
}

fn assert_ok(src: &str) {
    let cs = codes(src);
    assert!(cs.is_empty(), "expected 0 diagnostics, got {cs:?} for:\n{src}");
}

#[test]
fn parenthesized_conversion_callees_reject() {
    assert_code(
        "fn main() {\n    x := (int)(\"abc\")\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "fn main() {\n    x := (int8)(2.5)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "fn main() {\n    x := ([]int)(\"abc\")\n    _ = x\n}\n",
        "HYB-E6006",
    );
}

#[test]
fn bool_conversion_target_rejects_non_bool() {
    assert_code(
        "fn main() {\n    x := bool(1)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "fn main() {\n    x := bool(2.5)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "fn main() {\n    x := bool(\"abc\")\n    _ = x\n}\n",
        "HYB-E6006",
    );
}

#[test]
fn type_aliased_targets_reject() {
    assert_code(
        "type my_int = int\n\nfn main() {\n    x := my_int(\"abc\")\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "type my_u8 = u8\nconst n = 300\n\nfn main() {\n    x := my_u8(n)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "type my_slice = []int\n\nfn main() {\n    x := my_slice(\"abc\")\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "type my_elem = int\n\nfn main() {\n    x := []my_elem(\"abc\")\n    _ = x\n}\n",
        "HYB-E6006",
    );
}

#[test]
fn const_ident_and_folded_operands_reject() {
    assert_code(
        "const f = 2.5\n\nfn main() {\n    x := int(f)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "fn main() {\n    x := int(1.5 + 1.0)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "fn main() {\n    x := int(1 < 2)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "const s = \"abc\"\n\nfn main() {\n    x := []int(s)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "const n = 300\n\nfn main() {\n    x := u8(n)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "fn main() {\n    x := i64(1 << 63)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "fn main() {\n    x := u64(1 << 64)\n    _ = x\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "fn main() {\n    x := f32(1e30 * 1e20)\n    _ = x\n}\n",
        "HYB-E6006",
    );
}

#[test]
fn file_const_initializer_conversions_reject() {
    assert_code(
        "const a = int(2.5)\n\nfn main() {\n    _ = a\n}\n",
        "HYB-E6006",
    );
    assert_code(
        "const d = bool(1)\n\nfn main() {\n    _ = d\n}\n",
        "HYB-E6006",
    );
}

#[test]
fn valid_conversions_stay_legal() {
    // identity bool conversion
    assert_ok("fn main() {\n    x := bool(true)\n    _ = x\n}\n");
    // representable float constant to int
    assert_ok("fn main() {\n    x := int(3.0)\n    _ = x\n}\n");
    // runtime conversion of a variable
    assert_ok("fn main() {\n    f := 3\n    x := int(f)\n    _ = x\n}\n");
    // SPEC X4 literal fold: u8(300) == 44 (sub-64 skip is literal-only)
    assert_ok("fn main() {\n    x := u8(300)\n    _ = x\n}\n");
    // type name as conversion callee with a legal operand
    assert_ok("fn main() {\n    x := int(2)\n    _ = x\n}\n");
    // string conversion of an integer constant
    assert_ok("fn main() {\n    x := string(65)\n    _ = x\n}\n");
}
