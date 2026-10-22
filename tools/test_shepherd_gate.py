#!/usr/bin/env python3
"""test_shepherd_gate.py — offline self-tests for tools/shepherd.py.

No network, no subprocesses: only the pure functions that the workflows lean
on. If the kill-switch matrix, the dup planner, the conflict classifier, or
the probe parser drift, CI fails before any workflow can.
"""

import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autoland as al  # noqa: E402
import shepherd as sh  # noqa: E402


class GateMatrix(unittest.TestCase):
    """The kill switch is the safety floor for every workflow (plan §1.5)."""

    def test_off_blocks_everything(self):
        for req in ("read", "dry-run", "live"):
            self.assertFalse(sh.mode_allows("off", req), req)

    def test_dry_run_allows_read_and_comment_only(self):
        self.assertTrue(sh.mode_allows("dry-run", "read"))
        self.assertTrue(sh.mode_allows("dry-run", "dry-run"))
        self.assertFalse(sh.mode_allows("dry-run", "live"))

    def test_live_allows_all(self):
        for req in ("read", "dry-run", "live"):
            self.assertTrue(sh.mode_allows("live", req), req)

    def test_unknown_mode_falls_back_to_dry_run(self):
        self.assertTrue(sh.mode_allows("bogus", "read"))
        self.assertFalse(sh.mode_allows("bogus", "live"))


def _item(n, **kw):
    it = {"number": n, "wildcard_hold": False, "draft_parked": False,
          "dup_members": None, "dup_of": None}
    it.update(kw)
    return it


class DupPlanner(unittest.TestCase):
    """W4 Phase A must never propose against closed survivors, wildcard
    holds, or drafts (the [474, 616, 945] and [620, 910] lessons)."""

    def test_open_cluster_planned(self):
        items = [_item(5, dup_members=[6, 7]), _item(6), _item(7)]
        clusters = [[5, 6, 7]]
        plan = sh.plan_phase_a(items, clusters, is_open=lambda n: n in (5, 6, 7))
        self.assertEqual(plan, [(5, [6, 7])])

    def test_closed_survivor_skips_cluster(self):
        items = [_item(620, dup_members=[910]), _item(910)]
        clusters = [[620, 910]]
        plan = sh.plan_phase_a(items, clusters, is_open=lambda n: n == 910)
        self.assertEqual(plan, [])

    def test_wildcard_survivor_and_members_skipped(self):
        items = [_item(616, dup_members=[474, 945], wildcard_hold=True),
                 _item(474, wildcard_hold=True), _item(945)]
        clusters = [[474, 616, 945]]
        plan = sh.plan_phase_a(items, clusters, is_open=lambda n: True)
        self.assertEqual(plan, [])

    def test_wildcard_member_filtered_leaves_no_pairs(self):
        # cluster [384, 572]: survivor 572 open, member 384 wildcard-held ->
        # the only member is filtered out, so the cluster yields nothing.
        items = [_item(572, dup_members=[384]), _item(384, wildcard_hold=True)]
        clusters = [[384, 572]]
        plan = sh.plan_phase_a(items, clusters, is_open=lambda n: True)
        self.assertEqual(plan, [])


class ConflictClassifier(unittest.TestCase):
    """Docs-only conflicts resolve deterministically; anything else is human
    territory (the #870 policy, plan §3 W5)."""

    def test_docs_only(self):
        docs, real = sh.classify_conflicts(["PLAN.md", "STATUS.md"],
                                           {"PLAN.md", "STATUS.md"})
        self.assertEqual(docs, ["PLAN.md", "STATUS.md"])
        self.assertEqual(real, [])

    def test_any_code_conflict_is_real(self):
        docs, real = sh.classify_conflicts(["PLAN.md", "src/hycheck/buildable.rs"],
                                           {"PLAN.md", "STATUS.md"})
        self.assertEqual(docs, ["PLAN.md"])
        self.assertEqual(real, ["src/hycheck/buildable.rs"])


class ProbeParser(unittest.TestCase):
    """W6 parses added diff lines only, and matches the #804 registry entry."""

    DIFF = ("diff --git a/x.hy b/x.hy\n"
            "--- a/x.hy\n"
            "+++ b/x.hy\n"
            "@@ -1,3 +1,4 @@\n"
            " keep me\n"
            "-removed line with make(<-chan int)\n"
            "+let c = make(<-chan int, 1)\n"
            "+++ not an added line\n")

    def test_added_lines_skip_context_removed_and_file_headers(self):
        # '+'-lines only: context, '-'-lines, and '+++' file headers are not
        # additions (a removed line proving a premise does not count).
        lines = list(sh.added_lines(self.DIFF))
        self.assertEqual(lines, ["let c = make(<-chan int, 1)"])

    def test_registry_matches_added_only(self):
        rule = sh.PROBES[0]
        added = list(sh.added_lines(self.DIFF))
        self.assertTrue(any(re.search(rule["pattern"], ln) for ln in added))
        self.assertFalse(any("removed line" in ln for ln in added))


