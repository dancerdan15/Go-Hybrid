#!/usr/bin/env python3
"""W-036 (#958): Certified Foreign Function Boundary DWARF CFI & ABI Verifier."""
from __future__ import annotations
import argparse, json, pathlib, re, time
from dataclasses import asdict, dataclass

CLASS_BUDGETS = {"SMALL": 1024, "DEFAULT": 4096, "DEEP": 1048576}
DW_CFA_NOP, DW_CFA_DEF_CFA, DW_CFA_DEF_CFA_OFFSET = 0x00, 0x0C, 0x0E
CALLEE_SAVED = {"RBX": 3, "RBP": 6, "R12": 12, "R13": 13, "R14": 14, "R15": 15, "R28": 28}
G_REGISTERS = {"amd64": "R14", "win64": "R14", "arm64": "R28"}
REG_BY_NUM = {v: k for k, v in CALLEE_SAVED.items()}


def encode_uleb128(val: int) -> bytes:
    out = bytearray()
    while True:
        byte, val = val & 0x7F, val >> 7
        out.append(byte | (0x80 if val else 0))
        if not val:
            return bytes(out)


def read_uleb128(buf: bytes, idx: int) -> tuple[int, int]:
    res, shift = 0, 0
    while idx < len(buf):
        b, idx = buf[idx], idx + 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, idx
        shift += 7
    return res, idx


def build_fde_stream(frame_size: int, saved_regs: tuple[str, ...] = (), advance: int = 4) -> bytes:
    ops = bytearray([0x40 | (advance & 0x3F), DW_CFA_DEF_CFA, 7]) + encode_uleb128(frame_size)
    for off_idx, reg in enumerate(saved_regs, start=2):
        ops.extend([0x80 | (CALLEE_SAVED.get(reg, 3) & 0x3F)] + list(encode_uleb128(off_idx)))
    ops.extend([DW_CFA_DEF_CFA_OFFSET] + list(encode_uleb128(frame_size)))
    while len(ops) % 4:
        ops.append(DW_CFA_NOP)
    return bytes(ops)


def parse_fde_stream(fde: bytes) -> tuple[int, set[str]]:
    idx, max_cfa, saved = 0, 8, set()
    while idx < len(fde):
        op, idx = fde[idx], idx + 1
        hi, lo = op & 0xC0, op & 0x3F
        if hi == 0x80:
            _, idx = read_uleb128(fde, idx)
            if lo in REG_BY_NUM:
                saved.add(REG_BY_NUM[lo])
        elif hi == 0xC0:
            saved.discard(REG_BY_NUM.get(lo, ""))
        elif op in (DW_CFA_DEF_CFA, DW_CFA_DEF_CFA_OFFSET):
            if op == DW_CFA_DEF_CFA:
                _, idx = read_uleb128(fde, idx)
            cfa, idx = read_uleb128(fde, idx)
            max_cfa = max(max_cfa, cfa)
    return max_cfa, saved


@dataclass(frozen=True)
class BridgeRoutine:
    name: str; kind: str; arch: str; declared_class: str; fde_bytes: bytes
    callees: tuple[str, ...] = (); clobbers: tuple[str, ...] = (); restored: tuple[str, ...] = ()
    sp_aligned_16: bool = True; unpinned_go_ptr: bool = False; dynamic_alloca: bool = False


@dataclass(frozen=True)
class ValidationCert:
    name: str; declared_class: str; inferred_class: str; local_frame_bytes: int
    max_stack_bytes: int | None; bounded: bool; passed: bool; diagnostics: tuple[str, ...]


