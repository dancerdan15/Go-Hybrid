# W-036 (#958) — Certified Foreign Function Boundary: DWARF CFI & Assembly Translation Validation

Executable Wildcard Spike evaluating **Issue #958**: compile-time verification of ABI safety, stack budgets (`SMALL` ≤ 1 KiB, `DEFAULT` ≤ 4 KiB, `DEEP` 1 MiB pool), callee-saved/goroutine register preservation (`R14`/`R28` `g`), and pointer-escape discipline across C/Rust (`libhyrt.a`) and Go assembly (`runtime/go/hybridge/call_*.s`) boundaries using DWARF `.eh_frame` Call Frame Information (CFI) and static call-graph reachability.

## Quantitative Evaluation & Falsification Matrix (`HEAD` `7d63d5fc`)

| Falsifier / Criterion (#958) | Threshold | Measured Result | Verdict |
|------------------------------|-----------|-----------------|---------|
| **F1: Non-Recursive Stack Boundability** | `> 90.0%` of non-recursive C/Rust/asm routines | **93.33% (`14 / 15`)** bounded via `.eh_frame` CFA (`DW_CFA_def_cfa` / `DW_CFA_def_cfa_offset`) + DAG reachability; only dynamic `alloca`/VLA (`c_vla_scratch_kernel`) requires `DEEP` | **SURVIVES** |
| **F2: Compile-Time Validation Latency** | `< 500.0 ms` per package build | **~1.5 ms** (20-routine CFI corpus + live audit of 18 `descriptors.md` entries & 39 `call_*.s` `NOSPLIT` trampolines) | **SURVIVES (300× under budget)** |
| **F3: Hazard Rejection (`HYB-E6030`)** | Reject budget overflow, recursion, `g`/callee-saved clobber, unpinned Go ptr | **100% rejected** (`STACK_BUDGET_EXCEEDED` on 7,648 B `regex_automata::meta::strategy::new` fast path, `RECURSIVE_UNBOUNDED` on `serde_json::from_str`, `G_REG_CLOBBER(R14/R28)`, `CALLEE_SAVED_CLOBBER(RBX)`, `SP_MISALIGNED`, `GO_PTR_ESCAPE`) | **PROVEN** |

## Corpus & Live `runtime/` Audit Summary

1. **DWARF `.eh_frame` + Call-Graph Reachability (`cfi_bridge_verifier.py`):**
   - Decodes ULEB128-encoded `.eh_frame` FDE byte streams (`DW_CFA_advance_loc`, `DW_CFA_def_cfa`, `DW_CFA_def_cfa_offset`, `DW_CFA_offset`, `DW_CFA_restore`) to extract exact per-frame CFA bounds and saved register masks (`RBX`, `RBP`, `R12`–`R15`, `R28`).
   - Solves worst-case stack depth over the static call graph (`hy_sha3_digest256 → keccak_f1600 = 520 B ≤ 1,024 B SMALL`; `hy_regex_is_match → meta_strategy_new = 22,976 B > 4,096 B → DEEP`) and flags cycle reachability (`hy_json_parse` recursion) as `HYB-E6030:RECURSIVE_UNBOUNDED` unless routed to `CrossDeep*`.
2. **Live `interfaces/descriptors.md` & `runtime/go/hybridge/call_{amd64,arm64,win64}.s` Verification:**
   - Audits all **18** curated functions in `interfaces/descriptors.md` and **39** `TEXT ·*(SB), NOSPLIT` assembly trampolines across `call_amd64.s`, `call_arm64.s`, and `call_win64.s`, confirming `$1024` (`callFast*S`) vs `$4096` (`callFast*`) `OFF_g_stackguard0` headroom guards and zero `R14`/`R28`/`g` register clobbers (`asm_hazards = []`).

## Reproduce

```bash
python spikes/w036-cgo-asm-translation-val/cfi_bridge_verifier.py --check
python spikes/w036-cgo-asm-translation-val/cfi_bridge_verifier.py --json
```
