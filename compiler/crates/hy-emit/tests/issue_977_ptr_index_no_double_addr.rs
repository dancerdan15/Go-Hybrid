use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit_src(src: &str) -> String {
    let ast = parse_file("test.hy", src).expect("parses");
    emit_go(&ast).expect("emits")
}

#[test]
fn slice_and_array_of_pointers_index_not_double_addressed() {
    let src = r#"
struct inner {
    val int
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
    q := []*inner{new(inner)}
    a := [1]*inner{new(inner)}
    var s []*inner
    var arr [2]*inner
    take(q[0])
    take(a[0])
    take(s[0])
    take(arr[1])
    b.take(q[0])
    b.take(a[0])
}
"#;
    let go = emit_src(src);
    assert!(go.contains("take(q[0])") && !go.contains("take(&q[0])"), "got:\n{go}");
    assert!(go.contains("take(a[0])") && !go.contains("take(&a[0])"), "got:\n{go}");
    assert!(go.contains("take(s[0])") && !go.contains("take(&s[0])"), "got:\n{go}");
    assert!(go.contains("take(arr[1])") && !go.contains("take(&arr[1])"), "got:\n{go}");
    assert!(go.contains("b.take(q[0])") && !go.contains("b.take(&q[0])"), "got:\n{go}");
    assert!(go.contains("b.take(a[0])") && !go.contains("b.take(&a[0])"), "got:\n{go}");
}

#[test]
fn slice_of_values_index_still_auto_addressed() {
    let src = r#"
struct inner {
    val int
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
    vals := []inner{inner{val: 1}}
    var vs []inner
    take(vals[0])
    b.take(vs[0])
}
"#;
    let go = emit_src(src);
    assert!(go.contains("take(&vals[0])"), "expected take(&vals[0]), got:\n{go}");
    assert!(go.contains("b.take(&vs[0])"), "expected b.take(&vs[0]), got:\n{go}");
}
