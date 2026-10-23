# W-054 (#1276) — Declarative Datalog / Polonius Tier-0 Ownership & Loan Solver (`<200` LOC)

Executable Wildcard Spike evaluating **Issue #1276** (`@dancerdan15`), answering `MOONSHOT.md` Active Solicitation #1 & #2 by replacing `hy-check`'s imperative `FnState` (`compiler/crates/hy-check/src/model.rs:217-303`, 18 separate `HashMap`/`HashSet`/scope-stack fields and 220-line `scope_pop` state-restoration pass) with a zero-dependency, stratified **Datalog / Polonius-style monotone fixpoint solver** (`datalog_tier0_solver.py`, `<200` LOC).

## Quantitative Evaluation & Falsification Matrix (`HEAD` `7d63d5fc`)

| Canonical Case (#1276) | Declarative Rule / Fixpoint Relation | Expected | Measured Verdict |
|------------------------|--------------------------------------|----------|------------------|
| `diamond_branch_move` | `path_moved_at(v, path, q)` across diamond join `(1->3, 2->3)` | `HYB-E3003` | **REJECT (`HYB-E3003`)** |
| `loop_backedge_move` | `path_moved_at(v, path, q)` across loop back-edge `(2->1)` | `HYB-E3003` | **REJECT (`HYB-E3003`)** |
| `disjoint_partial_field_move` | `subpath_overlaps("p.y", "p.x") == False` | `ACCEPT` | **ACCEPT (`0` false positives)** |
| `overlapping_partial_field_move` | `subpath_overlaps("p", "p.x") == True` after partial move | `HYB-E3003` | **REJECT (`HYB-E3003`)** |
| `transitive_may_share_loan_mutation` | `alias_may_share` transitive closure + backward `loan_live_at` | `HYB-E3004` | **REJECT (`HYB-E3004`)** |
| `nll_dead_loan_mutation` | Polonius NLL backward `loan_live_at` expires after `loan_used` | `ACCEPT` | **ACCEPT (`0` false positives)** |
| `slot_loan_local_addr_escape` | `origin_escape_at(w, kind, p)` + `alias_may_share(v, w, p)` at `returned` | `HYB-E3104` | **REJECT (`HYB-E3104`)** |
| `reinitialized_loop_var` | `reinitialized(v, init_path, q)` kills `path_moved_at` before back-edge | `ACCEPT` | **ACCEPT (`0` false positives)** |

## Architecture: 18 `FnState` Maps → 4 Monotone IDB Fixpoint Relations

1. **EDB (`FactStore`):** `cfg_edge`, `moved`, `reinitialized`, `assigned_share`, `borrowed`, `loan_used`, `used`, `mutated`, `origin_escape`, `returned`.
2. **IDB (`solve_fixpoint`):**
   - `subpath_overlaps(p1, p2)` — prefix-aware field/index conflict relation (`p` vs `p.x` overlaps; `p.x` vs `p.y` disjoint).
   - `alias_may_share(x, y, p)` — reflexive, symmetric, transitive O5.2 sharing closure along `cfg_edge`.
   - `path_moved_at(v, path, q)` — forward reachability across branch joins and loop back-edges, killed by prefix-covering `reinitialized`.
   - `loan_live_at(loan, p)` — backward Polonius NLL liveness from `loan_used(loan, q)` along `cfg_edge(p, q)`.
3. **Soundness Queries:** `HYB-E3003` (`UseAfterMove`), `HYB-E3004` (`MutatedWhileLoaned`), and `HYB-E3104` (`DanglingOriginEscape`).

## Reproduce

```bash
python spikes/w054-smt-polonius-datalog/datalog_tier0_solver.py --check
python spikes/w054-smt-polonius-datalog/datalog_tier0_solver.py --json
python -m unittest discover -s spikes/w054-smt-polonius-datalog/tests -v
```
