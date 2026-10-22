# W-026 (`#673` / `#610`) Empirical Feasibility Spike Results

- **Issues Covered:**
  - [#673 — `fix(hy-check): reject untyped nil local declarations (var x = nil / x := nil / let x = nil) with HYB-E4003`](https://github.com/limitlessprojects/Go-Hybrid/issues/673) (closes `#672`)
  - [#610 — `[wildcard] Make FFI claims proof-carrying: verify boundary contracts against the linked Rust artifact` (`W-026`)](https://github.com/limitlessprojects/Go-Hybrid/issues/610)
- **Base Commit:** `ec3fe0b769697be18e5bb9a59eb4faf0247ec485`
- **Verification Command:** `python3 spikes/w026-ffi-boundary-cert/test_hy_ffi_cert.py && node spikes/w026-ffi-boundary-cert/verify_hy_ffi_cert.mjs`

---

## 1. Pre-Registered Falsifier & Positive Control Matrix (10/10 Passing)

| Case ID | Category | Surface `.hy` / Boundary Mutation | Legacy Baseline Verdict | `hy-ffi-cert` + `HYB-E4003` Verdict | Verified Contract Invariant |
| :--- | :---: | :--- | :---: | :---: | :--- |
| **`F1`** | Negative Falsifier (`#673/#672`) | `var ch = nil; x := nil; let y = nil; a, b := nil, 1; var _ = nil; g := fn() { z := nil }` | False `ACCEPT` (0 diags) | **`REJECT` (`HYB-E4003` × 6)** | N1/N2 untyped-nil local declarations rejected across flat & closure walks |
| **`F2`** | Negative Falsifier (`W-026`) | `jsonbinding.FnParse` routed via `Cross3S` (`SMALL <= 1 KiB`) | False `ACCEPT` (0 diags) | **`REJECT` (`HYB-E7002`)** | Recursive `serde_json::from_slice` closed call graph requires `DEEP` |
| **`F3`** | Negative Falsifier (`W-026`) | `regexbinding.FnCompile` routed via `Cross3` (`DEFAULT <= 4 KiB`) | False `ACCEPT` (0 diags) | **`REJECT` (`HYB-E7002`)** | `regex_syntax` + NFA compiler (`22,528 B` frame) requires `DEEP` |
| **`F4`** | Negative Falsifier (`W-026`) | `hy_sha3_256` mutated to `may_retain_ptr=true` + `may_callback_go=true` | False `ACCEPT` (0 diags) | **`REJECT` (`HYB-E7003`, `HYB-E7004`)** | Pinned-borrow retention & Rust->Go callbacks rejected pre-link |
| **`F5`** | Negative Falsifier (`W-026`) | `sha3binding.FnSha3_256` arity/ABI word drift (`c_arity=2`, `0x68795f61626930`) | False `ACCEPT` (0 diags) | **`REJECT` (`HYB-E7001`)** | 3-way `.hy_ffi_cert` SHA-256 & ABI handshake mismatch caught pre-link |
| **`P1`** | Positive Control | `sha3binding` (`hy_sha3_256`, `hy_sha3_512`, `hy_shake256`, streaming `Hasher`) | `ACCEPT` | **`ACCEPT`** | All 6 `sha3` exports certified `SMALL` (`<= 512 B` frame, non-recursive) |
| **`P2`** | Positive Control | `jsonbinding` (`FnType`/`FnGet*`/`FnLen`/`FnFree` `SMALL`; `FnParse`/`FnMarshalIndent` `DEEP`) | `ACCEPT` | **`ACCEPT`** | Mixed `SMALL` leaf / `DEEP` recursive boundary split certified |
| **`P3`** | Positive Control | `regexbinding` (`FnCompile`/`FnIsMatch`/`FnFind`/`FnReplaceAll` `DEEP`; `FnFree` `SMALL`) | `ACCEPT` | **`ACCEPT`** | All 4 DFA/NFA exports routed through `CrossDeep*` (`1 MiB` pool) |
| **`P4`** | Positive Control (`#673`) | `var e error = nil; var v serde_json.value = nil; var ch chan int` | `ACCEPT` | **`ACCEPT`** | N1 ascribed `error`, N2 nilable handle, and N3 zero-value `var` accepted |
| **`P5`** | Positive Control (`#673/#640`) | `const c = nil` (`HYB-E6003` single-locus control) + `var s []int = nil` (`HYB-E4001`) | `ACCEPT` (`0` `E4003`) | **`ACCEPT` (`0` `E4003`)** | Single-locus rule preserved: `const c = nil` fires only `HYB-E6003` |

---

## 2. Live `hyrt` Artifact Extraction & TCB Reduction Audit

Scanning `runtime/rust/hyrt/src/lib.rs`, `runtime/go/{sha3binding,jsonbinding,regexbinding}/*.go`, `compiler/crates/hy-check/src/foreign.rs`, `compiler/crates/hy-emit/src/shapes.rs`, and `interfaces/descriptors.md` at commit `ec3fe0b`:

- **Extracted `#[no_mangle] pub extern "C" fn` Symbols:** `21` exports (`19` bound in Go broker packages + `hy_abi_version` and `hy_handle_table_count`), deterministic bundle SHA-256 `2d8397a849aa6ee4...`.
- **Current Manual Boundary TCB Footprint:** `2,035` lines across `foreign.rs` (`583` LoC), `shapes.rs` (`455` LoC), and `descriptors.md` (`997` LoC).
- **Manual Descriptor Lines Eliminated by `hy-ffi-cert` Generation:** `-785` manual lines (`38.6%` TCB reduction).
