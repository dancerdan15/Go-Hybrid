#[allow(dead_code)]
#[path = "../src/lib.rs"]
mod hyrt;

use hyrt::*;
use std::ptr;
use std::sync::Mutex;

static TEST_LOCK: Mutex<()> = Mutex::new(());

fn live_count() -> u64 {
    let mut n = u64::MAX;
    assert_eq!(hy_json_live_count(&mut n), 0);
    n
}

fn parse_json(src: &str) -> u64 {
    let mut h = 0u64;
    assert_eq!(hy_json_parse(src.as_ptr() as u64, src.len() as u64, &mut h), 0);
    assert_ne!(h, 0);
    h
}

#[test]
fn json_set_on_invalid_or_non_object_target_preserves_value_handle() {
    let _g = TEST_LOCK.lock().unwrap();
    let base_live = live_count();

    let val = parse_json(r#"{"keep":7}"#);
    let mut arr = 0u64;
    assert_eq!(hy_json_new_array(&mut arr), 0);
    let key = "item";

    // 1. Setting a key on a JSON array must fail with ERR_JSON (2) and preserve `val`.
    assert_eq!(hy_json_set(arr, key.as_ptr() as u64, key.len() as u64, val), 2);
    let kkeep = "keep";
    let mut got = 0i64;
    assert_eq!(hy_json_get_int(val, kkeep.as_ptr() as u64, kkeep.len() as u64, &mut got), 0);
    assert_eq!(got, 7);

    // 2. Setting onto an unallocated/stale target handle must fail with ERR_TABLE (1) and preserve `val`.
    let bogus_target = (99u64 << 32) | 999u64;
    assert_eq!(hy_json_set(bogus_target, key.as_ptr() as u64, key.len() as u64, val), 1);
    assert_eq!(hy_json_get_int(val, kkeep.as_ptr() as u64, kkeep.len() as u64, &mut got), 0);
    assert_eq!(got, 7);

    // 3. Self-insertion (`v == value`) must fail with ERR_TABLE (1) and preserve `val`.
    assert_eq!(hy_json_set(val, key.as_ptr() as u64, key.len() as u64, val), 1);
    assert_eq!(hy_json_get_int(val, kkeep.as_ptr() as u64, kkeep.len() as u64, &mut got), 0);
    assert_eq!(got, 7);

    // 4. Positive control: setting onto a valid JSON object succeeds (0) and consumes `val`.
    let mut obj = 0u64;
    assert_eq!(hy_json_new_object(&mut obj), 0);
    assert_eq!(hy_json_set(obj, key.as_ptr() as u64, key.len() as u64, val), 0);
    assert_eq!(hy_json_get_int(val, kkeep.as_ptr() as u64, kkeep.len() as u64, &mut got), 1);

    assert_eq!(hy_json_release(arr), 0);
    assert_eq!(hy_json_release(obj), 0);
    assert_eq!(live_count(), base_live);
}

#[test]
fn value_text_and_to_string_pretty_propagate_table_errors() {
    let _g = TEST_LOCK.lock().unwrap();
    let base_live = live_count();

    let h = parse_json(r#"{"a":1}"#);
    assert_eq!(hy_json_release(h), 0);

    // Stale handle must return ERR_TABLE (1), never 0 or TRY_AGAIN (3).
    let mut need = 0u64;
    assert_eq!(hy_json_value_text(h, ptr::null_mut(), 0, &mut need), 1);
    assert_eq!(hy_json_to_string_pretty(h, ptr::null_mut(), 0, &mut need), 1);

    // Positive control: live handle reports TRY_AGAIN (3) on zero capacity, then 0 with buffer.
    let live = parse_json(r#"{"a":1}"#);
    assert_eq!(hy_json_value_text(live, ptr::null_mut(), 0, &mut need), 3);
    assert_eq!(need, 7);
    let mut buf = vec![0u8; need as usize];
    assert_eq!(hy_json_value_text(live, buf.as_mut_ptr(), buf.len(), &mut need), 0);
    assert_eq!(&buf, b"{\"a\":1}");

    let mut pretty_need = 0u64;
    assert_eq!(hy_json_to_string_pretty(live, ptr::null_mut(), 0, &mut pretty_need), 3);
    let mut pbuf = vec![0u8; pretty_need as usize];
    assert_eq!(hy_json_to_string_pretty(live, pbuf.as_mut_ptr(), pbuf.len(), &mut pretty_need), 0);
    assert!(std::str::from_utf8(&pbuf).unwrap().contains("\"a\": 1"));

    assert_eq!(hy_json_release(live), 0);
    assert_eq!(live_count(), base_live);
}
