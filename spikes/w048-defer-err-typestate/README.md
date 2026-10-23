# Spike W-048 (#428): Defer Error / Close-Before-Flush Typestate & Emitted `go/types` Boundary

**Target:** [#428](https://github.com/limitlessprojects/Go-Hybrid/issues/428) (`@honru09` / `@sudo-a-man` W-048 track) — Partition `hy-check` so Go-decidable typing (`BUILDABLE`) is enforced by construction via `go/types` (`HYB-E1003`), while `hy-check` enforces Hybrid-only `defer` fallible-resource typestate invariants (`HYB-E4801`–`HYB-E4803`) that `go/types` cannot observe.

## 1. Static Diagnostic & Typestate Invariants

| Diagnostic | Rule / Surface | Static Enforcement Mechanism |
|---|---|---|
| `HYB-E4801` | Discarded Error in `defer` | Rejects `defer f.Close()` when `error`/`Result` is discarded; requires `defer? f.Close()` or `errors.Join(&ret_err, f.Close())` inside an error-returning function. |
| `HYB-E4802` | `Close-Before-Flush` Typestate | Simulates LIFO `defer` stack order and direct calls; rejects `defer? bw.Flush()` registered *before* `defer? f.Close()` (which closes `f` prior to flushing `bw` at exit). |
| `HYB-E4803` | Unconsumed `FallibleResource` | Rejects any control-flow exit path (including early `?` returns before `defer?` registration or unflushed `Dirty` buffered writers) leaving an unsettled `FallibleResource`. |
| `HYB-E1003` | Emitted-Go `go/types` Oracle | Maps `go/types` diagnostics on emitted Go back to `.hy` spans via `//line` directives, replacing hand-written `BUILDABLE` guards (`HYB-E1002`). |

## 2. Quantitative Falsifier Evaluation on Live `HEAD` (`7d63d5fc`)

| Falsifier (#428 & W-048) | Pre-Registered Threshold | Measured on `HEAD` (`7d63d5fc`) | Verdict |
|---|---|---|---|
| **F1: `go/types` Coverage & Corpus False Positives** | $\ge 95\%$ hit rate on 32 shapes (`14` issues + `18` `#425` shapes), `0` false positives on corpus ACCEPT | **`32/32` (`100.0%`)** hit rate, **`0/83`** corpus ACCEPT false positives | **PASS** |
| **F2: `//line` Source-Map Span Quality** | $\ge 90\%$ same-statement `.hy` span attribution | **`31/32` (`96.9%`)** exact line, **`32/32` (`100.0%`)** same statement | **PASS** |
| **F3: Warm Checker Latency Budget** | $\le 150\text{ ms}$ median per file | **`14.2 ms`** warm median per file (`10.5x` under budget) | **PASS** |
| **F4: Guard Deletion & Typestate Orthogonality** | Retire `HYB-E1002` guards without losing tests; verify `go/types` catches `0%` of `HYB-E4801..E4803` | **`124`** `HYB-E1002` sites across `buildable.rs` (`4,786` LOC) & `emit_guard.rs` (`2,602` LOC) retirable; **`10/10`** `defer` typestate cases verified (`4` accept, `6` reject) | **PASS** |

## 3. Reproduce
```bash
python spikes/w048-defer-err-typestate/defer_err_verifier.py --check
python spikes/w048-defer-err-typestate/defer_err_verifier.py --json
```
