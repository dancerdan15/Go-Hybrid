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

struct doc_holder {
    view serde_json.view
}

fn read_id(v serde_json.view, xs []serde_json.view, opt option[serde_json.view], idx map[string]serde_json.view) (int, error) {
    var local_view serde_json.view = v
    _ = xs
    _ = opt
    _ = idx
    id := local_view.int("id")?
    return id, nil
}

fn main() {
    doc := serde_json.parse(`{"id": 7}`)?
    h := doc_holder{view: doc}
    n := h.view.int("id")?
    fmt.println(n)
}
"#;
    let go = emit(src);
    assert!(go.contains("\"hybrid.program/jsonbinding\""), "{go}");
    assert!(!go.contains("\"serde_json\"") && !go.contains("serde_json."), "{go}");
    assert!(go.contains("view jsonbinding.Value"), "{go}");
    assert!(
        go.contains("func read_id(v jsonbinding.Value, xs []jsonbinding.Value, opt option[jsonbinding.Value], idx map[string]jsonbinding.Value) (int, error)"),
        "{go}"
    );
    assert!(go.contains("var local_view jsonbinding.Value = v"), "{go}");
    assert!(go.contains("local_view.Int(\"id\")") && go.contains("h.view.Int(\"id\")"), "{go}");
}

#[test]
fn serde_json_view_and_value_methods_and_error_arities_emit_valid_go() {
    let src = r#"
import "rust/serde_json"

fn mutate_view(v serde_json.view, child serde_json.value) (string, error) {
    v.set("child", child)?
    v.set_int("count", 3)?
    v.set_bool("ok", true)?
    v.remove("legacy")?
    v.push_string("item")?
    sub := v.get("user")?
    uid := v.get("user").int("id")?
    name := v.string("name")?
    items := v.array("items")?
    pretty := v.to_string_pretty()?
    v.release()
    _ = sub
    _ = uid
    _ = name
    _ = items
    return pretty, nil
}

fn main() {
    doc := serde_json.new_object()
    doc.set_int("n", 1)?
    doc.remove("n")?
    for item in doc.get("data").array("items")? {
        id := item.int("id")?
        fmt.println(id)
    }
    s := doc.to_string_pretty()?
    doc.release()
    fmt.println(s)
}
"#;
    let go = emit(src);
    for expected in [
        "hyErr0 := v.Set(\"child\", child)",
        "hyErr1 := v.SetInt(\"count\", 3)",
        "hyErr2 := v.SetBool(\"ok\", true)",
        "hyErr3 := v.Remove(\"legacy\")",
        "hyErr4 := v.PushString(\"item\")",
        "v.Get(\"user\")",
        "v.Get(\"user\").Int(\"id\")",
        "v.String(\"name\")",
        "v.Array(\"items\")",
        "v.ToStringPretty()",
        "v.Release()",
        "doc.SetInt(\"n\", 1)",
        "doc.Remove(\"n\")",
        "doc.Get(\"data\").Array(\"items\")",
        "item.Int(\"id\")",
        "doc.ToStringPretty()",
        "doc.Release()",
    ] {
        assert!(go.contains(expected), "missing `{expected}` in:\n{go}");
    }
}
