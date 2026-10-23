#!/usr/bin/env python3
"""test_shepherd_gate.py — offline self-tests for tools/shepherd.py.

No network, no subprocesses: only the pure functions that the workflows lean
on. If the kill-switch matrix, the dup planner, the conflict classifier, or
the probe parser drift, CI fails before any workflow can.
"""

import json
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shepherd as sh  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "tests"


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


class _FakeGh:
    def __init__(self, repo="limitlessprojects/Go-Hybrid", get_map=None,
                 paginate_map=None, fail_delete_404=False):
        self.repo = repo
        self.get_map, self.paginate_map = get_map or {}, paginate_map or {}
        self.fail_delete_404 = fail_delete_404
        self.calls, self.remaining = 0, 4999
        self.deleted, self.posted, self.put_calls, self.got = [], [], [], []

    def get(self, path):
        self.calls += 1
        self.got.append(path)
        return self.get_map.get(path)

    def paginate(self, path, max_pages=10):
        self.calls += 1
        return list(self.paginate_map.get(path, []))

    def post(self, path, payload):
        self.calls += 1
        self.posted.append((path, payload))
        return 200, {}

    def req(self, method, path, payload=None):
        self.calls += 1
        if method == "DELETE":
            self.deleted.append(path)
            if self.fail_delete_404:
                raise RuntimeError("GET/POST %s -> 404: Label does not exist" % path)
            return 204, None
        if method == "PUT":
            self.put_calls.append((path, payload))
            return 202, {}
        return 200, self.get_map.get(path)


