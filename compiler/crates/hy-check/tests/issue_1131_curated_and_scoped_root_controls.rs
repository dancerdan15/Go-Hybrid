//! Issue #1131 — positive controls ensuring curated package roots (`hex.encode`,
//! `fmt.println`, `strings.to_upper`, `io.eof`) and scoped/file-level bindings
//! (locals, params, receivers, loop/match/closure bindings, `struct`/`enum`/`const`)
//! pass `hyb check` with `Verdict::Accept` and 0 diagnostics.

use hy_check::{check_file, Verdict};
use hy_syntax::parse_file;

fn assert_accepts_clean(src: &str) {
    let ast = parse_file("inline.hy", src)
        .unwrap_or_else(|diags| panic!("must parse (parser contract): {:?}", diags));
    let r = check_file(&ast);
    assert_eq!(
        r.verdict,
        Verdict::Accept,
        "expected Accept with 0 diagnostics for:\n{src}\ngot: {:?}",
        r.diagnostics
    );
    assert!(
        r.diagnostics.is_empty(),
        "expected 0 diagnostics, got: {:?}",
        r.diagnostics
    );
}

#[test]
fn curated_and_builtin_package_roots_accept_without_e1001() {
    assert_accepts_clean(
        "import \"strings\"\nfn main() {\n    s := hex.encode(\"ab\")\n    u := strings.to_upper(s)\n    fmt.println(u, io.eof)\n}\n",
    );
}

#[test]
fn local_param_receiver_and_nested_struct_field_roots_accept() {
    assert_accepts_clean(
        "struct inner {\n    d int\n}\nstruct req {\n    url string\n    b inner\n}\nfn (w req) get_url() string {\n    return w.url\n}\nfn read_param(widgets req) int {\n    return widgets.b.d\n}\nfn main() {\n    var widgets req\n    widgets.url = \"ok\"\n    _ = widgets.get_url()\n    _ = read_param(widgets)\n}\n",
    );
}

#[test]
fn loop_match_closure_and_file_level_decl_roots_accept() {
    assert_accepts_clean(
        "struct item {\n    val int\n}\nenum color {\n    red\n    blue\n}\nconst default_item = item{val: 7}\nfn wrap(x item) option[item] {\n    return some(x)\n}\nfn main() {\n    xs := []item{default_item}\n    for it in xs {\n        fmt.println(it.val)\n    }\n    opt := wrap(default_item)\n    match opt {\n        some(v) => {\n            fmt.println(v.val)\n        }\n        none => {}\n    }\n    f := fn(w item) int {\n        return w.val\n    }\n    fmt.println(f(default_item), default_item.val, color.red)\n}\n",
    );
}
