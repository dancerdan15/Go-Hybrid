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
                if mb_sha not in self.cmp_mb_by_sha:
                    raise AssertionError("unexpected merge_base compare: %s" % path)
                return dict(self.cmp_mb_by_sha[mb_sha])
            spec = path.split("/compare/", 1)[1]
            head_sha = spec.split("...", 1)[1]
            if head_sha not in self.cmp_head_by_sha:
                raise AssertionError("unexpected main...head compare: %s" % path)
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

    def test_collect_skips_compare_only_when_head_equals_main_sha(self):
        main_sha = "m" * 40
        old_mb = "1" * 40
        prs, full_by_num, files_by_num, cmp_head = [], {}, {}, {}
        # PRs 1..9: head == main_sha -> the only safe shortcut, no compare at all.
        for i in range(1, 10):
            prs.append(self._mk_pr(i, main_sha, base_sha=main_sha))
            full_by_num[i] = {"mergeable": True, "mergeable_state": "clean", "base": {"sha": main_sha}}
            files_by_num[i] = [{"filename": "tools/triage_bot.py", "additions": 5, "deletions": 1}]
        # Regression: branch cut from an old main, but base.sha is already the
        # fresh main tip (== main_sha). behind_by must come from main...head.
        for i in range(10, 20):
            head_sha = "%040x" % i
            prs.append(self._mk_pr(i, head_sha, base_sha=main_sha))
            full_by_num[i] = {"mergeable": True, "mergeable_state": "clean", "base": {"sha": main_sha}}
            files_by_num[i] = [{"filename": "tools/triage_bot.py", "additions": 5, "deletions": 1}]
            cmp_head[head_sha] = {
                "status": "diverged",
                "ahead_by": 2,
                "behind_by": 40,
                "merge_base_commit": {"sha": old_mb},
            }
        cmp_mb = {old_mb: {"status": "ahead", "ahead_by": 40, "behind_by": 0,
                           "files": [{"filename": "tools/triage_bot.py"}, {"filename": "README.md"}]}}

        gh = _FakeGh(main_sha, prs, full_by_num, files_by_num, cmp_head, cmp_mb)
        args = type("Args", (), {"jobs": 6})()
        with contextlib.redirect_stdout(io.StringIO()):
            items, got_main = tb.collect(tb.load_cfg(), gh, args)

        self.assertEqual(got_main, main_sha)
        self.assertEqual(len(items), 19)
        head_compares = [c for c in gh.compare_calls if not c.endswith("?per_page=250")]
        self.assertEqual(
            sorted(head_compares),
            sorted("/repos/%s/compare/%s...%040x" % (gh.repo, main_sha, i) for i in range(10, 20)),
        )
        by_num = {it["number"]: it for it in items}
        for i in range(1, 10):
            self.assertEqual(by_num[i]["behind_by"], 0)
            self.assertEqual(by_num[i]["overlap_risk"], [])
        for i in range(10, 20):
            self.assertEqual(by_num[i]["behind_by"], 40)
            self.assertEqual(by_num[i]["overlap_risk"], ["tools/triage_bot.py"])

    def test_collect_caches_shared_merge_base_compare_across_concurrent_workers(self):
        main_sha = "a" * 40
        shared_mb = "b" * 40
        prs, full_by_num, files_by_num, cmp_head = [], {}, {}, {}
        for i in range(1, 11):
            head_sha = "%040x" % (100 + i)
            prs.append(self._mk_pr(i, head_sha, base_sha=shared_mb))
            full_by_num[i] = {"mergeable": True, "mergeable_state": "clean", "base": {"sha": shared_mb}}
            touched = "tools/triage_bot.py" if i % 2 == 0 else "README.md"
            files_by_num[i] = [{"filename": touched, "additions": 10, "deletions": 2}]
            cmp_head[head_sha] = {
                "status": "diverged",
                "ahead_by": 1,
                "behind_by": 4,
                "merge_base_commit": {"sha": shared_mb},
            }
        cmp_mb = {
            shared_mb: {
                "status": "ahead",
                "ahead_by": 4,
                "behind_by": 0,
                "files": [{"filename": "tools/triage_bot.py"}, {"filename": "tools/shepherd.py"}],
            }
        }
        gh = _FakeGh(main_sha, prs, full_by_num, files_by_num, cmp_head, cmp_mb, mb_delay=0.02)
        args = type("Args", (), {"jobs": 6})()
        with contextlib.redirect_stdout(io.StringIO()):
            items, _ = tb.collect(tb.load_cfg(), gh, args)

        mb_calls = [c for c in gh.compare_calls if c.endswith("?per_page=250")]
        head_calls = [c for c in gh.compare_calls if not c.endswith("?per_page=250")]
        self.assertEqual(mb_calls, ["/repos/%s/compare/%s...%s?per_page=250" % (gh.repo, shared_mb, main_sha)])
        self.assertEqual(len(head_calls), 10)
        by_num = {it["number"]: it for it in items}
        for i in range(1, 11):
            self.assertEqual(by_num[i]["behind_by"], 4)
            if i % 2 == 0:
                self.assertEqual(by_num[i]["overlap_risk"], ["tools/triage_bot.py"])
            else:
                self.assertEqual(by_num[i]["overlap_risk"], [])

    def test_collect_behind_by_comes_from_main_head_compare_not_stale_base(self):
        main_sha = "c" * 40
        stale_base = "d" * 40
        mb_7 = "f" * 40
        mb_600 = "9" * 40
        mb_div0 = "8" * 40
        touched = [{"filename": "tools/triage_bot.py", "additions": 3, "deletions": 1}]
        prs, full_by_num, files_by_num, cmp_head = [], {}, {}, {}
        heads = {n: "%040x" % (200 + n) for n in (1, 2, 3, 4)}
        for n, head_sha in heads.items():
            # every PR carries the same stale base.sha (main is 20 commits ahead of it)
            prs.append(self._mk_pr(n, head_sha, base_sha=stale_base))
            full_by_num[n] = {"mergeable": True, "mergeable_state": "clean", "base": {"sha": stale_base}}
            files_by_num[n] = list(touched)
        # (a) head already merged main: behind_by 0 despite the stale base, no merge_base compare
        cmp_head[heads[1]] = {"status": "ahead", "ahead_by": 3, "behind_by": 0,
                              "merge_base_commit": {"sha": main_sha}}
        # (b) diverged, behind_by 7: overlap from the merge_base...main path list
        cmp_head[heads[2]] = {"status": "diverged", "ahead_by": 2, "behind_by": 7,
                              "merge_base_commit": {"sha": mb_7}}
        # (c) behind_by 600 > overlap_window: no merge_base compare for this merge_base
        cmp_head[heads[3]] = {"status": "diverged", "ahead_by": 1, "behind_by": 600,
                              "merge_base_commit": {"sha": mb_600}}
        # (d) "diverged" with behind_by 0: stays 0, never overwritten from a merge_base compare
        cmp_head[heads[4]] = {"status": "diverged", "ahead_by": 1, "behind_by": 0,
                              "merge_base_commit": {"sha": mb_div0}}
        cmp_mb = {
            mb_7: {"status": "ahead", "ahead_by": 7, "behind_by": 0,
                   "files": [{"filename": "tools/triage_bot.py"}]},
        }
        gh = _FakeGh(main_sha, prs, full_by_num, files_by_num, cmp_head, cmp_mb)
        args = type("Args", (), {"jobs": 2})()
        with contextlib.redirect_stdout(io.StringIO()):
            items, _ = tb.collect(tb.load_cfg(), gh, args)

        mb_calls = [c for c in gh.compare_calls if c.endswith("?per_page=250")]
        self.assertEqual(mb_calls, ["/repos/%s/compare/%s...%s?per_page=250" % (gh.repo, mb_7, main_sha)])
        by_num = {it["number"]: it for it in items}
        self.assertEqual(by_num[1]["behind_by"], 0)
        self.assertEqual(by_num[1]["overlap_risk"], [])
        self.assertEqual(by_num[2]["behind_by"], 7)
        self.assertEqual(by_num[2]["overlap_risk"], ["tools/triage_bot.py"])
        self.assertEqual(by_num[3]["behind_by"], 600)
        self.assertEqual(by_num[3]["overlap_risk"], [])
        self.assertEqual(by_num[4]["behind_by"], 0)
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

        # heads differ from main_sha, so every PR gets a main...head compare;
        # behind_by 0 with merge_base == main_sha keeps classify on the
        # behind_by == 0 branch (+1), so the expected scores are unchanged.
        cmp_head = {
            pr["head"]["sha"]: {
                "status": "ahead",
                "ahead_by": 1,
                "behind_by": 0,
                "merge_base_commit": {"sha": main_sha},
            }
            for pr in prs
        }
        gh = _FakeGh(main_sha, prs, full_by_num, files_by_num, cmp_head, {})
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

