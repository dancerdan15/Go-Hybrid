#!/usr/bin/env python3
"""W-033 (#949): Disjoint Fork-Join Parallel Slices via Go 3-Index Capacity Clipping."""
from __future__ import annotations
import argparse, json, re, sys
from dataclasses import asdict, dataclass
from pathlib import Path

HYB_E3015 = "HYB-E3015"
SPLIT_RE = re.compile(
    r"split\s+(?P<base>[A-Za-z_]\w*)(?:\[(?P<lo>[^:\]]*):(?P<hi>[^\]]*)\])?\s+"
    r"at\s+(?P<mid>[^\s]+)\s+(?:as|into)\s+(?P<left>[A-Za-z_]\w*)\s*,\s*(?P<right>[A-Za-z_]\w*)\s*\{(?P<body>.*?)\}",
    re.DOTALL,
)
TASK_SLICE_RE = re.compile(
    r"task\s+[A-Za-z_]\w*\s*\([^)]*?(?P<base>[A-Za-z_]\w*)\[(?P<lo>[^:\]]*):(?P<hi>[^:\]]*)(?::(?P<max>[^\]]*))?\]"
)


@dataclass(frozen=True)
class SlicePartition:
    base: str
    lo: int | str = 0
    mid: int | str = "mid"
    hi: int | str = "len"
    cap: int | str = "cap"
    left_hi: int | str | None = None
    right_lo: int | str | None = None
    three_index: bool = True
    elem_may_share: bool = False
    parent_mutated_in_split: bool = False


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    line: int = 1


def _parse_sym_offset(expr: int | str) -> tuple[str, int] | None:
    if isinstance(expr, int):
        return ("__const__", expr)
    s = str(expr).replace(" ", "")
    if re.fullmatch(r"-?\d+", s):
        return ("__const__", int(s))
    m = re.fullmatch(r"([A-Za-z_]\w*)([+-]\d+)?", s)
    return (m.group(1), int(m.group(2) or 0)) if m else None


def check_partition(part: SlicePartition) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    if part.parent_mutated_in_split:
        diags.append(Diagnostic(HYB_E3015, f"parent slice '{part.base}' accessed while split handles are active"))
    if part.elem_may_share:
        diags.append(Diagnostic(HYB_E3015, f"slice '{part.base}' element type carries interior reference alias (O4.6)"))
    if not part.three_index:
        diags.append(Diagnostic(HYB_E3015, f"unclipped 2-index slice on '{part.base}' leaves spare capacity across tasks"))
    l_hi = part.mid if part.left_hi is None else part.left_hi
    r_lo = part.mid if part.right_lo is None else part.right_lo
    lh, rl = _parse_sym_offset(l_hi), _parse_sym_offset(r_lo)
    if lh and rl and lh[0] == rl[0] and lh[1] > rl[1]:
        diags.append(Diagnostic(HYB_E3015, f"overlapping slice bounds on '{part.base}': left hi ({l_hi}) > right lo ({r_lo})"))
    pts = [_parse_sym_offset(x) for x in (0, part.lo, l_hi, r_lo, part.hi, part.cap)]
    if any(a and b and a[0] == b[0] and a[1] > b[1] for i, a in enumerate(pts) for j, b in enumerate(pts[i + 1 :], i + 1) if (i, j) != (2, 3)):
        diags.append(Diagnostic(HYB_E3015, f"invalid split bounds on '{part.base}': require 0 <= {part.lo} <= {part.mid} <= {part.hi} <= {part.cap}"))
    return diags


def check_raw_task_slices(src: str) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for m in SPLIT_RE.finditer(src):
        base, mid, body = m.group("base"), m.group("mid").strip(), m.group("body")
        lo_s, hi_s = (m.group("lo") or "0").strip() or "0", (m.group("hi") or "len").strip() or "len"
        parent_used = bool(re.search(rf"\b{re.escape(base)}\b", body))
        vals = [ int(x) if re.fullmatch(r"-?\d+", x) else x for x in (lo_s, mid, hi_s) ]
        cap_v = vals[2] if isinstance(vals[2], int) else "cap"
        diags.extend(check_partition(SlicePartition(base, vals[0], vals[1], vals[2], cap_v, parent_mutated_in_split=parent_used)))
    stripped = SPLIT_RE.sub("", src)
    spawns: dict[str, list[tuple[str, str, str | None]]] = {}
    for m in TASK_SLICE_RE.finditer(stripped):
        spawns.setdefault(m.group("base"), []).append((m.group("lo").strip() or "0", m.group("hi").strip() or "len", m.group("max")))
    for base, slices in spawns.items():
        for (lo1, hi1, max1), (lo2, hi2, _max2) in zip(slices, slices[1:]):
            three_idx = bool(max1 and max1.strip() == hi1)
            diags.extend(check_partition(SlicePartition(base, lo1, hi1, hi2, "cap", left_hi=hi1, right_lo=lo2, three_index=three_idx)))
    return diags


