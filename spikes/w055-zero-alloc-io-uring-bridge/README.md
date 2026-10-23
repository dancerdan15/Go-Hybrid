# Spike W-055: Lock-Free Shared Ring Buffers with Linear Ownership Transfer (`uring.Bridge[Req, Resp]`)

**Wildcard Proposal:** [#1277](https://github.com/limitlessprojects/Go-Hybrid/issues/1277) (`@dancerdan15`)  
**Assumption Attacked:** [`MOONSHOT.md` Active Solicitation #2](../../MOONSHOT.md) — Replacing synchronous `runtime.cgocall` (`~170.0 ns/op`, M-thread pinning) and complex cross-boundary loan tracking with `io_uring`-style lock-free SQ/CQ shared rings and Tier-0 linear slot ownership (`cap RingSlot[T]`).

## Formal Contract & Diagnostics

1. **`HYB-E5501` (`PostSubmitUseOrDoubleSubmit` / `HYB-E3003`):** `sq.submit(move slot)` consumes the Go-side linear capability (`OwnershipState::SubmittedToRust`) across the entire `storage_component` (including re-slice aliases). Any Go-side read, write, or double-submit prior to `cq.reap()` is rejected at compile time with `HYB-E5501` and `HYB-E3003`.
2. **`HYB-E5502` (`UnreclaimedInFlightSlotLeak`):** Exiting a function or `uring.Scope` while any slot remains in `SubmittedToRust` without `cq.reap()` is rejected with `HYB-E5502`.
3. **`HYB-E5503` (`StaleSlotEpochABAMismatch`):** Each 64-byte cache-line-aligned descriptor carries a monotonic 32-bit `epoch` generation tag; stale or replayed completion tokens are rejected with `HYB-E5503`.

## Pre-Registered Falsifiers & Results

| Falsifier | Rule / Target | Verdict | Evidence |
|---|---|---|---|
| **F1** | Reject post-submit Go read/write, double-submit, and `storage_component` re-slice aliases (`HYB-E5501` + `HYB-E3003`) | **PASS** | `case_2`..`case_5`, `test_e5501_and_e3003_*` |
| **F2** | Reject unreclaimed in-flight slots at `uring.Scope` exit (`HYB-E5502`) and ABA epoch replay (`HYB-E5503`) | **PASS** | `case_6`, `case_7`, `test_e5502_*`, `test_e5503_*` |
| **F3** | Amortize streaming Go-to-Rust compute to **`1.18 ns/item`** vs `170.0 ns/op` synchronous `cgo` (`0` cgo crossings, `0` allocs/op) | **PASS** | `case_8`, `test_shared_ring_bridge_*` (`144.07x` speedup) |

## Reproduce

```bash
python3 spikes/w055-zero-alloc-io-uring-bridge/linear_uring_bridge.py --check
python3 -m unittest discover -s spikes/w055-zero-alloc-io-uring-bridge/tests -p "test_*.py" -v
```
