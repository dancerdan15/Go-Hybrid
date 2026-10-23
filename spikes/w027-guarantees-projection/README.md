# W-027 (`#1193`) — Executable Projection of `GUARANTEES.md`

Pre-registered executable spike for [Issue #1193](https://github.com/limitlessprojects/Go-Hybrid/issues/1193): *"A guarantee you cannot execute is not a guarantee: make GUARANTEES.md a projection of the shipped test suite"*.

## Architecture (`project_guarantees.py`)
1. **Diagnostic Registry & Severity Coherence:** Cross-checks cited `HYB-E*` / `E31xx` codes against `compiler/crates/hy-diag/data/diagnostics.json` (`hy-diag/src/lib.rs`) and computes the reverse diff of registered Tier-0 codes (`E3*`/`E8*`) not yet projected in `GUARANTEES.md`.
2. **SPEC & Witness Resolution:** Verifies `§x.y` sections and `O*`/`C*`/`A*` rules against `SPEC-LANGUAGE.md` / `SPEC.md`, and checks whether each cited test/corpus witness exists on disk, lives in the shipped tree (not `spikes/`), and is wired into `.github/workflows/ci.yml`.
3. **Runtime Boundary & Census Drift:** Audits `runtime/rust/hyrt/src/lib.rs` and `runtime/go/jsonbinding/json_test.go` for generation-tag enforcement, and diffs the `GUARANTEES.md` evidence census against live `tests/checker-corpus/*.hy` headers.

## Live Repository Measurement (`HEAD`)
| Metric | Live Result | Verdict |
|---|---|---|
| Positive claims verified (`G-01`..`G-07`) | `7 / 10` (`VERIFIED`) | Backed by shipped corpus + `hy-check` tests in CI |
| Positive claims partial (`G-20`) | `1 / 10` (`PARTIAL`) | Backed by static `gates/G2.md` measurement log only |
| Positive claims unverified (`G-18`, `G-19`) | `2 / 10` (`UNVERIFIED`) | `G-18`: prose-only (`hyrt` has `0` unit tests); `G-19`: cites `spikes/spike3-memory/...` and shipped `hyrt` hardcodes `let gen = 1u32;` |
| Cited `HYB-*` codes registered | `10 / 10` (`100%`) | `HYB-E0003`, `E0004`, `E3002`, `E3003`, `E3005`, `E3101`..`E3104`, `E8001` valid; `9` additional `E30xx` codes unprojected |
| Evidence inventory census drift | `67/32/2` vs `86/38/2` | Live corpus holds `126` files (`86 REJECT / 38 ACCEPT / 2 ACCEPT-MANAGED`) |

## Usage & CI Gate Integration
- `python3 spikes/w027-guarantees-projection/project_guarantees.py --check` (Stage 1 CI gate: enforces registry/spec/witness structural integrity; exits `0` on `HEAD`).
- `python3 spikes/w027-guarantees-projection/project_guarantees.py --strict` (Stage 2 CI gate: fails on any `UNVERIFIED`/`PARTIAL` positive row or census drift).