class AutolandBundleTests(unittest.TestCase):
    """run_bundle() pre-validates repro.hy + fix.patch before building and
    always calls revert_on_failure() from its post-apply finally block."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="autoland-test-")
        self.addCleanup(self.tmp.cleanup)
        self.queue = Path(self.tmp.name) / "land-queue"
        self.queue.mkdir()
        self.slug = "issue-1125-sample"
        self.bundle = self.queue / self.slug
        self.bundle.mkdir()
        (self.bundle / "meta.json").write_text(
            json.dumps({"issue": 1125, "expected_code": "HYB-E1000"}),
            encoding="utf-8",
        )

    def test_missing_repro_returns_no_bundle_before_build(self):
        (self.bundle / "fix.patch").write_text("diff --git a/x b/x\n", encoding="utf-8")
        for live in (False, True):
            with self.subTest(live=live), \
                 mock.patch.object(al, "QUEUE", self.queue), \
                 mock.patch.object(al, "build") as m_build, \
                 mock.patch.object(al, "check_repro") as m_check:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = al.run_bundle(self.slug, live=live, token_env="GITHUB_TOKEN")
                self.assertEqual(rc, 2)
                self.assertIn("AUTOLAND-NO-BUNDLE", buf.getvalue())
                self.assertIn("missing repro.hy or fix.patch", buf.getvalue())
                m_build.assert_not_called()
                m_check.assert_not_called()

    def test_missing_fix_patch_returns_no_bundle_even_in_dry_run(self):
        (self.bundle / "repro.hy").write_text("fn main() {}\n", encoding="utf-8")
        for live in (False, True):
            with self.subTest(live=live), \
                 mock.patch.object(al, "QUEUE", self.queue), \
                 mock.patch.object(al, "build") as m_build, \
                 mock.patch.object(al, "check_repro") as m_check:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = al.run_bundle(self.slug, live=live, token_env="GITHUB_TOKEN")
                self.assertEqual(rc, 2)
                self.assertIn("AUTOLAND-NO-BUNDLE", buf.getvalue())
                self.assertIn("missing repro.hy or fix.patch", buf.getvalue())
                m_build.assert_not_called()
                m_check.assert_not_called()

    def test_complete_bundle_dry_run_succeeds_after_baseline_accept(self):
        (self.bundle / "repro.hy").write_text("fn main() {}\n", encoding="utf-8")
        (self.bundle / "fix.patch").write_text("diff --git a/x b/x\n", encoding="utf-8")
        with mock.patch.object(al, "QUEUE", self.queue), \
             mock.patch.object(al, "build") as m_build, \
             mock.patch.object(al, "check_repro", return_value=(0, "")) as m_check:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = al.run_bundle(self.slug, live=False, token_env="GITHUB_TOKEN")
            self.assertEqual(rc, 0)
            self.assertIn("AUTOLAND-DRYRUN", buf.getvalue())
            m_build.assert_called_once_with()
            m_check.assert_called_once_with(f"automation/land-queue/{self.slug}/repro.hy")

    def test_empty_or_invalid_bundle_artifacts_return_no_bundle_before_build(self):
        scenarios = [
            ("empty-repro", "", "diff --git a/x b/x\n", {"issue": 1125}),
            ("empty-patch", "fn main() {}\n", "", {"issue": 1125}),
            ("missing-issue", "fn main() {}\n", "diff --git a/x b/x\n", {"expected_code": "HYB-E1000"}),
            ("dir-repro", None, "diff --git a/x b/x\n", {"issue": 1125}),
        ]
        for label, repro_txt, patch_txt, meta_obj in scenarios:
            slug = f"case-{label}"
            bdir = self.queue / slug
            bdir.mkdir()
            (bdir / "meta.json").write_text(json.dumps(meta_obj), encoding="utf-8")
            if repro_txt is None:
                (bdir / "repro.hy").mkdir()
            else:
                (bdir / "repro.hy").write_text(repro_txt, encoding="utf-8")
            (bdir / "fix.patch").write_text(patch_txt, encoding="utf-8")
            with self.subTest(case=label), \
                 mock.patch.object(al, "QUEUE", self.queue), \
                 mock.patch.object(al, "build") as m_build, \
                 mock.patch.object(al, "check_repro") as m_check:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = al.run_bundle(slug, live=True, token_env="GITHUB_TOKEN")
                self.assertEqual(rc, 2)
                self.assertIn("AUTOLAND-NO-BUNDLE", buf.getvalue())
                m_build.assert_not_called()
                m_check.assert_not_called()

    def test_already_landed_bundle_skips_without_requiring_repro_or_patch(self):
        (self.bundle / "meta.json").write_text(
            json.dumps({"issue": 1125, "status": "landed", "landedSha": "deadbeef1234"}),
            encoding="utf-8",
        )
        with mock.patch.object(al, "QUEUE", self.queue), \
             mock.patch.object(al, "build") as m_build:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = al.run_bundle(self.slug, live=True, token_env="GITHUB_TOKEN")
            self.assertEqual(rc, 0)
            self.assertIn("AUTOLAND-SKIP", buf.getvalue())
            m_build.assert_not_called()

    def test_post_apply_failures_and_exceptions_always_revert_dirty_tree(self):
        (self.bundle / "repro.hy").write_text("fn main() {}\n", encoding="utf-8")
        (self.bundle / "fix.patch").write_text("diff --git a/x b/x\n", encoding="utf-8")
        corpus_dir = Path(self.tmp.name) / "tests" / "checker-corpus"
        corpus_dir.mkdir(parents=True)
        repro_dst = corpus_dir / f"_autoland_{self.slug}.hy"
        apply_ok = subprocess.CompletedProcess(["git", "apply"], 0, "", "")

        repro_dst.write_text("fn main() {}\n", encoding="utf-8")
        with mock.patch.object(al, "ROOT", Path(self.tmp.name)), \
             mock.patch.object(al, "QUEUE", self.queue), \
             mock.patch.object(al, "sh", return_value=apply_ok), \
             mock.patch.object(al, "log_event"), \
             mock.patch.object(al, "build", side_effect=[None, RuntimeError("build boom")]), \
             mock.patch.object(al, "check_repro", return_value=(0, "")), \
             mock.patch.object(al, "revert_on_failure") as m_revert:
            with self.assertRaises(RuntimeError):
                al.run_bundle(self.slug, live=True, token_env="GITHUB_TOKEN")
            m_revert.assert_called_once_with()
            self.assertFalse(repro_dst.exists())

        repro_dst.write_text("fn main() {}\n", encoding="utf-8")
        with mock.patch.object(al, "ROOT", Path(self.tmp.name)), \
             mock.patch.object(al, "QUEUE", self.queue), \
             mock.patch.object(al, "sh", return_value=apply_ok), \
             mock.patch.object(al, "log_event"), \
             mock.patch.object(al, "build"), \
             mock.patch.object(al, "check_repro", side_effect=[(0, ""), (1, "error[HYB-E1000]")]), \
             mock.patch.object(al, "battery", return_value=[("workspace", True, "")]), \
             mock.patch.object(al, "land_commit", side_effect=RuntimeError("push failed")), \
             mock.patch.object(al, "revert_on_failure") as m_revert:
            with self.assertRaises(RuntimeError):
                al.run_bundle(self.slug, live=True, token_env="GITHUB_TOKEN")
            m_revert.assert_called_once_with()
            self.assertFalse(repro_dst.exists())

        cases = [
            ("ineffective", [(0, ""), (0, "")], [], 5),
            ("wrong-code", [(0, ""), (1, "error[HYB-E9999]")], [], 6),
            ("battery-failed", [(0, ""), (1, "error[HYB-E1000]")],
             [("workspace", False, "1 failed")], 7),
        ]
        for label, repro_results, bat_results, expected_rc in cases:
            repro_dst.write_text("fn main() {}\n", encoding="utf-8")
            with self.subTest(case=label), \
                 mock.patch.object(al, "ROOT", Path(self.tmp.name)), \
                 mock.patch.object(al, "QUEUE", self.queue), \
                 mock.patch.object(al, "sh", return_value=apply_ok), \
                 mock.patch.object(al, "log_event"), \
                 mock.patch.object(al, "build"), \
                 mock.patch.object(al, "check_repro", side_effect=repro_results), \
                 mock.patch.object(al, "battery", return_value=bat_results), \
                 mock.patch.object(al, "revert_on_failure") as m_revert:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = al.run_bundle(self.slug, live=True, token_env="GITHUB_TOKEN")
                self.assertEqual(rc, expected_rc)
                m_revert.assert_called_once_with()
                self.assertFalse(repro_dst.exists())


if __name__ == "__main__":
    unittest.main()
