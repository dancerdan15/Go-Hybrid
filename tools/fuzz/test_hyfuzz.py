import contextlib
import hashlib
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hyfuzz

TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"
HYFUZZ_CASES = json.loads((TESTS_DIR / "hyfuzz_cases.json").read_text(encoding="utf-8"))
HYFUZZ_FIXTURE = json.loads((TESTS_DIR / "hyfuzz_fixture.json").read_text(encoding="utf-8"))
REAL_RUN = subprocess.run


class FuzzProcessTests(unittest.TestCase):
    def test_accept_and_clean_reject_are_valid(self):
        with tempfile.TemporaryDirectory() as workdir:
            for code in (0, 1):
                with self.subTest(code=code), patch.object(
                    hyfuzz.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, "", ""),
                ):
                    self.assertEqual(
                        hyfuzz.check_one("hyb", "fn main() {}", workdir, 1),
                        (None, code == 0),
                    )

    def test_signal_crashes_and_other_abnormal_exits_fail(self):
        with tempfile.TemporaryDirectory() as workdir:
            for code in (-11, -6, -9, 2, 101, 134, 139, 137, 3221225477):
                with self.subTest(code=code), patch.object(
                    hyfuzz.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, "", ""),
                ):
                    self.assertEqual(
                        hyfuzz.check_one("hyb", "fn main() {}", workdir, 1),
                        (f"UNEXPECTED exit={code}", False),
                    )

    def test_timeout_still_fails(self):
        with tempfile.TemporaryDirectory() as workdir, patch.object(
            hyfuzz.subprocess, "run",
            side_effect=subprocess.TimeoutExpired("hyb", 1),
        ):
            self.assertEqual(hyfuzz.check_one("hyb", "", workdir, 1), ("HANG", False))

    def test_internal_error_text_still_fails(self):
        with tempfile.TemporaryDirectory() as workdir:
            for text in hyfuzz.BAD_TEXT:
                with self.subTest(text=text), patch.object(
                    hyfuzz.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], 1, "", text),
                ):
                    bad, accepted = hyfuzz.check_one("hyb", "", workdir, 1)
                    self.assertTrue(bad.startswith("ICE:"))
                    self.assertFalse(accepted)

    def test_oserror_during_check_fails(self):
        with tempfile.TemporaryDirectory() as workdir, patch.object(
            hyfuzz.subprocess, "run",
            side_effect=OSError("exec format error"),
        ):
            bad, accepted = hyfuzz.check_one("hyb", "", workdir, 1)
            self.assertTrue(bad.startswith("EXEC_ERROR:"))
            self.assertFalse(accepted)

    def test_each_case_is_run_once_and_counted_from_that_run(self):
        with tempfile.TemporaryDirectory() as corpus:
            Path(corpus, "case.hy").write_text("fn main() {}", encoding="utf-8")
            for code in (0, 1):
                output = io.StringIO()
                with self.subTest(code=code), patch.object(sys, "argv", [
                    "hyfuzz.py", "--hyb", sys.executable, "--corpus", corpus, "--cases", "3",
                ]), patch.object(
                    hyfuzz.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, "", ""),
                ) as run, contextlib.redirect_stdout(output):
                    hyfuzz.main()
                    self.assertEqual(run.call_count, 3)
                    self.assertIn(f"clean-accepts={3 if code == 0 else 0}", output.getvalue())
                    self.assertIn(f"clean-rejects={3 if code == 1 else 0}", output.getvalue())

    def test_real_unexpected_exit_cannot_report_success(self):
        # Python is deliberately not a checker: `python check case.hy`
        # exits 2 without a compiler panic message, on Windows and POSIX.
        with tempfile.TemporaryDirectory() as corpus:
            Path(corpus, "case.hy").write_text("fn main() {}", encoding="utf-8")
            output = io.StringIO()
            with patch.object(sys, "argv", [
                "hyfuzz.py", "--hyb", sys.executable, "--corpus", corpus, "--cases", "1",
            ]), contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as failure:
                hyfuzz.main()
            self.assertEqual(failure.exception.code, 1)
            self.assertIn("UNEXPECTED exit=2", output.getvalue())
            self.assertNotIn("all invariants held", output.getvalue())


class FuzzArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hyfuzz-artifacts-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        (self.corpus / "sample.hy").write_text("fn main() {}", encoding="utf-8")
        self.source = "// full reproduction \u65e5\u672c\u8a9e\n" + "x " * 200 + "TAIL_MARKER\n"
        self.checker_script = self.root / "checker"
        self.checker_script.write_text(
            "#!" + sys.executable + "\n"
            "from pathlib import Path\nimport sys\n"
            "source = Path(sys.argv[2]).read_text(encoding='utf-8')\n"
            "sys.exit(23 if source.endswith('TAIL_MARKER\\n') else 0)\n",
            encoding="utf-8",
        )
        self.checker_script.chmod(0o755)
        if os.name == "nt":
            self.checker = self.root / "checker.cmd"
            self.checker.write_text(
                f'@"{sys.executable}" "{self.checker_script}" %*\n',
                encoding="utf-8",
            )
        else:
            self.checker = self.checker_script
        self.destination = self.root / "failures"

    def invoke(self, destination=True, source=None):
        args = [
            "hyfuzz.py", "--hyb", str(self.checker), "--corpus", str(self.corpus),
            "--cases", "1", "--seed", "470", "--timeout", "2",
        ]
        if destination:
            args += ["--failures-dir", str(self.destination)]
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with patch.object(sys, "argv", args), patch.object(
            hyfuzz, "gen_salad", return_value=self.source if source is None else source,
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                hyfuzz.main()
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_full_source_and_metadata_are_saved_and_replayable(self):
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 1, stdout + stderr)
        sources = list(self.destination.glob("hyfuzz-*/*.hy"))
        self.assertEqual(len(sources), 1)
        saved = sources[0]
        self.assertEqual(saved.read_text(encoding="utf-8"), self.source)
        metadata = json.loads(saved.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["seed"], 470)
        self.assertEqual(metadata["case_index"], 0)
        self.assertEqual(metadata["kind"], 0)
        self.assertEqual(metadata["failure"], "UNEXPECTED exit=23")
        self.assertEqual(metadata["source_file"], saved.name)
        self.assertEqual(metadata["source_sha256"], hashlib.sha256(saved.read_bytes()).hexdigest())
        replay = subprocess.run(
            [str(self.checker), "check", str(saved)], capture_output=True, timeout=5,
        )
        self.assertEqual(replay.returncode, 23)
        self.assertIn(str(saved), stdout)

    def test_success_creates_no_output_directory(self):
        code, stdout, stderr = self.invoke(source="fn main() {}")
        self.assertEqual(code, 0, stdout + stderr)
        self.assertFalse(self.destination.exists())

    def test_repeated_seed_does_not_overwrite_existing_reproduction(self):
        self.assertEqual(self.invoke()[0], 1)
        before = {path: path.read_bytes() for path in self.destination.rglob("*") if path.is_file()}
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(len(list(self.destination.glob("hyfuzz-*"))), 2)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_output_io_error_is_visible_and_not_success(self):
        self.destination.write_text("existing user data", encoding="utf-8")
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2, stdout + stderr)
        self.assertIn("cannot save failure artifact", stderr)
        self.assertNotIn("all invariants held", stdout)
        self.assertEqual(self.destination.read_text(encoding="utf-8"), "existing user data")

    def test_no_option_keeps_existing_failure_behavior(self):
        code, stdout, stderr = self.invoke(destination=False)
        self.assertEqual(code, 1, stdout + stderr)
        self.assertIn("UNEXPECTED exit=23", stdout)
        self.assertFalse(self.destination.exists())
class NestGenerationTests(unittest.TestCase):
    def test_branch_heavy_seed_keeps_one_deep_path(self):
        source = hyfuzz.gen_nest(random.Random(9498))
        self.assertEqual(source.count('fmt.println("deep")'), 1)
        self.assertLess(len(source), 4096)
        self.assertIn('else {', source)

    def test_all_branch_nesting_remains_linear(self):
        rng = random.Random(0)
        with patch.object(rng, 'randint', return_value=12), \
             patch.object(rng, 'choice', return_value=hyfuzz.NEST_SHAPES[3]):
            source = hyfuzz.gen_nest(rng)
        self.assertEqual(source.count('if x {'), 12)
        self.assertEqual(source.count('else {'), 12)
        self.assertEqual(source.count('fmt.println("deep")'), 1)
        self.assertLess(len(source), 4096)
