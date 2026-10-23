//! Integration tests for `serde_json.view` and `serde_json.value` Go emission.

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit(src: &str) -> String {
    let file = parse_file("t.hy", src).expect("parse");
    emit_go(&file).expect("emit")
}

#[test]
fn serde_json_view_type_annotations_map_to_jsonbinding_value() {
    let src = r#"
import "rust/serde_json"

fn read_name(v serde_json.view, xs []serde_json.view, opt option[serde_json.view], idx map[string]serde_json.view) (string, error) {
    var local_view serde_json.view = v
    _ = xs
    _ = opt
    _ = idx
    name := local_view.string("name")?
    return name, nil
}

fn main() {
    doc := serde_json.parse(`{"name": "ada"}`)?
    n := read_name(doc, []serde_json.view{doc}, some(doc), map[string]serde_json.view{"d": doc})?
    fmt.println(n)
}
"#;
    let go = emit(src);
    assert!(go.contains("\"hybrid.program/jsonbinding\""), "{go}");
    assert!(!go.contains("\"serde_json\"") && !go.contains("serde_json."), "{go}");
    assert!(
        go.contains("func read_name(v jsonbinding.Value, xs []jsonbinding.Value, opt option[jsonbinding.Value], idx map[string]jsonbinding.Value) (string, error)"),
        "{go}"
    );
    assert!(go.contains("var local_view jsonbinding.Value = v"), "{go}");
    assert!(go.contains("local_view.String(\"name\")"), "{go}");
}

#[test]
fn serde_json_view_methods_and_error_arities_emit_existing_binding_calls() {
    let src = r#"
import "rust/serde_json"

fn fill(v serde_json.view, list serde_json.view, child serde_json.value) (string, error) {
    v.set("child", child)?
    list.push_string("item")?
    uid := v.int("id")?
    name := v.string("name")?
    items := v.array("items")?
    _ = uid
    _ = items
    return name, nil
}

fn main() {
    doc := serde_json.parse(`{"items": [{"id": 1, "name": "ada", "items": []}]}`)?
    list := serde_json.new_array()
    for item in doc.array("items")? {
        s := fill(item, list, serde_json.new_object())?
        fmt.println(s)
    }
}
"#;
    let go = emit(src);
    for expected in [
        "hyErr0 := v.Set(\"child\", child)",
        "hyErr1 := list.PushString(\"item\")",
        "v.Int(\"id\")",
        "v.String(\"name\")",
        "v.Array(\"items\")",
        "doc.Array(\"items\")",
    ] {
        assert!(go.contains(expected), "missing `{expected}` in:\n{go}");
    }
}
