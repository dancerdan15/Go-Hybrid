//! Regression tests for #727: `TypeExpr::Fn` and `FnDecl` result parsing must
//! not cross a line break to swallow the following statement or declaration.

use hy_check::{check_file, Verdict};
use hy_syntax::ast::{Decl, Expr, LocalDecl, LocalKind, Stmt, TypeExpr};
use hy_syntax::parse_file;

#[test]
fn var_fn_type_does_not_swallow_next_line_assign_or_short_decl_issue_727() {
    let src = r#"
fn main() {
    var total = 0
    var cb fn() int
    total = 7
    var op fn(int)
    next_val := total + 1
    op(next_val)
    fmt.println(cb())
}
"#;
    let ast = parse_file("inline.hy", src).expect("must parse");
    let main_fn = ast
        .decls
        .iter()
        .find_map(|d| match d {
            Decl::Fn(f) if f.name == "main" => Some(f),
            _ => None,
        })
        .expect("main fn");
    let stmts = &main_fn.body.as_ref().expect("body").stmts;
    assert_eq!(stmts.len(), 7, "expected 7 statements, got {:?}", stmts);
    assert!(matches!(
        &stmts[1],
        Stmt::Local(LocalDecl {
            kind: LocalKind::Var,
            names,
            ty: Some(TypeExpr::Fn { results, .. }),
            values,
            ..
        }) if names == &["cb"] && results.len() == 1 && values.is_empty()
    ));
    assert!(matches!(&stmts[2], Stmt::Assign { targets, .. } if targets.len() == 1));
    assert!(matches!(
        &stmts[3],
        Stmt::Local(LocalDecl {
            kind: LocalKind::Var,
            names,
            ty: Some(TypeExpr::Fn { results, .. }),
            values,
            ..
        }) if names == &["op"] && results.is_empty() && values.is_empty()
    ));

    let res = check_file(&ast);
    assert_eq!(res.verdict, Verdict::Accept, "diags: {:?}", res.diagnostics);
}

#[test]
fn next_line_statement_after_fn_type_is_checked_for_const_reassignment_issue_727() {
    // Before #727, `total = 7` after `var cb fn() int` was swallowed into `cb`'s
    // return type and initializer, hiding the illegal reassignment of `const total`.
    let src = r#"
fn main() {
    const total = 0
    var cb fn() int
    total = 7
}
"#;
    let ast = parse_file("inline.hy", src).expect("must parse");
    let res = check_file(&ast);
    assert_eq!(
        res.verdict,
        Verdict::Reject,
        "const reassignment after var fn() int must be rejected, got: {:?}",
        res.diagnostics
    );
}

#[test]
fn void_fn_type_does_not_swallow_next_line_paren_expr_issue_727() {
    let src = r#"
fn main() {
    var total = 3
    var void_cb fn()
    (total)
    fmt.println(total)
}
"#;
    let ast = parse_file("inline.hy", src).expect("must parse");
    let main_fn = ast
        .decls
        .iter()
        .find_map(|d| match d {
            Decl::Fn(f) if f.name == "main" => Some(f),
            _ => None,
        })
        .expect("main fn");
    let stmts = &main_fn.body.as_ref().expect("body").stmts;
    assert_eq!(stmts.len(), 4);
    assert!(matches!(
        &stmts[1],
        Stmt::Local(LocalDecl {
            ty: Some(TypeExpr::Fn { results, .. }),
            ..
        }) if results.is_empty()
    ));
    assert!(matches!(
        &stmts[2],
        Stmt::Expr {
            expr: Expr::Paren { .. },
            ..
        }
    ));
}

#[test]
fn type_alias_and_parenthesized_multi_return_fn_types_issue_727() {
    let src = r#"
type handler_fn = fn(int)
type pair_fn = fn(int) (int, bool)

fn apply_pair(f fn(int) (code int, ok bool), v int) int {
    c, ok := f(v)
    if ok {
        return c
    }
    return 0
}

fn main() {
    var h handler_fn
    var p pair_fn
    var same_line fn(int, int) int int
    var higher fn(int) fn(int) bool
    x := 42
    h(x)
    a, b := p(x)
    q, r := same_line(a, x)
    inner_fn := higher(q)
    ok2 := inner_fn(r)
    if b && ok2 {
        fmt.println(apply_pair(p, x))
    }
}
"#;
    let ast = parse_file("inline.hy", src).expect("must parse");
    assert_eq!(ast.decls.len(), 4, "expected 4 top-level decls, got {:?}", ast.decls);
    assert!(matches!(
        &ast.decls[0],
        Decl::TypeAlias(ta) if ta.name == "handler_fn"
            && matches!(&ta.ty, TypeExpr::Fn { results, .. } if results.is_empty())
    ));
    assert!(matches!(
        &ast.decls[1],
        Decl::TypeAlias(ta) if ta.name == "pair_fn"
            && matches!(&ta.ty, TypeExpr::Fn { results, .. } if results.len() == 2)
    ));
    let main_fn = ast
        .decls
        .iter()
        .find_map(|d| match d {
            Decl::Fn(f) if f.name == "main" => Some(f),
            _ => None,
        })
        .expect("main fn");
    let stmts = &main_fn.body.as_ref().expect("body").stmts;
    assert!(matches!(
        &stmts[3],
        Stmt::Local(LocalDecl {
            names,
            ty: Some(TypeExpr::Fn { results, .. }),
            ..
        }) if names == &["higher"]
            && results.len() == 1
            && matches!(&results[0], TypeExpr::Fn { results: inner_res, .. } if inner_res.len() == 1)
    ));
    let res = check_file(&ast);
    assert_eq!(res.verdict, Verdict::Accept, "diags: {:?}", res.diagnostics);
}
