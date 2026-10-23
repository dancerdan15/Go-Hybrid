# Spike W-056: Sub-Nanosecond Foreign Memory Isolation via Hardware Memory Protection Keys (`PKRU` / `POR_EL0`)

**Wildcard Proposal:** [#1278](https://github.com/limitlessprojects/Go-Hybrid/issues/1278) (`@dancerdan15`)  
**Assumptions Attacked:** [`GUARANTEES.md` Rows 14 & 15](../../GUARANTEES.md) — Row 14 (`unsafe` raw cross-language pointers) and Row 15 (`cgo` / foreign C/Rust libraries) currently carry **No claim** because software fault isolation (`Wasm`/`SFI`) adds `30–50%` overhead and `mprotect()` page-table syscalls cost `1,540.0 ns` per FFI transition.

## Mechanism & Formal Contract

Modern x86_64 (`WRPKRU`, Intel Skylake-SP+ / AMD Zen 3+) and ARM64 (`MSR POR_EL0`, ARMv8.9-A POE) toggle page-table protection keys (`PTE` bits `59..62`) in user space in **`1.20 ns` (~4 cycles)** without kernel transitions or TLB shootdowns:

1. **Three Hardware MPK Domains:**
   - `PKEY_GO_HEAP = 1`: All Go runtime heaps, goroutine stacks, and GC metadata (`AD|WD = 0b11` during foreign FFI calls).
   - `PKEY_FFI_SHARED = 2`: Explicitly borrowed `@ffi_shared` linear buffers (`WD = 0b10` read-only for `&T`; `ALLOW = 0b00` read-write for `&mut T`).
   - `PKEY_FOREIGN_PRIV = 3`: Foreign Rust/C private allocator pages (`ALLOW = 0b00` inside FFI; `AD|WD = 0b11` in Go runtime).
2. **Static & Binary Verifier (`MpkDomainVerifier` + `PkruHardwareEmulator`):**
   - **`HYB-E5601` (`UnisolatedGoHeapPointerEscapeToMpkFfi`):** Rejects direct or transitive `storage_component` re-slice escapes of `PKEY_GO_HEAP = 1` memory into `@mpk_isolated` `extern "C"` / `extern "rust"` calls.
   - **`HYB-E5602` (`UnauthorizedWrpkruGadgetInForeignBlob`):** Rejects foreign object blobs or IR containing unauthorized `0F 01 EF` (`WRPKRU`) or `MSR POR_EL0` gadgets outside compiler-emitted trampolines.
   - **`HYB-E5603` (`UnrestoredPkruStateOnUnwindOrReturn`):** Enforces restoration of `GO_RUNTIME_PKRU` across all normal return and panic/unwind CFG edges.

## Verification

```bash
python3 spikes/w056-mpk-hardware-domain-isolation/mpk_domain_verifier.py --check
python3 -m unittest discover -s spikes/w056-mpk-hardware-domain-isolation/tests -p "test_*.py" -v
```
