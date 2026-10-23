//! Regression tests for #1115 (part 2): curated in-place mutators (`HYB-E3006`)
//! and unproven non-scalar `sync/atomic` package calls (`HYB-E3005`, Row 11).

use hy_check::{check_file, CheckResult, Verdict};
use hy_diag::Severity;
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let ast = parse_file("test.hy", src).expect("test snippet must parse");
    check_file(&ast)
}

fn check_codes(src: &str) -> Vec<String> {
    check(src)
        .diagnostics
        .into_iter()
        .filter(|d| d.severity == Severity::Error)
        .map(|d| d.code)
        .collect()
}

#[test]
fn curated_in_place_mutators_reject_const_targets_with_hyb_e3006() {
    let cases = [
        r#"import "encoding/hex"
const dst []byte = [0, 0, 0, 0]
fn main() {
    _, _ = hex.decode(dst, [102, 111, 111, 98])
}"#,
        r#"import "encoding/json"
struct cfg { port int }
const global_cfg = cfg{port: 8080}
fn main() {
    _ = json.unmarshal([123, 125], &global_cfg)
}"#,
        r#"import "errors"
struct target_err { code int }
const frozen_target = target_err{code: 1}
fn main() {
    err := errors.new("boom")
    _ = errors.as(err, &frozen_target)
}"#,
        r#"struct slot64 { v int64 }
const shared_slot = slot64{v: 0}
fn main() {
    atomic.store_int64(&shared_slot.v, 42)
}"#,
        r#"struct slot64 { v int64 }
const shared_slot = slot64{v: 0}
fn main() {
    _ = atomic.add_int64(&shared_slot.v, 1)
}"#,
        r#"struct slot64 { v int64 }
const shared_slot = slot64{v: 0}
fn main() {
    _ = atomic.swap_int64(&shared_slot.v, 2)
}"#,
        r#"struct slot64 { v int64 }
const shared_slot = slot64{v: 0}
fn main() {
    _ = atomic.compare_and_swap_int64(&shared_slot.v, 0, 1)
}"#,
        r#"struct box_int { v int }
const fixed = box_int{v: 1}
fn main() {
    _, _ = fmt.sscanf("42", "%d", &fixed.v)
}"#,
        r#"import "rust/flate2"
const frozen_w = flate2.new_writer()
fn main() {
    _ = flate2.write(frozen_w, [1, 2])
}"#,
    ];
    for src in cases {
        let codes = check_codes(src);
        assert!(
            codes.iter().any(|c| c == "HYB-E3006"),
            "expected HYB-E3006 for:\n{src}\ngot: {codes:?}"
        );
    }
}

#[test]
fn unproven_atomic_value_and_pointer_reject_at_spawn_and_send_hyb_e3005() {
    let src_val = r#"import "sync/atomic"
fn leak(v int) {}
fn main() {
    v := atomic.value(1)
    task leak(v)
}
"#;
    assert!(check_codes(src_val).iter().any(|c| c == "HYB-E3005"));

    let src_ptr = r#"import "sync/atomic"
fn leak_ptr(p atomic.pointer[int]) {}
fn main() {
    ch := make(chan int, 1)
    slot := 0
    p := atomic.pointer(&slot)
    ch <- p
    var gp atomic.pointer[int]
    task leak_ptr(gp)
}
"#;
    assert_eq!(
        check_codes(src_ptr)
            .iter()
            .filter(|c| *c == "HYB-E3005")
            .count(),
        2
    );
}

#[test]
fn positive_control_mutable_targets_and_scalar_atomic_load_accept() {
    let r = check(
        r#"import "encoding/hex"
import "encoding/json"
import "sync/atomic"
struct cfg { port int64 }
fn main() {
    dst := [0, 0]
    _, _ = hex.decode(dst, [48, 49])
    local_cfg := cfg{port: 80}
    _ = json.unmarshal([123, 125], &local_cfg)
    atomic.store_int64(&local_cfg.port, 90)
    ch := make(chan int64, 1)
    ch <- atomic.load_int64(&local_cfg.port)
}
"#,
    );
    assert_eq!(r.verdict, Verdict::Accept, "got {:?}", r.diagnostics);
}