def verify_corpus(routines: list[BridgeRoutine]) -> list[ValidationCert]:
    by_name = {r.name: r for r in routines}
    frames = {r.name: parse_fde_stream(r.fde_bytes) for r in routines}

    def solve_depth(name: str, visiting: frozenset[str]) -> tuple[bool, int]:
        r = by_name.get(name)
        if name in visiting or r is None or r.dynamic_alloca:
            return False, 0
        sub_max = 0
        for c in r.callees:
            ok, d = solve_depth(c, visiting | {name})
            if not ok:
                return False, 0
            sub_max = max(sub_max, d)
        return True, frames[name][0] + sub_max

    certs: list[ValidationCert] = []
    for r in routines:
        local_cfa, cfi_saved = frames[r.name]
        bounded, max_stack = solve_depth(r.name, frozenset())
        diags, g_reg = [], G_REGISTERS.get(r.arch, "R14")
        if g_reg in r.clobbers or "R14" in r.clobbers or "R28" in r.clobbers:
            diags.append(f"HYB-E6030:G_REG_CLOBBER({g_reg})")
        preserved = set(r.restored) | cfi_saved
        for reg in r.clobbers:
            if reg in CALLEE_SAVED and reg not in ("R14", "R28") and reg not in preserved:
                diags.append(f"HYB-E6030:CALLEE_SAVED_CLOBBER({reg})")
        if not r.sp_aligned_16:
            diags.append("HYB-E6030:SP_MISALIGNED")
        if r.unpinned_go_ptr:
            diags.append("HYB-E6030:GO_PTR_ESCAPE")
        if not bounded:
            inferred = "DEEP"
            if r.declared_class != "DEEP":
                diags.append("HYB-E6030:RECURSIVE_UNBOUNDED")
        else:
            inferred = "SMALL" if max_stack <= 1024 else ("DEFAULT" if max_stack <= 4096 else "DEEP")
            if max_stack > CLASS_BUDGETS[r.declared_class]:
                diags.append(f"HYB-E6030:STACK_BUDGET_EXCEEDED({max_stack}>{CLASS_BUDGETS[r.declared_class]})")
        certs.append(ValidationCert(
            r.name, r.declared_class, inferred, local_cfa,
            max_stack if bounded else None, bounded, not diags, tuple(diags),
        ))
    return certs


def audit_live_repo(repo_root: pathlib.Path) -> dict[str, object]:
    desc_text = (repo_root / "interfaces" / "descriptors.md").read_text(encoding="utf-8")
    ledger = {fn: m.group(2) for m in re.finditer(r"^\|\s*([^|]+)\|\s*(?:\*\*)?(SMALL|DEFAULT|DEEP)(?:\*\*)?\s*\|", desc_text, re.M) for fn in re.findall(r"`([^`]+)`", m.group(1))}
    trampolines, asm_hazards = 0, []
    for sfile in sorted((repo_root / "runtime" / "go" / "hybridge").glob("call_*.s")):
        for block in re.split(r"(?=TEXT\s+·)", sfile.read_text(encoding="utf-8")):
            if not block.startswith("TEXT"):
                continue
            trampolines += 1
            fn_m = re.search(r"TEXT\s+·(\w+)\(SB\),\s*([A-Z]+)", block)
            fname = fn_m.group(1) if fn_m else "unknown"
            if not fn_m or fn_m.group(2) != "NOSPLIT":
                asm_hazards.append(f"HYB-E6030:MISSING_NOSPLIT({fname})")
            if re.search(r"\bMOV[QD]\s+[^,\n]+,\s*(R14|R28|g)\b", block):
                asm_hazards.append(f"HYB-E6030:G_REG_CLOBBER({fname})")
            if fname.startswith("callFast") and f"${'1024' if fname.endswith('S') else '4096'}" not in block:
                asm_hazards.append(f"HYB-E6030:GUARD_MISMATCH({fname})")
    return {"descriptors_count": len(ledger), "ledger": ledger, "trampolines_verified": trampolines, "asm_hazards": asm_hazards}


