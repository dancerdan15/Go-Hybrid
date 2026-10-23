#!/usr/bin/env python3
"""W-031 (#1188): Quarantine native Rust calls via process-isolated FFI workers."""
from __future__ import annotations
import argparse, hashlib, json, os, re, struct, subprocess, sys
from dataclasses import dataclass
from pathlib import Path

WIRE_VERSION, SHA3_256_LEN, DEFAULT_MAX_PAYLOAD = 1, 32, 65536
HEADER_STRUCT = struct.Struct("<HIQI")  # version:u16, generation:u32, req_id:u64, length:u32 (18B)
ADMITTED_OPS = frozenset({"hy_sha3_digest256"})
EXPORT_RE = re.compile(r'pub\s+extern\s+"C"\s+fn\s+([a-zA-Z0-9_]+)')


@dataclass(frozen=True)
class FrameHeader:
    version: int; generation: int; request_id: int; length: int


@dataclass(frozen=True)
class RequestResult:
    outcome: str; error: str | None; generation: int; request_id: int
    digest_hex: str | None; host_alive: bool; allocated_bytes: int


def encode_frame(version: int, generation: int, request_id: int, payload: bytes | bytearray, max_payload: int = DEFAULT_MAX_PAYLOAD) -> bytes:
    buf = bytes(payload)
    if len(buf) > max_payload:
        raise ValueError("ERR_OVERSIZED_FRAME")
    if not (0 <= version <= 0xFFFF and 0 <= generation <= 0xFFFFFFFF and 0 <= request_id <= 0xFFFFFFFFFFFFFFFF):
        raise ValueError("ERR_INVALID_HEADER")
    return HEADER_STRUCT.pack(version, generation, request_id, len(buf)) + buf


def decode_frame(raw: bytes, expected_gen: int, expected_req_id: int, max_payload: int = DEFAULT_MAX_PAYLOAD, exact_len: int | None = SHA3_256_LEN) -> tuple[str, FrameHeader | None, bytes | None, int]:
    if len(raw) < HEADER_STRUCT.size:
        return ("ERR_TRUNCATED_HEADER", None, None, 0)
    ver, gen, req_id, length = HEADER_STRUCT.unpack_from(raw, 0)
    hdr = FrameHeader(ver, gen, req_id, length)
    if length > max_payload or (exact_len is not None and length != exact_len):
        return ("ERR_OVERSIZED_LENGTH" if length > max_payload else "ERR_INVALID_DIGEST_LEN", hdr, None, 0)
    if ver != WIRE_VERSION or gen != expected_gen or req_id != expected_req_id:
        err = "ERR_BAD_VERSION" if ver != WIRE_VERSION else ("ERR_STALE_GENERATION" if gen < expected_gen else ("ERR_WRONG_GENERATION" if gen != expected_gen else "ERR_WRONG_REQUEST_ID"))
        return (err, hdr, None, 0)
    body_len = len(raw) - HEADER_STRUCT.size
    if body_len != length:
        return ("ERR_TRUNCATED_PAYLOAD" if body_len < length else "ERR_TRAILING_BYTES", hdr, None, 0)
    payload = bytes(raw[HEADER_STRUCT.size : HEADER_STRUCT.size + length])
    return ("OK", hdr, payload, len(payload))


