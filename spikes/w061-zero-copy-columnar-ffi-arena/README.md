# Spike W-061: Zero-Copy Columnar Tape Views (`BatchView[T]`) Across the Go/Rust Boundary in O(1) Crossings

- **Wildcard Proposal:** Ref #1292 (Wildcard companion spike — keeps wildcard proposal open per `MOONSHOT.md`)
- **Original proposal author:** `@dancerdan15` (#1292)
- **Target Bottleneck:** `runtime/go/jsonbinding/json.go` (`Value.Array`, `Value.Int`, `Value.String`) + `runtime/rust/hyrt/src/lib.rs` (`TABLE: Mutex<Table>`, `hy_json_array_get`, `hy_json_release`) where iterating an $N$-element array incurs $\ge 2N$ FFI crossings, $N$ `Mutex<Table>` acquisitions, $N$ handle-table slots, and `~758 ns/elem` overhead.

## 1. 64-Bit Binary `ColumnarTape` & `string_slab` Layout

Every node is encoded as a 64-bit little-endian word (`struct.pack("<Q")`) with an 8-bit tag (`[63:56]`) and a 56-bit payload (`[55:0]`):

| Tag Constant | Code | 56-Bit Payload Encoding (`[55:0]`) | Traversal / Allocation Property |
|---|---|---|---|
| `TAG_NULL` / `TAG_BOOL` | `0x00` / `0x01` | `0` or `0`/`1` boolean bit | Inline 8-byte word, `0` heap allocs |
| `TAG_INT64` / `TAG_FLOAT64` | `0x02` / `0x03` | Signed 56-bit int / top-56 IEEE-754 bits | Inline scalar read in `O(1)` |
| `TAG_STRING` | `0x04` | `(offset_u32 << 24) \| length_u24` | Slice into single UTF-8 `string_slab` (`0` `CString` allocs) |
| `TAG_OBJECT_START` / `TAG_ARRAY_START` | `0x05` / `0x07` | `(count_u24 << 32) \| jump_idx_u32` | `skip_value(idx)` jumps to `jump_idx` in **O(1)** without walking interior nodes |
| `TAG_OBJECT_END` / `TAG_ARRAY_END` | `0x06` / `0x08` | `start_idx_u32` | Paired container boundary marker |

## 2. Generational Epoch-Pin (`ArenaEpochGuard`) & Tier-0 Verifier (`verify_batch_view_program`)

While a lexical `BatchView[T]` slice is live (`pinned_views > 0`), the owning `ColumnarArena` epoch is pinned:

| Diagnostic | Invariant Enforced | Trigger Condition |
|---|---|---|
| **`HYB-E6201`** | No Use-After-Arena-Reset / Free | `ArenaReset` or `ArenaFree` while `pinned_views > 0` (live `BatchView[T]`) |
| **`HYB-E6202`** | Lexical Scope Non-Escape | `EscapeViewToTask`, `StoreViewGlobal`, or returning `BatchView[T]` from `with_batch_view` |
| **`HYB-E6203`** | Epoch Generation & Immutability | `ReadView` with stale `view_epoch != arena_epoch` or `MutateView` on a pinned view |

## 3. $O(N)$ Handle-Per-Element (`hyrt`) vs $O(1)$ `ColumnarTape` Benchmark

| Document Size ($N$) | Legacy `hyrt` FFI Calls ($2N$) | Legacy `Mutex<Table>` Locks ($N$) | Legacy Handle Slots ($N$) | Legacy Est. Time (`758 ns/elem`) | `ColumnarTape` FFI Calls | `ColumnarTape` Arena Buffers | Per-Element `CString` Allocs |
|---|---|---|---|---|---|---|---|
| `N = 10` | `20` | `10` | `10` | `7.58 us` | **`1`** | **`1`** | **`0`** |
| `N = 256` | `512` | `256` | `256` | `194.05 us` | **`1`** | **`1`** | **`0`** |
| `N = 1,000` | `2,000` | `1,000` | `1,000` | `758.00 us` | **`1`** | **`1`** | **`0`** |
| `N = 10,000` | `20,000` | `10,000` | `10,000` | `7.58 ms` | **`1`** | **`1`** | **`0`** |

## Reproduce & Verify

- Module: `spikes/w061-zero-copy-columnar-ffi-arena/columnar_ffi_arena.py`
- Test Suite: `spikes/w061-zero-copy-columnar-ffi-arena/tests/test_columnar_ffi_arena.py`

```bash
python -B spikes/w061-zero-copy-columnar-ffi-arena/columnar_ffi_arena.py --check
python -B spikes/w061-zero-copy-columnar-ffi-arena/columnar_ffi_arena.py --json
```
