#!/usr/bin/env python3
"""Unit and integration tests for W-057 (Issue #1279) Global Choreography Endpoint Projection."""
from __future__ import annotations
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import choreography_projector as cp  # noqa: E402


class TestW057ChoreographyProjector(unittest.TestCase):
    def test_f1_two_buyer_projection_and_select_synthesis(self) -> None:
        passed, total, deadlocks, details = cp.run_canonical_suite()
        self.assertEqual((passed, total, deadlocks), (8, 8, 0))
        self.assertTrue(all(d["passed"] for d in details))

    def test_f1_rejects_choice_knowledge_gap_and_idle_branch_asymmetry_hyb_e5701(self) -> None:
        gap = cp.Choreography(
            "KnowledgeGap", ("Client", "Server", "Auditor"),
            (cp.ChoiceStep("Client", (
                ("Buy", (cp.MsgStep("Client", "Server", "Buy", "Item"), cp.MsgStep("Server", "Auditor", "Notify", "Unit"), cp.MsgStep("Auditor", "Server", "Archive", "Id"))),
                ("Cancel", (cp.MsgStep("Client", "Server", "Cancel", "Item"), cp.MsgStep("Server", "Auditor", "Notify", "Unit"), cp.MsgStep("Auditor", "Server", "Drop", "Id"))),
            )),),
        )
        res = cp.ChoreographyProjector.project_and_verify(gap)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, cp.HYB_E5701)
        idle_asym = cp.Choreography(
            "IdleAsym", ("A", "B", "C"),
            (cp.ChoiceStep("A", (
                ("Left", (cp.MsgStep("A", "B", "L", "Int"), cp.MsgStep("B", "C", "Ping", "Unit"))),
                ("Right", (cp.MsgStep("A", "B", "R", "Int"),)),
            )),),
        )
        res_idle = cp.ChoreographyProjector.project_and_verify(idle_asym)
        self.assertEqual((res_idle.ok, res_idle.code), (False, cp.HYB_E5701))
        payload_collision = cp.Choreography(
            "PayloadCollision", ("A", "B", "C"),
            (cp.ChoiceStep("A", (
                ("L", (cp.MsgStep("A", "B", "L", "Int"), cp.MsgStep("B", "C", "Notify", "Int"), cp.MsgStep("C", "B", "Act1", "Unit"))),
                ("R", (cp.MsgStep("A", "B", "R", "Int"), cp.MsgStep("B", "C", "Notify", "String"), cp.MsgStep("C", "B", "Act2", "Unit"))),
            )),),
        )
        res_coll = cp.ChoreographyProjector.project_and_verify(payload_collision)
        self.assertEqual((res_coll.ok, res_coll.code), (False, cp.HYB_E5701))
        mergeable_pos = cp.Choreography(
            "DisjointNotifyPositiveControl", ("Client", "Server", "Auditor"),
            (cp.ChoiceStep("Client", (
                ("Buy", (cp.MsgStep("Client", "Server", "Buy", "Item"), cp.MsgStep("Server", "Auditor", "NotifyBuy", "Unit"), cp.MsgStep("Auditor", "Server", "Archive", "Id"))),
                ("Cancel", (cp.MsgStep("Client", "Server", "Cancel", "Item"), cp.MsgStep("Server", "Auditor", "NotifyCancel", "Unit"), cp.MsgStep("Auditor", "Server", "Drop", "Id"))),
            )),),
        )
        res_pos = cp.ChoreographyProjector.project_and_verify(mergeable_pos)
        self.assertTrue(res_pos.ok)
        self.assertEqual((res_pos.code, res_pos.deadlock_states, res_pos.orphan_messages), ("OK", 0, 0))
        self.assertIsInstance(res_pos.endpoints["Auditor"][0], cp.LocalSelect)
        self.assertEqual(res_pos.endpoints["Auditor"][0].kind, "external")

    def test_f2_rejects_self_comm_and_disconnected_causality_hyb_e5702(self) -> None:
        self_comm = cp.Choreography("SelfSend", ("A", "B"), (cp.MsgStep("A", "A", "Mirror", "Int"),))
        res_self = cp.ChoreographyProjector.project_and_verify(self_comm)
        self.assertEqual((res_self.ok, res_self.code), (False, cp.HYB_E5702))
        empty_br = cp.Choreography("EmptyBr", ("A", "B"), (cp.ChoiceStep("A", (("Empty", ()),)),))
        res_empty = cp.ChoreographyProjector.project_and_verify(empty_br)
        self.assertEqual((res_empty.ok, res_empty.code), (False, cp.HYB_E5702))
        disconn = cp.Choreography(
            "Disconnected", ("A", "B", "C"),
            (cp.ChoiceStep("A", (
                ("B1", (cp.MsgStep("A", "B", "Go", "Unit"), cp.MsgStep("C", "B", "Premature", "Unit"))),
                ("B2", (cp.MsgStep("A", "B", "Stop", "Unit"),)),
            )),),
        )
        res_disc = cp.ChoreographyProjector.project_and_verify(disconn)
        self.assertEqual((res_disc.ok, res_disc.code), (False, cp.HYB_E5702))

    def test_f3_rejects_local_deviation_and_row16_send_after_close_hyb_e5703(self) -> None:
        proto = cp.Choreography(
            "Row16Stream", ("Producer", "Consumer"),
            (
                cp.MsgStep("Producer", "Consumer", "Frame", "Bytes", close_after=True),
                cp.MsgStep("Consumer", "Producer", "Summary", "Int"),
            ),
        )
        proj = cp.ChoreographyProjector.project_and_verify(proto)
        self.assertTrue(proj.ok)
        self.assertEqual((proj.deadlock_states, proj.orphan_messages, proj.closed_send_panics), (0, 0, 0))
        conformant = cp.ChoreographyProjector.project_and_verify(proto, dict(proj.endpoints))
        self.assertTrue(conformant.ok)
        self.assertEqual((conformant.code, conformant.deadlock_states, conformant.orphan_messages, conformant.closed_send_panics), ("OK", 0, 0, 0))
        deadlock_impl = {**proj.endpoints, "Consumer": (cp.LocalRecv("Producer", "Frame", "Bytes", close_after=True),)}
        dev_dl = cp.ChoreographyProjector.project_and_verify(proto, deadlock_impl)
        self.assertEqual((dev_dl.ok, dev_dl.code, dev_dl.deadlock_states), (False, cp.HYB_E5703, 1))
        ghost_impl = {**proj.endpoints, "Consumer": (cp.LocalSend("GhostPeer", "Bogus", "Unit"),)}
        dev_ghost = cp.ChoreographyProjector.project_and_verify(proto, ghost_impl)
        self.assertEqual((dev_ghost.ok, dev_ghost.code, dev_ghost.deadlock_states), (False, cp.HYB_E5703, 1))
        bad_impl = {
            **proj.endpoints,
            "Producer": (
                cp.LocalSend("Consumer", "Frame", "Bytes", close_after=True),
                cp.LocalSend("Consumer", "IllegalAfterClose", "Bytes"),
                cp.LocalRecv("Consumer", "Summary", "Int"),
            ),
        }
        dev = cp.ChoreographyProjector.project_and_verify(proto, bad_impl)
        self.assertFalse(dev.ok)
        self.assertEqual(dev.code, cp.HYB_E5703)
        self.assertGreaterEqual(dev.closed_send_panics, 1)

    def test_guarantees_row16_and_moonshot_solicitation_live_repo_anchors(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        guarantees = (repo_root / "GUARANTEES.md").read_text(encoding="utf-8")
        moonshot = (repo_root / "MOONSHOT.md").read_text(encoding="utf-8")
        self.assertIn("| 16 | Send-on-closed-channel panics |", guarantees)
        self.assertIn("a liveness/protocol property, out of Tier-0's scope", guarantees)
        self.assertIn("Attack the emit target.", moonshot)
        self.assertIn("Deadlock-freedom", moonshot)

    def test_cli_check_and_json_modes(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cp.main(["--check"])
        self.assertEqual(rc, 0)
        self.assertIn("W-057 (#1279) OK: 8/8 cases verified, 0 deadlock states, 100% EPP mergeability sound", buf.getvalue())
        buf_json = io.StringIO()
        with patch("sys.stdout", buf_json):
            rc_j = cp.main(["--json"])
        self.assertEqual(rc_j, 0)
        payload = json.loads(buf_json.getvalue())
        self.assertEqual((payload["spike"], payload["issue"], payload["passed"], payload["deadlocks"]), ("W-057", 1279, 8, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
