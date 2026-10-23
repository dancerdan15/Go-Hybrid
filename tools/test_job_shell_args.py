import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("job_shell_args", ROOT / "tools/job.py")
job_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(job_tool)


@unittest.skipUnless(Path("/bin/sh").exists(), "requires /bin/sh")
class KaggleShellArgumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hybrid-cli-args-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "argv.jsonl"
        # An apostrophe exercises shell quoting beyond the common space case.
        self.cli = self.root / "CLI owner's directory" / "fake kaggle"
        self.cli.parent.mkdir()
        self.cli.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " "
            + shlex.quote(str(self.root / "fake_cli.py")) + ' "$@"\n',
            encoding="utf-8",
        )
        self.cli.chmod(0o755)
        self.plain_cli = self.root / "kaggle"
        self.plain_cli.write_text(self.cli.read_text(), encoding="utf-8")
        self.plain_cli.chmod(0o755)
        (self.root / "fake_cli.py").write_text(
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "with pathlib.Path(" + repr(str(self.log)) + ").open('a') as f:\n"
            "    f.write(json.dumps(args) + '\\n')\n"
            "if args[:2] == ['kernels', 'output']:\n"
            "    out = pathlib.Path(args[args.index('-p') + 1])\n"
            "    out.mkdir(parents=True, exist_ok=True)\n"
            "    (out / 'summary.json').write_text('{\"ok\": true}')\n"
            "elif args[:2] == ['kernels', 'status']:\n"
            "    print('\"KernelWorkerStatus.RUNNING\"')\n",
            encoding="utf-8",
        )
        self.patch_cli = mock.patch.object(job_tool, "KAGGLE_CLI", str(self.cli))
        self.patch_cli.start()
        self.addCleanup(self.patch_cli.stop)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_submit_preserves_cli_and_worker_paths_with_spaces_and_quotes(self):
        worker = self.root / "worker owner's tree"
        worker.mkdir()
        template = "JOB_JSON = None  # INJECTED_BY_tools/job.py\n"
        (worker / "script.py").write_text(template)
        jobdir = self.root / "input"
        jobdir.mkdir()
        (jobdir / "job.json").write_text(json.dumps({"job": "offline", "commands": []}))
        actual_sh = job_tool.sh

        def local_sh(command, **kwargs):
            # Do not read a real Git remote or alter any checkout.
            if command == "git checkout -- worker/script.py":
                return 0, ""
            return actual_sh(command, **kwargs)

        with mock.patch.object(job_tool, "WORKER_DIR", str(worker)), \
             mock.patch.object(job_tool, "resolve_head", return_value="a" * 40), \
             mock.patch.object(job_tool, "sh", side_effect=local_sh), \
             contextlib.redirect_stdout(io.StringIO()):
            job_tool.kaggle_submit(str(jobdir))
        self.assertEqual(self.calls(), [["kernels", "push", "-p", str(worker)]])
        self.assertTrue((jobdir / "submitted.json").exists())

    def test_status_treats_shell_metacharacters_as_literal_slug(self):
        marker = self.root / "must-not-exist"
        slug = "owner/kernel;touch " + shlex.quote(str(marker))
        with mock.patch.object(job_tool, "KAGGLE_CLI", str(self.plain_cli)), \
             contextlib.redirect_stdout(io.StringIO()):
            job_tool.kaggle_status(slug)
        self.assertFalse(marker.exists())
        self.assertEqual(self.calls(), [["kernels", "status", slug]])

    def test_wait_uses_the_same_literal_slug(self):
        slug = "owner/kernel with 'quotes'"
        with mock.patch.object(job_tool, "KAGGLE_CLI", str(self.plain_cli)), \
             mock.patch.object(job_tool.time, "time", side_effect=[0, 0, 61]), \
             mock.patch.object(job_tool.time, "sleep"), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(job_tool.kaggle_wait(1, slug), 2)
        self.assertEqual(self.calls(), [["kernels", "status", slug]])

    def test_fetch_preserves_destination_path_and_default_slug(self):
        # tools/job.py stages the download under result/raw (PR #494) and
        # promotes only from that staging directory; the argument contract this
        # test pins is that the slug and destination still reach the CLI as
        # single, properly quoted argv tokens.
        jobdir = self.root / "results owner's tree"
        with mock.patch.object(job_tool, "KAGGLE_CLI", str(self.plain_cli)), \
             mock.patch.object(job_tool, "kaggle_slug", return_value="owner/kernel"), \
             contextlib.redirect_stdout(io.StringIO()):
            job_tool.kaggle_fetch(str(jobdir))
        self.assertEqual(len(self.calls()), 1)
        argv = self.calls()[0]
        self.assertEqual(argv[:2], ["kernels", "output"])
        self.assertEqual(argv[2], "owner/kernel")
        self.assertEqual(argv[3], "-p")
        destination = Path(argv[4])
        raw = jobdir / "result/raw"
        self.assertEqual(destination.parent, raw)
        self.assertTrue(destination.name.startswith(".staging-"))
        summary = json.loads((jobdir / "result/summary.json").read_text())
        self.assertTrue(summary["ok"])

    def test_plain_status_default_slug_control(self):
        with mock.patch.object(job_tool, "KAGGLE_CLI", str(self.plain_cli)), \
             mock.patch.object(job_tool, "kaggle_slug", return_value="owner/kernel"), \
             contextlib.redirect_stdout(io.StringIO()):
            job_tool.kaggle_status()
        self.assertEqual(self.calls(), [["kernels", "status", "owner/kernel"]])


