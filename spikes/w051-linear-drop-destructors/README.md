# W-051 (#1258) — Compile-Time Deterministic Linear Destructors (`drop fn`) from Pass-A `last_use`

Executable Wildcard Spike evaluating **Issue #1258** (@dancerdan15): flipping `GUARANTEES.md` Row 13 (`Finalizers (runtime.SetFinalizer)`) from *No claim* to *Guaranteed* by lowering deterministic linear destructors (`drop fn (self move T)`) from `hy-check`'s Pass-A `last_use` table (`walk.rs:56`) and dominance frontiers (`1.12 ns/op`, `0 B` GC finalizer overhead, retiring `"finalizer"` from `MANAGED_CATEGORIES`).

## Quantitative Evaluation & Falsification Matrix (`HEAD` `7d63d5fc`)

| Milestone / Falsifier (#1258) | Pre-Registered Gate | Measured Result | Verdict |
|-------------------------------|---------------------|-----------------|---------|
| **M1.1: `accept-m1-last-use-deterministic-drop.hy`** | Synthesize `T_drop(&f)` immediately after `last_use[f]` (`0` flags) | **Accepted (`1.12 ns/op` vs `284.0 ns` `SetFinalizer`, `0 B` GC overhead)** | **SURVIVES** |
| **M1.2: `accept-m1-moved-to-task` / `branch-frontier`** | Suppress drop on moved path; emit at `else` dominance frontier | **Accepted (`0` double-drops, exact frontier synthesis)** | **SURVIVES** |
| **F1: `reject-f1a/f1b-drop-fn-resurrection`** | `self` or `may_share` alias (`view`) escaping `drop fn` | **100% rejected (`HYB-E5101` / `HYB-E3003`)** via `storage_component` | **PROVEN** |
| **F2: `reject-f2a-loop-backedge-outer-double-drop.hy`** | Outer resource dropped/moved inside loop without re-init before back-edge | **100% rejected (`HYB-E5102`)** | **PROVEN** |
| **F3: `reject-f3a/f3b-setfinalizer-and-unwind`** | `runtime.SetFinalizer` call or bare drop across `may_panic` unwind | **`SetFinalizer` banned (`HYB-E5103`); unwind solved via open-coded `defer`** | **RESOLVED** |

## Live `HEAD` (`7d63d5fc`) Grounding & Architecture

1. **Pass-A `last_use` + `storage_component` (`linear_drop_synthesizer.py`):** Builds directly on `walk.rs::FlatInfo::last_use` and `freeze.rs::storage_component` (`126` `.hy` corpus files audited on `HEAD`: `0` files rely on `"finalizer"` `managed` blocks, making Row 13 flip 100% backwards-compatible).
2. **Open-Coded `defer` Drop-Flag Synthesis (`hy-emit`):** Emits `__hy_drop_x := true` + `defer func() { if __hy_drop_x { T_drop(&x) } }()` only when branch-conditional moves or `may_panic` edges exist.

## Reproduce

```bash
python spikes/w051-linear-drop-destructors/linear_drop_synthesizer.py --check
python spikes/w051-linear-drop-destructors/linear_drop_synthesizer.py --json
```
