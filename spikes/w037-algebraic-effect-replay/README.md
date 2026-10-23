# W-037 — Deterministic Concurrency via Algebraic Effect Sandboxes (#957)

Executable Wildcard Track M1/M2 spike evaluating whether Hybrid `deterministic(seed: 0x...) { ... }` blocks can give reproducible task/channel schedules and replay certificates, modelled in a **single-threaded Python simulator** (no Go runtime involved).

Note: "W-037" is a self-assigned ID that is not in the MOONSHOT ledger; the work is tracked under #957.

## Architecture (`effect_replay_sandbox.py`)

1. **Static Ambient Purity Gate (`HYB-E6020`)**: `audit_deterministic_source()` is scoped to the bodies of `deterministic(...) { ... }` blocks (brace-matched on comment-stripped text; an unterminated block runs to end of text). It flags wall-clock calls (`time.Now()`), `rand.*`, `os.*`/`net.*`/`http.*` I/O and `for..in` over `map[K]V` variables (collected from the whole file), while admitting virtual timers (`time.sleep`, `time.after`). `whole_file=True` is used only for the corpus scan below.
2. **Algebraic Effect Schedule ($\lambda$-steps)**: `DeterministicSandbox` lowers `Spawn`, `Sleep`, `ChanSend`, `ChanRecv`, `ChanClose`, `Select` into coroutine yields ordered by virtual time and a `SplitMix64` schedule PRNG seeded by `seed`.
3. **Replay Certificates & Deadlock Witnesses (`HYB-E6021` / `HYB-E6022`)**: each run emits a SHA-256 over the event trace (`L<step>@t<vtime>:<task>:<effect>`); if all tasks block with no pending timers a deterministic `DeadlockWitness` is emitted.

## Falsifier status (`seed=0x42`; measured numbers: see `RESULTS.md`)

| Falsifier | Criterion (#957) | Result | Verdict |
|---|---|---|---|
| **F1: Replay Identity** | Identical event sequence under adversarial jitter | Deterministic by construction in a single-threaded Python simulator. "Jitter" = a permutation of the host-side `ready`/`sleeping`/`select_wait`/`blocked` dict iteration order at every $\lambda$-step (separate `SplitMix64` stream, never the schedule PRNG), applied before the canonical sort. Negative control: with `canonical_order=False` (host dict order, no sort), jittered runs diverge from the non-canonical jitter-0 run, so the check can fail (`jitter_is_live`). Pinned: worker pool `4e8e0ae5a2b3...`, 54 $\lambda$-steps; pipeline `6fe8a234305a...`, 49 $\lambda$-steps, `total=204`. Match counts: see `RESULTS.md`. This does **not** exercise real host thread scheduling or CPU load (the actual #957 falsifier). | see `RESULTS.md` |
| **F2: Purity & Liveness** | Reject ambient non-determinism (`HYB-E6020`), catch deadlocks | Regex scan of deterministic block bodies; circular channel wait emits `HYB-E6021` at $\lambda=3$. Known false negatives: method-value `time.Now` (no call), `rand.Shuffle`, `os.Environ()`, map-typed struct fields (`for k, v in cfg.table`). String literals containing braces are not handled by the block matcher; a `deterministic {` header inside a string literal is treated as a block; a `"http://..."` string inside a block is stripped by the `//` comment remover (which may eat the closing brace so the block runs to end of file). No completeness claim. | see `RESULTS.md` |
| **F3: Throughput Budget** | Certificate overhead $\le 2.5\times$ | Noisy Python `trace=True` vs `trace=False` ratio (audit measured 0.98, 2.02, 2.15 over three CLI runs). No Go baseline; `trace=False` also disables the schedule PRNG, so this is not an isolated cost. | **INCONCLUSIVE** |
| **Corpus: files with no regex hit** | `examples/` + `tests/checker-corpus/` `.hy` files | Whole-file regex scan (no corpus program contains a deterministic block; nothing is executed): `137` files, `86` concurrent (`task`/`chan`/`select`), `4` with a regex hit, `85` concurrent files without a hit. Says nothing about determinism of those programs. | INFORMATIONAL |

## Reproduction

```bash
python spikes/w037-algebraic-effect-replay/effect_replay_sandbox.py --check --json
python -m unittest discover -s spikes/w037-algebraic-effect-replay/tests -v
```

Results and evidence: [`RESULTS.md`](RESULTS.md). `--check` exits 1 if replay identity, `total=204`, the deadlock witness, or `jitter_is_live` fails.
