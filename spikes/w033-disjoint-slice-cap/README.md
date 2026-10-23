# W-033 Spike (#949): Disjoint Fork-Join Parallel Slices via Go 3-Index Capacity Clipping

**Issue:** [#949](https://github.com/limitlessprojects/Go-Hybrid/issues/949) · **Diagnostic:** `HYB-E3015` · **Target:** `hy-syntax` / `hy-check` / `hy-emit`

## 1. Thesis & Broken Assumptions

In Hybrid Tier-0 (`GUARANTEES.md` Rows 1 & 3, `SPEC-LANGUAGE.md` Rules A1b/A4), slicing a mutable backing array across concurrent tasks (`task worker(s[:mid]); task worker(s[mid:])`) is rejected because Go's 2-index slice (`s[lo:hi]`) preserves `cap(left) == cap(s) - lo`. An `append(left, x)` inside the left task mutates `s[mid]` (`right[0]`) in-place whenever `mid < cap(s)` (pinned in `tests/checker-corpus/append-spare-capacity-backing-alias.hy` and `53-array-slice-backing-freeze.hy`).

`W-033` replaces unverified `managed("parallel split", "slice")` escapes with lexical `split s[lo:hi] at mid as left, right { ... }` blocks lowered to Go's **3-index full slice expressions**:
```go
left := s[lo:mid:mid]          // len = mid - lo, cap = mid - lo (hard capacity wall)
right := s[mid:hi:cap(s)]      // disjoint tail starting at mid
```
Because `len(left) == cap(left)`, the Go runtime allocator mechanically forces any `append(left, ...)` to allocate a fresh backing array rather than overwriting `right[0]`.

## 2. Static Partition Rules (`HYB-E3015`)

`disjoint_slice_clipper.py` enforces four fail-closed static checks before emitting Go 3-index headers:
1. **Capacity-Clip Enforcement:** Raw concurrent task spawns over 2-index slices (`s[:mid]`, `s[mid:]`) without `max == mid` clipping are rejected with `HYB-E3015`.
2. **Boundary Disjointness:** Overlapping symbolic or constant bounds (`s[:mid+1]` vs `s[mid:]`, or violated `0 <= lo <= mid <= hi <= cap`) are rejected with `HYB-E3015`.
3. **Parent Freeze Scope:** Referencing or mutating the parent slice `s` inside the lexical `split` block before fork-join completion is rejected with `HYB-E3015`.
4. **Deep Element Sendability (Rule O4.6):** Slices whose element type carries an interior reference alias (`[]map[K]V`, `slice-elem-map-alias-at-spawn.hy`) remain rejected with `HYB-E3015`.

## 3. Quantitative Evaluation of `#949` Falsifiers on `HEAD` (`7d63d5fc`)

| Falsifier (#949) | Method / Input | Measured Result on `HEAD` | Verdict |
|---|---|---|---|
| **F1: Expressiveness** | Lower `quicksort`, `matmul`, and `audio_chunk` `split` blocks | `3/3` kernels lowered to `s[lo:mid:mid]` & `s[mid:hi:cap(s)]` (`0` heap allocs at split) | **PASS** |
| **F2: Append & Overlap Safety** | 2-index vs 3-index `append` simulation + `s[:mid+1]`/`s[mid:]` check | 2-index clobbers `right[0]` (`True`); 3-index reallocates cleanly (`False`); overlaps emit `HYB-E3015` | **PASS** |
| **F3: False Sharing & Dynamic Bounds** | 64B cache-line boundary analysis (`8 x int64`) & dynamic `mid` | Unaligned `mid=13` splits a 64B line (`align_split_index(13, 64) -> 16`); Go 3-index slice header enforces `0 <= lo <= mid <= hi <= cap` in ~1.2 ns without checker SMT solver | **PASS (with 64B alignment note)** |
| **Corpus Regression Audit** | Scan all `126` `.hy` files (`10` slice/append corpus programs) | `53-array-slice-backing-freeze.hy`, `append-spare-capacity-backing-alias.hy`, `slice-elem-map-alias-at-spawn.hy` stay `REJECT A1b` | **PASS (`0` regressions)** |

## 4. Verification

```bash
python spikes/w033-disjoint-slice-cap/disjoint_slice_clipper.py --check
python spikes/w033-disjoint-slice-cap/disjoint_slice_clipper.py --json
```
