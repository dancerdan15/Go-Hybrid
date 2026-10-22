# W-026 (`#673` / `#610`): Proof-Carrying FFI Boundary Contracts (`hy-ffi-cert`) & N1/N2 Untyped-Nil Perimeter Verification

- **Target Issues:**
  - [#673 / #672 — N1 Untyped-Nil Local Declarations (`HYB-E4003`) & N1/N2 Foreign Perimeter Nil Homes](https://github.com/limitlessprojects/Go-Hybrid/pull/673)
  - [#610 — Wildcard W-026: Make FFI claims proof-carrying: verify boundary contracts against the linked Rust artifact](https://github.com/limitlessprojects/Go-Hybrid/issues/610)
- **Track:** Track 3 — Wildcard Track (`BOUNTY.md` / `MOONSHOT.md`) & Main Soundness Ladder (`GUARANTEES.md` Rows 4, 13, 19)
- **Base Commit:** `ec3fe0b769697be18e5bb9a59eb4faf0247ec485`
- **Spike Directory:** `spikes/w026-ffi-boundary-cert/`

---

## 1. Problem Statement & Root Cause in Current Architecture

Hybrid enforces strict compile-time guarantees inside `.hy` source code, but two boundary seams connect surface declarations to the foreign Go/Rust perimeter:

1. **Hand-Curated FFI Descriptor Trust Seam (`#610` / `W-026`):**
   - `interfaces/descriptors.md` asserts per-callee stack budgets (`SMALL <= 1024 B`, `DEFAULT <= 4096 B`, `DEEP` 1 MiB pool) as prose rather than machine-checked proofs over the compiled `runtime/rust/hyrt/src/lib.rs` artifact.
   - `compiler/crates/hy-check/src/foreign.rs` (`1,281` LoC) and `compiler/crates/hy-emit/src/shapes.rs` (`665` LoC) duplicate foreign call modes (`Produce`, `Borrow`, `Mutate`, `Consume`), argument arities, and result shapes as hard-coded tables.
   - If a `SMALL` callee acquires unbounded recursion or a large stack frame (such as `serde_json::from_str` with `remaining_depth = 128` or `regex_automata::meta::strategy::new` allocating `7,648 B` / `22,976 B`), or if a foreign export retains a borrowed pointer, introduces a callback edge, blocks on a mutex under a non-blocking fast path, or drifts from its `abiWordExpected` (`0x68795f61626931`) signature, hand-curated tables can agree with each other while lying about the linked binary.

2. **N1/N2 Nil-Home & Untyped-Nil Local Declaration Seam (`#673` / `#672`):**
   - By rule `N1`/`N2`, `nil` has exactly two valid homes in Hybrid: `error` and the declared foreign/FFI perimeter, both of which require an explicit declared type.
   - When a local declaration omits type ascription (`var x = nil`, `x := nil`, `let x = nil`, `a, b := nil, 1`, `var _ = nil`, or `if x := nil; cond`), an ascription-gated checker (`if let Some(t) = &l.ty` in `loans.rs::process_local` and `closures.rs::check_stmt_boundaries`) assigns `TypeShape::Unknown` and emits zero diagnostics, producing unbuildable Go (`use of untyped nil in variable declaration`, violating `GUARANTEES.md` Row 13 `BUILDABLE`).

---

## 2. Formal Specification of `hy-ffi-cert` & `HYB-E4003`

We replace unverified prose descriptors and ascription-gated nil checks with a single, deterministic verification pipeline:

```text
hyrt/src/lib.rs (19 extern "C" exports) ──► hy-ffi-cert Extractor & Call-Graph Closure
runtime/go/*binding/*.go (Go Brokers)   ──► 3-Way Contract Verifier & Safe Fallback Engine
.hy Local Declarations (N1/N2/N3)       ──► Unconditional Nil-Home & FFI Perimeter Gate
```

### 2.1 Proof-Carrying Boundary Contract Schema

For every `#[no_mangle] pub extern "C" fn` symbol $s$ extracted from `runtime/rust/hyrt/src/lib.rs`, `hy-ffi-cert` constructs a canonical contract tuple:

$$\mathcal{C}(s) = \langle \text{sym}, \vec{\tau}_{\text{in}}, \tau_{\text{ret}}, \text{mode}, \mathcal{E}_{\text{ret}}, \mathcal{E}_{\text{cb}}, \mathcal{E}_{\text{blk}}, B_{\text{frame}}, \text{rec}, \kappa_{\text{stack}}, h_{\text{sha256}} \rangle$$

where:
- $\vec{\tau}_{\text{in}}, \tau_{\text{ret}}$: Concrete C-ABI parameter and return types (`*const u8`, `*mut u8`, `u64`, `usize`, `i32`, `i64`).
- $\text{mode} \in \{\text{Produce}, \text{Borrow}, \text{Mutate}, \text{Consume}, \text{Safe}\}$: Ownership transfer mode across the boundary.
- $\mathcal{E}_{\text{ret}}, \mathcal{E}_{\text{cb}}, \mathcal{E}_{\text{blk}} \in \{\text{true}, \text{false}\}$: Static effect flags for pointer retention (`may_retain`), Go re-entrancy (`may_callback`), and synchronization blocking (`may_block`).
- $B_{\text{frame}} \in \mathbb{N}$: Worst-case closed call-graph stack frame size in bytes, with $\text{rec} \in \{\text{true}, \text{false}\}$ marking direct/mutual recursion or input-proportional tree walks.

### 2.2 Automatic Safe Fallback & Fail-Closed Admission Rules

Instead of trusting curated annotations, `hy-ffi-cert` enforces five mechanical admission rules:

1. **Compositional Stack Closure (`HYB-E7002`):**
   $$\kappa_{\text{stack}}(s) = \begin{cases} \text{DEEP} & \text{if } \text{rec}(s) \lor \text{indirect}(s) \lor B_{\text{frame}}(s) > 4096 \\ \text{DEFAULT} & \text{if } \neg\text{rec}(s) \land 1024 < B_{\text{frame}}(s) \le 4096 \\ \text{SMALL} & \text{if } \neg\text{rec}(s) \land B_{\text{frame}}(s) \le 1024 \end{cases}$$
   Any descriptor claiming `SMALL` (`Cross*S`) or `DEFAULT` (`Cross*`) when $\kappa_{\text{stack}}(s) = \text{DEEP}$ is rejected with `HYB-E7002` and downgraded to `DEEP` (`CrossDeep*`).
2. **No-Retain Pinned-Borrow Rule (`HYB-E7003`):**
   If $\mathcal{E}_{\text{ret}}(s) = \text{true}$, passing a Go-pinned pointer (`runtime.Pinner`) is rejected with `HYB-E7003`; the boundary must use generation-tagged `OwnHandle` (`(gen << 32) | (idx + 1)`) or copy semantics.
3. **No-Callback Safe-Mode Rule (`HYB-E7004`):**
   If $\mathcal{E}_{\text{cb}}(s) = \text{true}$, the export is rejected in safe mode (`HYB-E7004`) because re-entering Go from a trampoline stack corrupts `g.stackguard0`.
4. **Blocking Effect Admission (`HYB-E7005`):**
   If $\mathcal{E}_{\text{blk}}(s) = \text{true}$ on a non-blocking fast-path trampoline without scheduler handoff, `hy-ffi-cert` rejects the fast-path claim (`HYB-E7005`).
5. **3-Way Signature & Hash Handshake (`HYB-E7001` / `HYB-E7006`):**
   Every symbol consumed by `runtime/go/{sha3binding,jsonbinding,regexbinding}/broker.go` must match $\mathcal{C}(s)$ in arity, C-ABI word width, trampoline class, and embedded SHA-256 contract digest (`abiWordExpected = 0x68795f61626931` + per-export digest).

### 2.3 N1/N2 Untyped-Nil Local Declaration Gate (`HYB-E4003`)

For every local binding statement $S \in \{\text{var}, \text{let}, \text{:=}, \text{if-init}\}$ in both the flat function walk (`loans.rs::process_local`) and the closure/block walk (`closures.rs::check_stmt_boundaries`):
- If $S$ has type ascription $\tau$: validate `nil` against $\tau$ under `HYB-E4001` (`error` and certified N2 foreign types accept; slices/maps/channels/structs reject `HYB-E4001`).
- If $S$ has **no type ascription** ($\tau = \bot$) and any RHS initializer expression is bare `nil` (including blank `var _ = nil` and multi-value `a, b := nil, 1`): unconditionally emit **`HYB-E4003`** (`untyped-nil-declaration`, rule `N`, tier0 `N1`) with three deterministic fix routes:
  1. Ascribe the N1/N2 nil home (`var e error = nil`);
  2. Declare without initializer for the N3 zero value (`var ch chan int`);
  3. Use `option[T]` (`none`) to represent absence.
- Local `const c = nil` remains exclusively under `HYB-E6003` (`nil is not constant`, issue `#640`), preventing double-firing.
