# Spike W-052: Effect-polymorphic foreign callback contracts (model-only)

**Status:** PARTIAL / model-only. Written AFTER the run, from the PR audit
(`audit/pr_1281.md`). The hypotheses F1-F3 below are reconstructed from the PR
README; they landed in the same commit as the verdict, so they were NOT
pre-registered before running (AGENTS.md spike protocol not met). Treat the
PASS results as demonstrations on fixtures, not as confirmed predictions.
**Code:** `callback_effect_checker.py` (stand-alone Python model), `tests/test_callback_effect_checker.py`.
**Proposal:** issue #1256. "W-052" is a self-assigned ID; `MOONSHOT.md` has no
#1256 entry yet (triage first).

## Question / design under test

Can `GUARANTEES.md` Row 9 ("values escaping into Go APIs ... no checker claim")
be tightened by giving higher-order foreign APIs a two-modality contract
(`SyncScoped(subject_arg)` / `ConcurrentMulti`) plus a fail-closed default for
unannotated callbacks (Rule FC)? The model is a line/regex scanner over source
text. It is NOT `hy-check`, not built on the hy-syntax AST, and nothing under
`compiler/`, `runtime/`, `tools/`, `.github/` changed, so real `hyb check`
behaviour is identical before and after.

## Hypotheses (reconstructed)

| ID | Claim | Falsifier | Model result |
|---|---|---|---|
| F1 | `SyncScoped`: mutating the subject container (or an alias) in the callback is `HYB-E5201`; a disjoint local accumulator is accepted | any subject/alias write accepted, or `comps += 1` rejected | PASS on its hand-written fixture |
| F2 | `ConcurrentMulti`: mutable GC capture or outer `var` mutation is `HYB-E5202`; `let`/`const`/channel captures accepted | `counts["hits"] += 1` accepted, or `events <- prefix` rejected | PASS on its hand-written fixture |
| F3 | Closure to an unannotated foreign callee is `HYB-E5203`; `managed(_, "interop")` accepts | bare call accepted, or managed call rejected | PASS on its hand-written fixture |

## Counterexamples fired by the audit

Eight probes written by the auditor against the PR's original model.

| # | Probe | Contract expects | Model output (before) | Status |
|---|---|---|---|---|
| 1 | `var c Counter` + handler `c.n += 1` (field write) | E5202 | `[]` missed | OPEN |
| 2 | `bump := fn(){ hits++ }`, handler calls `bump()` (helper closure) | E5202 | `[]` missed | OPEN |
| 3 | `counts := new_counts()` + `counts.inc()` (call-result type falls back to `int`) | E5202 | `[]` missed | OPEN |
| 4 | `register(fn(x int){ hits++ })` (unqualified callee) | E5203 | `[]` missed | OPEN |
| 5 | `h := foreign.register_hook(fn(x int){ hits++ })` (assigned result; parsed as a closure declaration, Rule FC bypassed). Forms now handled: `x := …`, `let/const/var x = …`, `a, b := …?`, `a = …`, `_ = …`, `return …` | E5203 | `[]` missed | FIXED in this commit for those forms (field/index LHS, nested in a stored closure literal: OPEN, same class as 2) |
| 6 | `err := http.handle_func("/x", fn(w, r){ counts["k"] += 1 })` | E5202 | `[]` missed | FIXED in this commit (same fix as 5) |
| 7 | `items.push(1)` inside `sort.slice(items, ...)` (method mutation) | E5201 | `[]` missed | OPEN |
| 8 | `go fn(){ hits++ }()` inside a SyncScoped callback (escapes the sync scope) | reject | `[]` missed | OPEN |

Separate finding, receiver-name keying: the registry held `mux.handle`, keyed
on the identifier. Corpus 10 with `mux` renamed to `router` gave `E5203
router.handle` instead of `E5202 hits`, and example 06 renamed gave 2x `E5203
router.handle` (false positives). FIXED: the contract is now keyed on the
receiver type (`http.serve_mux.handle`, `http.serve_mux.handle_func`), inferred
from `http.new_serve_mux()`; an undeclared receiver falls to Rule FC (E5203).
The fix for 5/6 matches `_DECL_RE` first and analyses a call right-hand side
before the closure-literal path; for non-declaration lines a leading `return `
or identifier-list assignment prefix (`a, b := `, `a = `, `_ = `) is stripped
before call matching. A pure `hook := fn(...) {...}` is stored as a closure as
before.

Known limitation: receivers typed only via function parameters
(`fn routes(mux *http.serve_mux)`) are not bound, so `mux.handle(...)` there
falls to fail-closed E5203 (a false positive). An explicit
`var mux *http.serve_mux` is handled (leading `*` stripped for the lookup).
Foreign calls nested inside a stored closure literal are never analysed.

## Evidence notes

- 9 of the 10 `examples/` contain no `fn(`; only `06-http-server` has callback
  call sites (2, both `mux.handle`). The "zero diagnostics on examples" figure
  is therefore not evidence of precision.
- Corpus scan, 126 `.hy` files: only corpus 10 is flagged, and it is the only
  program passing a closure into a foreign call. The effective sample is 1.
- Real `hyb check` (audit build from base): corpus 10, example 06 and a
  map-race repro (`counts["hits"] += 1` in a handler) all print `OK (0
  diagnostics)`. "Corpus 10 flipped" holds only inside the model.
- `--check` now also reports `examples_with_callbacks`, `callback_call_sites`
  and `renamed_receiver_ok` (corpus 10 and example 06 with `mux` -> `router`
  give the same verdicts as the originals).
- CI does not run this spike's tests (the registration guard scans only
  `tools/**` and `tests/**`).

## Verdict

PARTIAL / model-only. F1-F3 hold only on the model's own fixtures. The 6 OPEN
counterexamples (1-4, 7, 8) refute any soundness or "guaranteed" claim for this
model. `GUARANTEES.md` Row 9 stays "No checker claim". The deliverable is the
contract shape plus an honest list of where a regex model breaks.

## Next steps

1. Re-do the prototype on the hy-syntax AST (resolved types, field paths,
   closure call graph, `go` statements) and re-run counterexamples 1-4, 7, 8.
2. Get #1256 triaged into `MOONSHOT.md` before using the W-052 ID.
3. Pre-register hypotheses in this file BEFORE the next run.

## Reproduction

```bash
python3 -B spikes/w052-reentrant-callback-effects/callback_effect_checker.py --check
python3 -B -m unittest discover -s spikes/w052-reentrant-callback-effects/tests -p "test_*.py" -v
```
