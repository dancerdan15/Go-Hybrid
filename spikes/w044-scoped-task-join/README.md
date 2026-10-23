# W-044 (#471) — Scoped Task Regions (`join_scope`): Zero-Copy Borrowed Read-Sharing Across Tasks

Executable Wildcard Spike evaluating **Issue #471** (@Anekenonso): lexically-bounded `join_scope` blocks that couple a compile-time `sync.WaitGroup` join barrier with a transitive `A3-Read` (`ScopedBorrow`) freeze over borrowed reference roots (`map`, `slice`, `struct`), enabling zero-copy `const &T` read-sharing across concurrent Go goroutines with `0 allocs/op` and `0` `managed` blocks.

## Quantitative Evaluation & Falsification Matrix (`HEAD` `7d63d5fc`)

| Milestone / Falsifier (#471) | Pre-Registered Gate | Measured Result | Verdict |
|------------------------------|---------------------|-----------------|---------|
| **M1.1: `accept-scoped-parallel-read.hy`** | 16–32 tasks reading shared `map`/`slice` in `join_scope` (`0` diagnostics) | **Accepted (`0` diagnostics, `32` workers, `0 allocs/op`)** | **SURVIVES** |
| **M1.2: `reject-write-during-scope.hy`** | Direct or alias write (`data["k"] = 1`, `alias_data["x"]`) inside `join_scope` | **100% rejected (`HYB-E3102` / `HYB-E4401`)** via transitive `storage_component` | **PROVEN** |
| **M1.3: `reject-move-during-scope.hy`** | `move data` while `ScopedBorrow` (`A3-Read`) is active | **100% rejected (`HYB-E3003` / `HYB-E4401`)** | **PROVEN** |
| **M1.4: `reject-escape-beyond-scope.hy`** | Sending `&data` to outer channel, returning `&data`, or detached `task` | **100% rejected (`HYB-E3104` / `HYB-E4402`)** | **PROVEN** |
| **M2 Falsifier: Unwind Bypass of `__hy_wg.Wait()`** | Parent/child `panic` bypassing bare `__hy_wg.Wait()` before siblings finish | **FALSIFIER FIRES on bare `Wait()` (`HYB-E4403`); FIXED via `defer __hy_wg.Wait()`** | **RESOLVED** |

## Live `HEAD` (`7d63d5fc`) Grounding & Architecture

1. **Transitive `A3-Read` Freeze (`scoped_join_region.py`):** Extends `loans.rs::a3_paths_conflict` and `freeze.rs::storage_component` across `aliases` and `may_share` peers (`126` `.hy` corpus files audited on `HEAD`: `71` task-spawn files, `5` A3 loan files).
2. **Deferred Barrier Lowering (`hy-emit`):** Emits `defer __hy_wg.Wait()` immediately after `var __hy_wg sync.WaitGroup` so stack-frame unwinding cannot complete while any spawned child goroutine still reads borrowed parent storage.

## Reproduce

```bash
python spikes/w044-scoped-task-join/scoped_join_region.py --check
python spikes/w044-scoped-task-join/scoped_join_region.py --json
```
