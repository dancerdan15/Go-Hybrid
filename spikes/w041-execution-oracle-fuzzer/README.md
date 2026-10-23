# W-041 (#1011) — Execution-Oracle Fuzzing (`INVARIANT 5: accepted ⇒ runs clean under -race`)

Executable Wildcard Spike evaluating **Issue #1011** (Original proposal credit: `@1932819331hy-debug`): extending the fuzzer beyond `W-023` (`INVARIANT 4: accepted ⇒ go build succeeds`) with a deterministic **Execution-Oracle semantics leg (`INVARIANT 5`)** combining an AST-driven Hybrid generator, Happens-Before (`HB`) vector-clock `-race` + `O8` store-back runtime oracle, Zeller `ddmin` delta-debugger, and shrink-normalized SHA-256 deduplicator (`≤ 3/week` auto-file rate cap).

## Quantitative Evaluation & Falsification Matrix (`HEAD` `7d63d5fc`)

| Falsifier / Criterion (#1011) | Pre-Registered Threshold | Measured Result | Verdict |
|-------------------------------|--------------------------|-----------------|---------|
| **F1: Negative Control (Seeded Pre-Fix P0 Recall)** | Re-find `8 / 8` (`100.0%`) seeded P0 shapes (`#970` ×3, `#946`, `#953`, `#966`, `#971`, `#974`) that pass `go build` (`INVARIANT 4`) | **100.0% (`8 / 8`)** re-found across `PANIC_OOB` (`#970a`), `LOST_STORE_BACK` (`#970b`, `#970c`, `#953`), and `DATA_RACE` (`#946`, `#966`, `#971`, `#974`) | **SURVIVES** |
| **F2: Zeller `ddmin` Minimality & Dedup Normalization** | 1-minimal witness (`shrunk_len < orig_len`, `< 10 min` human repro) + canonical dedup key (`≤ 3/week` cap) | **100% 1-minimal** (`3–4` statements, `8` distinct SHA-256 dedup keys, capped to `3`, evaluated in **~1.5 ms**) | **SURVIVES** |
| **F3: Clean Determinism & `HEAD` Execution-Residual Table** | Deterministic verdicts + per-rule-family residual across `100` AST cases & `126` `tests/checker-corpus/*.hy` files (`38 ACCEPT`, `2 ACCEPT-MANAGED`, `86 REJECT`) | **`0.00%` (`0 / 75`)** `HEAD` execution residual vs **`25.00%` (`25 / 100`)** pre-fix residual across all 5 rule families | **PROVEN (`H0` Saturation on `HEAD`, `H1` Proven on Pre-Fix)** |

## Per-Rule-Family Execution-Residual Table (`100` Seeded AST Programs)

| Rule Family | Generated | Pre-Fix `INVARIANT 4` (`go build`) Residual | Pre-Fix `INVARIANT 5` (`-race` + `O8`) Divergences | `HEAD` Accepted (`hyb check`) | `HEAD` `INVARIANT 5` Execution Residual |
|-------------|-----------|---------------------------------------------|----------------------------------------------------|-------------------------------|-----------------------------------------|
| `O8_SLICE_LOAN` (`#970a/b/c`) | `22` | `0.00%` | `7` (`PANIC_OOB` / `LOST_STORE_BACK`) | `15` | **`0.00%` (`0 / 15`)** |
| `E3102_MAP_SLOT` (`#946`) | `18` | `0.00%` | `3` (`DATA_RACE`) | `15` | **`0.00%` (`0 / 15`)** |
| `O8_MATCH_STORE_BACK` (`#953`) | `21` | `0.00%` | `6` (`LOST_STORE_BACK`) | `15` | **`0.00%` (`0 / 15`)** |
| `A2_TASK_SPAWN_MOVE` (`#966`, `#971`) | `23` | `0.00%` | `8` (`DATA_RACE`) | `15` | **`0.00%` (`0 / 15`)** |
| `A6_RETURN_ORIGIN_ALIAS` (`#974`) | `16` | `0.00%` | `1` (`DATA_RACE`) | `15` | **`0.00%` (`0 / 15`)** |

## Reproduce

```bash
python spikes/w041-execution-oracle-fuzzer/race_oracle_fuzzer.py --check
python spikes/w041-execution-oracle-fuzzer/race_oracle_fuzzer.py --json
```
