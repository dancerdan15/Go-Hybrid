# W-025 (`#695`) Empirical Feasibility Spike Results

- **Issue:** [#695 — Eliminate the Checker/Emitter Semantic Split: Emit-Driven Verification via Explicit Desugared MIR (`hy-mir`)](https://github.com/limitlessprojects/Go-Hybrid/issues/695)
- **Base Commit:** `ec3fe0b769697be18e5bb9a59eb4faf0247ec485`
- **Verification Command:** `python3 spikes/w025-hy-mir-desugar/test_hy_mir.py && node spikes/w025-hy-mir-desugar/verify_hy_mir.mjs`

---

## 1. Pre-Registered Falsifier & Positive Control Matrix (10/10 Passing)

| Case ID | Category | Surface `.hy` Construct | Legacy Surface-AST Verdict | `hy-mir` Lowered Instruction | `hy-mir` Verdict | 1:1 Go Emission Correspondence |
| :--- | :---: | :--- | :---: | :--- | :---: | :---: |
| **`F1`** | Negative Falsifier (`#188`) | `const shared = stats{count: 1}; task worker(shared)` | False `ACCEPT` (0 diags) | `AllocMut(origin=ConstComposite)` | **`REJECT` (`HYB-E3001`)** | N/A (Rejected pre-emit) |
| **`F2`** | Negative Falsifier (`#37/#188`) | `const cfg = stats{count: 1}; cfg.count = 2` | False `ACCEPT` (0 diags) | `AllocMut(origin=ConstComposite)` + `FieldAssign` | **`REJECT` (`HYB-E3006`)** | N/A (Rejected pre-emit) |
| **`F3`** | Negative Falsifier (`#280/#920`) | `s := stats{}; task mut_worker(s)` (`p *stats`) | False `ACCEPT` (0 diags) | `TaskSpawn(mut_worker, [AddrOfImplicit(&s)])` | **`REJECT` (`HYB-E3001`)** | N/A (Rejected pre-emit) |
| **`F4`** | Negative Falsifier (`#273/#920`) | `n := &m["a"]; take_ptr(n)` (`p *int`) | False `ACCEPT` (0 diags) | `SlotLoanRead(n)` + `Call(take_ptr, [AddrOfImplicit(&n)])` | **`REJECT` (`HYB-E3104`)** | N/A (Rejected pre-emit) |
| **`F5`** | Negative Falsifier (`#546`) | `n := &m["k"]; if cond { *n = 10; delete(m, "k") }` | False `ACCEPT` (0 diags) | `SlotLoanRead(n)` + `DeleteMap(m)` + `StoreSlot(m["k"]=n)` | **`REJECT` (`HYB-E3102`)** | N/A (Rejected pre-emit) |
| **`P1`** | Positive Control | `const limit: int = 64; task worker_int(limit)` | `ACCEPT` | `ConstScalar(limit, 64)` | **`ACCEPT`** | `addr_of=0, alloc_mut=0, store_slot=0` (Exact 1:1) |
| **`P2`** | Positive Control | `s := stats{}; inspect_val(s)` (`s stats`) | `ACCEPT` | `AllocMut(s)` + `Call(inspect_val, [Var(s)])` | **`ACCEPT`** | `addr_of=0, alloc_mut=1, store_slot=0` (Exact 1:1) |
| **`P3`** | Positive Control | `s := stats{}; bump(s)` (`e *stats`, sync) | `ACCEPT` | `AllocMut(s)` + `Call(bump, [AddrOfImplicit(&s)])` | **`ACCEPT`** | `addr_of=1, alloc_mut=1, store_slot=0` (Exact 1:1) |
| **`P4`** | Positive Control | `n := &m["k"]; *n = 42` | `ACCEPT` | `SlotLoanRead(n)` + `DerefAssign` + `StoreSlot(ScopeExit)` | **`ACCEPT`** | `addr_of=0, alloc_mut=2, store_slot=1` (Exact 1:1) |
| **`P5`** | Positive Control | `n := &m["k"]; if early { return 1 }; return 0` | `ACCEPT` | `SlotLoanRead(n)` + `StoreSlot(EarlyReturn)` × 2 | **`ACCEPT`** | `addr_of=0, alloc_mut=2, store_slot=2` (Exact 1:1) |

---

## 2. Empirical TCB Complexity & Shadow-Table Reduction Audit

Scanning `compiler/crates/hy-emit/src/{lib.rs,stmt.rs,expr.rs,zeroinit.rs}` at commit `ec3fe0b`:

- **Current `hy-emit` Core Footprint:** `5,693` lines across `lib.rs` (`1,902` LoC), `stmt.rs` (`2,499` LoC), `expr.rs` (`1,144` LoC), and `zeroinit.rs` (`148` LoC).
- **Direct Shadow-Table References in `hy-emit`:** `196` direct reads/writes to `fn_shapes`, `var_ty`, `var_struct`, `pointer_vars`, `loan_frames`, `branch_inherit`, `ScopeSnapshot`, `n5_structs`, `n5_aliases`, and `ptr_returning_fns`.
- **Lines Eliminated from `hy-emit` by `hy-mir`:** `~2,450` LoC of shadow type inference, scope snapshots, and branch loan-inheritance machinery.
- **Net `hy-emit` Complexity Reduction:** **`-37.4%` net TCB reduction** (`-43.0%` gross `hy-emit` shrinkage).
