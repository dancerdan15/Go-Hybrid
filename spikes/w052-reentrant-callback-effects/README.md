# Spike W-052 — Effect-Polymorphic Foreign Callback Contracts (`GUARANTEES.md` Row 9, proposal, model-only)

- **Wildcard Proposal Issue:** [#1256](https://github.com/limitlessprojects/Go-Hybrid/issues/1256) (not yet triaged into `MOONSHOT.md`; "W-052" is a self-assigned label)
- **Assumption Attacked:** `GUARANTEES.md` Row 9 (`Values escaping into Go APIs: net/http handlers, callbacks — No checker claim`), `SPEC-LANGUAGE.md §8.5 C9`, `§9.1 O10`, `§9.2`, `§9.3 O4.10`, `OQ1`.
- **Verdict:** **PARTIAL / model-only.**
  - This is a stand-alone line/regex Python model (`callback_effect_checker.py`), NOT `hy-check` and not AST-based.
  - The PR touches nothing under `compiler/`, `runtime/`, `tools/`, `.github/`, so `hyb check` is unchanged: corpus 10 is still ACCEPT under the real checker and `GUARANTEES.md` Row 9 remains "No checker claim".
  - F1–F3 pass only on hand-written strings inside the model. "Corpus 10 flipped" holds in the model only.
  - The audit fired 8 counterexamples (see [RESULTS.md](RESULTS.md)). 2 are fixed here (assigned-result Rule FC bypass for `x := f(fn…)`, `let/var x = …`, `a, b := …?`, `a = …`, `_ = …`, `return …`; assigned `err := http.handle_func(...)`); 6 stay OPEN. The `mux` to `router` rename false positives are fixed by keying on the receiver type.
  - 9/10 `examples/` contain no callbacks. The zero-diagnostics figure rests only on example 06's 2 `mux.handle` call sites and is not evidence of precision.
  - RESULTS.md was written after the run; the hypotheses below were not pre-registered before running.

## Proposed Contract: Two-Modality `#[callback(SyncScoped | ConcurrentMulti)]`

1. **`#[callback(SyncScoped(subject_arg))]`** (`sort.slice`, `filepath.walk`, `bytes.map`):
   - Invoked synchronously on the caller's goroutine `0..N` times before returning; never retained.
   - Disjoint local accumulators (`var comps int`) may be mutated freely without `managed`.
   - The traversed container `subject_arg` (and aliases sharing its storage root) is frozen for the callback body; mutating it is **`HYB-E5201`** (`CallbackReentrantContainerMutation`).
2. **`#[callback(ConcurrentMulti)]`** (`http.handle_func`, `http.listen_and_serve`, `time.after_func`, `http.serve_mux.handle`, `http.serve_mux.handle_func`, `http.handler`):
   - Retained and invoked concurrently across goroutines `0..N` times. Methods are keyed on the receiver type (`http.serve_mux`, inferred from `http.new_serve_mux()` or an explicit `var mux *http.serve_mux`), not on the variable name. Receivers typed only via function parameters (`fn routes(mux *http.serve_mux)`) are not tracked and fall to fail-closed E5203.
   - Captures of immutable bindings (`let`, `const`, primitives) and channels are accepted; capturing mutable GC state or mutating an outer `var` is **`HYB-E5202`** (`ConcurrentCallbackMutableCapture`). Flipping corpus 10 `hits++` from `ACCEPT (CONTESTED)` to `REJECT` happens in the model only.
3. **Rule FC fail-closed (`HYB-E5203`)**: a closure passed to a foreign function with no contract (including an unknown receiver) is `UnannotatedForeignCallbackEscape` unless wrapped in `managed("reason", "interop")`.

## Falsifier Matrix (`F1`–`F3`, reconstructed; see RESULTS.md)

| ID | Diagnostic | Scenario | Expected | Observed |
|---|---|---|---|---|
| `F1` | `HYB-E5201` | `sort.slice(items, fn…)` writing `items`/`alias` vs local `comps += 1` | Reject `items`, `alias`; accept `comps` | PASS (model only) |
| `F2` | `HYB-E5202` | `http.handle_func` mutating `counts["hits"]` vs `events <- prefix` | Reject `counts`; accept `events`, `prefix` | PASS (model only) |
| `F3` | `HYB-E5203` | `foreign.register_hook(fn…)` bare vs `managed(…, "interop")` | Reject bare; accept `managed` | PASS (model only) |

## Reproduction

```bash
python3 -B spikes/w052-reentrant-callback-effects/callback_effect_checker.py --check
python3 -B -m unittest discover -s spikes/w052-reentrant-callback-effects/tests -p "test_*.py" -v
```
