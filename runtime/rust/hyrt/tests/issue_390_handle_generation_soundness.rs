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
fn release_increments_slot_generation_and_rejects_stale_handle() {
    let _g = TEST_LOCK.lock().unwrap();
    let base_live = live_count();

    let mut h1 = 0u64;
    assert_eq!(hy_json_new_object(&mut h1), 0);
    assert_eq!(live_count(), base_live + 1);

    // Release h1: its slot enters the freelist with an incremented generation.
    assert_eq!(hy_json_release(h1), 0);
    assert_eq!(live_count(), base_live);

    // Next allocation reuses the same slot index but must carry a new generation.
    let h2 = parse_json(r#"{"v":123}"#);
    assert_eq!(h2 & 0xFFFF_FFFF, h1 & 0xFFFF_FFFF, "freelist should reuse slot index");
    assert_ne!(h2 >> 32, h1 >> 32, "reused slot must advance generation");
    assert_ne!(h2, h1);

    // Stale h1 release (e.g. Go runtime.AddCleanup firing late) must be a no-op.
    assert_eq!(hy_json_release(h1), 0);
    assert_eq!(live_count(), base_live + 1, "stale release must not drop new occupant");

    // Stale h1 reads must fail with ERR_TABLE (1), while h2 succeeds.
    let key = "v";
    let mut out_val = 0i64;
    assert_eq!(hy_json_get_int(h1, key.as_ptr() as u64, key.len() as u64, &mut out_val), 1);
    assert_eq!(hy_json_get_int(h2, key.as_ptr() as u64, key.len() as u64, &mut out_val), 0);
    assert_eq!(out_val, 123);

    assert_eq!(hy_json_release(h2), 0);
    assert_eq!(live_count(), base_live);
}

#[test]
fn set_move_increments_child_slot_generation_and_protects_reused_slot() {
    let _g = TEST_LOCK.lock().unwrap();
    let base_live = live_count();

    let mut parent = 0u64;
    assert_eq!(hy_json_new_object(&mut parent), 0);
    let child = parse_json(r#"{"n":42}"#);

    let key = "child";
    assert_eq!(hy_json_set(parent, key.as_ptr() as u64, key.len() as u64, child), 0);
    assert_eq!(live_count(), base_live + 1);

    // Allocate a new value that reuses `child`'s freed slot.
    let fresh = parse_json(r#"{"n":99}"#);
    assert_eq!(fresh & 0xFFFF_FFFF, child & 0xFFFF_FFFF);
    assert_ne!(fresh, child);

    // Simulated late cleanup on moved `child` handle must not free `fresh`.
    assert_eq!(hy_json_release(child), 0);
    let nkey = "n";
    let mut n = 0i64;
    assert_eq!(hy_json_get_int(fresh, nkey.as_ptr() as u64, nkey.len() as u64, &mut n), 0);
    assert_eq!(n, 99);

    assert_eq!(hy_json_release(fresh), 0);
    assert_eq!(hy_json_release(parent), 0);
    assert_eq!(live_count(), base_live);
}

#[test]
fn unpack_rejects_zero_index_and_zero_generation_handles() {
    let _g = TEST_LOCK.lock().unwrap();
    let invalid_handles = [
        0u64,
        u64::MAX,
        1u64 << 32,          // raw_idx == 0, gen == 1 (previously underflowed raw_idx - 1)
        (7u64 << 32) | 0u64, // raw_idx == 0, gen == 7
        1u64,                // raw_idx == 1, gen == 0
        42u64,               // raw_idx == 42, gen == 0
    ];
    let key = "k";
    for bad in invalid_handles {
        let mut out_int = 0i64;
        let mut out_len = 0u64;
        assert_eq!(hy_json_get_int(bad, key.as_ptr() as u64, key.len() as u64, &mut out_int), 1);
        assert_eq!(hy_json_array_len(bad, &mut out_len), 1);
        assert_eq!(hy_json_value_text(bad, ptr::null_mut(), 0, &mut out_len), 1);
        assert_eq!(hy_json_release(bad), 0);
    }
}
