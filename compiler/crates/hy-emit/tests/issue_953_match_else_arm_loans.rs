//! Issue #953: verify that `Emitter::emit_match` preserves O8 map-slot loan
//! store-backs inside the `else_arm` (`none` / `_`) of a `simple_unguarded_chain`
//! `match` (`stmt.rs:2240`), both before an early `return` and when a loan is
//! handed down into the arm via `branch_inherit`.

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit_hy(src: &str) -> String {
    let ast = parse_file("issue_953_match_else_arm_loans.hy", src).expect("parses");
    emit_go(&ast).expect("emits")
}

fn else_block(go: &str) -> &str {
    let marker = "} else {";
    let start = go.rfind(marker).expect("has else block") + marker.len();
    let rest = &go[start..];
    let end = rest.find("\n\t}").expect("has closing brace for else");
    &rest[..end]
}

#[test]
fn simple_unguarded_match_none_arm_flushes_loan_before_return_issue_953() {
    let go = emit_hy(
        "\
fn update_or_read(opt option[int]) int {
    m := map[string]int{\"k\": 10}
    p := &m[\"k\"]
    p = 99
    match opt {
        some(v): {
            p = v
        }
        none: {
            return m[\"k\"]
        }
    }
    return m[\"k\"]
}
",
    );
    let eb = else_block(&go);
    let store_pos = eb
        .find("m[\"k\"] = p")
        .unwrap_or_else(|| panic!("expected store-back in else arm, got:\n{go}"));
    let ret_pos = eb
        .find("return m[\"k\"]")
        .unwrap_or_else(|| panic!("expected return in else arm, got:\n{go}"));
    assert!(
        store_pos < ret_pos,
        "store-back must precede return inside else arm:\n{go}"
    );
}

#[test]
fn simple_unguarded_match_none_arm_flushes_inherited_loan_at_block_end_issue_953() {
    let go = emit_hy(
        "\
fn apply_opt(opt option[int]) int {
    m := map[string]int{\"k\": 10}
    p := &m[\"k\"]
    p = 99
    match opt {
        some(v): {
            p = v
            _ = m[\"k\"]
        }
        none: {
            p = p + 1
        }
    }
    return m[\"k\"]
}
",
    );
    let eb = else_block(&go);
    let mutate_pos = eb
        .find("p = p + 1")
        .unwrap_or_else(|| panic!("expected mutation in else arm, got:\n{go}"));
    let store_pos = eb
        .find("m[\"k\"] = p")
        .unwrap_or_else(|| panic!("expected inherited loan store-back in else arm, got:\n{go}"));
    assert!(
        mutate_pos < store_pos,
        "inherited loan store-back must follow mutation at end of else arm:\n{go}"
    );
}

#[test]
fn simple_unguarded_match_wildcard_else_arm_flushes_before_map_read_issue_953() {
    let go = emit_hy(
        "\
fn fallback_read(opt option[int]) int {
    m := map[string]int{\"k\": 10}
    p := &m[\"k\"]
    p = 99
    match opt {
        some(v): {
            p = v
            _ = m[\"k\"]
        }
        _: {
            _ = m[\"k\"]
        }
    }
    return m[\"k\"]
}
",
    );
    let eb = else_block(&go);
    let store_pos = eb
        .find("m[\"k\"] = p")
        .unwrap_or_else(|| panic!("expected store-back in wildcard else arm, got:\n{go}"));
    let read_pos = eb
        .find("_ = m[\"k\"]")
        .unwrap_or_else(|| panic!("expected map read in wildcard else arm, got:\n{go}"));
    assert!(
        store_pos < read_pos,
        "unused inherited loan must flush before map read in wildcard else arm:\n{go}"
    );
}
