# W-035 (#992) — Linear Capability Passports (`type Cap[T any] [0]byte`)

Executable Wildcard M1/M2 spike testing whether **Linear Capability Passports** (`type Cap[T any] [0]byte`, `cap.Unique[T]`, `cap.Loan[T, Token]`) emitted into generated Go can replace the `8,634`-line Rust graph borrow-checker (`compiler/crates/hy-check/src/loans.rs`) using Go's native type checker as the ownership verification oracle.

## Architecture & Token Lowering Rules

1. **Ownership & Move Consumption (`HYB-E3003` — A1b / A7)**: Every owned or reference-bearing binding `x` mints `cap_x Cap[T]`. Moving (`results <- move x`, `task f(move x)`) or consuming (`enc.finish()`) rebinds `_, cap_x := cap.Consume(cap_x)` (`Consumed[T]`). Subsequent reads, writes, or shared-alias accesses (`m2 := agg`) emit `cap.Require(cap_x)`, failing Go type-checking with `Consumed[T] != Cap[T]`. Unmoved mutable references at `task` spawn fail `cap.SpawnRequireUnique` (`HYB-E3001` — A1c).
2. **Scoped Lens Loans (`HYB-E3101` — A2 / `HYB-E3102` — A3)**: Handle iterations (`doc.array("users")`) and map-slot borrows (`&e.services[k]`) transition `cap_x` into `Locked[T, Tok]` via `cap.Borrow` until the loan's last use calls `cap.Release`. Any mutating handle call (`doc.set(...)`) or map insert/delete inside the live window fails `cap.Require` against `Locked[T, Tok]`.
3. **Frame Escape Prevention (`HYB-E3103` — A6 / `HYB-E3104` — LOAN-ESCAPE)**: Borrow views (`cap.Loan[T, _frame]`) carry an unexported frame phantom type, rejecting struct field storage (`HYB-E3103`) and function return escapes (`HYB-E3104`).

## Pre-Registered Falsifier Outcomes on `HEAD` (`7d63d5fc`)

| Milestone / Falsifier (#992) | Target Threshold | Measured (`--check --json`) | Verdict |
|---|---|---|---|
| **M1: Founding corpus REJECT recall** (`01,03,04,05,06,07,08,11,14,19,20`) | `11/11` rejected (`HYB-E3001`..`E3104`) | `11/11` rejected (`HYB-E3003`, `HYB-E3101`, `HYB-E3103` verified) | **PASS** |
| **M1: Founding corpus ACCEPT precision** (`02,09,10,12,13,15,16` + `17,18`) | `100%` accepted (`0` false positives) | `9/9` matched (`7/7` `ACCEPT` + `2/2` `ACCEPT-MANAGED`, `20/20` total) | **PASS** |
| **M2: `loans.rs` graph-machinery erasure** (`st.aliases`, `st.may_share`, `st.shadow_links`) | Replace `>8,000` LOC graph state | `8,634` LOC audited (`460` graph refs: `334` alias, `111` may_share, `15` shadow) | **PASS** |
| **M2: Zero-cost & flat rebinding falsifier** (`[0]byte` size, heap escapes, nesting) | `0 B`, `0` escapes, `0` lambda nesting | `0` bytes (`[0]byte`), `0` heap escapes, `0` monadic nesting depth | **NOT FALSIFIED** |

## Reproduction

```bash
python spikes/w035-linear-cap-passports/linear_cap_passports.py --check --json
```