def lower_split_stmt(src: str) -> str:
    diags = check_raw_task_slices(src)
    if diags:
        raise ValueError(f"{diags[0].code}: {diags[0].message}")
    def _repl(m: re.Match[str]) -> str:
        b, lo, hi = m.group("base"), (m.group("lo") or "0").strip() or "0", (m.group("hi") or "").strip() or f"len({m.group('base')})"
        mid, left, right, body = m.group("mid").strip(), m.group("left"), m.group("right"), m.group("body").strip()
        return f"{left} := {b}[{lo}:{mid}:{mid}]\n{right} := {b}[{mid}:{hi}:cap({b})]\n{body}"
    return SPLIT_RE.sub(_repl, src)


def simulate_append_isolation(backing: list[int], mid: int, append_val: int, three_index: bool = True) -> dict[str, object]:
    if not (0 <= mid <= len(backing)):
        raise ValueError(f"{HYB_E3015}: mid {mid} out of bounds [0, {len(backing)}]")
    arr = list(backing)
    left_cap = mid if three_index else len(arr)
    if mid < left_cap:
        arr[mid] = append_val
        left_out, reallocated = arr[: mid + 1], False
    else:
        left_out, reallocated = arr[:mid] + [append_val], True
    right_first = arr[mid] if mid < len(arr) else None
    clobbered = (not three_index) and (mid < len(backing)) and (right_first == append_val)
    return {"three_index": three_index, "left_cap": left_cap, "left_reallocated": reallocated,
            "right_first": right_first, "clobbered": clobbered, "race_detected": clobbered}


def align_split_index(mid: int, length: int, elem_bytes: int = 8, line_bytes: int = 64) -> int:
    if elem_bytes <= 0 or line_bytes <= 0 or length < 0:
        raise ValueError(f"{HYB_E3015}: invalid alignment parameters (length={length}, elem_bytes={elem_bytes}, line_bytes={line_bytes})")
    stride = max(1, line_bytes // elem_bytes)
    aligned = ((max(0, mid) + stride // 2) // stride) * stride
    return max(0, min(length, aligned))


def audit_corpus(repo_root: Path | None = None) -> dict[str, object]:
    root = repo_root or Path(__file__).resolve().parents[2]
    corpus_dir = root / "tests" / "checker-corpus"
    files = sorted(corpus_dir.glob("*.hy")) if corpus_dir.exists() else []
    slice_files = [f.name for f in files if "slice" in f.name or "append" in f.name]
    pinned = ["53-array-slice-backing-freeze.hy", "append-spare-capacity-backing-alias.hy", "slice-elem-map-alias-at-spawn.hy"]
    pinned_ok = all((corpus_dir / p).exists() and "REJECT" in (corpus_dir / p).read_text(encoding="utf-8") for p in pinned) if files else False
    return {"total_corpus_files": len(files), "slice_append_files": len(slice_files), "pinned_rejects_intact": pinned_ok}


def evaluate_falsifiers(repo_root: Path | None = None) -> dict[str, object]:
    kernels = {
        "quicksort": "split buf at pivot as left, right { task qsort(move left); task qsort(move right) }",
        "matmul": "split rows at mid as top, bot { task gemm(move top); task gemm(move bot) }",
        "audio_chunk": "split pcm[0:4096] at 2048 as ch_a, ch_b { task dsp(move ch_a); task dsp(move ch_b) }",
    }
    lowered = {k: lower_split_stmt(v) for k, v in kernels.items()}
    overlap_diags = check_raw_task_slices("task worker(s[:mid+1])\ntask worker(s[mid:])")
    unclipped_diags = check_raw_task_slices("task worker(s[:mid])\ntask worker(s[mid:])")
    sim_2idx = simulate_append_isolation([10, 20, 30, 40], 2, 99, three_index=False)
    sim_3idx = simulate_append_isolation([10, 20, 30, 40], 2, 99, three_index=True)
    corpus = audit_corpus(repo_root)
    return {
        "spike": "W-033", "issue": 949,
        "f1_expressiveness": {"kernels_lowered": len(lowered), "all_three_index": all(":cap(" in code for code in lowered.values())},
        "f2_safety": {"two_index_clobbers": sim_2idx["clobbered"], "three_index_clobbers": sim_3idx["clobbered"],
                      "overlap_rejected_e3015": any(d.code == HYB_E3015 for d in overlap_diags),
                      "unclipped_rejected_e3015": any(d.code == HYB_E3015 for d in unclipped_diags)},
        "f3_false_sharing": {"cache_line_bytes": 64, "unaligned_mid": 13, "aligned_mid": align_split_index(13, 64)},
        "corpus_audit": corpus,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W-033 (#949) Disjoint Slice Capacity Clipper")
    ap.add_argument("--check", action="store_true", help="Run self-check and falsifier suite")
    ap.add_argument("--json", action="store_true", help="Emit JSON report")
    args = ap.parse_args(argv)
    report = evaluate_falsifiers()
    ok = (report["f1_expressiveness"]["all_three_index"] and report["f2_safety"]["two_index_clobbers"]
          and not report["f2_safety"]["three_index_clobbers"] and report["f2_safety"]["overlap_rejected_e3015"])
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"W-033 (#949) Disjoint Slice Capacity Clipping: {'PASS' if ok else 'FAIL'} (corpus={report['corpus_audit']['total_corpus_files']})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
