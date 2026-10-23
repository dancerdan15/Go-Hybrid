#!/usr/bin/env python3
"""Unit and Fail-to-Pass integration tests for project_guarantees.py (W-027 / #1193)."""
from __future__ import annotations
import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path

SPIKE_DIR = Path(__file__).resolve().parents[1]
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

import project_guarantees as pg  # noqa: E402


def _write_synthetic_repo(
    root: Path, *, extra_code: str = "", witness_link: str = "[01_move_reject.hy](tests/checker-corpus/01_move_reject.hy)",
    create_witness: bool = True, spec_ref: str = "§4.1, O1", census_line: str = "Current census: **1 REJECT / 0 ACCEPT / 0 ACCEPT-MANAGED**",
) -> None:
    diag_dir = root / "compiler/crates/hy-diag/data"
    diag_dir.mkdir(parents=True, exist_ok=True)
    diag_dir.joinpath("diagnostics.json").write_text(json.dumps({"codes": [{"code": "HYB-E3002", "severity": "error", "category": "tier0"}]}), encoding="utf-8")
    root.joinpath("SPEC-LANGUAGE.md").write_text("# Spec\nSection §4.1 enforces rule O1.\n", encoding="utf-8")
    corpus_dir = root / "tests/checker-corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    if create_witness:
        corpus_dir.joinpath("01_move_reject.hy").write_text("// EXPECTED-VERDICT: REJECT\n// HYB-E3002\n", encoding="utf-8")
    code_str = f"HYB-E3002{', ' + extra_code if extra_code else ''}"
    root.joinpath("GUARANTEES.md").write_text(
        "# Guarantees\n\n| # | Surface | Action | Claim | Evidence |\n|---|---|---|---|---|\n"
        f"| 1 | Static (`hy-check`) | Move check (`{code_str}`, {spec_ref}) | **Guaranteed**: move checked | {witness_link} |\n"
        f"| 2 | Go runtime | Goroutine scheduling | **Inherited from Go** | Go runtime |\n\n{census_line}\n",
        encoding="utf-8",
    )


