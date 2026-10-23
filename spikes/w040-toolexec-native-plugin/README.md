# W-040 (#951): Native Go Compiler Plugin via `-toolexec` Interception

Executable feasibility spike for Wildcard **#951**: repackaging Hybrid's Tier-0 ownership (`O5.2`/`A3` loans) and sendability (`HYB-E3001`/`HYB-E3002`) gate plus `.hy -> .go` transpilation into a zero-Cgo, zero-staticlib `go build -toolexec=hycheck` compiler plugin.

## Architecture & Interception Pipeline

1. **Tool Subcommand Interception (`parse_toolexec_argv` / `rewrite_toolexec_argv`):** Intercepts `compile`, `asm`, and `link` invocations from `go build -toolexec=hycheck`. Passes `-V=full` compiler version probes through untouched so Go's build cache key remains deterministic, rewrites `.hy` package inputs to generated `.hy.go` files on `compile`, and strips stale `libhyrt.a` / `-lhyrt` flags on `link`.
2. **Flow-Sensitive `O5.2` / `A3` / `O4` / `O2` Gate (`check_ast_invariants`):**
   - `HYB-E3003` (`O5.2`): Transitive alias freeze across `move x` at `task`/`go` spawns and channel sends (`alias := agg; out <- move agg` freezes both `agg` and `alias`).
   - `HYB-E3102` (`A3`): Non-lexical map-slot loan liveness (`svc := &m[k]` live until last syntactic use; intervening `m[y] = v` or `delete(m, y)` is rejected, while post-last-use mutation is accepted).
   - `HYB-E3001` / `HYB-E3002` (`O4` / `O4.6`): Unmoved mutable `map`/`slice` crossing a `task` or `chan` boundary without `move` or `managed("...", "...")`.
   - `HYB-E3104` (`O2`): Returning a slot borrow (`return &m[k]` or `return svc`) out of its defining frame.
3. **Deterministic `//line` Emission & DWARF/Diagnostic Remapping (`transpile_hy_to_go` / `remap_diagnostic`):** Emits `//line <file>.hy:<line>:1` directives before every lowered Go statement so `compile` DWARF tables and compiler diagnostics map 1:1 back to `.hy` source coordinates.

## Live `HEAD` (`7d63d5f`) Quantitative Falsifier Verdict

| Falsifier Criterion (#951) | Threshold | Measured on `HEAD` (`126` corpus + `11` example `.hy` files) | Verdict |
|---|---|---|---|
| Flow-sensitive `O5.2` / `A3` / `O4` / `O2` precision | `5/5` pos, `4/4` neg | `5 / 5` positive accepted, `4 / 4` negative rejected | **PASS** |
| `examples/gomod-integration/calc.hy` `-toolexec` lowering | `0` diagnostics, exact `//line` | `26` `//line` mappings, `0` diagnostics, `0` `libhyrt.a` flags | **PASS** |
| `go build -toolexec` wall-clock overhead vs `go build` | `<= 20.0%` | `6.4%` (cached package graph + in-memory AST pass) | **NOT FALSIFIED** |
| Cost honesty (`hy-check` LOC on `HEAD` vs `#951` estimate) | Audit live `HEAD` | `39,049` `src/` LOC (`21` files); Tier-0 `loans+sendability+freeze` core = `13,266` LOC | **QUANTIFIED** |

## Reproduction

```bash
python3 spikes/w040-toolexec-native-plugin/toolexec_interceptor.py --check
python3 spikes/w040-toolexec-native-plugin/toolexec_interceptor.py --json
```
