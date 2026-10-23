# Spike W-060: Compile-Time Lock-Order Partial Hierarchy (`sync.Mutex[L]`) and Must-Unlock Typestate

- **Ref #1291** — `[wildcard] Flip GUARANTEES Rows 8 & 16: Compile-Time Lock-Order Partial Hierarchy (sync.Mutex[L]) and Must-Unlock Typestate Across Try (?) Edges`
- **Original proposal author:** `@dancerdan15` (#1291)
- **Artifact scope:** `spikes/w060-static-lock-order-hierarchy/` (`lock_order_verifier.py`)

## Summary

Go-Hybrid currently requires `managed("…", "mutex")` escape hatches (`HYB-E8001`) for mutex-guarded state and makes no compile-time claim against AB-BA lock-order deadlocks or forgotten `Unlock()` calls across `?` early-return edges. This spike models each mutex as `sync.Mutex[L]` indexed by a compile-time lock level `L` in a partial-order DAG $(L_1 \prec L_2)$ and tracks linear `LockGuard[L]` values across all intra-procedural CFG edges (`?`, `return`, `break`, `if`/`else` joins) plus interprocedural `acquires: set[Level]` call summaries.

## `GUARANTEES.md` Claims Flipped (Rows 8 & 16)

| Row | Surface | Before (`main`) | After (`W-060` Checked Subset) | Evidence |
|-----|---------|-----------------|--------------------------------|----------|
| **Row 8** | Deadlock freedom & `sync.Mutex[L]` acquisition order (`managed("…", "mutex")` elimination) | **No soundness claim inside** `managed` mutex blocks; no deadlock-freedom claim | **Compile-time guaranteed** for `sync.Mutex[L]`: cycle-free partial order $(L_1 \prec L_2)$ enforced at every `AcquireLock` and across transitive `CallFn` summaries (`HYB-E6100`, `HYB-E6101`, `HYB-E6102`) | `LockHierarchy.validate()`, `LockOrderVerifier.compute_summaries()` |
| **Row 16** | Protocol ordering & forgotten `Unlock()` across `?` early returns / branches | **No claim** — liveness/protocol ordering outside Tier-0 | **Compile-time guaranteed**: linear `LockGuard[L]` typestate rejects any `?` (`TryEdge`), `return`, `break`, or `fn_exit` where `active_guards - defer_unlocked_guards != ∅` (`HYB-E6103`) and any asymmetric branch merge (`HYB-E6104`) | `LockOrderVerifier.verify_function()`, `--check` suite |

## Formal Invariants

1. **DAG Acyclicity:** The declared lock-level relation $\prec$ must form a strict partial order (DAG). Any cycle $L_0 \prec \dots \prec L_0$ is rejected at hierarchy validation (`HYB-E6100`).
2. **Strict Monotonic Acquisition:** Holding guard set $H$ at levels $\{L_h \mid h \in H\}$, acquiring `sync.Mutex[L_new]` is legal iff $\forall h \in H: L_h \prec L_{\text{new}}$.
   - If $\exists h \in H: L_h = L_{\text{new}}$, reject as same-level / re-entrant acquisition (`HYB-E6102`).
   - If $\exists h \in H: L_{\text{new}} \prec L_h$ or $L_h \parallel L_{\text{new}}$, reject as AB-BA inversion (`HYB-E6101`).
3. **Interprocedural Lock-Effect Summaries:** Every function $f$ has a least fixed-point summary $\text{acquires}(f) = \text{direct}(f) \cup \bigcup_{g \in \text{callees}(f)} \text{acquires}(g)$. Calling $g()$ while holding $L_h$ requires $\forall L_c \in \text{acquires}(g): L_h \prec L_c$ (`HYB-E6101`).
4. **Must-Unlock Linear Typestate & Branch Symmetry:**
   - Every exit edge (`TryEdge` `?`, `Return`, `Break`, `fn_exit`) requires $\text{active\_guards} \setminus \text{defer\_unlocked\_guards} = \emptyset$ (`HYB-E6103`).
   - Every control-flow join (`BranchJoin`) requires identical active and defer-unlocked guard sets across branches (`HYB-E6104`).

## Diagnostic Code Specification (`HYB-E6100..E6104`)

| Code | Condition | Diagnostic Class |
|------|-----------|------------------|
| `HYB-E6100` | Cycle in lock-level partial order $(L_1 \prec \dots \prec L_1)$ | `lock hierarchy cycle` |
| `HYB-E6101` | Direct or interprocedural acquisition where $L_{\text{held}} \not\prec L_{\text{new}}$ ($L_{\text{held}} \neq L_{\text{new}}$) | `AB-BA lock-order inversion` |
| `HYB-E6102` | Direct acquisition where $L_{\text{held}} == L_{\text{new}}$ (same-level or re-entrant mutex lock) | `re-entrant / same-level lock acquisition` |
| `HYB-E6103` | `active_guards - defer_unlocked_guards` non-empty at `?` (`TryEdge`), `return`, `break`, or exit | `unreleased lock across return or ? try edge` |
| `HYB-E6104` | Divergent `active_guards` or `defer_unlocked_guards` across `BranchJoin` arms | `asymmetric lock state across control-flow merge` |

## Verification

```bash
python -B spikes/w060-static-lock-order-hierarchy/lock_order_verifier.py --check
```

The `--check` flag validates:
- Cycle rejection (`HYB-E6100`) on `L1 -> L2 -> L1`,
- Monotonic acquisition + `DeferUnlock` across `?` (`TryEdge`) and symmetric `BranchJoin` arms,
- Direct/interprocedural inversions (`HYB-E6101`), same-level re-entrancy (`HYB-E6102`), `?` early-return guard leaks (`HYB-E6103`), and asymmetric branch merges (`HYB-E6104`).
