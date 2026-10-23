//! Regression tests for #727: `fn(...)` field types inside `struct` declarations
//! must not absorb subsequent struct fields across a newline boundary.

use hy_check::{check_file, Verdict};
use hy_syntax::ast::{Decl, TypeExpr};
use hy_syntax::parse_file;

#[test]
fn struct_fn_typed_fields_preserve_following_fields_across_newlines_issue_727() {
    let src = r#"
struct event_bus {
    on_event fn(int) bool
    name string
    on_tick fn()
    count int
    on_pair fn(int) (int, bool)
    active bool
}

fn main() {
    bus := event_bus{
        on_event: fn(v int) bool { return v > 0 },
        name: "main_bus",
        on_tick: fn() {},
        count: 4,
        on_pair: fn(v int) (int, bool) { return v, true },
        active: true,
    }
    fmt.println(bus.name, bus.count, bus.active)
}
"#;
    let ast = parse_file("inline.hy", src).expect("must parse");
    let st = ast
        .decls
        .iter()
        .find_map(|d| match d {
            Decl::Struct(s) if s.name == "event_bus" => Some(s),
            _ => None,
        })
        .expect("struct event_bus");

    assert_eq!(
        st.fields.len(),
        6,
        "all 6 struct fields must be preserved, got {:?}",
        st.fields
    );
    assert_eq!(st.fields[0].name, "on_event");
    assert!(matches!(
        &st.fields[0].ty,
        TypeExpr::Fn { results, .. } if results.len() == 1
    ));
    assert_eq!(st.fields[1].name, "name");
    assert!(matches!(&st.fields[1].ty, TypeExpr::Named { name, .. } if name == "string"));

    assert_eq!(st.fields[2].name, "on_tick");
    assert!(matches!(
        &st.fields[2].ty,
        TypeExpr::Fn { results, .. } if results.is_empty()
    ));
    assert_eq!(st.fields[3].name, "count");
    assert!(matches!(&st.fields[3].ty, TypeExpr::Named { name, .. } if name == "int"));

    assert_eq!(st.fields[4].name, "on_pair");
    assert!(matches!(
        &st.fields[4].ty,
        TypeExpr::Fn { results, .. } if results.len() == 2
    ));
    assert_eq!(st.fields[5].name, "active");
    assert!(matches!(&st.fields[5].ty, TypeExpr::Named { name, .. } if name == "bool"));

    let res = check_file(&ast);
    assert_eq!(res.verdict, Verdict::Accept, "diags: {:?}", res.diagnostics);
}

#[test]
fn const_struct_field_after_fn_field_rejects_mutation_issue_727() {
    // Verifies that `name` and `count` following `on_event` / `on_tick` are
    // tracked by the checker as real fields on `const bus`.
    let src = r#"
struct event_bus {
    on_event fn(int) bool
    name string
    on_tick fn()
    count int
}

fn main() {
    const bus = event_bus{
        on_event: fn(v int) bool { return v > 0 },
        name: "locked",
        on_tick: fn() {},
        count: 1,
    }
    bus.count = 2
}
"#;
    let ast = parse_file("inline.hy", src).expect("must parse");
    let res = check_file(&ast);
    assert_eq!(
        res.verdict,
        Verdict::Reject,
        "mutating field `count` after `on_tick fn()` on a const struct must be rejected, got: {:?}",
        res.diagnostics
    );
}