class GuaranteesProjectionTests(unittest.TestCase):
    def test_live_head_projection_invariants(self) -> None:
        rep = pg.project_guarantees(pg.REPO_ROOT)
        s, bd = rep["summary"], rep["bidirectional_diagnostics"]
        self.assertEqual(s["total_rows"], 20)
        self.assertEqual([r["row_id"] for r in rep["rows"]], [f"G-{i:02d}" for i in range(1, 21)])
        self.assertEqual(s["positive_rows"], 10)
        self.assertEqual(sum(1 for r in rep["rows"] if r["claim_kind"] != "positive"), 10)
        self.assertEqual(s["positive_verified"], 7)
        self.assertEqual(s["positive_partial"], 1)
        self.assertEqual(s["positive_unverified"], 2)
        self.assertEqual(s["unverified_or_partial_positive_ids"], ["G-18", "G-19", "G-20"])
        self.assertTrue(s["all_cited_codes_registered"])
        self.assertTrue(s["all_spec_refs_resolved"])
        self.assertEqual(len(bd["cited_in_guarantees"]), 10)
        self.assertEqual(bd["missing_or_invalid_in_registry"], [])
        self.assertEqual(bd["broken_executable_witnesses"], [])
        self.assertGreaterEqual(s["corpus_census"]["REJECT"], 82)
        self.assertEqual(s["corpus_census"]["ACCEPT"], 38)
        self.assertEqual(s["corpus_census"]["ACCEPT-MANAGED"], 2)
        self.assertEqual(s["corpus_census"].get("NO-VERDICT", 0), 0)
        self.assertTrue(any("67/32/2" in d for d in rep["inventory_drift"]))
        rows_by_id = {r["row_id"]: r for r in rep["rows"]}
        for gid in (f"G-{i:02d}" for i in range(1, 8)):
            self.assertEqual(rows_by_id[gid]["projected_status"], "VERIFIED")
        self.assertEqual(rows_by_id["G-18"]["projected_status"], "UNVERIFIED")
        self.assertEqual(rows_by_id["G-19"]["projected_status"], "UNVERIFIED")
        self.assertEqual(rows_by_id["G-20"]["projected_status"], "PARTIAL")
        self.assertTrue(s["runtime_boundary_audit"]["hyrt_hardcoded_gen_1"])
        self.assertEqual(s["runtime_boundary_audit"]["hyrt_unit_tests"], 0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pg.main(["--root", str(pg.REPO_ROOT), "--check"]), 0)
            self.assertEqual(pg.main(["--root", str(pg.REPO_ROOT), "--strict"]), 1)

    def test_synthetic_all_verified_passes_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_synthetic_repo(root)
            rep = pg.project_guarantees(root)
            self.assertEqual(rep["summary"]["positive_verified"], 1)
            self.assertEqual(rep["summary"]["unverified_or_partial_positive_ids"], [])
            self.assertEqual(rep["inventory_drift"], [])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pg.main(["--root", str(root), "--check"]), 0)
                self.assertEqual(pg.main(["--root", str(root), "--strict"]), 0)

    def test_unregistered_diagnostic_code_fails_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_synthetic_repo(root, extra_code="HYB-E9999")
            rep = pg.project_guarantees(root)
            self.assertIn("HYB-E9999", rep["bidirectional_diagnostics"]["missing_or_invalid_in_registry"])
            self.assertFalse(rep["summary"]["all_cited_codes_registered"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pg.main(["--root", str(root), "--check"]), 1)

    def test_missing_witness_and_unresolved_spec_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_synthetic_repo(
                root,
                witness_link="[99_missing.hy](tests/checker-corpus/99_missing.hy)",
                create_witness=False,
                spec_ref="§99.9, O99",
                census_line="Current census: **5 REJECT / 0 ACCEPT / 0 ACCEPT-MANAGED**",
            )
            rep = pg.project_guarantees(root)
            bd = rep["bidirectional_diagnostics"]
            self.assertIn("tests/checker-corpus/99_missing.hy", bd["broken_executable_witnesses"])
            self.assertEqual(bd["unresolved_spec_refs"], ["O99", "§99.9"])
            self.assertEqual(rep["rows"][0]["projected_status"], "UNVERIFIED")
            self.assertEqual(len(rep["inventory_drift"]), 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pg.main(["--root", str(root), "--check"]), 1)
                self.assertEqual(pg.main(["--root", str(root), "--strict"]), 1)

    def test_adversarial_vectors_and_edge_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside_hy = base / "outside.hy"
            outside_hy.write_text("// EXPECTED-VERDICT: REJECT\n// HYB-E3002\n", encoding="utf-8")
            root = base / "repo"
            root.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pg.main(["--root", str(root), "--check"]), 1)
            _write_synthetic_repo(root, witness_link="[evil](../outside.hy)", create_witness=False)
            (root / "compiler/crates/hy-diag/data/diagnostics.json").write_text("{corrupt", encoding="utf-8")
            (root / "compiler/crates/hy-diag/src").mkdir(parents=True, exist_ok=True)
            (root / "compiler/crates/hy-diag/src/codes.rs").write_text("// HYB-E3002\n", encoding="utf-8")
            (root / "tests/checker-corpus/01_headerless.hy").write_text("// HYB-E3002 without verdict\n", encoding="utf-8")
            tdir = root / "compiler/crates/hy-check/tests"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "empty_test.rs").write_text("// mentions HYB-E3002 but has no test attr\n", encoding="utf-8")
            rep = pg.project_guarantees(root)
            self.assertEqual(rep["rows"][0]["projected_status"], "UNVERIFIED")
            self.assertIn("../outside.hy", rep["bidirectional_diagnostics"]["broken_executable_witnesses"])
            self.assertEqual(rep["summary"]["corpus_census"].get("NO-VERDICT"), 1)
            p_lf, p_crlf = base / "lf.hy", base / "crlf.hy"
            p_lf.write_bytes(b"// EXPECTED-VERDICT: REJECT\n")
            p_crlf.write_bytes(b"// EXPECTED-VERDICT: REJECT\r\n")
            p_lf_same = root / "lf.hy"
            p_lf_same.write_bytes(b"// EXPECTED-VERDICT: REJECT\r\n")
            self.assertEqual(pg._sha256_paths([p_lf]), pg._sha256_paths([p_lf_same]))


if __name__ == "__main__":
    unittest.main()
