# W-034 (#947): Zero-Allocation Typestate Session Protocols over Bare Go Channels

Executable feasibility spike for Wildcard **#947**: extending `hy-check`'s flow-sensitive `FnState` cursor (`loans.rs` / `walk.rs`) with linear channel protocol typestates (`chan[send Req -> recv Resp -> close]`) while emitting bare Go `chan` primitives with zero wrapper allocations.

## Architecture & Diagnostic Surface

1. **Linear Protocol Cursor (`FnState.proto_cursor`):** Each channel binding tracks an ordered `tuple[ProtoStep, ...]`. `send`, `recv`, and `close` advance the cursor in-place without runtime continuation structs.
2. **Involutive Endpoint Duality (`dual(S)`):** Task spawns verify `dual(caller) == callee` (`send T <-> recv T`, terminal `close` preserved), rejecting symmetric recv/send deadlocks (`HYB-E7001` / `HYB-E6012`).
3. **Obligation & Control-Flow Join Soundness:**
   - `HYB-E7001`: Out-of-order protocol step or operation on a `close`d channel (flips `GUARANTEES.md` row 16 in the checked subset).
   - `HYB-E7002` (`HYB-E6010`): Returning from a function with unfulfilled protocol steps (`send`/`recv`/`close` remaining).
   - `HYB-E7003` (`HYB-E6011`): Protocol state divergence across `if`/`match`/`select` arms or loop back-edges.
4. **Zero-Allocation Go Lowering (`hy-emit`):** Homogeneous protocols (`chan[send int -> recv int -> close]`) lower to a single `make(chan int)`; heterogeneous protocols (`Req != Resp`) lower to directional unboxed twin channels `(chan Req, chan Resp)` (`0` wrapper structs, `0.0%` interface-boxing overhead).

## Live `HEAD` (`7d63d5f`) Quantitative Falsifier Verdict

| Falsifier Criterion (#947) | Threshold | Measured on `HEAD` (`136` `.hy` files) | Verdict |
|---|---|---|---|
| Positive expressiveness (`5` canonical patterns) | `5 / 5` byte-exact bare Go | `5 / 5` (`0` wrapper allocs) | **PASS** |
| Negative bug-catching (`E7001`/`E7002`/`E7003`) | `100%` deterministic reject | `4 / 4` rejected | **PASS** |
| `select` loop back-edge `managed` escape rate | `<= 15.0%` | `0.0%` (`10` `select` files, `85` `chan` files) | **NOT FALSIFIED** |
| Heterogeneous lowering boxing regression | `<= 5.0%` | `0.0%` (directional twin `chan T`) | **NOT FALSIFIED** |

## Reproduction

```bash
python3 spikes/w034-typestate-channels/typestate_channels.py --check
python3 spikes/w034-typestate-channels/typestate_channels.py --json
```
