# W-039 (#952) — Untrusted `hy-emit`: Static Translation Validation Certificates

Executable Wildcard M2 spike testing whether **Compile-Time Translation Validation Certificates** (`type _witness_<kind>_<id> [0]func()`) and **Static Bisimulation Verification** between `hy-check` obligations and emitted Go eliminate `hy-emit` from the Trusted Computing Base (TCB) with zero runtime footprint.

## Architecture & Bisimulation Obligations
1. **O8 Map-Slot Store-Back Completeness (`HYB-TV-O8`)**: Verifies every `&m[k]` loan lowers to copy-out + store-back (`m[k] = v`), snapshots mutated/effectful index keys into `hyKeyN` (`emit_slot_loan_place`), and flushes store-backs before `return`/`break`/`continue` or task/send boundaries (`#546`).
2. **Mutability & Value-Copy Preservation (`HYB-TV-MUT`)**: Enforces immutability of `let`/`const` bindings (rejecting reassignments and `&x` address escapes) and verifies value-copy semantics (`A4`/`O9`) across `task` spawns and `ch <- v` channel sends.
3. **`defer` LIFO & Evaluation Order (`HYB-TV-ORD`)**: Verifies peeled `defer f()` calls, pre-`defer` hoisting of `?` arguments (`P1 §7`), LIFO exit-wrapper execution (`E6`), and left-to-right pre-try evaluation (`hoist_order_before_try`).

## Pre-Registered Falsifier Outcomes on `HEAD` (`7d63d5fc`)
| Falsifier / Target (#952) | Threshold | Measured (`--check --json`) | Verdict |
|---|---|---|---|
| **M1: 50 Adversarial Emitter Mutation Suite** (`18` O8, `16` MUT, `16` ORD) | `100%` rejected (`50/50`) | `50/50` (`100.0%`) caught with exact family code (`HYB-TV-O8`/`MUT`/`ORD`) | **PASS** |
| **M2: Live `tests/checker-corpus/` Audit** (`09`, `16`, `23`, `map-slot`, `member-defer`) | `100%` certified (`0` false positives) | `5/5` `CERTIFIED-BISIMILAR` (`14` obligations verified) | **PASS** |
| **F1: Runtime Overhead & Binary Footprint** (`[0]func()` phantom witness types) | `0` bytes / `0` allocs/op | `0` bytes (`sizeof([0]func()) == 0`, erased by `cmd/compile`) | **NOT FALSIFIED** |

Run: `python spikes/w039-emit-translation-cert/emit_translation_validator.py --check --json`
