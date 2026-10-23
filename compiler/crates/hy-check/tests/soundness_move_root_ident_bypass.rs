use hy_check::{check_file, CheckResult, Verdict};
use hy_syntax::parse_file;

fn check(src: &str) -> CheckResult {
    let file = parse_file("test.hy", src).expect("test source must parse");
    check_file(&file)
}

#[test]
fn move_wrapped_copy_in_helper_records_may_share_and_rejects_a1b_e3003() {
    let res = check(
        "fn relay_copy(dst []map[string]int, src []map[string]int) {\n\
           tmp := make([]map[string]int, 1)\n\
           copy(move tmp, src)\n\
           copy(dst, tmp)\n\
         }\n\
         fn worker(xs []map[string]int) {\n\
           xs[0][\"k\"] = 10\n\
         }\n\
         fn main() {\n\
           dst := make([]map[string]int, 1)\n\
           src := []map[string]int{map[string]int{\"k\": 1}}\n\
           relay_copy(dst, src)\n\
           task worker(move dst)\n\
           src[0][\"k\"] = 2\n\
         }\n",
    );
    assert_eq!(res.verdict, Verdict::Reject, "{:?}", res.diagnostics);
    assert!(
        res.diagnostics.iter().any(|d| d.code == "HYB-E3003"),
        "expected HYB-E3003 (A1b), got: {:?}",
        res.diagnostics
    );
}

#[test]
fn move_wrapped_copy_of_const_slice_rejects_o14_e3006() {
    let res = check(
        "const const_slice = []map[string]int{map[string]int{\"k\": 1}}\n\
         fn relay_copy(dst []map[string]int, src []map[string]int) {\n\
           tmp := make([]map[string]int, 1)\n\
           copy(move tmp, src)\n\
           copy(dst, tmp)\n\
         }\n\
         fn main() {\n\
           dst := make([]map[string]int, 1)\n\
           relay_copy(dst, const_slice)\n\
           dst[0][\"k\"] = 99\n\
           f := fn() {\n\
             local_dst := make([]map[string]int, 1)\n\
             copy(move local_dst, const_slice)\n\
             local_dst[0][\"k\"] = 88\n\
           }\n\
           f()\n\
         }\n",
    );
    assert_eq!(res.verdict, Verdict::Reject, "{:?}", res.diagnostics);
    let e3006_count = res
        .diagnostics
        .iter()
        .filter(|d| d.code == "HYB-E3006")
        .count();
    assert_eq!(
        e3006_count, 2,
        "expected 2 HYB-E3006 diagnostics, got: {:?}",
        res.diagnostics
    );
}

#[test]
fn move_wrapped_receiver_rejects_a2_e3101_and_a3_e3102() {
    // A2 (HYB-E3101): `(move doc).set(...)` while borrow loan from `doc.array(...)` is live.
    let a2 = check(
        "import \"rust/serde_json\"\n\
         fn main() {\n\
           doc := serde_json.parse(`{}`)?\n\
           for u in doc.array(\"users\")? {\n\
             if u.int(\"logins\")? == 0 {\n\
               (move doc).set(\"pruned\", true)?\n\
             }\n\
           }\n\
         }\n",
    );
    assert_eq!(a2.verdict, Verdict::Reject, "{:?}", a2.diagnostics);
    assert!(
        a2.diagnostics.iter().any(|d| d.code == "HYB-E3101"),
        "expected HYB-E3101 (A2), got: {:?}",
        a2.diagnostics
    );

    // A3 (HYB-E3102): `(move h).put(...)` while map-slot loan is live.
    let a3 = check(
        "import \"fmt\"\n\
         struct holder { m map[string]int }\n\
         fn (h *holder) put(k string, v int) {\n\
           h.m[k] = v\n\
         }\n\
         fn main() {\n\
           h := holder{m: map[string]int{\"a\": 1}}\n\
           slot := &h.m[\"a\"]\n\
           (move h).put(\"b\", 2)\n\
           fmt.println(slot)\n\
         }\n",
    );
    assert_eq!(a3.verdict, Verdict::Reject, "{:?}", a3.diagnostics);
    assert!(
        a3.diagnostics.iter().any(|d| d.code == "HYB-E3102"),
        "expected HYB-E3102 (A3), got: {:?}",
        a3.diagnostics
    );
}

#[test]
fn positive_control_unshared_copy_accepts() {
    let ok = check(
        "import \"fmt\"\n\
         fn main() {\n\
           dst := make([]int, 2)\n\
           src := []int{10, 20}\n\
           n := copy(dst, src)\n\
           fmt.println(n, dst[0], src[0])\n\
         }\n",
    );
    assert_eq!(ok.verdict, Verdict::Accept, "{:?}", ok.diagnostics);
}