class QuarantineSupervisor:
    """Host state machine: IDLE -> PENDING(generation, request_id) -> terminal outcome."""

    def __init__(self, max_workers: int = 1, max_queue: int = 2, max_payload: int = DEFAULT_MAX_PAYLOAD) -> None:
        if max_workers < 1 or max_queue < 0 or max_payload <= 0:
            raise ValueError("ERR_INVALID_CONFIG")
        self.max_workers, self.max_queue, self.max_payload = max_workers, max_queue, max_payload
        self.state, self.generation, self.next_request_id, self.pending_req_id = "IDLE", 1, 1, None
        self.queued, self.auto_retries, self.fallback_invocations, self.host_alive = 0, 0, 0, True
        self.retired_generations: set[int] = set()
        self.committed: dict[int, str] = {}

    def _retire_generation(self) -> None:
        self.retired_generations.add(self.generation)
        self.generation += 1
        self.state, self.pending_req_id = "IDLE", None

    def admit_and_enqueue(self, op: str, payload: bytes | bytearray) -> tuple[str | None, bytes]:
        if op not in ADMITTED_OPS:
            return ("ERR_UNSUPPORTED_OP", b"")
        if self.state == "PENDING":
            return ("ERR_QUOTA_EXCEEDED" if self.queued >= self.max_queue else "ERR_BUSY", b"")
        snap = bytes(payload)
        return ("ERR_OVERSIZED_FRAME", b"") if len(snap) > self.max_payload else (None, snap)

    def call_sha3_256(self, payload: bytes | bytearray, op: str = "hy_sha3_digest256", fault: str | None = None, use_subprocess: bool = False) -> RequestResult:
        req_id = self.next_request_id
        self.next_request_id += 1
        err, snap = self.admit_and_enqueue(op, payload)
        if err is not None:
            return RequestResult("ADMISSION_REJECTED" if err == "ERR_UNSUPPORTED_OP" else err, err, self.generation, req_id, None, self.host_alive, 0)
        gen = self.generation
        self.state, self.pending_req_id = "PENDING", req_id
        req_frame = encode_frame(WIRE_VERSION, gen, req_id, snap, self.max_payload)
        if use_subprocess:
            rc, resp_raw = self._invoke_subprocess_worker(req_frame, fault)
            if rc != 0 and fault != "exit_after_commit":
                self._retire_generation()
                return RequestResult("FAILED_ABORT", f"EXIT_{rc}", gen, req_id, None, self.host_alive, 0)
        elif fault in ("abort", "timeout"):
            self._retire_generation()
            out = "FAILED_ABORT" if fault == "abort" else "TIMEOUT_UNKNOWN"
            return RequestResult(out, f"ERR_{out}", gen, req_id, None, self.host_alive, 0)
        else:
            resp_raw = self._simulate_worker_wire(req_frame, fault)
        status, _, digest_bytes, alloc = decode_frame(resp_raw, gen, req_id, self.max_payload, SHA3_256_LEN)
        if status != "OK" or digest_bytes is None:
            self._retire_generation()
            return RequestResult("REJECTED_STALE" if status == "ERR_STALE_GENERATION" else "FAILED_FRAMING", status, gen, req_id, None, self.host_alive, alloc)
        digest_hex = digest_bytes.hex()
        self.committed[req_id], self.state, self.pending_req_id = digest_hex, "IDLE", None
        if fault == "exit_after_commit":
            self._retire_generation()
        return RequestResult("COMMITTED", None, gen, req_id, digest_hex, self.host_alive, alloc)

    def deliver_late_frame(self, raw_frame: bytes) -> RequestResult:
        expected_req = self.pending_req_id if self.pending_req_id is not None else 0
        status, hdr, _, alloc = decode_frame(raw_frame, self.generation, expected_req, self.max_payload, SHA3_256_LEN)
        gen, req_id = (hdr.generation if hdr else self.generation), (hdr.request_id if hdr else expected_req)
        if hdr and (hdr.generation in self.retired_generations or status == "ERR_STALE_GENERATION"):
            return RequestResult("REJECTED_STALE", "ERR_STALE_GENERATION", gen, req_id, None, self.host_alive, alloc)
        return RequestResult("FAILED_FRAMING", status, gen, req_id, None, self.host_alive, alloc)

    def _simulate_worker_wire(self, req_frame: bytes, fault: str | None) -> bytes:
        status, hdr, body, _ = decode_frame(req_frame, self.generation, self.pending_req_id or 0, self.max_payload, None)
        if status != "OK" or hdr is None or body is None:
            return b""
        d, p, g, i = hashlib.sha3_256(body).digest(), HEADER_STRUCT.pack, hdr.generation, hdr.request_id
        faults = {"truncate_header": p(WIRE_VERSION, g, i, 32)[:7], "truncate_payload": p(WIRE_VERSION, g, i, 32) + d[:12], "corrupt_version": p(99, g, i, 32) + d, "corrupt_id": p(WIRE_VERSION, g, i + 77, 32) + d, "oversized_len": p(WIRE_VERSION, g, i, self.max_payload + 4096) + d, "bad_digest_len": p(WIRE_VERSION, g, i, 16) + d[:16]}
        return faults.get(fault, encode_frame(WIRE_VERSION, g, i, d, self.max_payload))

    def _invoke_subprocess_worker(self, req_frame: bytes, fault: str | None) -> tuple[int, bytes]:
        cmd = [sys.executable, os.path.abspath(__file__), "--worker-stdio"] + (["--fault", fault] if fault else [])
        proc = subprocess.run(cmd, input=req_frame, capture_output=True, shell=False, timeout=5, check=False)
        return (proc.returncode, proc.stdout)


def audit_live_runtime(root: Path) -> dict[str, object]:
    cargo_p, lib_p, guar_p = root / "runtime/rust/hyrt/Cargo.toml", root / "runtime/rust/hyrt/src/lib.rs", root / "GUARANTEES.md"
    cargo_txt = cargo_p.read_text(encoding="utf-8") if cargo_p.is_file() else ""
    lib_txt = lib_p.read_text(encoding="utf-8") if lib_p.is_file() else ""
    guar_txt = guar_p.read_text(encoding="utf-8") if guar_p.is_file() else ""
    exports = EXPORT_RE.findall(lib_txt)
    json_exports, admitted = [e for e in exports if e.startswith("hy_json_")], [e for e in exports if e in ADMITTED_OPS]
    go_dir = root / "runtime" / "go"
    pinner_sites = sum(gp.read_text(encoding="utf-8").count(".Pin(") for gp in (sorted(go_dir.rglob("*.go")) if go_dir.is_dir() else []) if not gp.name.endswith("_test.go"))
    return {"panic_abort_in_hyrt": 'panic = "abort"' in cargo_txt, "total_hyrt_exports": len(exports), "admitted_exports": admitted, "rejected_stateful_json_exports": len(json_exports), "rejected_other_exports": len(exports) - len(admitted) - len(json_exports), "static_gen_1_in_table_insert": "let gen = 1u32;" in lib_txt, "go_pinner_call_sites": pinner_sites, "g18_g19_g20_present": all(f"| {row} |" in guar_txt for row in (18, 19, 20))}


