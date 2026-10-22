# W-025 (`#695`): Eliminate the Checker/Emitter Semantic Split via Explicit Desugared MIR (`hy-mir`)

- **Wildcard Proposal:** [#695 — Eliminate the Checker/Emitter Semantic Split: Emit-Driven Verification via Explicit Desugared MIR (`hy-mir`)](https://github.com/limitlessprojects/Go-Hybrid/issues/695)
- **Track:** Track 3 — Wildcard Track (`BOUNTY.md` / `MOONSHOT.md`)
- **Base Commit:** `ec3fe0b769697be18e5bb9a59eb4faf0247ec485`
- **Spike Directory:** `spikes/w025-hy-mir-desugar/`

---

## 1. Problem Statement & Root Cause in Current Architecture

Today, Hybrid compiles `.hy` source through a dual-interpretation pipeline:

```text
hy-syntax (Surface AST) ──► hy-check (Tier-0 Verifier)
                        └──► hy-emit  (Stateful Go Lowering + Shadow Type Tables)
```

Because `hy-check` validates ownership, sendability, and borrow windows over raw `hy-syntax` surface AST nodes while `hy-emit` independently reconstructs types and rewrites operational semantics during Go code generation, any semantic rewrite performed by `hy-emit` after `hy-check` creates a **Checker/Emitter Impedance Soundness Seam**:

1. **Composite `const` -> Mutable `var` Lowering (`#188` / `T22`):**
   Go rejects `const` for composite literals (`struct`, `map`, `slice`), so `hy-emit` lowers `const s = stats{count: 1}` into a mutable Go `var s = stats{count: 1}` allocation (`zeroinit.rs` / `stmt.rs`). A naive surface-AST pass sees a `Decl::Const` node and grants it the class-4 immutability exemption across `task` boundaries.
2. **Implicit `&` Auto-Addressing at Pointer Parameters (`#280` / `#920`):**
   `Emitter::fn_shapes` and `Emitter::pointer_vars` auto-address value arguments `s` into `&s` when calling `fn worker(p *stats)`. On the surface AST, the call site is `worker(s)` (by value), hiding the address-of borrow `&s` from surface syntax rules unless duplicated in `hy-check`.
3. **Map-Slot Loan Desugaring & Branch Store-Backs (`#546` / `O8`):**
   Go forbids taking the address of a map index (`&m[k]`). `hy-emit` lowers `n := &m[k]` into a copy-read (`n := m[k]`) plus deferred `m[k] = n` store-backs tracked in `Emitter::loan_frames` and `Emitter::branch_inherit`. Whenever control-flow flushing in `hy-emit` diverges from `hy-check`'s lexical loan window, stale reads or mid-loan `delete(m, k)` races occur.

---

## 2. The `hy-mir` Architecture

We replace the dual-interpretation split with a single, explicit, desugared Mid-level Intermediate Representation (`hy-mir`) placed **before** `hy-check`:

```text
hy-syntax (AST) ──► hy-mir (Explicit Lowering) ──► hy-check (MIR Verifier) ──► hy-emit (1:1 Total Printer)
```

### 2.1 Core `hy-mir` Instructions

$$\begin{aligned}
\text{MirDecl} &::= \text{Struct}(\text{name}, \vec{f}) \mid \text{Fn}(\text{name}, \vec{p}, \vec{r}, \text{MirBlock}) \\
\text{MirStmt} &::= \text{ConstScalar}(\text{name}, \tau, e, \text{span}) \\
               &\;\mid\; \text{AllocMut}(\text{name}, \tau, e, \text{origin} \in \{\text{Let}, \text{Var}, \text{ConstComposite}\}, \text{span}) \\
               &\;\mid\; \text{SlotLoanRead}(\text{loan\_var}, \text{map\_var}, e_{\text{key}}, \text{span}) \\
               &\;\mid\; \text{StoreSlot}(\text{map\_var}, e_{\text{key}}, \text{loan\_var}, \text{reason} \in \{\text{ScopeExit}, \text{BranchExit}, \text{EarlyReturn}\}, \text{span}) \\
               &\;\mid\; \text{Assign}(lhs, rhs, \text{span}) \mid \text{DeleteMap}(\text{map\_var}, e_{\text{key}}, \text{span}) \\
               &\;\mid\; \text{TaskSpawn}(\text{callee}, \vec{a}, \text{span}) \mid \text{CallStmt}(\text{callee}, \vec{a}, \text{span}) \\
               &\;\mid\; \text{If}(e_{\text{cond}}, \text{MirBlock}_{\text{then}}, \text{MirBlock}_{\text{else}}, \text{span}) \mid \text{Return}(\vec{e}, \text{span}) \\
\text{MirExpr} &::= \text{Var}(\text{name}) \mid \text{Lit}(v) \mid \text{StructLit}(\text{ty}, \vec{kv}) \mid \text{MakeMap}(k, v) \\
               &\;\mid\; \text{AddrOfExplicit}(e, \text{span}) \mid \text{AddrOfImplicit}(e, \text{target\_param}, \text{span}) \\
               &\;\mid\; \text{Deref}(e, \text{span}) \mid \text{Move}(e, \text{span})
\end{aligned}$$

### 2.2 Falsifiable Invariant (1:1 Emission Correspondence)

Every `&` (address-of), mutable `var` / `:=` allocation, and map-slot writeback emitted in the target Go source corresponds **1-for-1** to an explicit `AddrOfExplicit` / `AddrOfImplicit`, `AllocMut` / `SlotLoanRead`, or `StoreSlot` node in `hy-mir` that has already passed `hy-check`.

`hy-emit` drops all five shadow type/loan tables (`fn_shapes`, `var_ty`, `var_struct`, `pointer_vars`, `loan_frames` / `branch_inherit`), shrinking `hy-emit`'s stateful lowering footprint by **>35%**.