def _find_bash_and_awk():
    for candidate in ("/bin/bash", "C:/Program Files/Git/bin/bash.exe", shutil.which("bash")):
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run(
                [candidate, "-c", "command -v awk"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if probe.returncode == 0 and probe.stdout.strip():
                return candidate, probe.stdout.strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError):
            continue
    return None, None


def _bash_path(path: Path) -> str:
    posix = path.resolve().as_posix()
    return f"/{posix[0].lower()}{posix[2:]}" if len(posix) > 2 and posix[1] == ":" else posix


BASH_BIN, AWK_BIN = _find_bash_and_awk()
CHECK_MATRIX_FIXTURE = ROOT / "tools/tests/issue_1127_env_check_matrix.json"
CLI_ARGS_FIXTURE = ROOT / "tools/tests/issue_1127_env_cli_args.json"


@unittest.skipUnless(BASH_BIN and AWK_BIN, "requires bash and awk")
class EnvShPinAndCliTests(unittest.TestCase):
    def setUp(self):
        self.matrix = json.loads(CHECK_MATRIX_FIXTURE.read_text(encoding="utf-8"))
        self.cli_spec = json.loads(CLI_ARGS_FIXTURE.read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory(prefix="hybrid-env-sh-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home_z = self.root / "home-z"
        self.go_bin = self.home_z / "tools/go/bin"
        self.go_bin.mkdir(parents=True)
        self.sys_bin = self.root / "sys-bin"
        self.sys_bin.mkdir()
        self._write_exec(self.sys_bin / "awk", f'exec "{AWK_BIN}" "$@"')
        for cmd, body in (
            ("df", "printf 'Filesystem Size Used Avail Use%% Mounted\\nfs 10G 5G 5G 50%% /\\n'"),
            ("tail", 'last=""; while IFS= read -r l; do last="$l"; done; printf "%s\\n" "$last"'),
            ("free", "printf 'total used free\\nMem: 4000 1000 3000\\n'"),
            ("head", 'IFS= read -r l1 || :; IFS= read -r l2 || :; printf "%s\\n%s\\n" "$l1" "$l2"'),
            ("nproc", "printf '2\\n'"),
        ):
            self._write_exec(self.sys_bin / cmd, body)
        self.env_sh = self.root / "env.sh"
        raw = (ROOT / "tools/env.sh").read_text(encoding="utf-8")
        isolated = raw.replace("/home/z", _bash_path(self.home_z)).replace(
            ":$PATH", ":" + _bash_path(self.sys_bin)
        )
        self.env_sh.write_text(isolated, encoding="utf-8", newline="\n")

    @staticmethod
    def _write_exec(path: Path, body: str):
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def _configure_toolchains(self, go_out, go_rc, rust_out, rust_rc):
        for name, out, rc in (("go", go_out, go_rc), ("rustc", rust_out, rust_rc)):
            target = self.go_bin / name
            if out is None:
                target.unlink(missing_ok=True)
            else:
                self._write_exec(target, f"printf '%s\\n' {shlex.quote(out)}\nexit {int(rc)}")

    def test_env_sh_lf_and_static_contract(self):
        raw_bytes = (ROOT / "tools/env.sh").read_bytes()
        self.assertNotIn(b"\r\n", raw_bytes)
        text = raw_bytes.decode("utf-8")
        self.assertNotIn("rg -q", text)
        self.assertIn(f'PINNED_GO="{self.matrix["pinned_go"]}"', text)
        self.assertIn(f'PINNED_RUST="{self.matrix["pinned_rust"]}"', text)
        syntax = subprocess.run(
            [BASH_BIN, "-n", _bash_path(ROOT / "tools/env.sh")],
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_direct_check_matrix_without_rg(self):
        for case in self.matrix["cases"]:
            with self.subTest(case=case["name"]):
                self._configure_toolchains(
                    case["go_out"], case["go_rc"], case["rust_out"], case["rust_rc"]
                )
                res = subprocess.run(
                    [BASH_BIN, _bash_path(self.env_sh), "--check"],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                self.assertEqual(res.returncode, case["expected_rc"], res.stdout + res.stderr)
                if case["expected_rc"] == 0:
                    self.assertNotIn("WARN:", res.stdout)
                for warn in case["warns"]:
                    self.assertIn(warn, res.stdout)

    def test_sourced_check_returns_nonzero_on_mismatch_and_zero_on_match(self):
        self._configure_toolchains("go version go1.26.90 linux/amd64", 0, "rustc 1.99.0 (stable)", 0)
        bad = subprocess.run(
            [BASH_BIN, "-c", f'source "{_bash_path(self.env_sh)}" && check'],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(bad.returncode, 1)
        self.assertIn("WARN: go != go1.26.9 (got: go1.26.90)", bad.stdout)

        self._configure_toolchains("go version go1.26.9 linux/amd64", 0, "rustc 1.99.0 (stable)", 0)
        ok = subprocess.run(
            [BASH_BIN, "-c", f'source "{_bash_path(self.env_sh)}" && check'],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertNotIn("WARN:", ok.stdout)

    def test_unknown_or_extra_cli_args_rejected_with_usage_on_stderr(self):
        for argv in self.cli_spec["rejected_args"]:
            with self.subTest(argv=argv):
                res = subprocess.run(
                    [BASH_BIN, _bash_path(self.env_sh), *argv],
                    capture_output=True, text=True, timeout=15, check=False,
                )
                self.assertEqual(res.returncode, 2)
                self.assertIn(self.cli_spec["usage_stderr"], res.stderr)
                self.assertNotIn("Go:", res.stdout)

    def test_no_arg_direct_invocation_prints_summary_and_exits_zero(self):
        self._configure_toolchains("go version go1.26.9 linux/amd64", 0, "rustc 1.99.0 (stable)", 0)
        res = subprocess.run(
            [BASH_BIN, _bash_path(self.env_sh)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        for prefix in self.cli_spec["summary_prefixes"]:
            self.assertIn(prefix, res.stdout)


if __name__ == "__main__":
    unittest.main()