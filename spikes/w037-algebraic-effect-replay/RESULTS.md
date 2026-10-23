# W-037 (#957): Algebraic effect replay sandbox — RESULTS

**Status:** The original PR shipped results without a pre-registered RESULTS.md. This file records the hypothesis and pass/fail thresholds as restated on 2026-10-23 during the audit fix, before re-running the measurements below. It is therefore a post-hoc restatement, not a true pre-registration. "W-037" is a self-assigned ID that is not in the MOONSHOT ledger. Code: `effect_replay_sandbox.py` (throwaway spike code).

## Question / hypothesis

A seeded virtual-time scheduler over algebraic effects (Spawn/Sleep/ChanSend/ChanRecv/ChanClose/Select) yields bit-identical traces independent of host-side iteration order. A scoped static regex gate flags ambient non-determinism inside `deterministic(...) { }` bodies.

## Pre-stated thresholds

| ID | Pass criterion | Source |
|---|---|---|
| F1 | PASS iff 128/128 jittered runs (64 worker pool + 64 pipeline; jitters `(r+1)*97` and `(r+1)*131`) match the jitter-0 digest AND the negative control diverges in >=1 run (otherwise the check is vacuous). The negative control compares jittered `canonical_order=False` runs against the non-canonical jitter-0 run, so it fails if the shuffle is a no-op | README.md F1; effect_replay_sandbox.py:186-191 |
| F2 | PASS iff the unit tests' impure sample yields 5 HYB-E6020 at lines 2,3,4,6,7, out-of-block calls are not flagged, and the circular wait yields HYB-E6021 at lambda=3 | `tests/test_effect_replay_sandbox.py:32-33` (5 HYB-E6020 at lines 2,3,4,6,7); README.md F2 for the lambda=3 deadlock |
| F3 | Overhead <= 2.5x. No Go baseline exists, so at most a Python trace-on/off ratio | README.md F3 |
| Corpus | Informational only | README.md |

## Scope limits

- Single-threaded Python simulator. No Go runtime is involved.
- Jitter = permutation of host-side dict order of `ready`/`sleeping`/`select_wait`/`blocked` at each lambda-step, drawn from a separate SplitMix64 stream (effect_replay_sandbox.py:63-66, 76, 101-102).
- Does NOT test real OS thread scheduling, CPU load, or Go.
- Issue #957 asked for 1,000,000 simulated schedules and 10,000 runs under varying host CPU load. Not done: 2 x 64 = 128 jittered runs (effect_replay_sandbox.py:186).
- No hy-syntax / hy-check integration.
- HYB-E6020/6021/6022 are not registered in `compiler/crates/hy-diag/data/diagnostics.json` (re-checked by the closing review on 2026-10-23: no `E602x` code outside this spike).
- Known regex false negatives: method-value `time.Now` (no call), `rand.Shuffle`, `os.Environ()`, map-typed struct fields; braces inside string literals (README.md F2; effect_replay_sandbox.py:26-30).

## Raw results

Date and environment (audit brief 2026-10-23): Windows/amd64, Python 3.12.10, base HEAD 1028b554 plus the fix working tree.

Commands:
- `python -B -m unittest discover -s spikes/w037-algebraic-effect-replay/tests -p "test_*.py" -v` -> 10 tests, OK (audit logs `unittest.txt`, lines 13-15).
- `python -B spikes/w037-algebraic-effect-replay/effect_replay_sandbox.py --check --json` x3 -> rc 0, 0, 0.

Values from the `--check --json` runs (`audit/fix/pr_1249/logs/cli_1.json`, `cli_2.json`, `cli_3.json`). Identical in all three runs unless noted:

| Metric | Value | Source |
|---|---|---|
| worker_pool_matches | 64/64 | cli_1..3.json `replay` |
| pipeline_matches | 64/64 | cli_1..3.json `replay` |
| replay_identical_ratio | 1.0 | cli_1..3.json `replay` |
| worker_pool_sha256 | `4e8e0ae5a2b34b08966ee63b7e9cb1fef7b3b4a845a82ff30fa02bd06c68e338` | cli_1..3.json |
| worker_pool_steps | 54 lambda-steps; output `[2,2,4,6,8,10,12,18]` | cli_1..3.json |
| pipeline_sha256 | `6fe8a234305a15e3271ceb44c5ff1727845ea0ff4bd62a61f48b94a60ccb2e54` | cli_1..3.json |
| pipeline_steps | 49 lambda-steps; total 204 | cli_1..3.json |
| seed_sensitivity_verified | true | cli_1..3.json |
| jitter_negative_control_diverged | 64/64 (jittered `canonical_order=False` runs vs the `canonical_order=False` jitter-0 run) | cli_1..3.json |
| negative-control mutation probe | with `_shuffle_dict` replaced by a no-op: `jitter_negative_control_diverged=0`, `jitter_is_live=False` (runs=8), so the check can fail | `audit/fix/pr_1249/logs/noop_probe.txt` |
| jitter_is_live | true | cli_1..3.json |
| deadlock | HYB-E6021, 2 blocked tasks | cli_1..3.json |
| overhead_ratio | 1.68 (cli_1), 1.51 (cli_2), 1.55 (cli_3) | cli_1..3.json; varies by run |
| Earlier overhead on original PR | 0.98, 2.02, 2.15 | README.md F3 (audit brief); not in the logs read here |

Corpus (`corpus` block, identical in all three runs; source cli_1..3.json):
- 137 `.hy` files total.
- 86 concurrent (`task`/`chan`/`select`).
- 4 files with a whole-file regex hit.
- 85 concurrent files without a hit (derived: 86 - 85 = 1, so one of the 4 hit files is concurrent).

## Verdicts

| Falsifier | Verdict | Note |
|---|---|---|
| F1 Replay identity | PASS | Within stated scope: simulator determinism under host-order perturbation. Not the #957 host-load falsifier. |
| F2 Purity and liveness | PASS | Tested spellings only; see false negatives. |
| F3 Throughput budget | INCONCLUSIVE | Noisy: 0.98 to 2.15 across the 6 runs above (3 audit + 3 here); no Go baseline. |
| Corpus | INFORMATIONAL | Whole-file regex only; says nothing about determinism of corpus programs. |

**Overall:** the spike does not yet demonstrate the #957 claim of reproducibility under real host scheduling.