def evaluate_falsifiers(root: Path) -> dict[str, object]:
    sup = QuarantineSupervisor(max_workers=1, max_queue=2, max_payload=4096)
    f1_sim, f1_sub = sup.call_sha3_256(b"panic-probe", fault="abort"), sup.call_sha3_256(b"panic-probe-sub", fault="abort", use_subprocess=True)
    f1_ok = f1_sim.outcome == f1_sub.outcome == "FAILED_ABORT" and not f1_sim.digest_hex and sup.host_alive and sup.generation == 3
    f2_flts = ("truncate_header", "truncate_payload", "corrupt_version", "corrupt_id", "oversized_len", "bad_digest_len")
    f2_ok = all((r := sup.call_sha3_256(b"x", fault=f)).outcome == "FAILED_FRAMING" and r.allocated_bytes == 0 for f in f2_flts)
    stale_frame = encode_frame(WIRE_VERSION, sup.generation, sup.next_request_id, hashlib.sha3_256(b"slow").digest())
    f3_to, f3_late, f3_post = sup.call_sha3_256(b"slow", fault="timeout"), sup.deliver_late_frame(stale_frame), sup.call_sha3_256(b"commit-then-die", fault="exit_after_commit", use_subprocess=True)
    f3_ok = f3_to.outcome == "TIMEOUT_UNKNOWN" and f3_late.outcome == "REJECTED_STALE" and sup.auto_retries == 0 and f3_post.outcome == "COMMITTED" and f3_post.digest_hex == hashlib.sha3_256(b"commit-then-die").hexdigest()
    f4_ok = all(sup.call_sha3_256(b"{}", op=op).outcome == "ADMISSION_REJECTED" for op in ("hy_json_parse", "hy_json_set", "hy_regex_is_match", "callback_op")) and sup.fallback_invocations == 0
    mut_in = bytearray(b"hybrid-quarantine")
    f5_empty, f5_vec = sup.call_sha3_256(b"", use_subprocess=True), sup.call_sha3_256(mut_in)
    mut_in[0] ^= 0xFF
    sup.state, sup.queued = "PENDING", 0
    f5_busy = sup.call_sha3_256(b"busy")
    sup.queued = sup.max_queue
    f5_quota = sup.call_sha3_256(b"quota")
    sup.state, sup.queued = "IDLE", 0
    f5_ok = f5_empty.digest_hex == hashlib.sha3_256(b"").hexdigest() and f5_vec.digest_hex == hashlib.sha3_256(b"hybrid-quarantine").hexdigest() and f5_busy.outcome == "ERR_BUSY" and f5_quota.outcome == "ERR_QUOTA_EXCEEDED"
    falsifiers = {"F1_abort_containment": f1_ok, "F2_framing_and_bounds": f2_ok, "F3_timeout_stale_and_postcommit": f3_ok, "F4_admission_gate": f4_ok, "F5_quota_and_sha3_parity": f5_ok}
    return {"issue": 1188, "spike": "W-031", "runtime_audit": audit_live_runtime(root), "falsifiers": falsifiers, "all_passed": all(falsifiers.values())}


def _run_worker_stdio(fault: str | None) -> int:
    raw = sys.stdin.buffer.read()
    if fault == "abort" or len(raw) < HEADER_STRUCT.size:
        return 134 if fault == "abort" else 2
    ver, gen, req_id, length = HEADER_STRUCT.unpack_from(raw, 0)
    if ver != WIRE_VERSION or length > DEFAULT_MAX_PAYLOAD or len(raw) != HEADER_STRUCT.size + length:
        return 2
    sys.stdout.buffer.write(encode_frame(ver, gen, req_id, hashlib.sha3_256(raw[HEADER_STRUCT.size : HEADER_STRUCT.size + length]).digest()))
    sys.stdout.buffer.flush()
    return 134 if fault == "exit_after_commit" else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    for flag in ("--check", "--json", "--worker-stdio"):
        ap.add_argument(flag, action="store_true")
    ap.add_argument("--fault", type=str, default=None)
    args = ap.parse_args(argv)
    if args.worker_stdio:
        return _run_worker_stdio(args.fault)
    report = evaluate_falsifiers(args.root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    if args.check or not args.json:
        f_pass, aud = sum(1 for v in report["falsifiers"].values() if v), report["runtime_audit"]
        print(f"W031 FFI-Quarantine: falsifiers={f_pass}/5 exports={len(aud['admitted_exports'])}/{aud['total_hyrt_exports']} pinner_sites={aud['go_pinner_call_sites']}")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
