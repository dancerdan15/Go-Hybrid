//! Issue #1206: SPEC N5 zero-value map auto-allocation for fixed-size array elements.

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit(src: &str) -> String {
    let file = parse_file("t.hy", src).expect("parse");
    emit_go(&file).expect("emit")
}

#[test]
fn fixed_array_of_maps_and_map_structs_zero_init_and_new() {
    let src = r#"
type map_pair = [2]map[string]int

struct bag {
    counts map[string]int
}

struct shelf {
    slots [2]map[string]int
    tag string
}

fn main() {
    var arr [2]map[string]int
    var bags [2]bag
    var s shelf
    s2 := shelf{tag: "a"}
    ap := new([2]map[string]int{})
    bp := new([2]bag{})
    mp := new(map_pair)
}
"#;
    let go = emit(src);
    assert!(
        go.contains("var arr [2]map[string]int = [2]map[string]int{map[string]int{}, map[string]int{}}"),
        "{go}"
    );
    assert!(
        go.contains("var bags [2]bag = [2]bag{bag{counts: map[string]int{}}, bag{counts: map[string]int{}}}"),
        "{go}"
    );
    assert!(
        go.contains("var s shelf = shelf{slots: [2]map[string]int{map[string]int{}, map[string]int{}}}"),
        "{go}"
    );
    assert!(
        go.contains("s2 := shelf{tag: \"a\", slots: [2]map[string]int{map[string]int{}, map[string]int{}}}"),
        "{go}"
    );
    assert!(go.contains("ap := &[2]map[string]int{map[string]int{}, map[string]int{}}"), "{go}");
    assert!(go.contains("bp := &[2]bag{bag{counts: map[string]int{}}, bag{counts: map[string]int{}}}"), "{go}");
    assert!(go.contains("mp := &[2]map[string]int{map[string]int{}, map[string]int{}}"), "{go}");
}

#[test]
fn fixed_array_without_maps_positive_control_stays_uninitialized() {
    let src = r#"
struct plain_item {
    x int
}

fn main() {
    var nums [4]int
    var items [2]plain_item
}
"#;
    let go = emit(src);
    assert!(go.contains("\tvar nums [4]int\n"), "{go}");
    assert!(go.contains("\tvar items [2]plain_item\n"), "{go}");
}
