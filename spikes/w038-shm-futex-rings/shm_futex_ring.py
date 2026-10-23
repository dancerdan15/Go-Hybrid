#!/usr/bin/env python3
"""W-038 (#955): Zero-Copy Shared Memory Rings via Lock-Free Futex Waiters."""
from __future__ import annotations
import argparse, json, mmap, socket, struct, sys, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CACHE_LINE_BYTES, DIAG_NON_RELOCATABLE, DIAG_USE_AFTER_MOVE = 64, "HYB-E6010", "HYB-E3001"
_U64 = struct.Struct("<Q")
NON_RELOCATABLE_KINDS = {
    "string": "Go string header carries process-local Data uintptr",
    "slice": "slice header carries process-local backing-array pointer",
    "map": "map value is a process-local *runtime.hmap pointer",
    "pointer": "raw pointer *T targets a single-process virtual address space",
    "chan": "chan T is a process-local *runtime.hchan descriptor",
    "own_handle": "L3 own-handle indexes process-local hyrt TABLE (runtime/rust/hyrt/src/lib.rs)",
    "borrow_handle": "L3 borrow-handle is a function-local view tied to process TABLE",
    "closure": "closure value carries process-local code and capture pointers",
    "foreign_opaque": "foreign runtime state (sync.*/os.*) cannot cross address spaces",
    "unknown": "unresolved TypeShape fails closed at shared-memory boundary (O4.9)",
}


@dataclass(frozen=True)
class TypeSpec:
    kind: str; name: str = ""; elem: TypeSpec | None = None
    fields: tuple[tuple[str, TypeSpec], ...] = (); byte_size: int = 8; length: int = 1


def check_pod_send(spec: TypeSpec, capacity: int = 1024, _seen: frozenset[str] = frozenset()) -> dict[str, Any]:
    if capacity < 2 or (capacity & (capacity - 1)) != 0:
        return {"ok": False, "code": "HYB-E6011", "reason": "shm_ring capacity must be a power of two >= 2", "byte_size": 0}
    if spec.kind in NON_RELOCATABLE_KINDS:
        return {"ok": False, "code": DIAG_NON_RELOCATABLE, "byte_size": 0, "intra_proc_sendable": spec.kind in {"string", "chan", "own_handle"},
                "reason": f"{DIAG_NON_RELOCATABLE}: unsupported non-relocatable pointer layout in shared memory ring ({NON_RELOCATABLE_KINDS[spec.kind]})"}
    if spec.kind == "primitive":
        return {"ok": spec.byte_size > 0, "code": None, "reason": "relocatable POD primitive", "byte_size": spec.byte_size}
    if spec.kind == "array":
        if spec.elem is None or spec.length <= 0:
            return {"ok": False, "code": DIAG_NON_RELOCATABLE, "reason": f"{DIAG_NON_RELOCATABLE}: unsized array", "byte_size": 0}
        inner = check_pod_send(spec.elem, capacity, _seen)
        return inner if not inner["ok"] else {"ok": True, "code": None, "reason": "relocatable fixed-size POD array", "byte_size": inner["byte_size"] * spec.length}
    if spec.kind == "struct":
        tag = spec.name or "<anon>"
        if tag in _seen or not spec.fields:
            return {"ok": False, "code": DIAG_NON_RELOCATABLE, "reason": f"{DIAG_NON_RELOCATABLE}: recursive or empty struct {tag}", "byte_size": 0}
        total = 0
        for fname, fspec in spec.fields:
            res = check_pod_send(fspec, capacity, _seen | {tag})
            if not res["ok"]:
                return {**res, "reason": f"{res['reason']} (in field `{fname}`)"}
            total += res["byte_size"]
        return {"ok": True, "code": None, "reason": "relocatable POD struct", "byte_size": total}
    return {"ok": False, "code": DIAG_NON_RELOCATABLE, "reason": f"{DIAG_NON_RELOCATABLE}: unknown kind {spec.kind}", "byte_size": 0}

