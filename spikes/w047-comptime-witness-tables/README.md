# W-047 (`#448`): Compile-Time Witness Tables & Linear Close-Right / Acyclic Spawn Deadlock Verifier

Evaluates compile-time witness specialization (`witness T: Contract`) and `#448`'s three-part deadlock-freedom discipline (**R1** linear close right with plural send rights, **R2** structured acyclic task spawn DAG, **R3** dominated/guarded blocking receives) against live `HEAD` (`7d63d5fc`).

## Diagnostic Matrix & Falsifier Results

| Rule / Falsifier | Diagnostic | Verdict on Live `HEAD` (`126` Corpus Files, `38` `ACCEPT`) |
|---|---|---|
| **Contract Ownership Witness (`witness T: Contract`)** | `HYB-E4701` | Rejects missing methods, `move`/`ref`/`mut` ownership-mode mismatches, and dynamic interface boxing of affine types (`0 B` heap boxing; static `__hy_witness_T_Contract` vtable for arity `> 2`, inline devirtualization for arity `<= 2`). |
| **R1 — Linear Close Right, Plural Send Right (Row 16)** | `HYB-E4702` | **SURVIVES (`38/38` `ACCEPT` preserved)**: `15-fanin-per-task-state.hy` (4-task fan-in `results <- move local`) stays `ACCEPT` unchanged while non-owner `close(ch)` and unjoined sender close races are rejected, flipping `GUARANTEES.md` Row 16. |
| **R2 — Structured Spawn DAG (No Cyclic Join / Orphan Child)** | `HYB-E4703` | Rejects parent returning with unjoined live children and cyclic `wait_task`/`join` edges (`taskA -> taskB -> taskA`). |
| **R3 — Blocking Receive Progress Obligation (`for v in ch` / `<-ch`)** | `HYB-E4704` | **M2 Falsifier Fired on Bare `<-ch` (`15/38` `ACCEPT` impacted)**: Strict R3 on bare `<-ch` without `close(ch)` or `select` guard would reject `15/38` (`39.5%`) pre-registered `ACCEPT` programs (unless discharged by bounded-send dominance or narrowed to `for v in ch` + bare-receive lint). |
| **Six-and-Six Adversarial Corpus (`#448` Step 2)** | `HYB-E4702`–`E4704` | **`6/6` ACCEPT** (fan-in, buffered backpressure, timeout `select`, `close` + `for v in ch`, `default` `select`, 2-stage pipeline) and **`6/6` REJECT** (send/close race, cyclic join, unclosed receive, orphan child, non-owner close, `managed`-only close). |
| **Honest Falsifier (`#448` Step 3: Livelock vs Deadlock)** | `livelock_risk=True` | `select { default }` spinning on a never-ready arm is classified as livelock (`accepted=True, livelock_risk=True`), requiring `GUARANTEES.md` Row 16 to state *"no send-on-close, no cyclic join, no unbounded blocking receive"*. |

## Verification

```bash
python spikes/w047-comptime-witness-tables/witness_specializer.py --check
python spikes/w047-comptime-witness-tables/witness_specializer.py --json
```
