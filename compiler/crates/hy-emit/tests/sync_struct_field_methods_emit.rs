//! Integration tests for `sync.rw_mutex`, `sync.wait_group`, and `sync.once` Go emission.

use hy_emit::emit_go;
use hy_syntax::parse_file;

fn emit(src: &str) -> String {
    let file = parse_file("t.hy", src).expect("parse");
    emit_go(&file).expect("emit")
}

#[test]
fn sync_struct_fields_and_locals_emit_capitalized_go_types_and_methods() {
    let src = r#"
struct pool_guard {
    mu   sync.rw_mutex
    alt  sync.rwmutex
    wg   sync.wait_group
    once sync.once
}

fn main() {
    p := pool_guard{}
    p.mu.lock()
    p.mu.unlock()
    _ = p.mu.try_lock()
    p.mu.rlock()
    p.mu.runlock()
    _ = p.mu.try_rlock()
    p.alt.rlock()
    p.alt.runlock()
    p.wg.add(1)
    p.wg.done()
    p.wg.wait()
    p.once.do(fn() {
        fmt.println("init")
    })
}
"#;
    let go = emit(src);
    assert!(go.contains("\"sync\""), "{go}");
    assert!(go.contains("mu   sync.RWMutex") && go.contains("alt  sync.RWMutex"), "{go}");
    assert!(!go.contains("sync.RwMutex") && !go.contains("sync.Rwmutex"), "{go}");
    assert!(go.contains("wg   sync.WaitGroup") && go.contains("once sync.Once"), "{go}");
    for call in [
        "p.mu.Lock()",
        "p.mu.Unlock()",
        "p.mu.TryLock()",
        "p.mu.RLock()",
        "p.mu.RUnlock()",
        "p.mu.TryRLock()",
        "p.alt.RLock()",
        "p.alt.RUnlock()",
        "p.wg.Add(1)",
        "p.wg.Done()",
        "p.wg.Wait()",
        "p.once.Do(func()",
    ] {
        assert!(go.contains(call), "missing `{call}` in:\n{go}");
    }
}

#[test]
fn sync_local_vars_and_params_emit_exact_initialism_methods() {
    let src = r#"
fn exercise_sync(rw *sync.rw_mutex, wg *sync.wait_group, once *sync.once) {
    var local_rw sync.rw_mutex
    local_rw.rlock()
    local_rw.runlock()
    _ = local_rw.try_rlock()
    rw.rlock()
    rw.runlock()
    wg.add(1)
    wg.done()
    wg.wait()
    once.do(fn() {})
}
"#;
    let go = emit(src);
    assert!(
        go.contains("func exercise_sync(rw *sync.RWMutex, wg *sync.WaitGroup, once *sync.Once)"),
        "{go}"
    );
    assert!(go.contains("var local_rw sync.RWMutex"), "{go}");
    for call in [
        "local_rw.RLock()",
        "local_rw.RUnlock()",
        "local_rw.TryRLock()",
        "rw.RLock()",
        "rw.RUnlock()",
        "wg.Add(1)",
        "wg.Done()",
        "wg.Wait()",
        "once.Do(func()",
    ] {
        assert!(go.contains(call), "missing `{call}` in:\n{go}");
    }
    assert!(!go.contains(".Rlock(") && !go.contains(".Runlock(") && !go.contains(".TryRlock("), "{go}");
}
