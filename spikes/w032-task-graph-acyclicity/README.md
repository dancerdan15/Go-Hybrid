# W-032 (#950) — Certified Task-Graph Acyclicity (`HYB-E7001`)

Executable Wildcard M2 spike testing whether **Task Dependency Epochs (`epoch(N)`)** and **Topological Join Certificates** can flip `GUARANTEES.md` Row 16 from **No claim** to a checked-subset deadlock-freedom guarantee for structured task regions.

## Architecture & Decision Procedure
1. **Wait-For Graph Construction (`build_wait_graph`)**: Extracts directed wait edges `(u -> v)` across structured tasks from `task.wait(v)` joins, blocking channel receives `<-ch` matched against producer sends `ch <-`, orphan unbuffered sends (`unbuf_*` / `latch_*`), `close(ch)` epoch transitions (`@send` -> `@join`), and explicit `epoch(N)` orderings.
2. **Shortest Cycle Witness (`find_canonical_cycle`)**: Deterministic lexicographical BFS extracts the canonical shortest cycle `(u, ..., u)` and emits `HYB-E7001` (`REJECT-CYCLE`). Dynamic unbounded spawns emit `HYB-E7002` (`REJECT-UNBOUNDED`, fail-closed).
3. **Topological Join Certificates (`kahn_topological_epochs`)**: Kahn's algorithm computes a canonical topological sort and assigns minimal `epoch(N)` witnesses (`u -> v ==> epoch(u) > epoch(v)`) with zero runtime overhead in emitted Go.

## Pre-Registered Falsifier Outcomes on `HEAD` (`7d63d5fc`)
| Falsifier / Target (#950) | Threshold | Measured (`--check --json`) | Verdict |
|---|---|---|---|
| **B1: Post-mortem deadlock suite** (K8s #45612, Docker #24810, CockroachDB latch, etcd, gRPC, NATS) | `100%` cycle recall (`HYB-E7001`) | `6/6` rejected with minimal cycle witness; `2/2` controls accepted | **PASS** |
| **B2: Live `tests/checker-corpus/` pipeline fixtures** (`15`, `16`, `17`, `33`) | `100%` accepted (`0` regressions) | `4/4` `ACCEPT-CERTIFIED` | **PASS** |
| **F1: Annotation overhead** | `<= 1.0` line per `50` LOC | `0.0` lines per `50` LOC (inferred from channel/join edges) | **NOT FALSIFIED** |
| **F2: Idiomatic `select` worker cancellation** | `0` false-positive cycle rejections | `0` false positives (`select` non-blocking/cancel arms are escape sinks) | **NOT FALSIFIED** |

Run: `python spikes/w032-task-graph-acyclicity/task_graph_certifier.py --check --json`
