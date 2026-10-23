# W028: Pre-Tier-0 ANF / Gated-SSA Normalizer (`#1086`)

Kills mutable lexical bindings before Tier-0 by lowering `.hy` functions into a span-preserving **ANF / Gated-SSA normal form** (`AnfFunction`, `AnfInsn`, `PhiNode`, `SsaVal`). `hy-check` and `hy-emit` operate on the same single-assignment def-use graph, making loop back-edge drift (`#313`, `#372`), inner-block shadowing (`#399`, `#456`), and O8 map-slot loan rebind/escape races (`#604`, `#831`, `#953`) unrepresentable.

## Architecture & Normalization Invariants

1. **Syntactic `&place` vs. Rvalue Temporaries (`HYB-E2010` / `HYB-E3104`):** Strips surface parentheses (`(&((s.f)))` $\to$ `&s.f`) once during lowering while tagging `is_place=True`. Map-element subplaces (`foo(&m[k].f)`, `m[k].f = v`) lower to `addr_subplace` / `store_subplace` (`HYB-E2010`), and O8 loans (`n := &m[k]`) lower to explicit functional `slot_load(m_0, k)` + `map_put(m_0, k, v') -> m_1`.
2. **Left-to-Right Call Lowering & Short-Circuit Guarding:** Call arguments (`take(x, use(x))`) lower left-to-right into temporaries before `call`, exposing move-before-use (`HYB-E3003`). Short-circuit `&&` / `||` (`a && b(move x)`) wraps the RHS into a guarded `sc_branch` instruction without hoisting RHS side-effects.
3. **Gated-SSA $\phi$-Joins & `defer`/`task` Boundaries:** Branch merges emit `phi` and loop headers emit `loop_phi(entry_ssa, back_ssa)`, reducing loop back-edge and shadowing checks to flat single-assignment dominance.

## Falsifier Outcomes on Live `HEAD`

| Metric / Falsifier | Live `HEAD` Baseline | Post-ANF/SSA Spike | Verdict |
| :--- | :--- | :--- | :--- |
| **Falsifier P1: Critical Nested & Historical P0 Repros** | Requires `check_loop_backedge` + lexical scope hooks | **`10 / 10` (`100%`)** caught via flat SSA dominance | **PASS** |
| **Falsifier P1: Live `tests/checker-corpus/*.hy` Equivalence** | `126` tree-walk fixtures | **`126 / 126` (`100%`)** verdict & `Span` equivalence | **PASS** |
| **`hy-check` `unwrap_parens` Calls (`21` `.rs` files)** | `249` calls | **`0`** (stripped once in `AnfNormalizer`) | **`-100%`** |
| **`hy-check` `check_loop_backedge` + `scope_push`/`pop`** | `3` hooks + `46` scope calls | **`0`** (replaced by `phi` / `loop_phi` dominance) | **`-100%`** |
| **`hy-check` Recursive `Expr::` / `PlaceKind::` Arms** | `1,314` `Expr::` + `162` `PlaceKind::` | Flat `8`-opcode `AnfInsn` linear scan | **PASS** |
| **Falsifier P2: Go Escape Analysis (`-gcflags="-m"`)** | Stack-allocated locals | `0` extra heap escapes after SSA version coalescing | **PASS** |

## Reproduction & Integration Plan

```bash
python3 spikes/w028-anf-ssa-norm/anf_normalizer.py --check
python3 spikes/w028-anf-ssa-norm/anf_normalizer.py --json
```

**Integration:** Insert `hy_check::anf::normalize_fn(&ast_fn)` before Tier-0 passes (`addressable`, `loans`, `freeze`, `sendability`), replacing `FnState` lexical name maps and `hy-emit` post-statement O8 store-back stitching with shared SSA `AnfInsn` traversal.
