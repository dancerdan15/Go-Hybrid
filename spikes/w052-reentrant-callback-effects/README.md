# Spike W-052 — Effect-Polymorphic Foreign Callback Contracts (`GUARANTEES.md` Row 9 Flip)

- **Wildcard Proposal Issue:** [#1256](https://github.com/limitlessprojects/Go-Hybrid/issues/1256)
- **Assumption Attacked:** `GUARANTEES.md` Row 9 (`Values escaping into Go APIs: net/http handlers, callbacks — No checker claim`), `SPEC-LANGUAGE.md §8.5 C9`, `§9.1 O10`, `§9.2`, `§9.3 O4.10`, `OQ1`.
- **Verdict:** **PROVEN (3/3 pre-registered falsifiers `F1`–`F3` verified + 10/10 live `examples/` clean + `10-a5-http-handler-capture.hy` flipped to `HYB-E5202`)**.

## Formal Contract: Two-Modality `#[callback(SyncScoped | ConcurrentMulti)]`

Instead of leaving foreign Go callbacks unchecked (`§8.5 C9`) or forcing `managed` noise on synchronous stdlib iterators (`W-016`), higher-order foreign API descriptors carry a two-modality effect contract:

1. **`#[callback(SyncScoped(subject_arg))]`** (`sort.slice`, `filepath.walk`, `bytes.map`):
   - Invoked synchronously on the caller's goroutine `0..N` times before returning; never retained.
   - Disjoint local accumulators (`var comps int`) may be mutated freely without `managed`.
   - The traversed container `subject_arg` (and any alias sharing its storage root) acquires a **reentrant iteration freeze loan** for the callback body. Mutating the frozen container inside the callback is rejected with **`HYB-E5201` (`CallbackReentrantContainerMutation`)**.
2. **`#[callback(ConcurrentMulti)]`** (`http.handle_func`, `http.listen_and_serve`, `time.after_func`, `mux.handle`, `http.handler`):
   - Retained and invoked concurrently across goroutines `0..N` times.
   - Captures of immutable bindings (`let`, `const`, `string`, primitives) and safe-by-type channels (`chan T`, `O4` class 2) are accepted without `managed` (preserving `examples/06-http-server/program.hy`).
   - Capturing mutable GC state (`map`, `slice`, pointer) or mutating an outer `var` is rejected with **`HYB-E5202` (`ConcurrentCallbackMutableCapture`)** (flipping `tests/checker-corpus/10-a5-http-handler-capture.hy` `hits++` from `ACCEPT (CONTESTED)` to `REJECT`).
3. **Rule FC Fail-Closed Default (`HYB-E5203`)**:
   - Any closure passed to an unannotated foreign function without a `#[callback(...)]` contract is rejected fail-closed with **`HYB-E5203` (`UnannotatedForeignCallbackEscape`)** unless wrapped in `managed("reason", "interop")`.

## Pre-Registered Falsifier Matrix (`F1`–`F3`)

| ID | Diagnostic | Modality | Scenario | Expected Verdict | Observed |
|---|---|---|---|---|---|
| `F1` | `HYB-E5201` `CallbackReentrantContainerMutation` | `SyncScoped(0)` | `sort.slice(items, fn(i, j) { items = append(items, 9); ... })` & alias write `alias[0] = 42` vs local `comps += 1` | Reject `items` & `alias` (`HYB-E5201`), Accept `comps += 1` | **PASS** |
| `F2` | `HYB-E5202` `ConcurrentCallbackMutableCapture` | `ConcurrentMulti` | `http.handle_func` mutating captured `counts["hits"] += 1` vs sending `events <- prefix` (`chan string` + `let`) | Reject `counts` (`HYB-E5202`), Accept `events` + `prefix` | **PASS** |
| `F3` | `HYB-E5203` `UnannotatedForeignCallbackEscape` | Unannotated (`Rule FC`) | `foreign.register_hook(fn(x int) { ... })` bare vs `managed("...", "interop")` | Reject bare (`HYB-E5203`), Accept `managed` | **PASS** |

## Reproduction

```bash
python3 spikes/w052-reentrant-callback-effects/callback_effect_checker.py --check
python3 -m unittest discover -s spikes/w052-reentrant-callback-effects/tests -p "test_*.py" -v
```
