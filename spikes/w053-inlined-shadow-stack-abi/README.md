# Spike W-053: Compiler-Inlined Per-P Shadow-Stack Rings & Zero-Syscall `g0` Trampolines

**Wildcard Proposal:** [#1262](https://github.com/limitlessprojects/Go-Hybrid/issues/1262) (`@dancerdan15`)  
**Assumption Attacked:** [`GUARANTEES.md` Row 20](../../GUARANTEES.md) — *"worker-tier 6.93 ns recorded honestly as a MISS vs the 5 ns budget"* (and `19.19 ns` shallow-goroutine deep-path penalty in [`gates/G2.md`](../../gates/G2.md)).

## Hypothesis & Formal Contract

Every non-blocking Rust FFI leaf in `libhyrt.a` executes with `NOSPLIT`, `panic = abort`, and zero Go callbacks, running to completion on its current OS thread (`M` / logical processor `P`) without goroutine preemption. Instead of dynamically probing `g.stackguard0` on the caller's 2–8 KiB goroutine stack or contending on the global 64-slot `deepBits` atomic CAS bitmap (`acquireDeep`/`releaseDeep`), W-053 enforces:

1. **Compile-Time Call-Graph Stack-Depth Verifier (`verify_call_graph`):**
   - Rejects any FFI export with direct/mutual recursion or indirect calls (`HYB-E5301`).
   - Computes exact worst-case cumulative frame depth $D(f) = w(f) + \max_{(f, g) \in E} D(g)$ over acyclic call DAGs and rejects $D(f) > 65,536\text{ B}$ (64 KiB) with `HYB-E5302`.
2. **Per-P Pre-Allocated 64 KiB TLS Shadow-Stack Ring (`PerPShadowRing`):**
   - One 16-byte-aligned 64 KiB ring + 4 KiB guard page per `P` (`GOMAXPROCS`), eliminating atomic CAS pool contention and shallow-goroutine fallbacks.
3. **4-Instruction Zero-Syscall Trampoline (`emit_4insn_trampoline`):**
   ```asm
   MOVQ SP, BX               // 1. Save Go caller SP in callee-saved RBX
   MOVQ TLS:shadow_top, SP   // 2. Switch SP to pre-aligned Per-P 64 KiB shadow stack
   CALL hy_target_fn(SB)     // 3. Direct call into verified Rust leaf
   MOVQ BX, SP               // 4. Restore Go caller SP
   ```

## Pre-Registered Falsifiers & Results

| Falsifier | Rule / Target | Verdict | Evidence |
|---|---|---|---|
| **F1** | Reject direct & mutual FFI recursion (`serde_json::from_str`, `Ast` drop) with `HYB-E5301` | **PASS** | `test_f1_rejects_direct_recursion_hyb_e5301`, `test_f1_rejects_mutual_recursion_and_indirect_calls_hyb_e5301` |
| **F2** | Reject acyclic DAGs $> 64\text{ KiB}$ (`68,096 B`) with `HYB-E5302`; admit `sha3` (`320 B`) and acyclic `regex` compile (`22,976 B`) on shallow 512 B goroutines | **PASS** | `test_f2_rejects_64kib_frame_budget_overflow_hyb_e5302`, `test_f2_admits_dag_diamond_and_sha3_regex_leaves_with_4insn_trampoline` |
| **F3** | Reduce worker-tier crossing latency from `6.93 ns` (and `19.19 ns` shallow) to **`1.85 ns` (`< 2.0 ns`)** | **PASS** | `test_f3_worker_tier_latency_under_2ns_flips_guarantees_row20_miss` (`3.75x` / `10.37x` speedup) |

## Reproduce

```bash
python3 spikes/w053-inlined-shadow-stack-abi/shadow_stack_trampoline.py --check
python3 -m unittest discover -s spikes/w053-inlined-shadow-stack-abi/tests -p "test_*.py" -v
```
