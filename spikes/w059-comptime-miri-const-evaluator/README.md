# W-059: Unified Comptime Evaluator with Exact Go Arbitrary-Precision Semantics

- **Ref #1290** (`[wildcard] Kill the 4 Split Constant Folders (arith / const_overflow / const_init / constconv): Unified Comptime Evaluator with Exact Go Arbitrary-Precision Semantics`)
- **Original proposal author:** @dancerdan15 (#1290)
- **Diagnostic family:** `HYB-E5001`..`HYB-E5005`, `HYB-E6003`

## Problem: 4 Divergent Constant Folders on `HEAD`

Go-Hybrid currently duplicates compile-time constant folding across four disconnected AST walkers (~2,979 LOC total), each using truncated host primitives (`i128` / `f64`) instead of Go's arbitrary-precision untyped constant semantics:

| Legacy Module | LOC | Representation | Root-Cause Defect on `HEAD` | Replaced By |
|---|---:|---|---|---|
| `hy-check/src/arith.rs` | 850 | `Option<i128>`, `f64` | `const_eval_int` caps shifts at `< 128`; `divisor_float_fold_is_zero` uses `f64` (`1e30 + 1.0 - 1e30 == 0.0` false `HYB-E5002`) | `ComptimeEnv._eval_binop` (`UntypedInt` / `UntypedFloat`) |
| `hy-check/src/const_overflow.rs` | 1,551 | `ConstFold { Val(i128), NotConst, TooBig }`, `f64` | `1 << 200` becomes `TooBig`; `(1 << 200) >> 195` returns `NotConst` instead of `32`; `f32::MAX as f64` disagrees with `(f as f32).is_infinite()` | `ComptimeEnv.convert` + `ieee754_round` (`F32_MAX`, `F64_MAX`) |
| `hy-check/src/const_init.rs` | 306 | `bool` (`expr_is_constant_inner`) | Separate recursive AST walk checks const-ness without computing values or detecting dependency cycles | `ComptimeEnv.resolve` (cycle-safe `HYB-E5005` + gas budget) |
| `hy-emit/src/constconv.rs` | 272 | `Option<i128>` (`const_int_expr`) | 4th integer parser (`parse_int`), `shift >= 127 -> None`, and zero access to resolved `const_values` | `ConstVal.to_go_literal()` + `ComptimeEnv.convert(allow_wrap=...)` |

## Unified Architecture (`comptime_evaluator.py`)

1. **Single Algebraic Value Hierarchy (`ConstVal`):**
   - `UntypedInt(int)`: arbitrary-precision integer constants (no intermediate `i128` overflow on `1 << 200 >> 195 == 32`).
   - `UntypedFloat(Fraction)`: exact rational arithmetic (`1e30 + 1 - 1e30 == 1`, exact decimal and `0x1.fffffep+127` hex-floats).
   - `UntypedComplex(Fraction, Fraction)`, `UntypedBool(bool)`, `UntypedString(str)`: full untyped constant domain.
   - `TypedVal(ScalarType, value)`: target-typed constants with exact integer range checks (`i8`..`u64`, `int`, `uint`, `uintptr`, `byte`, `rune`) and exact IEEE-754 `roundTiesToEven` rounding (`ieee754_round`) against `F32_MAX = (2 - 2^-23) * 2^127` and `F64_MAX = (2 - 2^-52) * 2^1023`.
2. **Cycle-Safe & Gas-Metered `ComptimeEnv`:**
   - Resolves arbitrary-order `const` declarations on demand with deterministic cycle detection (`HYB-E5005: const dependency cycle: A -> B -> A`) and step-budget metering (`max_steps`).
   - Serves both `hy-check` diagnostics (`HYB-E5001`..`HYB-E5005`, `HYB-E6003`) and `hy-emit` canonical Go literal lowering (`to_go_literal()`) from a single `ComptimeResult`.

## Diagnostic Coverage

| Code | Condition | Canonical Trigger Example |
|---|---|---|
| `HYB-E5001` | Constant integer or float overflows target scalar range | `u8(300)`, `f32(1 << 128)`, `string(1 << 512)` |
| `HYB-E5002` | Constant integer, float, or complex division/modulo by zero | `100 / (1e30 - 1e30)`, `1 % 0` |
| `HYB-E5003` | Non-integral float or non-zero-imaginary complex truncated to int/float | `int(2.5)`, `int(complex(3, 1))` |
| `HYB-E5004` | Negative shift count or non-integer shift operand/count | `1 << -1`, `1 << 2.5` |
| `HYB-E5005` | Constant dependency cycle or comptime gas-budget exhaustion | `A = B + 1; B = A + 1` |
| `HYB-E6003` | Non-constant call or unresolved identifier in `const` initializer | `foo(1)`, `UNKNOWN_CONST` |

## Verification

```bash
python -B spikes/w059-comptime-miri-const-evaluator/comptime_evaluator.py --check
```
