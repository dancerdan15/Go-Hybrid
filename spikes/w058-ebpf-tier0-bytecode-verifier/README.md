# W-058 (#1289) — eBPF-Style Abstract-Interpretation Bytecode Verifier for Tier-0

- **Ref:** #1289 (`[wildcard] eBPF-Style Abstract-Interpretation Bytecode Verifier for Tier-0: Unifying Ownership, Loans, Nullability, and Range Bounds in One Linear Pass`)
- **Original proposal author:** `@dancerdan15` (#1289)
- **Moonshot Alignment:** `MOONSHOT.md` Active Solicitation #2 (*"Replace the borrow-checker machinery"*) & #4 (*"Impossible guarantees"*).

## Premise & Live `HEAD` (`7d63d5f`) Audit: Fragmented `hy-check` AST Passes vs Unified Register Lattice
In `compiler/crates/hy-check/src/`, Tier-0 safety properties are split across **4 separate AST/statement passes** (`22,057` LOC across 11 files) that cannot share control-flow refinements:

| Safety Domain | Current `hy-check` Pass (`HEAD` `7d63d5f`) | Fragmented LOC | Unified `RegState` Component | Verified Diagnostics |
|---|---|---|---|---|
| **1. Ownership & Moves** | `freeze.rs` (`1,356`) + `sendability.rs` (`3,136`) + `walk.rs` (`857`) | `5,349` | `OwnershipState ∈ {OWNED, MOVED, SHARED, BORROWED}` | `HYB-E3001`, `HYB-E3003` |
| **2. Map-Slot & Borrow Loans** | `loans.rs` (`8,421`) + `addr_loan.rs` (`482`) | `8,903` | `LoanMask ∈ uint64` (bitwise loan-origin mask) | `HYB-E3102` (A3 slot invalidation) |
| **3. Pointer Nullability** | `shape.rs` (`1,520`) + `emit_guard.rs` (`2,533`) | `4,053` | `NullState ∈ {NON_NULL, MAYBE_NULL}` | `HYB-E4001` (nullable deref) |
| **4. Range & Arith Bounds** | `const_overflow.rs` (`1,499`) + `arith.rs` (`817`) + `range_iter.rs` (`469`) | `2,785` | `Interval[lo, hi]` with bounded widening $\nabla$ | `HYB-E5001`, `HYB-E5002` |
| **State & Join Machinery** | `model.rs` (`967`, 18 `FnState` maps + 220-line `scope_pop`) | `967` | Pointwise `join_states` ($\sqcup$) + back-edge $\nabla$ | $O(V + E)$ linear convergence |

## Mathematical Definition of the 4-Component `RegState` Product Lattice
Each register $r \in \{0, \dots, R-1\}$ carries a 4-tuple state in the finite-height product lattice $\mathcal{L}_{\text{reg}} = \mathcal{O} \times \mathcal{M} \times \mathcal{N} \times \mathcal{I}$, $\text{RegState}(r) = (\text{ownership}, \text{loan\_mask}, \text{nullability}, [lo, hi])$. At every CFG join point ($p_1 \to q, p_2 \to q$), states merge component-wise via `join_reg`:
1. **Ownership Join ($\sqcup_{\mathcal{O}}$):** $\text{OWNED} \sqcup \text{MOVED} = \text{MOVED}$ (any moving branch invalidates post-dominating uses); $\text{OWNED} \sqcup \text{BORROWED} = \text{BORROWED}$; $\text{OWNED} \sqcup \text{SHARED} = \text{SHARED}$.
2. **LoanMask Join ($\sqcup_{\mathcal{M}}$):** $m_1 \sqcup m_2 = m_1 \mathbin{|} m_2$ (may-loan bitmask union across incoming paths; `MutateMap` rejects iff `mask != 0` with `HYB-E3102`).
3. **Nullability Join ($\sqcup_{\mathcal{N}}$) & Refinement:** $\text{NON\_NULL} \sqcup \text{MAYBE\_NULL} = \text{MAYBE\_NULL}$; `GuardNonNull(r)` narrows $r \mapsto \text{NON\_NULL}$ along dominated edges.
4. **Interval Join ($\sqcup_{\mathcal{I}}$) & Bounded Widening ($\nabla_{\mathcal{I}}$):** Standard join $[ \min(lo_1, lo_2), \max(hi_1, hi_2) ]$; on loop back-edges ($s\_pc \le pc$), $\nabla_{\mathcal{I}}$ jumps decreasing lower bounds to $B_{\min}$ and increasing upper bounds to $B_{\max}$, bounding lattice chain height to $H(\mathcal{I}_{\nabla}) \le 2$ and guaranteeing $O(V + E)$ linear-time fixpoint termination.
5. **Cross-Domain Synergy (15 Ops):** Unlike isolated AST passes, a single `IndexSlice` or `MutateMap` instruction simultaneously checks ownership (`InitReg`, `MoveReg`, `UseReg`, `TaskSpawnMove`), live map loans (`BorrowMapSlot`, `ReleaseLoan`, `MutateMap`), nullability (`GuardNonNull`, `DerefReg`), and range bounds (`GuardRange`, `BinOpChecked`, `IndexSlice`, `BranchCond`, `Jump`, `Return`).

## Canonical Verification Matrix (`ebpf_tier0_verifier.py --check`)

| Scenario | Exercised Instruction & Lattice Transition | Expected | Verdict |
|---|---|---|---|
| `use_after_move` | `MoveReg(1, 0)` $\to$ `r0 = MOVED` $\to$ `UseReg(0)` | `HYB-E3001` | **REJECT (`HYB-E3001`)** |
| `shared_spawn_move` | `InitReg(0, SHARED)` $\to$ `TaskSpawnMove(0)` | `HYB-E3003` | **REJECT (`HYB-E3003`)** |
| `diamond_branch_move_join` | `BranchCond` moves `r0` on one arm; $\text{OWNED} \sqcup \text{MOVED} = \text{MOVED}$ at join | `HYB-E3001` | **REJECT (`HYB-E3001`)** |
| `map_slot_loan_invalidation` | `BorrowMapSlot(1, 0, "k")` sets `mask=0x2` $\to$ `MutateMap(0, "delete")` | `HYB-E3102` | **REJECT (`HYB-E3102`)** |
| `map_slot_loan_released_ok` | `BorrowMapSlot(1, 0, "k")` $\to$ `ReleaseLoan(1, 0)` clears bit $\to$ `MutateMap` | `ACCEPT` | **ACCEPT (`0` errors)** |
| `nullable_deref_unguarded` | `InitReg(0, MAYBE_NULL)` $\to$ `DerefReg(0)` without `GuardNonNull(0)` | `HYB-E4001` | **REJECT (`HYB-E4001`)** |
| `checked_i8_overflow` | `BinOpChecked(2, "+", 0, 1, 8)` on `[100, 120] + [10, 20] = [110, 140]` | `HYB-E5001` | **REJECT (`HYB-E5001`)** |
| `slice_oob_without_guard` | `IndexSlice(2, 0, 1, 16)` with `r1` interval `[0, 16]` ($\text{hi} \ge 16$) | `HYB-E5002` | **REJECT (`HYB-E5002`)** |
| `loop_widen_and_guarded_slice_ok` | Loop back-edge $\nabla_{\mathcal{I}}$ + `GuardNonNull(0)` + `GuardRange(1, 0, 7)` + `IndexSlice` | `ACCEPT` | **ACCEPT (`0` errors)** |

## Reproduction
```bash
python -B spikes/w058-ebpf-tier0-bytecode-verifier/ebpf_tier0_verifier.py --check
python -B spikes/w058-ebpf-tier0-bytecode-verifier/ebpf_tier0_verifier.py --json
python -B -m unittest discover -s spikes/w058-ebpf-tier0-bytecode-verifier/tests -p "test_*.py" -v
```