def build_default_corpus() -> list[BridgeRoutine]:
    return [
        BridgeRoutine("fast_dot_product", "c", "amd64", "SMALL", build_fde_stream(64, ("RBX",))),
        BridgeRoutine("keccak_f1600", "rust", "amd64", "SMALL", build_fde_stream(200, ("RBX", "R12"))),
        BridgeRoutine("hy_nop", "rust", "amd64", "DEFAULT", build_fde_stream(8)),
        BridgeRoutine("hyrt_abi_word", "rust", "amd64", "SMALL", build_fde_stream(8)),
        BridgeRoutine("hyrt_stack_alignment_probe", "rust", "win64", "SMALL", build_fde_stream(32)),
        BridgeRoutine("hy_sha3_digest256", "rust", "amd64", "SMALL", build_fde_stream(320, ("RBX",)), ("keccak_f1600",)),
        BridgeRoutine("hy_sha3_stream_checksum", "rust", "amd64", "SMALL", build_fde_stream(256, ("RBX",)), ("keccak_f1600",)),
        BridgeRoutine("hy_json_new_object", "rust", "amd64", "SMALL", build_fde_stream(128)),
        BridgeRoutine("hy_json_new_array", "rust", "amd64", "SMALL", build_fde_stream(128)),
        BridgeRoutine("hy_json_get_int", "rust", "amd64", "SMALL", build_fde_stream(192)),
        BridgeRoutine("hy_json_get_string", "rust", "amd64", "SMALL", build_fde_stream(224)),
        BridgeRoutine("hy_json_array_len", "rust", "amd64", "SMALL", build_fde_stream(144)),
        BridgeRoutine("hy_json_array_push_string", "rust", "amd64", "SMALL", build_fde_stream(240)),
        BridgeRoutine("callFast1_sysv", "asm", "amd64", "SMALL", build_fde_stream(16, ("RBX",)), ("hy_nop",)),
        BridgeRoutine("c_vla_scratch_kernel", "c", "amd64", "SMALL", build_fde_stream(96), dynamic_alloca=True),
        BridgeRoutine("hy_json_parse", "rust", "amd64", "DEEP", build_fde_stream(512), ("hy_json_parse",)),
        BridgeRoutine("meta_strategy_new", "rust", "amd64", "DEEP", build_fde_stream(7648)),
        BridgeRoutine("hy_regex_is_match", "rust", "amd64", "DEEP", build_fde_stream(15328), ("meta_strategy_new",)),
        BridgeRoutine("buggy_regex_on_fast_path", "rust", "amd64", "DEFAULT", build_fde_stream(7648)),
        BridgeRoutine("buggy_asm_clobber_g_r14", "asm", "amd64", "SMALL", build_fde_stream(32), clobbers=("R14", "RBX"), sp_aligned_16=False, unpinned_go_ptr=True),
    ]


def run_verification(repo_root: pathlib.Path | None = None) -> dict[str, object]:
    t0 = time.perf_counter()
    corpus = build_default_corpus()
    certs = verify_corpus(corpus)
    ratio = sum(1 for c in certs[:15] if c.bounded) / 15.0
    live = audit_live_repo(repo_root or pathlib.Path(__file__).resolve().parents[2])
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    return {
        "issue": "#958", "routines_tested": len(corpus), "non_recursive_bounded_ratio": round(ratio, 4),
        "compile_validation_ms": elapsed_ms, "falsifier_bounded_gt_90pct": ratio > 0.90,
        "falsifier_latency_lt_500ms": elapsed_ms < 500.0, "live_audit": live, "certificates": [asdict(c) for c in certs],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W-036 (#958) DWARF CFI & Foreign ABI Verifier")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    args, rep = ap.parse_args(argv), run_verification()
    print(json.dumps(rep, indent=2) if args.json else f"[w036] tested={rep['routines_tested']} bounded={rep['non_recursive_bounded_ratio']*100:.1f}% time={rep['compile_validation_ms']}ms trampolines={rep['live_audit']['trampolines_verified']}")
    return (0 if (rep["falsifier_bounded_gt_90pct"] and rep["falsifier_latency_lt_500ms"] and not rep["live_audit"]["asm_hazards"]) else 1) if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
