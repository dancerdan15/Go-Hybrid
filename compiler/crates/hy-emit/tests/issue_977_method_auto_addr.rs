use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit_src(src: &str) -> String {
    let ast = parse_file("test.hy", src).expect("parses");
    emit_go(&ast).expect("emits")
}

#[test]
fn method_callee_single_and_multi_ptr_params() {
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
fn (b box) pair(v *inner, w *inner) {
    _ = v
    _ = w
}
fn main() {
    b := box{}
    p := &b
    v := inner{val: 42}
    w := inner{val: 7}
    b.take(v)
    p.take(v)
    b.pair(v, w)
}
"#;
    let go = emit_src(src);
    assert!(go.contains("b.take(&v)"), "expected b.take(&v), got:\n{go}");
    assert!(go.contains("p.take(&v)"), "expected p.take(&v), got:\n{go}");
    assert!(go.contains("b.pair(&v, &w)"), "expected b.pair(&v, &w), got:\n{go}");
}

#[test]
fn method_callee_leaves_value_params_and_already_ptr_args_intact() {
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
fn (b *box) keep(v inner) {
    _ = v
}
fn main() {
    b := box{}
    v := inner{val: 1}
    pv := &v
    b.keep(v)
    b.take(pv)
    b.take(&v)
    b.take(new(inner))
}
"#;
    let go = emit_src(src);
    assert!(go.contains("b.keep(v)") && !go.contains("b.keep(&v)"), "got:\n{go}");
    assert!(go.contains("b.take(pv)") && !go.contains("b.take(&pv)"), "got:\n{go}");
    assert!(go.contains("b.take(&v)") && !go.contains("b.take(&&v)"), "got:\n{go}");
    assert!(go.contains("b.take(new(inner))"), "got:\n{go}");
}
