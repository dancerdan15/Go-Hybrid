use hy_syntax::{parse_file, Decl, Expr, Stmt};

fn parse_rhs_expr(rhs_src: &str) -> Expr {
    let src = format!("fn main() {{\n  x := {}\n}}\n", rhs_src);
    let file = parse_file("test.hy", &src).expect("must parse");
    match &file.decls[0] {
        Decl::Fn(f) => match &f.body.as_ref().unwrap().stmts[0] {
            Stmt::Local(l) => l.values[0].clone(),
            other => panic!("expected Local stmt, got {:?}", other),
        },
        other => panic!("expected Fn decl, got {:?}", other),
    }
}

#[test]
fn root_ident_unwraps_move_and_access_chains() {
    assert_eq!(parse_rhs_expr("move dst").root_ident(), Some("dst"));
    assert_eq!(parse_rhs_expr("(move dst)[0]").root_ident(), Some("dst"));
    assert_eq!(parse_rhs_expr("(move doc).f").root_ident(), Some("doc"));
    assert_eq!(parse_rhs_expr("(move s)[0:1]").root_ident(), Some("s"));
    // Positive control: binary expression has no root identifier.
    assert_eq!(parse_rhs_expr("1 + 2").root_ident(), None);
}

#[test]
fn as_array_lit_unwraps_paren_and_move_wrappers() {
    let paren_arr = parse_rhs_expr("([2]int{1, 2})");
    let (_, elem_ty, arr_ty) = paren_arr
        .as_array_lit()
        .expect("parenthesized array literal must resolve via as_array_lit");
    assert_eq!(elem_ty.render(), "int");
    assert_eq!(arr_ty.render(), "[2]int");

    let move_arr = parse_rhs_expr("move ([2]int{1, 2})");
    let (_, elem_ty2, arr_ty2) = move_arr
        .as_array_lit()
        .expect("move-wrapped array literal must resolve via as_array_lit");
    assert_eq!(elem_ty2.render(), "int");
    assert_eq!(arr_ty2.render(), "[2]int");

    // Positive controls: slice literal and binary expression are not array literals.
    assert!(parse_rhs_expr("[]int{1, 2}").as_array_lit().is_none());
    assert!(parse_rhs_expr("1 + 2").as_array_lit().is_none());
}
