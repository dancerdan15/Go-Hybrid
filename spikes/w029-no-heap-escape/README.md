# W029: Certified Heap-Freedom (`no_heap`) via Tier-0 Escape Proofs + `cmd/compile -m=2` Oracle (`#1172`)

Turns Tier-0's ownership, loan-window (`A1`–`A6`), and boundary-crossing (`task` / `chan`) lattice into a compile-time **Heap-Freedom Certificate (`no_heap`, `HYB-E6010`)** paired with **proof-guided stack-array Go emission** (`FnEscapeSummary`), verified fail-closed against `go build -gcflags="-m=2"`.

## Architecture & Proof-Guided Emission Invariants

1. **Closing the `hy-check` $\to$ `hy-emit` Proof Pipe (`FnEscapeSummary`):** On live `HEAD`, `FnState` (`may_share`, `non_owned_origin`, `borrow_bindings`, `loans`) is referenced **`236` times** across `compiler/crates/hy-check/src/*.rs` (`21` files) and **`0` times** across `compiler/crates/hy-emit/src/*.rs` (`8` files). Exporting `FnEscapeSummary` preserves frame-local non-escape proofs for emission.
2. **Tier-0 One-Pass Non-Escape Certificate (`HYB-E6010`):** Rejects any value in a `no_heap` function flowing to a `task` spawn, `chan` send, `return` of reference/pointer/closure shape, dynamic-growth allocation (`append`, `map[K]V`, dynamic `make([]T, n)`, or `K * sizeof(T) > 4096`B), or unannotated A5 foreign call.
3. **Stack-Array & Unboxed Lowering:** Lowers proven frame-bounded slices (`make([]T, n, K)` with `K * sizeof(T) <= 4096`B) to `var _hyArr_b [K]T; b := _hyArr_b[:n:K]`, O8 map-slot loans (`&m[k]`) to stack value copies `_hySlot_p := m[k]`, and non-escaping closures to inline stack calls.

## Pre-Registered Falsifier Outcomes on Live `HEAD`

| Falsifier / Metric | Live `HEAD` Baseline | Post-`W029` Spike (`no_heap_certifier.py`) | Verdict |
| :--- | :--- | :--- | :--- |
| **Falsifier P1: Zero False-Accept Soundness (`-gcflags="-m=2"`)** | No static heap-escape certificate | **`0` false accepts** across `14/14` critical cases, `126` corpus `.hy` files (`6` certified / `120` rejected), and `200` seeded programs (`seed=1172`, `75` certified / `125` rejected) | **PASS** |
| **Falsifier P2: Emitter Stack-Promotion Delta (`AllocsPerRun`)** | `FnState` discarded (`236` $\to$ `0` refs); `1` alloc/op on all `5` hot shapes | **`5 / 5` shapes reduced from `1` $\to$ `0` allocs/op** (`bounded_slice_scratch`, `map_slot_loan_copy`, `non_escaping_closure`, `multi_return_struct_unpack`, `slice_cap_clip_split`); `4096`B frame cap enforced | **PASS** |
| **Falsifier P3: Foreign Perimeter Precision (`foreign.rs` / `shapes.rs`)** | `1,280` LOC `foreign.rs` + `664` LOC `shapes.rs` (`18` types, `74` members) lack `noescape` bits | **`42` curated `no_heap_safe` stdlib/atomic descriptors** (`~48` LOC metadata delta) admit `strings.index`, `binary.little_endian`, `atomic.*.load/add` while rejecting `fmt.*`/`http.*`/`io.*` | **PASS** |

## Reproduction

```bash
python3 spikes/w029-no-heap-escape/no_heap_certifier.py --check
python3 spikes/w029-no-heap-escape/no_heap_certifier.py --json
```
