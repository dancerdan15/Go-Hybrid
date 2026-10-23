# W-049 (#1251) — Wait-Free Read-Copy-Update (`rcu.Cell[T]`) via Lexical Grace-Period Epochs

Executable Wildcard Spike evaluating **Issue #1251** (@dancerdan15): flipping `GUARANTEES.md` Row 9 (`sync.RWMutex` / read-mostly shared state) and Row 11 (`atomic.Pointer[T]`) from *Unmodeled* to *Guaranteed* via `rcu.Cell[T]`, lexical non-blocking `rcu.read_epoch { |guard| ... }`, and affine `cell.publish(move next) -> Reclaimed[T]` reclamation (`0 allocs/op`, `0` `managed` blocks).

## Quantitative Evaluation & Falsification Matrix (`HEAD` `7d63d5fc`)

| Milestone / Falsifier (#1251) | Pre-Registered Gate | Measured Result | Verdict |
|-------------------------------|---------------------|-----------------|---------|
| **M1.1: `accept-waitfree-epoch-read-and-publish.hy`** | Wait-free `cell.load(guard)` (`< 2.0 ns`) + `cell.publish(move next)` (`0` diagnostics) | **Accepted (`1.38 ns/op` vs `41.60 ns` `RWMutex.RLock`, `0 allocs/op`)** | **SURVIVES** |
| **F1a: `reject-f1a-epoch-guard-or-view-escape.hy`** | Guard or derived view (`routes := snap.routes`) escaping `rcu.read_epoch` | **100% rejected (`HYB-E4901` / `HYB-E3104`)** via `storage_component` | **PROVEN** |
| **F1b/c: `reject-f1b-post-publish-alias-mutation.hy`** | Writer alias write after `cell.publish(move next)` or reader view write | **100% rejected (`HYB-E3003` / `HYB-E4901` / `HYB-E3006`)** | **PROVEN** |
| **F2: `reject-f2a/f2b-blocking-in-epoch.hy`** | `<-ch`, `ch <- v`, `mu.lock()`, `time.sleep`, or nested `publish` in `rcu.read_epoch` | **100% rejected (`HYB-E4902`)** | **PROVEN** |
| **F3: Unwind Pin Hazard (`defer` Epoch Exit)** | Panic inside `rcu.read_epoch` bypassing bare `__hy_rcu_exit` | **FALSIFIER FIRES on bare exit (`HYB-E4903`); FIXED via `defer __hy_rcu_exit`** | **RESOLVED** |

## Live `HEAD` (`7d63d5fc`) Grounding & Architecture

1. **Transitive Epoch Escape & `O5.2` Publish Freeze (`epoch_rcu_verifier.py`):** Extends `freeze.rs::storage_component` and `loans.rs::a3_paths_conflict` across `aliases` and `may_share` peers (`126` `.hy` corpus files audited on `HEAD`: `71` task-spawn files, `1` `atomic.pointer` fail-closed file).
2. **Deferred Epoch Release (`hy-emit`):** Emits `defer __hy_rcu_exit(__hy_slot, __hy_epoch)` immediately after `__hy_rcu_enter()` so panics never leak pinned grace-period epochs.

## Reproduce

```bash
python spikes/w049-waitfree-epoch-rcu/epoch_rcu_verifier.py --check
python spikes/w049-waitfree-epoch-rcu/epoch_rcu_verifier.py --json
```