class FuzzInputValidationTests(unittest.TestCase):
    def test_missing_hyb_binary_fails_closed(self):
        with tempfile.TemporaryDirectory() as corpus:
            Path(corpus, "case.hy").write_text("fn main() {}", encoding="utf-8")
            with patch.object(sys, "argv", [
                "hyfuzz.py", "--hyb", "/nonexistent/path/to/hyb", "--corpus", corpus, "--cases", "1",
            ]), self.assertRaises(SystemExit) as failure:
                hyfuzz.main()
            self.assertTrue(bool(failure.exception.code))

    def test_directory_hyb_fails_closed(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as corpus:
            Path(corpus, "case.hy").write_text("fn main() {}", encoding="utf-8")
            with patch.object(sys, "argv", [
                "hyfuzz.py", "--hyb", d, "--corpus", corpus, "--cases", "1",
            ]), self.assertRaises(SystemExit) as failure:
                hyfuzz.main()
            self.assertTrue(bool(failure.exception.code))

    def test_missing_corpus_directory_fails_closed(self):
        with patch.object(sys, "argv", [
            "hyfuzz.py", "--hyb", sys.executable, "--corpus", "/nonexistent/corpus", "--cases", "1",
        ]), self.assertRaises(SystemExit) as failure:
            hyfuzz.main()
        self.assertTrue(bool(failure.exception.code))

    def test_file_corpus_fails_closed(self):
        with tempfile.NamedTemporaryFile() as tf:
            with patch.object(sys, "argv", [
                "hyfuzz.py", "--hyb", sys.executable, "--corpus", tf.name, "--cases", "1",
            ]), self.assertRaises(SystemExit) as failure:
                hyfuzz.main()
            self.assertTrue(bool(failure.exception.code))

    def test_non_positive_cases_fails_closed(self):
        with tempfile.TemporaryDirectory() as corpus:
            Path(corpus, "case.hy").write_text("fn main() {}", encoding="utf-8")
            for cases in ("0", "-5"):
                with self.subTest(cases=cases):
                    with patch.object(sys, "argv", [
                        "hyfuzz.py", "--hyb", sys.executable, "--corpus", corpus, "--cases", cases,
                    ]), self.assertRaises(SystemExit) as failure:
                        hyfuzz.main()
                    self.assertTrue(bool(failure.exception.code))

    def test_non_positive_timeout_fails_closed(self):
        with tempfile.TemporaryDirectory() as corpus:
            Path(corpus, "case.hy").write_text("fn main() {}", encoding="utf-8")
            for timeout in ("0", "-1.0", "nan", "inf", "-inf"):
                with self.subTest(timeout=timeout):
                    with patch.object(sys, "argv", [
                        "hyfuzz.py", "--hyb", sys.executable, "--corpus", corpus, "--cases", "1", "--timeout", timeout,
                    ]), self.assertRaises(SystemExit) as failure:
                        hyfuzz.main()
                    self.assertTrue(bool(failure.exception.code))

    def test_finite_positive_timeout_accepted(self):
        with tempfile.TemporaryDirectory() as corpus:
            Path(corpus, "case.hy").write_text("fn main() {}", encoding="utf-8")
            with patch.object(sys, "argv", [
                "hyfuzz.py", "--hyb", sys.executable, "--corpus", corpus, "--cases", "1", "--timeout", "2.5",
            ]), patch.object(hyfuzz, "check_one", return_value=(None, True)):
                # Returns cleanly without exiting with error
                hyfuzz.main()

    def test_non_executable_hyb_fails_closed_with_exit_2(self):
        with tempfile.TemporaryDirectory() as root:
            hyb_file = Path(root, "not_exec_hyb")
            hyb_file.write_text("not an executable", encoding="utf-8")
            hyb_file.chmod(0o644)
            corpus = Path(root, "corpus")
            corpus.mkdir()
            (corpus / "case.hy").write_text("fn main() {}", encoding="utf-8")
            failures_dir = Path(root, "failures")
            stderr = io.StringIO()
            with patch.object(sys, "argv", [
                "hyfuzz.py", "--hyb", str(hyb_file), "--corpus", str(corpus),
                "--cases", "5", "--failures-dir", str(failures_dir),
            ]), patch.object(hyfuzz.os, "access", return_value=False), \
                    patch.object(hyfuzz, "check_one") as check_mock, \
                    contextlib.redirect_stderr(stderr), \
                    self.assertRaises(SystemExit) as failure:
                hyfuzz.main()
            self.assertEqual(failure.exception.code, HYFUZZ_FIXTURE["config_error_exit_code"])
            self.assertIn("not executable", stderr.getvalue())
            self.assertEqual(check_mock.call_count, 0)
            self.assertFalse(failures_dir.exists())

    def test_load_corpus_skips_directories_and_replaces_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as corpus:
            root = Path(corpus)
            for entry in HYFUZZ_FIXTURE["corpus_entries"]:
                target = root / entry["name"]
                if entry["type"] == "dir":
                    target.mkdir()
                else:
                    target.write_bytes(bytes.fromhex(entry["hex_bytes"]))
            loaded = hyfuzz.load_corpus(corpus)
            self.assertEqual(len(loaded), HYFUZZ_FIXTURE["expected_loaded_count"])
            self.assertTrue(any("\ufffd" in item for item in loaded))

    def test_corpus_with_only_hy_directory_exits_2(self):
        with tempfile.TemporaryDirectory() as corpus:
            Path(corpus, "only_dir.hy").mkdir()
            stderr = io.StringIO()
            with patch.object(sys, "argv", [
                "hyfuzz.py", "--hyb", sys.executable, "--corpus", corpus, "--cases", "1",
            ]), contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as failure:
                hyfuzz.main()
            self.assertEqual(failure.exception.code, HYFUZZ_FIXTURE["config_error_exit_code"])
            self.assertIn("no corpus programs found", stderr.getvalue())

    def test_check_one_handles_non_utf8_process_output_and_timeout_bytes(self):
        with tempfile.TemporaryDirectory() as workdir:
            helper = Path(workdir, "emit_bytes.py")
            for case in HYFUZZ_CASES["non_utf8_outputs"]:
                with self.subTest(case=case["name"]):
                    helper.write_text(
                        "import sys\n"
                        f"sys.{case['stream']}.buffer.write(bytes.fromhex({case['hex_bytes']!r}))\n"
                        f"sys.{case['stream']}.buffer.flush()\n"
                        f"raise SystemExit({case['returncode']})\n",
                        encoding="utf-8",
                    )
                    with patch.object(
                        hyfuzz.subprocess, "run",
                        side_effect=lambda cmd, **kw: REAL_RUN(
                            [sys.executable, str(helper), *cmd[1:]], **kw
                        ),
                    ):
                        bad, accepted = hyfuzz.check_one("hyb", "fn main() {}", workdir, 2)
                    if "expected_failure_prefix" in case:
                        self.assertIsNotNone(bad)
                        self.assertTrue(bad.startswith(case["expected_failure_prefix"]))
                    else:
                        self.assertEqual(bad, case["expected_failure"])
                    self.assertEqual(accepted, case["expected_accepted"])

            timeout_exc = subprocess.TimeoutExpired("hyb", 1, output=b"\xff\xfe", stderr=b"\x80\x81")
            with patch.object(hyfuzz.subprocess, "run", side_effect=timeout_exc):
                self.assertEqual(hyfuzz.check_one("hyb", "fn main() {}", workdir, 1), ("HANG", False))

    def test_early_stop_reports_actual_executed_cases(self):
        cfg = HYFUZZ_CASES["early_stop"]
        with tempfile.TemporaryDirectory() as corpus:
            Path(corpus, "case.hy").write_text("fn main() {}", encoding="utf-8")
            stdout = io.StringIO()
            with patch.object(sys, "argv", [
                "hyfuzz.py", "--hyb", sys.executable, "--corpus", corpus,
                "--cases", str(cfg["requested_cases"]), "--seed", str(cfg["seed"]),
            ]), patch.object(
                hyfuzz, "check_one", return_value=("UNEXPECTED exit=2", False),
            ) as check_mock, contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as failure:
                hyfuzz.main()
            self.assertEqual(failure.exception.code, 1)
            self.assertEqual(check_mock.call_count, cfg["expected_executed"])
            self.assertIn(
                f"hyfuzz: {cfg['expected_executed']} cases, seed={cfg['seed']}, clean-rejects=0, clean-accepts=0",
                stdout.getvalue(),
            )


if __name__ == "__main__":
    unittest.main()
