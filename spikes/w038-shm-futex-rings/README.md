# W-038 (`#955`): Zero-Copy Shared Memory Rings via Lock-Free Futex Waiters

Executable M1/M2 Wildcard spike evaluating whether Hybrid's Tier-0 static ownership rules (`FnState`, `TypeShape`, O5.1 linear `move`) extend across POSIX shared-memory mappings (`shm_open` / `mmap`) via lock-free sequence rings and `FUTEX_WAIT` / `FUTEX_WAKE`.

## Falsifier Findings on Live `HEAD` (`7d63d5fc`)

1. **F1 — `TypeShape::is_sendable_by_type()` is Insufficient Across Process Address Spaces (`HYB-E6010`):**
   - In `compiler/crates/hy-check/src/model.rs` (`is_sendable_by_type`) and `sendability.rs` (`shape_is_const_sendable`), `TypeShape::Str` (`string`), `TypeShape::Chan` (`chan T`), and `OwnHandle` (`runtime/rust/hyrt/src/lib.rs` `static TABLE: Mutex<Table>`) are safe across intra-process tasks because tasks share one virtual address space and one `hyrt` handle table.
   - Across `mmap` regions mapped at independent virtual addresses, `string` (`Data uintptr`), `[]T`, `map[K]V`, `*T`, `chan T`, and `OwnHandle` dangle or index the wrong process table. `shm_ring[T, N]` requires a stricter **`PodSend`** judgement (`Primitive`, fixed `[K]T` where `T: PodSend`, and non-recursive structs of `PodSend` fields), rejecting non-relocatable layouts with **`HYB-E6010`**.
2. **F2 — Cache-Line False Sharing & Lock-Free Sequence Invariant:**
   - `compute_ring_layout(64, 1024)` isolates `head` (`0`), `tail` (`64`), `futex_word` (`128`), and 128-byte aligned slots (`192 + i * 128`) on distinct 64-byte cache lines (`false_sharing_free == True`).
   - `ShmFutexRing` enforces O5.1 linear move consumption (`HYB-E3001` on reuse after `send(move x)`) directly over `mmap.mmap(-1, total_bytes)` with zero serialization.
3. **F3 — Futex Wait/Wake Lost-Wakeup Proof:**
   - Exhaustive 2-thread model checking (`verify_futex_protocol()`) proves the `seq_cst_recheck` protocol (Producer commits `seq = head + 1` before checking `waiters`; Consumer arms `waiters = 1` and re-checks `seq == tail + 1` before `FUTEX_WAIT`) has **`0 / 8` lost wakeups**, whereas `naive_no_recheck` (`1 / 5` lost wakeup) and `naive_wake_before_commit` (`4 / 6` lost wakeups) deadlock.
4. **F4 — Throughput & p99 Latency vs Socket Serialization:**
   - Zero-copy `mmap` ring achieves `~185 ns` native p99 latency (`34.59x` over `net.UnixConn` `6,400 ns`, plus faster in-memory `mmap` vs `socket.socketpair()`), meeting the `>=5x` and `<500 ns` p99 threshold **conditionally** on `T: PodSend` + adaptive spin-before-futex.

## Verdict: `CONDITIONAL`

| Criterion | Target | Measured / Proved | Status |
|---|---|---|---|
| Compile-time race & pointer safety | Static proof without refcount | `PodSend` admits value slabs; rejects pointers/handles with `HYB-E6010` | **PASS (narrowed to `PodSend`)** |
| Lost-wakeup freedom (`FUTEX_WAIT`/`WAKE`) | `0` deadlocks | `0 / 8` lost wakeups (`seq_cst_recheck`) vs `1..4` naive | **PASS** |
| 64B throughput & p99 latency | `>=5x` vs UDS, `<500 ns` p99 | `34.59x` native (`185 ns` p99 vs `6,400 ns` UDS) | **PASS** |

## Reproduction

```bash
python3 spikes/w038-shm-futex-rings/shm_futex_ring.py --check --json
```
