# W-030 (`#1210`) — Word-Atomicity Floor Below Tier-0 for `managed`

Executable spike for [Issue #1210](https://github.com/limitlessprojects/Go-Hybrid/issues/1210): *"`managed` is a memory-safety escape, not a race escape: ACCEPT-MANAGED programs tear slice/interface headers into arbitrary memory corruption — add a word-atomicity floor below Tier-0 that `managed` cannot waive"*.

## Architecture (`word_atomicity_floor.py`)
1. **Deep Multi-Word Reachability Classifier (`classify_type`, `O4.6`):** Distinguishes single-word tear-free values (`int`, `bool`, `chan T`, `*T` with single-word fields, `sync.mutex`) from multi-word headers (`string` 2-word `(ptr,len)`, `[]T` 3-word `(ptr,len,cap)`, `any`/`interface{}` 2-word `(type,data)`, and structs containing them) plus `map[K]V` runtime bucket residuals.
2. **Region-Sensitive `HYB-E8001` Lowering (`lower_to_word_atomic_go`):** Inspects values crossing `managed` check-sites (`HYB-E8001`) and boxes reachable multi-word fields behind `atomic.Pointer[T]` (`Store(&box)` on write, `*Load()` on read), preserving single-word pointer atomicity so concurrent readers never observe torn `(ptr,len,cap)` or `(type,data)` pairs.

## Pre-Registered Falsifier Outcomes on Live `HEAD` (`7d63d5f`)
| Falsifier | Target / Invariant | Live `HEAD` Result | Verdict |
|---|---|---|---|
| **F0: Unlowered Tearing (`torn.hy`, `torn_write.hy`, `confuse.hy`)** | Multi-word slice/interface race under `managed` | `torn.hy` tears `(ptr_small, len=2^20, cap=1)` (OOB read/write); `confuse.hy` tears `(type=boxed, data=0x1000)` (`SIGSEGV`) | **REPRODUCED** |
| **F1: Word-Atomic Floor Lowering (`--managed-word-atomic`)** | `0` foreign reads/writes & `0` faults under `atomic.Pointer[T]` | `0` `len != cap` mismatches, `0` OOB accesses, `0` type-confusion faults (`4` lines lowered per repro) | **PASS** |
| **F2: Microbenchmark Cost Budget (`box_test.go`)** | Publish $\le 25\text{ ns}$ (`1` alloc, `24 B`), read overhead $\le +0.5\text{ ns}$ | Publish `18.8 ns` vs `0.32 ns` plain (`<= 25.0 ns`); read `2.04 ns` vs `2.07 ns` plain (`-0.03 ns <= +0.5 ns`) | **PASS** |
| **F3: Shipped Corpus & Examples Zero-Cost (`O6.3`/`O6.4`)** | `0` emitted Go lines changed across shipped corpus & examples | `126` corpus (`02` shares `map[string]int` under `"mutex"`; `18` shares `*pool_stats` of `int`s) + `11` examples $\to$ **`0` lines changed** | **PASS** |
| **F4: Normative Spec & `GUARANTEES.md` Audit** | Identify `SPEC-LANGUAGE.md` `O6.1`/`O20` & `GUARANTEES.md` row `17` DRF omission | Confirmed live on `HEAD`: `O6.1` (*"not a memory escape"*), `O20` (*"no memory-unsafe escape"*), row `17` (*"Inherited from Go"*) | **VERIFIED** |

## Reproduction
```bash
python3 spikes/w030-word-atomicity-floor/word_atomicity_floor.py --check
python3 spikes/w030-word-atomicity-floor/word_atomicity_floor.py --json
```
