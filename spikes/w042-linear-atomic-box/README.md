# Spike W-042 (`#833`): Linear Atomic Pointer Swaps (`atomic.Box[T]`)

Executable Wildcard Track M1/M2 verification spike for **#833** (author: `@magomedta`): flipping [GUARANTEES.md](../../GUARANTEES.md) Row 11 from `[TBD]` / `No claim yet` to **Guaranteed** for `atomic.Box[T]` via compile-time **Exchange Linearity** while preserving fail-closed `HYB-E3005` on raw `atomic.pointer[T]` / `atomic.value`.

## Exchange-Linearity Rules & Diagnostic Matrix

| Operation / Boundary | Ownership Transfer / Invariant | Verdict |
|---|---|---|
| `task worker(&box)` / `ch <- &box` | `atomic.Box[T]` is Sendable Class 2 (`Class2:LinearAtomicBox`) | **ACCEPT** |
| `task worker(&raw_ptr)` (`atomic.pointer[T]`) | Raw foreign atomic pointer remains unproven (PR #52 fence) | **REJECT (`HYB-E3005`)** |
| `old := box.swap(move v)` | Consumes exclusive unloaned `v`; returns exclusive `old: T` | **ACCEPT** |
| Use of `v` after `box.swap(move v)` | Lexical use-after-move on swapped-in binding (SPEC §9.3 O5) | **REJECT (`HYB-E3003`)** |
| `box.swap(v)` (unmoved / loaned / aliased) | Exchange input must be `move v` with 0 active loans (O5.4 / O2) | **REJECT (`HYB-E3004`)** |
| `box.compare_and_swap(exp, move v)` | Reads `exp`, consumes `move v` on swap handoff (reclaims on fail) | **ACCEPT** |
| `opt := box.take()` | Consuming extraction (`swap(nil) -> option[T]`), exclusive return | **ACCEPT** |
| `p := box.load()` on mutable `T` | Non-consuming load would fan out shared mutable aliases | **REJECT (`HYB-E4201`)** |
| `p := box.load()` on immutable/scalar `T` | Deeply immutable Class 1/4 pointee safe to read without exchange | **ACCEPT** |

## Falsifier Evaluation on Live `HEAD`

- **G1 (Zero Pointee Data Races):** `verify_treiber_stack` (200 CAS-pushed / `take`-popped mutable nodes across 8 tasks) and `verify_rcu_buffer_swap` (100 swaps across 4 writers) verify 100% single-owner pointee handoff (`race_violations == 0`).
- **G2 (Live Repo & Corpus Audit):** Confirms `GUARANTEES.md` Row 11 is `[TBD]`, `foreign.rs` gates non-scalar atomics via `is_unproven_atomic`, and `atomic-pointer-fail-closed-reject.hy` pins `HYB-E3005`.

```bash
python3 spikes/w042-linear-atomic-box/linear_atomic_box.py --check
python3 -m unittest discover -s spikes/w042-linear-atomic-box/tests -v
```
