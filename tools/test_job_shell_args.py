import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("job_shell_args", ROOT / "tools/job.py")
job_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(job_tool)

WORKER_SPEC = importlib.util.spec_from_file_location("worker_script", ROOT / "worker/script.py")
worker_script = importlib.util.module_from_spec(WORKER_SPEC)
WORKER_SPEC.loader.exec_module(worker_script)

CLONE_FIXTURE = ROOT / "tools/tests/issue_1101_worker_clone_fallback.json"
QUOTING_FIXTURE = ROOT / "tools/tests/issue_1101_worker_shell_quoting.json"


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


class WorkerKernelPreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clone_fx = json.loads(CLONE_FIXTURE.read_text(encoding="utf-8"))
        cls.quote_fx = json.loads(QUOTING_FIXTURE.read_text(encoding="utf-8"))

    def setUp(self):
        self.orig_cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory(prefix="hybrid-worker-1101-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(worker_script.os.chdir, self.orig_cwd)
        self.root = Path(self.temp.name)

    def run_worker(self, job_payload, outcomes, work_dir=None):
        work = str(work_dir or (self.root / "work"))
        recorded = []

        def fake_sh(cmd, timeout=600, cwd=None):
            recorded.append((cmd, timeout, cwd))
            for prefix, rc in outcomes.items():
                if cmd.startswith(prefix):
                    return rc, ""
            return 0, ""

        with mock.patch.object(worker_script, "WORK", work), \
             mock.patch.object(worker_script, "JOB_JSON", json.dumps(job_payload) if job_payload else None), \
             mock.patch.object(worker_script, "sh", side_effect=fake_sh), \
             contextlib.redirect_stdout(io.StringIO()), \
             self.assertRaises(SystemExit) as cm:
            worker_script.main()
        return cm.exception.code, recorded

    def test_sh_executes_without_tail_pipe_and_propagates_exit_code(self):
        proc = mock.Mock(returncode=128, stdout=b"x" * 3500)
        with mock.patch.object(worker_script.subprocess, "run", return_value=proc) as m_run, \
             contextlib.redirect_stdout(io.StringIO()) as buf:
            rc, out = worker_script.sh("git clone --quiet -- repo", 300, cwd="repo")
        self.assertEqual(rc, 128)
        self.assertEqual(len(out), 3500)
        m_run.assert_called_once_with(
            ["/bin/sh", "-c", "git clone --quiet -- repo"],
            cwd="repo", timeout=300,
            stdout=worker_script.subprocess.PIPE,
            stderr=worker_script.subprocess.STDOUT,
        )
        self.assertNotIn("| tail", buf.getvalue())

    def test_clone_and_checkout_failure_matrix_from_fixture(self):
        sha = self.clone_fx["pinned_sha"]
        repo = self.clone_fx["upstream_repo"]
        for sc in self.clone_fx["scenarios"]:
            with self.subTest(scenario=sc["name"]):
                outcomes = {
                    "git clone ": sc.get("clone_rc", 0),
                    "git checkout ": sc.get("checkout_rc", 0),
                    "curl ": sc.get("curl_rc", 0),
                    "tar ": sc.get("tar_rc", 0),
                }
                code, calls = self.run_worker({"job": sc["name"], "repo": repo, "sha": sha}, outcomes)
                self.assertEqual(code, sc["expect_exit"])
                cmds = [c for c, _, _ in calls]
                for c in cmds:
                    for bad in self.quote_fx["forbidden_pipe_substrings"]:
                        self.assertNotIn(bad, c)
                has_curl = any(c.startswith("curl -fsSL ") and self.clone_fx["codeload_prefix"] + sha in c for c in cmds)
                has_tar = any(c.startswith("tar xzf ") for c in cmds)
                has_runner = any(c.startswith("python3 repo/tools/runner/runner.py ") for c in cmds)
                self.assertEqual(has_curl, sc["expect_codeload"])
                if "expect_tar" in sc:
                    self.assertEqual(has_tar, sc["expect_tar"])
                self.assertEqual(has_runner, sc["expect_exit"] == 0)

    def test_custom_repo_failure_never_falls_back_to_upstream_codeload(self):
        job = {"job": "custom", "repo": self.clone_fx["custom_repo"], "sha": self.clone_fx["pinned_sha"]}
        for outcomes, expected in (({"git clone ": 128}, 128), ({"git clone ": 0, "git checkout ": 1}, 1)):
            code, calls = self.run_worker(job, outcomes)
            self.assertEqual(code, expected)
            cmds = [c for c, _, _ in calls]
            self.assertFalse(any(c.startswith(("curl ", "tar ", "python3 repo/tools/runner/")) for c in cmds))

    def test_shell_quoting_and_option_safety_from_fixture(self):
        work_dir = self.root / self.quote_fx["work_dir_suffix"]
        for sha in (self.quote_fx["dangerous_sha"], self.quote_fx["option_like_sha"]):
            job = {"job": "q", "repo": self.quote_fx["dangerous_repo"], "sha": sha}
            code, calls = self.run_worker(job, {}, work_dir=work_dir)
            self.assertEqual(code, 0)
            clone_cmd = next(c for c, _, _ in calls if c.startswith("git clone "))
            checkout_cmd, _, checkout_cwd = next(t for t in calls if t[0].startswith("git checkout "))
            runner_cmd = next(c for c, _, _ in calls if c.startswith("python3 repo/tools/runner/"))
            self.assertEqual(
                shlex.split(clone_cmd),
                self.quote_fx["required_git_clone_tokens"] + [self.quote_fx["dangerous_repo"], "repo"],
            )
            self.assertEqual(checkout_cwd, "repo")
            self.assertEqual(
                shlex.split(checkout_cmd),
                self.quote_fx["required_git_checkout_tokens"] + [sha],
            )
            self.assertEqual(
                shlex.split(runner_cmd),
                [
                    "python3", "repo/tools/runner/runner.py",
                    str(work_dir / "job.json"),
                    "--results-dir", f"{work_dir}/results",
                    "--repo-dir", "repo",
                ],
            )

    def test_upstream_repo_variants_and_missing_job_json_guard(self):
        code, _ = self.run_worker(None, {})
        self.assertEqual(code, 2)
        for repo in worker_script.UPSTREAM_REPOS:
            code, calls = self.run_worker({"job": "up", "repo": repo, "sha": "HEAD"}, {"git clone ": 128})
            self.assertEqual(code, 0)
            self.assertTrue(any(c.startswith("curl -fsSL ") for c, _, _ in calls))


if __name__ == "__main__":
    unittest.main()