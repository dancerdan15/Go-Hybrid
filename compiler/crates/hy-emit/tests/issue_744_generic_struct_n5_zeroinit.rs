//! Issue #744: SPEC N5 zero-value map auto-allocation for instantiated generic structs.

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit(src: &str) -> String {
    let file = parse_file("t.hy", src).expect("parse");
    emit_go(&file).expect("emit")
}

#[test]
fn generic_struct_direct_map_field_zero_init_lit_and_new() {
    let src = r#"
struct box_val[t] {
    m map[string]int
    v t
}

struct outer_box[t] {
    inner box_val[t]
}

fn main() {
    var b box_val[int]
    b2 := box_val[int]{v: 42}
    b3 := box_val[u8]{}
    b_given := box_val[int]{m: map[string]int{"k": 1}, v: 9}
    bp := new(box_val[int])
    var ob outer_box[int]
}
"#;
    let go = emit(src);
    assert!(go.contains("var b box_val[int] = box_val[int]{m: map[string]int{}}"), "{go}");
    assert!(go.contains("b2 := box_val[int]{v: 42, m: map[string]int{}}"), "{go}");
    assert!(go.contains("b3 := box_val[uint8]{m: map[string]int{}}"), "{go}");
    assert!(go.contains("b_given := box_val[int]{m: map[string]int{\"k\": 1}, v: 9}"), "{go}");
    assert!(go.contains("bp := &box_val[int]{m: map[string]int{}}"), "{go}");
    assert!(
        go.contains("var ob outer_box[int] = outer_box[int]{inner: box_val[int]{m: map[string]int{}}}"),
        "{go}"
    );
}

#[test]
fn generic_struct_type_param_substituted_to_map_and_positive_control() {
    let src = r#"
type str_map = map[string]int

struct holder[m_ty] {
    inner m_ty
}

struct key_map[k comparable, v any] {
    items map[k]v
}

struct plain_box[t] {
    v t
}

fn main() {
    var h holder[map[string]int]
    h2 := holder[map[string]int]{}
    hp := new(holder[str_map])
    hp2 := new(holder[map[string]int]{})
    var km key_map[string, int]
    var p plain_box[int]
    p2 := plain_box[int]{v: 7}
    pp := new(plain_box[int])
    var h_plain holder[int]
}
"#;
    let go = emit(src);
    assert!(
        go.contains("var h holder[map[string]int] = holder[map[string]int]{inner: map[string]int{}}"),
        "{go}"
    );
    assert!(go.contains("h2 := holder[map[string]int]{inner: map[string]int{}}"), "{go}");
    assert!(go.contains("hp := &holder[str_map]{inner: str_map{}}"), "{go}");
    assert!(go.contains("hp2 := &holder[map[string]int]{inner: map[string]int{}}"), "{go}");
    assert!(
        go.contains("var km key_map[string, int] = key_map[string, int]{items: map[string]int{}}"),
        "{go}"
    );
    assert!(go.contains("\tvar p plain_box[int]\n"), "{go}");
    assert!(go.contains("p2 := plain_box[int]{v: 7}"), "{go}");
    assert!(go.contains("pp := new(plain_box[int])"), "{go}");
    assert!(go.contains("\tvar h_plain holder[int]\n"), "{go}");
}
