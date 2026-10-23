# Spike W-046 (#482): Data-Carrying Linear Sync Primitives & Monotone Task Regions

**Target:** [#482](https://github.com/limitlessprojects/Go-Hybrid/issues/482) (`@Arnavquartz`) & Linear Sync Surface (`Mutex[T]`, `RWMutex[T]`, `WaitGroup` Token, `MonotoneCell[T]`) — Flip synchronized shared-state and monotone region boundaries from unmodeled `managed("...", "mutex")` / `GUARANTEES.md` Row 9 & Known Limits exclusions to statically verified linear capabilities.

## 1. Static Diagnostic & Capability Invariants

| Diagnostic | Surface / Rule | Enforcement Mechanism |
|---|---|---|
| `HYB-E4601` | Lexical Guard Escape / Premature Snapshot | Rejects returning, sending, spawning, or storing `guard` (or any derived alias) outside `with m.lock() as guard:`, and rejects `MonotoneCell[T].snapshot()` before `wg.wait()` quiescence. |
| `HYB-E4602` | Bare `sync.Mutex` / Non-Monotone Op | Rejects data-less `sync.Mutex`, unlocked payload access, `RWMutex` writes under `rlock()`, and non-monotone operations (`remove`, `absence_test`, `live_iter`, `first_winner`, `mutable_ref`). |
| `HYB-E4603` | Re-Entrant Lock / Unsettled Token / Cycle | Rejects nested `m.lock()` on the same `Mutex[T]`/`RWMutex[T]` inside an active guard, unsettled `wg.token()` without `defer tok.done()` before `wg.wait()`, and unseeded cyclic threshold waits. |

## 2. Quantitative Falsifier Evaluation on Live `HEAD` (`7d63d5fc`)

### A. Static Corpus Battery (`10/10` Verified)
- **Accept (`3/3`):** `accept-mutex-guard-and-wg-token.hy` (`with m.lock() as guard:` + `wg.token()` $\rightarrow$ `move tok` $\rightarrow$ `defer tok.done()` $\rightarrow$ `wg.wait()`), `accept-rwmutex-shared-readers.hy` (`rlock` + `wlock`), `accept-monotone-region-quiescence.hy` (`MonotoneCell[int]` snapshot after `wg.wait()`).
- **Reject (`7/7`):** `reject-guard-reference-escape.hy` (`HYB-E4601`), `reject-bare-sync-mutex.hy` (`HYB-E4602`), `reject-reentrant-self-deadlock.hy` (`HYB-E4603`), `reject-rwmutex-write-under-rlock.hy` (`HYB-E4602`), `reject-unsettled-wg-token.hy` (`HYB-E4603`), `reject-premature-monotone-snapshot.hy` (`HYB-E4601`), `reject-nonmonotone-removal.hy` (`HYB-E4602`).

### B. Issue #482 Interleaving & Negative-Control Matrix

| Suite | Mode | Schedules | Distinct Snapshots | Distinct Task Obs | Schedule-Independent | Verdict |
|---|---|---:|---:|---:|---|---|
| Positive diamond propagation | `union` + threshold `await` | `30` | `1` (`(1, 2, 3)`) | `1` | **True** | **PASS** |
| Unseeded cyclic threshold wait | `t1` awaits `{2}`, `t2` awaits `{1}` | `6` | `0` (blocked) | `0` | **False** (Deadlocked) | **REJECT (`HYB-E4603`)** |
| Negative Control (a): live snapshot | `read_live` before `wg.wait()` | `30` | `1` | `3` | **False** | **FALSIFIED (as predicted)** |
| Negative Control (b): insert flag | `write` returns `added: bool` | `2` | `1` | `2` | **False** | **FALSIFIED (as predicted)** |
| Negative Control (c): overwrite | `write` overwrites instead of union | `2` | `2` (`(1,)`, `(2,)`) | `1` | **False** | **FALSIFIED (as predicted)** |

## 3. Reproduce
```bash
python spikes/w046-linear-sync-primitives/linear_sync_verifier.py --check
python spikes/w046-linear-sync-primitives/linear_sync_verifier.py --json
```