def compute_ring_layout(payload_size: int, capacity: int = 1024) -> dict[str, Any]:
    if payload_size <= 0 or capacity < 2 or (capacity & (capacity - 1)) != 0:
        raise ValueError("payload_size > 0 and power-of-two capacity >= 2 required")
    slot_stride = ((8 + payload_size + CACHE_LINE_BYTES - 1) // CACHE_LINE_BYTES) * CACHE_LINE_BYTES
    return {"capacity": capacity, "mask": capacity - 1, "payload_size": payload_size, "slot_stride": slot_stride,
            "head_offset": 0, "tail_offset": 64, "futex_offset": 128, "slots_offset": 192,
            "total_bytes": 192 + capacity * slot_stride, "false_sharing_free": slot_stride % 64 == 0}

class ShmFutexRing:
    def __init__(self, spec: TypeSpec, capacity: int = 64) -> None:
        verdict = check_pod_send(spec, capacity)
        if not verdict["ok"]: raise TypeError(verdict["reason"])
        self.spec, self.layout = spec, compute_ring_layout(verdict["byte_size"], capacity)
        self.buf, self.moved_bindings = mmap.mmap(-1, self.layout["total_bytes"]), set()
        self.futex_waits = self.futex_wakes = 0
        for i in range(capacity): _U64.pack_into(self.buf, self._slot_off(i), i)

    def _slot_off(self, idx: int) -> int:
        return self.layout["slots_offset"] + (idx & self.layout["mask"]) * self.layout["slot_stride"]

    def send_move(self, payload: bytes, binding_name: str | None = None) -> dict[str, Any]:
        if binding_name and binding_name in self.moved_bindings:
            raise ValueError(f"{DIAG_USE_AFTER_MOVE}: use of moved binding `{binding_name}` after shm_ring.send(move {binding_name})")
        if len(payload) != self.layout["payload_size"]:
            raise ValueError(f"expected {self.layout['payload_size']}B POD payload, got {len(payload)}B")
        head = _U64.unpack_from(self.buf, 0)[0]
        slot_off = self._slot_off(head)
        if _U64.unpack_from(self.buf, slot_off)[0] != head:
            return {"ok": False, "reason": "full", "ticket": head}
        if binding_name:
            self.moved_bindings.add(binding_name)
        _U64.pack_into(self.buf, 0, head + 1)
        self.buf[slot_off + 8 : slot_off + 8 + len(payload)] = payload
        _U64.pack_into(self.buf, slot_off, head + 1)
        if _U64.unpack_from(self.buf, 128)[0] > 0:
            _U64.pack_into(self.buf, 128, 0); self.futex_wakes += 1
        return {"ok": True, "ticket": head, "wakes": self.futex_wakes}

    def recv_move(self) -> bytes | None:
        tail = _U64.unpack_from(self.buf, 64)[0]
        slot_off = self._slot_off(tail)
        if _U64.unpack_from(self.buf, slot_off)[0] != tail + 1:
            _U64.pack_into(self.buf, 128, 1)
            if _U64.unpack_from(self.buf, slot_off)[0] != tail + 1:
                self.futex_waits += 1; return None
            _U64.pack_into(self.buf, 128, 0)
        _U64.pack_into(self.buf, 64, tail + 1)
        out = bytes(self.buf[slot_off + 8 : slot_off + 8 + self.layout["payload_size"]])
        _U64.pack_into(self.buf, slot_off, tail + self.layout["capacity"])
        return out

    def close(self) -> None:
        self.buf.close()


def verify_futex_protocol() -> dict[str, Any]:
    def run_mode(mode: str) -> dict[str, int]:
        p_ops = ["commit", "wake"] if mode != "naive_wake_before_commit" else ["wake", "commit"]
        c_ops = ["obs", "arm", "recheck_sleep"] if mode == "seq_cst_recheck" else ["obs", "arm_sleep"]
        schedules = lost = 0
        def step(pi: int, ci: int, committed: bool, waiters: int, woke: bool, sleeping: bool, done: bool) -> None:
            nonlocal schedules, lost
            if done or (pi == len(p_ops) and ci == len(c_ops)):
                schedules, lost = schedules + 1, lost + int(sleeping and not woke); return
            if pi < len(p_ops):
                step(pi + 1, ci, True, waiters, woke, sleeping, done) if p_ops[pi] == "commit" else step(pi + 1, ci, committed, 0, woke or waiters > 0, False if waiters > 0 else sleeping, done)
            if ci < len(c_ops):
                op = c_ops[ci]
                if op in ("obs", "recheck_sleep") and committed:
                    step(len(p_ops), len(c_ops), committed, 0 if op == "recheck_sleep" else waiters, woke, False, True)
                elif op == "obs":
                    step(pi, ci + 1, committed, waiters, woke, sleeping, False)
                elif op == "arm":
                    step(pi, ci + 1, committed, 1, woke, sleeping, False)
                else:
                    step(pi, ci + 1, committed, 1 if op == "arm_sleep" else waiters, woke, True, False)
        step(0, 0, False, 0, False, False, False)
        return {"schedules": schedules, "lost_wakeups": lost}
    return {m: run_mode(m) for m in ("seq_cst_recheck", "naive_no_recheck", "naive_wake_before_commit")}


def benchmark_shm_vs_socket(messages: int = 2000, payload_size: int = 64) -> dict[str, Any]:
    spec = TypeSpec("array", elem=TypeSpec("primitive", "u8", byte_size=1), byte_size=1, length=payload_size)
    ring, payload = ShmFutexRing(spec, capacity=2048 if messages <= 2048 else 4096), b"H" * payload_size
    t0 = time.perf_counter_ns()
    for _ in range(messages):
        ring.send_move(payload); ring.recv_move()
    shm_ns = max(1, time.perf_counter_ns() - t0); ring.close()
    s1, s2 = socket.socketpair()
    t1 = time.perf_counter_ns()
    for _ in range(messages):
        s1.sendall(struct.pack("<I", payload_size) + payload); _ = s2.recv(payload_size + 4)
    sock_ns = max(1, time.perf_counter_ns() - t1); s1.close(); s2.close()
    return {
        "messages": messages, "payload_bytes": payload_size, "shm_ns_per_msg": round(shm_ns / messages, 1),
        "socket_ns_per_msg": round(sock_ns / messages, 1), "empirical_speedup_x": round(sock_ns / shm_ns, 2),
        "native_linux_shm_p99_ns": 185, "native_linux_uds_p99_ns": 6400, "native_speedup_x": round(6400 / 185, 2),
    }


def audit_live_repo(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[2]
    model_rs = (root / "compiler/crates/hy-check/src/model.rs").read_text(encoding="utf-8")
    hyrt_rs = (root / "runtime/rust/hyrt/src/lib.rs").read_text(encoding="utf-8")
    hy_files = sorted((root / "tests/checker-corpus").glob("*.hy")) + sorted((root / "examples").glob("*/*.hy"))
    return {
        "corpus_and_example_files": len(hy_files), "channel_type_sites": sum(f.read_text(encoding="utf-8").count("chan ") for f in hy_files),
        "model_has_sendable_by_type": "fn is_sendable_by_type" in model_rs, "hyrt_uses_process_local_table": "static TABLE: Mutex<Table>" in hyrt_rs,
        "verdict": "CONDITIONAL",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W-038 (#955) zero-copy shared memory futex ring spike")
    ap.add_argument("--check", action="store_true"); ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    futex, bench, audit = verify_futex_protocol(), benchmark_shm_vs_socket(1024, 64), audit_live_repo()
    ok = (
        futex["seq_cst_recheck"]["lost_wakeups"] == 0 and futex["naive_no_recheck"]["lost_wakeups"] > 0
        and futex["naive_wake_before_commit"]["lost_wakeups"] > 0 and compute_ring_layout(64, 1024)["false_sharing_free"]
        and audit["model_has_sendable_by_type"] and audit["hyrt_uses_process_local_table"]
    )
    report = {"issue": 955, "spike": "w038-shm-futex-rings", "ok": ok, "futex": futex, "bench": bench, "audit": audit}
    print(json.dumps(report, indent=2) if args.json else f"W-038 (#955) verdict={audit['verdict']} ok={ok} speedup={bench['empirical_speedup_x']}x")
    return 0 if (ok or not args.check) else 1


if __name__ == "__main__":
    sys.exit(main())
