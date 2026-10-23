use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit_src(src: &str) -> String {
    let ast = parse_file("test.hy", src).expect("parses");
    emit_go(&ast).expect("emits")
}

#[test]
fn map_pointer_value_and_struct_pointer_field_not_double_addressed() {
    let src = r#"
struct inner {
    val int
}
struct holder {
    item *inner
    plain inner
}
struct box {
    x int
}
fn take(v *inner) {
    _ = v
}
fn (b *box) take(v *inner) {
    _ = v
}
fn main() {
    b := box{}
    var m map[string]*inner
    h := holder{item: new(inner), plain: inner{val: 3}}
    take(m["k"])
    take(h.item)
    b.take(h.item)
    take(h.plain)
    b.take(h.plain)
}
"#;
    let go = emit_src(src);
    assert!(go.contains("take(m[\"k\"])") && !go.contains("take(&m[\"k\"])"), "got:\n{go}");
    assert!(go.contains("take(h.item)") && !go.contains("take(&h.item)"), "got:\n{go}");
    assert!(go.contains("b.take(h.item)") && !go.contains("b.take(&h.item)"), "got:\n{go}");
    assert!(go.contains("take(&h.plain)"), "expected take(&h.plain), got:\n{go}");
    assert!(go.contains("b.take(&h.plain)"), "expected b.take(&h.plain), got:\n{go}");
}

#[test]
fn closure_fnlit_method_and_pointer_index_auto_address() {
    let src = r#"
struct inner {
    val int
}
struct box {
    x int
}
fn (b *box) take(v *inner) {
    _ = v
}
fn main() {
    run := fn() {
        b := box{}
        v := inner{val: 9}
        q := []*inner{new(inner)}
        b.take(v)
        b.take(q[0])
    }
    run()
}
"#;
    let go = emit_src(src);
    assert!(go.contains("b.take(&v)"), "expected b.take(&v) in closure, got:\n{go}");
    assert!(go.contains("b.take(q[0])") && !go.contains("b.take(&q[0])"), "got:\n{go}");
}
