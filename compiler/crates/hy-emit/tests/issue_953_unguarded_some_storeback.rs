//! Issue #953: verify that `Emitter::emit_match` emits O8 map-slot loan
//! store-backs inside the `else_arm` (`none` / `_`) of the `has_unguarded_some`
//! branch (`stmt.rs:2311`, mixed guarded `some(v) if ...` + unguarded `some(v)`
//! + `none` / `_`), and does not emit spurious store-backs when no loan is active.

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit_hy(src: &str) -> String {
    let ast = parse_file("issue_953_unguarded_some_storeback.hy", src).expect("parses");
    emit_go(&ast).expect("emits")
}

fn outer_else_block(go: &str) -> &str {
    let marker = "\t} else {\n";
    let start = go.rfind(marker).expect("has outer else block") + marker.len();
    let rest = &go[start..];
    let end = rest.find("\n\t}").expect("has outer else closing brace");
    &rest[..end]
}

#[test]
fn has_unguarded_some_none_arm_flushes_inherited_slot_loan_issue_953() {
    let go = emit_hy(
        "\
fn classify_and_store(opt option[int]) int {
    m := map[string]int{\"k\": 10}
    p := &m[\"k\"]
    p = 99
    match opt {
        some(v) if v > 100: {
            p = v + 1
            _ = m[\"k\"]
        }
        some(v) if v > 0: {
            p = v
            _ = m[\"k\"]
        }
        some(v): {
            p = 0
            _ = m[\"k\"]
        }
        none: {
            p = p + 5
        }
    }
    return m[\"k\"]
}
",
    );
    assert!(
        go.contains("hyMatched"),
        "expected has_unguarded_some lowering with hyMatched temp:\n{go}"
    );
    let eb = outer_else_block(&go);
    let mutate_pos = eb
        .find("p = p + 5")
        .unwrap_or_else(|| panic!("expected mutation in none arm, got:\n{go}"));
    let store_pos = eb
        .find("m[\"k\"] = p")
        .unwrap_or_else(|| panic!("expected O8 store-back in none arm, got:\n{go}"));
    assert!(
        mutate_pos < store_pos,
        "store-back must follow mutation in has_unguarded_some else arm:\n{go}"
    );
}

#[test]
fn has_unguarded_some_wildcard_arm_flushes_loan_before_return_issue_953() {
    let go = emit_hy(
        "\
fn classify_or_return(opt option[int]) int {
    m := map[string]int{\"k\": 10}
    p := &m[\"k\"]
    p = 99
    match opt {
        some(v) if v > 10: {
            p = v
        }
        some(v) if v > 0: {
            p = v + 1
        }
        some(v): {
            p = 0
        }
        _: {
            return m[\"k\"]
        }
    }
    return m[\"k\"]
}
",
    );
    assert!(
        go.contains("hyMatched"),
        "expected has_unguarded_some lowering:\n{go}"
    );
    let eb = outer_else_block(&go);
    let store_pos = eb
        .find("m[\"k\"] = p")
        .unwrap_or_else(|| panic!("expected store-back before return in wildcard arm, got:\n{go}"));
    let ret_pos = eb
        .find("return m[\"k\"]")
        .unwrap_or_else(|| panic!("expected return in wildcard arm, got:\n{go}"));
    assert!(
        store_pos < ret_pos,
        "store-back must precede return in has_unguarded_some wildcard else arm:\n{go}"
    );
}

#[test]
fn positive_control_match_without_slot_loan_has_no_storeback_issue_953() {
    let go = emit_hy(
        "\
fn read_only_match(opt option[int]) int {
    m := map[string]int{\"k\": 10}
    match opt {
        some(v) if v > 10: {
            return v + m[\"k\"]
        }
        some(v) if v > 0: {
            return v
        }
        some(_): {
            return 0
        }
        none: {
            return m[\"k\"]
        }
    }
}
",
    );
    assert!(
        !go.contains("m[\"k\"] ="),
        "match without active O8 slot loan must not emit any map store-back:\n{go}"
    );
}
