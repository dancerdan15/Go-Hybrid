# W-031 (`#1188`): Fault-Contained Native Rust Calls via Process Quarantine

Executable M1/M2 verification spike for `#1188`: separates native Rust process faults (`panic = "abort"` in `runtime/rust/hyrt/Cargo.toml`) from the Go host request lifecycle (`Idle -> Pending(generation, id) -> Terminal Outcome`).

## Live `HEAD` Runtime Audit (`runtime/rust/hyrt/` & `runtime/go/`)

| Dimension | Live `HEAD` Measurement | Quarantine Boundary (`W-031`) |
|---|---|---|
| Failure domain (`G-18`) | `panic = "abort"` in shared address space kills Go host | Supervised worker exits (`134`); host survives (`FAILED_ABORT`) |
| Export admission (`hyrt/src/lib.rs`) | `21` C-ABI exports (`14` stateful `hy_json_*`, `7` stateless/probes) | Admits only `hy_sha3_digest256` (`1/21`); rejects `20/21` (`0` fallback) |
| Boundary memory & tags (`G-19`) | `11` `runtime.Pinner.Pin(` sites; `table_insert` hardcodes `gen = 1u32` | Private byte copies; monotonic host `generation` retired on fault/timeout |
| Call-path cost (`G-20`) | `3.37–3.44 ns` sandbox / `6.93 ns` worker in-process trampoline | 18-byte framed pipe IPC (`<HIQI`); trades μs IPC latency for crash containment |

## Falsifier Verdicts (`python3 spikes/w031-ffi-quarantine/ffi_quarantine.py --check`)

- **F1 (Pre-commit abort):** Child `abort` (`rc=134`) before full frame yields `FAILED_ABORT`, retires `generation`, publishes no digest, and keeps `host_alive=True`.
- **F2 (Framing & length bounds):** Truncated headers/payloads, bad version/ID, and `length > max_payload` reject before allocation (`allocated_bytes == 0`).
- **F3 (Timeout & post-commit death):** `TIMEOUT_UNKNOWN` retires the generation (`0` auto-retries) and rejects late frames as `REJECTED_STALE`; exit after commit preserves `COMMITTED`.
- **F4 (Admission gate):** `hy_json_*`, `hy_regex_*`, and callbacks fail closed (`ADMISSION_REJECTED`, `fallback_invocations == 0`).
- **F5 (Quota & SHA3-256 parity):** Enforces `ERR_BUSY`/`ERR_QUOTA_EXCEEDED` saturation bounds and matches `sha3 = "=0.10.8"` (`empty` + mutated caller buffer).
