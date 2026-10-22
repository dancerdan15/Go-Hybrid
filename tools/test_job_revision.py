"""Submit-time revision checks, using only local repositories and fake uploads."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location("revision_job", Path(__file__).with_name("job.py"))
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


class SubmitRevisionTests(unittest.TestCase):
    def test_failed_git_lookup_aborts_before_payload_or_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = root / "worker"
            worker.mkdir()
            template = (job.INJECT_MARKER + "\n").encode()
            (worker / "script.py").write_bytes(template)
            (root / "job.json").write_text(json.dumps({"job": "offline", "commands": []}))
            uploads = []

            def fake_sh(command, **kwargs):
                if command.startswith("git rev-parse"):
                    return 128, "fatal: HEAD is not a valid revision"
                if "kernels push" in command:
                    uploads.append(command)
                return 0, "fixture"

            with mock.patch.object(job, "WORKER_DIR", str(worker)), \
                 mock.patch.object(job, "sh", side_effect=fake_sh), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    job.kaggle_submit(str(root))
            self.assertNotEqual(raised.exception.code, 0)
            self.assertEqual(uploads, [])
            self.assertEqual((worker / "script.py").read_bytes(), template)
            self.assertFalse((worker / "job.json").exists())
            self.assertFalse((root / "submitted.json").exists())

    def test_unborn_local_repository_cannot_supply_a_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)

            def local_git(command, **kwargs):
                # Run only the fixed revision lookup against a disposable repo.
                result = subprocess.run(command.split(), cwd=tmp, capture_output=True, text=True)
                return result.returncode, result.stdout + result.stderr

            with mock.patch.object(job, "sh", side_effect=local_git):
                with self.assertRaises(SystemExit):
                    job.resolve_head()

    def test_committed_local_repository_resolves_exact_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "--allow-empty", "-qm", "fixture"], cwd=tmp,
                           check=True, capture_output=True)
            expected = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp, text=True).strip()

            def local_git(command, **kwargs):
                result = subprocess.run(command.split(), cwd=tmp, capture_output=True, text=True)
                return result.returncode, result.stdout + result.stderr

            with mock.patch.object(job, "sh", side_effect=local_git):
                self.assertEqual(job.resolve_head(), expected)


tb_spec = importlib.util.spec_from_file_location("triage_bot_under_test", Path(__file__).with_name("triage_bot.py"))
tb = importlib.util.module_from_spec(tb_spec)
tb_spec.loader.exec_module(tb)

sh_spec = importlib.util.spec_from_file_location("shepherd_under_test", Path(__file__).with_name("shepherd.py"))
sh = importlib.util.module_from_spec(sh_spec)
sh_spec.loader.exec_module(sh)


class _FakeGh:
    def __init__(self, main_sha, prs, full_by_num, files_by_num, cmp_head_by_sha, cmp_mb_by_sha, mb_delay=0.0):
        import threading
        self.repo = "limitlessprojects/Go-Hybrid"
        self.main_sha = main_sha
        self.prs = prs
        self.full_by_num = full_by_num
        self.files_by_num = files_by_num
        self.cmp_head_by_sha = cmp_head_by_sha
        self.cmp_mb_by_sha = cmp_mb_by_sha
        self.mb_delay = mb_delay
        self.lock = threading.Lock()
        self.compare_calls = []

    def paginate(self, path, max_pages=20, key=None):
        if "/pulls?state=open" in path:
            return list(self.prs)
        for n, files in self.files_by_num.items():
            if path.startswith("/repos/%s/pulls/%d/files" % (self.repo, n)):
                return list(files)
        raise AssertionError("unexpected paginate path: %s" % path)

    def get(self, path):
        import time
        if path == "/repos/%s/commits/main" % self.repo:
            return {"sha": self.main_sha}
        for n, full in self.full_by_num.items():
            if path == "/repos/%s/pulls/%d" % (self.repo, n):
                return dict(full) if full is not None else None
        if "/compare/" in path:
            with self.lock:
                self.compare_calls.append(path)
            if path.endswith("?per_page=250"):
                spec = path.split("/compare/", 1)[1].split("?", 1)[0]
                mb_sha = spec.split("...", 1)[0]
                if self.mb_delay:
                    time.sleep(self.mb_delay)
                return dict(self.cmp_mb_by_sha[mb_sha])
            spec = path.split("/compare/", 1)[1]
            head_sha = spec.split("...", 1)[1]
            return dict(self.cmp_head_by_sha[head_sha])
        raise AssertionError("unexpected get path: %s" % path)


class TriageBotQuotaAndTestPatternsTests(unittest.TestCase):
    @staticmethod
    def _mk_pr(number, head_sha, base_sha=None):
        pr = {
            "number": number,
            "title": "PR #%d" % number,
            "user": {"login": " contributor "},
            "created_at": "2026-10-20T00:00:00Z",
            "updated_at": "2026-10-20T00:00:00Z",
            "draft": False,
            "head": {"sha": head_sha},
        }
        if base_sha is not None:
            pr["base"] = {"sha": base_sha}
        return pr

    def test_collect_skips_compare_when_base_equals_main_sha(self):
        main_sha = "m" * 40
        prs, full_by_num, files_by_num = [], {}, {}
        for i in range(1, 51):
            head_sha = main_sha if i == 50 else "%040x" % i
            # Mix PRs having base.sha on list item, only on full PR, or stale list base vs fresh full base
            base_on_list = main_sha if i % 3 == 1 else ("0" * 40 if i % 3 == 2 else None)
            prs.append(self._mk_pr(i, head_sha, base_sha=base_on_list))
            full_by_num[i] = None if i == 50 else {
                "mergeable": True,
                "mergeable_state": "clean",
                "base": {"sha": main_sha},
            }
            files_by_num[i] = [{"filename": "tools/triage_bot.py", "additions": 5, "deletions": 1}]

        gh = _FakeGh(main_sha, prs, full_by_num, files_by_num, {}, {})
        args = type("Args", (), {"jobs": 6})()
        with contextlib.redirect_stdout(io.StringIO()):
            items, got_main = tb.collect(tb.load_cfg(), gh, args)

        self.assertEqual(got_main, main_sha)
        self.assertEqual(len(items), 50)
        self.assertEqual(gh.compare_calls, [])
        for it in items:
            self.assertEqual(it["behind_by"], 0)
            self.assertEqual(it["overlap_risk"], [])

    def test_collect_caches_shared_merge_base_compare_across_concurrent_workers(self):
        main_sha = "a" * 40
        stale_base = "b" * 40
        prs, full_by_num, files_by_num = [], {}, {}
        for i in range(1, 11):
            head_sha = "%040x" % (100 + i)
            prs.append(self._mk_pr(i, head_sha, base_sha=stale_base))
            full_by_num[i] = {"mergeable": True, "mergeable_state": "clean", "base": {"sha": stale_base}}
            touched = "tools/triage_bot.py" if i % 2 == 0 else "README.md"
            files_by_num[i] = [{"filename": touched, "additions": 10, "deletions": 2}]
        cmp_mb = {
            stale_base: {
                "status": "ahead",
                "ahead_by": 4,
                "behind_by": 0,
                "files": [{"filename": "tools/triage_bot.py"}, {"filename": "tools/shepherd.py"}],
            }
        }
        gh = _FakeGh(main_sha, prs, full_by_num, files_by_num, {}, cmp_mb, mb_delay=0.02)
        args = type("Args", (), {"jobs": 6})()
        with contextlib.redirect_stdout(io.StringIO()):
            items, _ = tb.collect(tb.load_cfg(), gh, args)

        self.assertEqual(
            gh.compare_calls,
            ["/repos/%s/compare/%s...%s?per_page=250" % (gh.repo, stale_base, main_sha)],
        )
        by_num = {it["number"]: it for it in items}
        for i in range(1, 11):
            self.assertEqual(by_num[i]["behind_by"], 4)
            if i % 2 == 0:
                self.assertEqual(by_num[i]["overlap_risk"], ["tools/triage_bot.py"])
            else:
                self.assertEqual(by_num[i]["overlap_risk"], [])

    def test_collect_diverged_status_fallback_computes_behind_by_from_merge_base(self):
        main_sha = "c" * 40
        div_base = "d" * 40
        real_mb = "f" * 40
        prs, full_by_num, files_by_num, cmp_head = [], {}, {}, {}
        for i in (1, 2):
            head_sha = "%040x" % (200 + i)
            prs.append(self._mk_pr(i, head_sha, base_sha=div_base))
            full_by_num[i] = {"mergeable": True, "mergeable_state": "clean", "base": {"sha": div_base}}
            files_by_num[i] = [{"filename": "tools/triage_bot.py", "additions": 3, "deletions": 1}]
        # Also include a 3rd PR with no base.sha that falls back to main...head compare
        head_3 = "%040x" % 203
        prs.append(self._mk_pr(3, head_3, base_sha=None))
        full_by_num[3] = {"mergeable": True, "mergeable_state": "clean"}
        files_by_num[3] = [{"filename": "tools/triage_bot.py", "additions": 3, "deletions": 1}]
        cmp_head[head_3] = {
            "status": "diverged",
            "behind_by": 0,
            "merge_base_commit": {"sha": real_mb},
        }
        # PR #4: ancient PR (behind_by=600 > overlap_window=100) must NOT fetch merge_base...main_sha
        head_4 = "%040x" % 204
        prs.append(self._mk_pr(4, head_4, base_sha=None))
        full_by_num[4] = {"mergeable": False, "mergeable_state": "dirty"}
        files_by_num[4] = [{"filename": "tools/triage_bot.py", "additions": 3, "deletions": 1}]
        cmp_head[head_4] = {
            "status": "behind",
            "behind_by": 600,
            "merge_base_commit": {"sha": "9" * 40},
        }
        cmp_mb = {
            div_base: {
                "status": "diverged",
                "ahead_by": 0,
                "behind_by": 2,
                "merge_base_commit": {"sha": real_mb},
                "files": None,
            },
            real_mb: {
                "status": "ahead",
                "ahead_by": 7,
                "behind_by": 0,
                "files": [{"filename": "tools/triage_bot.py"}],
            },
        }
        gh = _FakeGh(main_sha, prs, full_by_num, files_by_num, cmp_head, cmp_mb)
        args = type("Args", (), {"jobs": 2})()
        with contextlib.redirect_stdout(io.StringIO()):
            items, _ = tb.collect(tb.load_cfg(), gh, args)

        mb_compare_calls = [
            c for c in gh.compare_calls
            if c == "/repos/%s/compare/%s...%s?per_page=250" % (gh.repo, real_mb, main_sha)
        ]
        self.assertEqual(len(mb_compare_calls), 1)
        by_num = {it["number"]: it for it in items}
        for n in (1, 2, 3):
            self.assertEqual(by_num[n]["behind_by"], 7)
            self.assertEqual(by_num[n]["overlap_risk"], ["tools/triage_bot.py"])
        self.assertEqual(by_num[4]["behind_by"], 600)
        self.assertEqual(by_num[4]["overlap_risk"], [])

    def test_has_test_file_and_classify_tests_included_alignment(self):
        self.assertEqual(tb.TEST_PATTERNS, sh.TEST_PATTERNS)
        self.assertFalse(tb.has_test_file(None))
        self.assertFalse(tb.has_test_file([None, ""]))
        self.assertTrue(tb.has_test_file(r"tests\foo.hy"))
        self.assertTrue(tb.has_test_file([r"benchmarks\corpus\case.hy"]))
        positive_paths = [
            "tools/test_foo.py",
            "pkg/foo_test.go",
            "crates/foo_test.rs",
            "spikes/x/y_accept.hy",
            "spikes/x/y_reject.hy",
            "tests/x.hy",
            "benchmarks/corpus/case.hy",
        ]
        negative_paths = [
            "tools/triage_bot.py",
            "README.md",
            "compiler/src/main.rs",
            "runtime/go/bridge.go",
        ]
        for p in positive_paths:
            with self.subTest(positive=p):
                self.assertTrue(tb.has_test_file(p))
                self.assertTrue(tb.has_test_file([p]))
        for p in negative_paths:
            with self.subTest(negative=p):
                self.assertFalse(tb.has_test_file(p))
                self.assertFalse(tb.has_test_file([p]))

        main_sha = "e" * 40
        all_paths = positive_paths + negative_paths
        prs, full_by_num, files_by_num = [], {}, {}
        for idx, path in enumerate(all_paths, start=1):
            head_sha = "%040x" % (300 + idx)
            prs.append(self._mk_pr(5000 + idx, head_sha, base_sha=main_sha))
            full_by_num[5000 + idx] = {"mergeable": True, "mergeable_state": "clean", "base": {"sha": main_sha}}
            files_by_num[5000 + idx] = [{"filename": path, "additions": 20, "deletions": 2}]

        gh = _FakeGh(main_sha, prs, full_by_num, files_by_num, {}, {})
        args = type("Args", (), {"jobs": 4})()
        cfg = tb.load_cfg()
        with contextlib.redirect_stdout(io.StringIO()):
            items, _ = tb.collect(cfg, gh, args)
        classified, _ = tb.classify(cfg, items)
        by_num = {it["number"]: it for it in classified}

        for idx, path in enumerate(all_paths, start=1):
            it = by_num[5000 + idx]
            expected_tests = path in positive_paths
            with self.subTest(classified_path=path):
                self.assertEqual(it["tests_included"], expected_tests)
                if expected_tests:
                    self.assertIn("triage:tests", it["labels"])
                    self.assertEqual(it["score"], 9)
                else:
                    self.assertNotIn("triage:tests", it["labels"])
                    self.assertEqual(it["score"], 6)


if __name__ == "__main__":
    unittest.main()

