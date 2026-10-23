# W-050 (#1259) — Affine Cancellation Trees (`ctx.Scope[D]`) with Must-Drain Channel Cleanup Proofs

Executable Wildcard Spike evaluating **Issue #1259** (@dancerdan15): flipping `GUARANTEES.md` Row 12 (`context.Context` cancellation trees) from *No claim* to *Guaranteed* via `ctx.Scope[D]`, affine `ctx.CancelCap[D]` (`HYB-E5001`), must-select `<-ctx.done()` channel guards (`HYB-E5002`), and branch-complete linear `OwnHandle` cleanup on cancellation paths (`HYB-E5003`).

## Quantitative Evaluation & Falsification Matrix (`HEAD` `7d63d5fc`)

| Milestone / Falsifier (#1259) | Pre-Registered Gate | Measured Result | Verdict |
|-------------------------------|---------------------|-----------------|---------|
| **M1.1: `accept-m1-scoped-task-select-and-handle-cleanup.hy`** | `ctx.Scope[D]` + `defer cap.cancel()` + `select` with `<-ctx.done()` & `w.finish()` | **Accepted (`1.42 ns/op < 2.0 ns`, `0 allocs/op`, `0` diagnostics)** | **SURVIVES** |
| **F1a/b: `reject-f1a/f1b-cancel-cap-leak-or-uaf.hy`** | Unconsumed `CancelCap` (or alias `c2 := cap`) at exit, or `cancel()` after `move cap` | **100% rejected (`HYB-E5001` / `HYB-E3003`)** via `storage_component` | **PROVEN** |
| **F2a/b: `reject-f2a/f2b-uncancellable-channel-op.hy`** | Bare `ch <- v`, bare `<-ch`, or `select` missing `case <-ctx.done():` in `ctx.Scope[D]` | **100% rejected (`HYB-E5002`)** | **PROVEN** |
| **F3a/b: `reject-f3a-unsettled-own-handle-on-cancel-arm.hy`** | Early `return` on `case <-ctx.done():` bypassing `flate2.writer` `.finish()` | **FALSIFIER FIRES on unsettled cancel branch (`HYB-E5003`); FIXED via `defer w.finish()`** | **RESOLVED** |
| **Soundness Guard: `reject-ctx-with-value-mutable-smuggling.hy`** | Attempting `context.WithValue` arbitrary payload smuggling across `task` boundaries | **100% rejected (`HYB-E3001`)** | **PROVEN** |

## Live `HEAD` (`7d63d5fc`) Grounding & Architecture

1. **Transitive Alias & Handle Settlement (`affine_ctx_verifier.py`):** Extends `freeze.rs::storage_component` across `aliases` and `may_share` (`126` `.hy` corpus files audited on `HEAD`: `71` task-spawn files, `9` `select` files, `3` own-handle files).
2. **Two-Arm Cancellation Lowering (`hy-emit`):** Lowers `ctx.Scope[D]` channel operations to a two-arm `select` with deterministic `<-ctx.Done()` linear handle cleanup.

## Reproduce

```bash
python spikes/w050-affine-cancellation-trees/affine_ctx_verifier.py --check
python spikes/w050-affine-cancellation-trees/affine_ctx_verifier.py --json
```