class StateLabelRatchet(unittest.TestCase):
    """Issue #986: bot:conflict must not be a one-way ratchet across sync-labels
    and W2 rebase when a PR becomes mergeable again."""

    def test_sync_item_labels_fixture_matrix(self):
        fx = json.loads((FIXTURES_DIR / "issue_986_sync_labels_ratchet_fixture.json").read_text())
        for case in fx["cases"]:
            with self.subTest(case=case["name"]):
                gh = _FakeGh()
                added, removed = sh.sync_item_labels(gh, dict(case["item"]))
                self.assertEqual((added, removed), (case["expected_added"], case["expected_removed"]))
                expected_del = ["/repos/%s/issues/%d/labels/%s" % (
                    gh.repo, case["item"]["number"], lbl.replace(":", "%3A"))
                    for lbl in case["expected_removed"]]
                self.assertEqual(gh.deleted, expected_del)
                expected_post = [("/repos/%s/issues/%d/labels" % (gh.repo, case["item"]["number"]),
                                  {"labels": case["expected_added"]})] if case["expected_added"] else []
                self.assertEqual(gh.posted, expected_post)

    def test_sync_item_labels_fetches_when_labels_omitted_and_ignores_404(self):
        gh = _FakeGh(
            get_map={"/repos/limitlessprojects/Go-Hybrid/issues/905/labels?per_page=100": [
                {"name": "bot:conflict"}]},
            fail_delete_404=True,
        )
        it = {"number": 905, "bucket": "LAND-CANDIDATE"}
        with patch.object(sh.tb, "log"):
            self.assertEqual(sh.sync_item_labels(gh, it, dry=True),
                             (["bot:land-candidate"], ["bot:conflict"]))
        self.assertEqual((gh.deleted, gh.posted), ([], []))
        self.assertEqual(sh.sync_item_labels(gh, it, dry=False),
                         (["bot:land-candidate"], ["bot:conflict"]))
        self.assertEqual(gh.deleted, [
            "/repos/limitlessprojects/Go-Hybrid/issues/905/labels/bot%3Aconflict"])
        self.assertEqual(gh.posted, [(
            "/repos/limitlessprojects/Go-Hybrid/issues/905/labels",
            {"labels": ["bot:land-candidate"]})])

    def test_cmd_rebase_clears_stale_conflict_when_mergeable_true(self):
        fx = json.loads((FIXTURES_DIR / "issue_986_rebase_stale_conflict_fixture.json").read_text())
        repo = "limitlessprojects/Go-Hybrid"
        get_map = {"/repos/%s/commits/main" % repo: {"sha": fx["main_sha"]}}
        open_prs = []
        for pr in fx["open_prs"]:
            n = pr["number"]
            open_prs.append({"number": n, "created_at": pr["created_at"],
                             "draft": pr["draft"], "labels": pr["labels"]})
            get_map["/repos/%s/pulls/%d" % (repo, n)] = {
                "number": n, "mergeable": pr["mergeable"], "head": {"sha": pr["head_sha"]}}
            get_map["/repos/%s/compare/%s...%s" % (repo, fx["main_sha"], pr["head_sha"])] = {
                "behind_by": pr["behind_by"]}
        gh = _FakeGh(repo=repo, get_map=get_map, paginate_map={
            "/repos/%s/pulls?state=open&per_page=100" % repo: open_prs})
        with patch.object(sh.tb, "load_token", return_value="tok"), \
             patch.object(sh.tb, "Gh", return_value=gh), \
             patch.object(sh.tb, "log"):
            sh.cmd_rebase(SimpleNamespace(dry=False, token_file=None, token_env=None))
        self.assertNotIn("/repos/%s/pulls/913" % repo, gh.got)
        self.assertIn("/repos/%s/pulls/912" % repo, gh.got)
        self.assertEqual(gh.deleted, ["/repos/%s/issues/911/labels/bot%%3Aconflict" % repo])
        self.assertEqual(gh.put_calls, [("/repos/%s/pulls/911/update-branch" % repo,
                                         {"expected_head_sha": "head911000000000"})])

    def test_adversarial_red_team_vectors(self):
        repo = "limitlessprojects/Go-Hybrid"
        # 1. draft_parked=True + non-state labels (track:soundness, triage:unique) + TRIAGE.json triage:*-only list
        gh = _FakeGh(get_map={
            "/repos/%s/issues/920/labels?per_page=100" % repo: [
                {"name": "bot:conflict"}, {"name": "track:soundness"}, {"name": "triage:unique"}]
        })
        self.assertEqual(sh.sync_item_labels(gh, {"number": 919, "bucket": "LAND-CANDIDATE",
                                                  "draft_parked": True, "labels": ["bot:conflict"]}), ([], []))
        self.assertEqual((gh.deleted, gh.posted), ([], []))
        added, removed = sh.sync_item_labels(gh, {"number": 920, "bucket": "STALE-BASE",
                                                  "labels": ["triage:behind", "triage:tests"]})
        self.assertEqual((added, removed), (["bot:stale-base"], ["bot:conflict"]))
        self.assertEqual(gh.deleted, ["/repos/%s/issues/920/labels/bot%%3Aconflict" % repo])
        # 2. cmd_rebase with mergeable=None and dry_run=True (0 POST, 0 DELETE, 0 PUT)
        gh_dry = _FakeGh(
            repo=repo,
            get_map={
                "/repos/%s/commits/main" % repo: {"sha": "main000"},
                "/repos/%s/pulls/921" % repo: {"number": 921, "mergeable": None, "head": {"sha": "h921"}},
                "/repos/%s/pulls/922" % repo: {"number": 922, "mergeable": True, "head": {"sha": "h922"}},
                "/repos/%s/compare/main000...h922" % repo: {"behind_by": 60},
            },
            paginate_map={"/repos/%s/pulls?state=open&per_page=100" % repo: [
                {"number": 921, "created_at": "2026-04-01T00:00:00Z", "draft": False, "labels": [{"name": "bot:conflict"}]},
                {"number": 922, "created_at": "2026-04-02T00:00:00Z", "draft": False, "labels": [{"name": "bot:conflict"}]},
            ]},
        )
        with patch.object(sh.tb, "load_token", return_value="tok"), \
             patch.object(sh.tb, "Gh", return_value=gh_dry), \
             patch.object(sh.tb, "log"):
            sh.cmd_rebase(SimpleNamespace(dry=False, dry_run=True, token_file=None, token_env=None))
        self.assertEqual((gh_dry.deleted, gh_dry.posted, gh_dry.put_calls), ([], [], []))


if __name__ == "__main__":
    unittest.main()
