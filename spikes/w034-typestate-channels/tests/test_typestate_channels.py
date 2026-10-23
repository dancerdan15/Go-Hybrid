"""Unit and integration tests for W-034 (#947) zero-allocation typestate channels."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import typestate_channels as tc


class TestTypestateChannels(unittest.TestCase):
    def test_parse_and_format_protocol_roundtrip(self) -> None:
        spec = "chan[send Req -> recv Resp -> close]"
        steps = tc.parse_protocol(spec)
        self.assertEqual(len(steps), 3)
        self.assertEqual(tc.format_protocol(steps), spec)
        self.assertEqual(tc.parse_protocol("chan[end]"), ())
        self.assertEqual(tc.format_protocol(()), "chan[end]")
        loop_spec = "chan[send Ping -> recv Pong -> loop]"
        self.assertEqual(tc.format_protocol(tc.parse_protocol(loop_spec)), loop_spec)
        self.assertEqual(tc.format_protocol(tc.parse_protocol("chan[\r\n send Req ->\r\n close\r\n]")), "chan[send Req -> close]")
        for bad in ("chan[]", "chan[foo]", "chan[invalid_op Foo bar]", "chan[close -> send int]", "chan[loop -> close]"):
            with self.assertRaisesRegex(ValueError, "HYB-E7000", msg=bad):
                tc.parse_protocol(bad)

    def test_involutive_duality_and_spawn_verification(self) -> None:
        for spec in (
            "chan[send Req -> recv Resp -> close]",
            "chan[recv Job -> send Ack -> loop]",
            "chan[end]",
        ):
            steps = tc.parse_protocol(spec)
            self.assertEqual(tc.dual(tc.dual(steps)), steps)
            self.assertEqual(tc.verify_spawn_duality(steps, tc.dual(steps)), [])
        diags = tc.verify_spawn_duality(
            "chan[send Req -> recv Resp -> close]",
            "chan[send Req -> recv Resp -> close]",
        )
        self.assertEqual(len(diags), 1)
        self.assertIn("HYB-E7001", diags[0])
        self.assertIn("HYB-E6012", diags[0])

    def test_flow_sensitive_check_function_and_managed_escape(self) -> None:
        # E7001: wrong step order & post-close send
        d_order = tc.check_function(
            {"ch": "chan[send int -> recv int -> close]"},
            [("recv", "ch", "int"), ("send", "ch", "int"), ("close", "ch")],
        )
        self.assertTrue(any("HYB-E7001" in d for d in d_order))
        d_closed = tc.check_function(
            {"ch": "chan[close]"},
            [("close", "ch"), ("send", "ch", "int")],
        )
        self.assertTrue(any("HYB-E7001" in d for d in d_closed))
        # E7002: unfulfilled obligation at function exit
        d_unfin = tc.check_function({"ch": "chan[send int -> close]"}, [("send", "ch", "int")])
        self.assertTrue(any("HYB-E7002" in d for d in d_unfin))
        # E7003: branch join mismatch (2-arm & 3-arm), empty arms safety, and loop partial consumption
        d_branch = tc.check_function(
            {"ch": "chan[send int -> close]"},
            [("branch", [[("send", "ch", "int"), ("close", "ch")], [("send", "ch", "int"), ("close", "ch")], [("send", "ch", "int")]])],
        )
        self.assertTrue(any("HYB-E7003" in d for d in d_branch))
        d_empty_arms = tc.check_function({"ch": "chan[send int -> close]"}, [("branch", [])])
        self.assertTrue(any("HYB-E7002" in d for d in d_empty_arms))
        d_loop_err = tc.check_function(
            {"ch": "chan[send Ping -> recv Pong -> loop]"},
            [("loop", [("send", "ch", "Ping")])],
        )
        self.assertTrue(any("HYB-E7003" in d for d in d_loop_err))
        # Valid cyclic loop protocol & managed=True escape hatch
        self.assertEqual(
            tc.check_function(
                {"ch": "chan[send int -> loop]"},
                [("loop", [("send", "ch", "int")])],
            ),
            [],
        )
        self.assertEqual(
            tc.check_function({"ch": "chan[send int -> close]"}, [], managed=True),
            [],
        )

    def test_zero_allocation_go_lowering(self) -> None:
        hom = tc.lower_channel("pp", "chan[send int -> recv int -> close]", buf=4)
        self.assertEqual(hom.strategy, "bare_chan")
        self.assertEqual(hom.decls, ("pp := make(chan int, 4)",))
        self.assertEqual(hom.alloc_wrappers, 0)
        self.assertEqual(hom.boxing_overhead_pct, 0.0)
        het = tc.lower_channel("rpc", "chan[send Req -> recv Resp -> close]")
        self.assertEqual(het.strategy, "directional_pair")
        self.assertEqual(het.decls, ("rpc_req := make(chan Req)", "rpc_resp := make(chan Resp)"))
        self.assertEqual(het.alloc_wrappers, 0)
        self.assertEqual(het.boxing_overhead_pct, 0.0)

    def test_canonical_positive_and_negative_cases(self) -> None:
        self.assertEqual(len(tc.CANONICAL_PATTERNS), 5)
        for name, (chs, ops) in tc.CANONICAL_PATTERNS.items():
            with self.subTest(pattern=name):
                self.assertEqual(tc.check_function(chs, ops), [])
        self.assertEqual(len(tc.NEGATIVE_CASES), 4)
        for name, (chs, ops, expected_code) in tc.NEGATIVE_CASES.items():
            with self.subTest(case=name):
                diags = tc.check_function(chs, ops)
                self.assertTrue(any(expected_code in d for d in diags), f"{name}: {diags}")

    def test_live_corpus_audit_and_cli_modes(self) -> None:
        report = tc.audit_corpus()
        self.assertEqual(report["total_hy_files"], 136)
        self.assertEqual(report["channel_files"], 85)
        self.assertEqual(report["select_files"], 10)
        self.assertEqual(report["close_files"], 10)
        self.assertEqual(report["positive_patterns_passed"], 5)
        self.assertEqual(report["negative_cases_caught"], 4)
        self.assertFalse(report["falsified"])
        self.assertEqual(tc.audit_corpus(Path("__nonexistent_root__"))["total_hy_files"], 0)
        with patch("sys.stdout", new_callable=io.StringIO) as out_chk:
            self.assertEqual(tc.main(["--check"]), 0)
            self.assertIn("w034-typestate-channels OK: 5/5 positive, 4/4 negative", out_chk.getvalue())
        with patch("sys.stdout", new_callable=io.StringIO) as out_json:
            self.assertEqual(tc.main(["--check", "--json"]), 0)
            payload = json.loads(out_json.getvalue())
            self.assertEqual(payload["issue"], 947)
            self.assertEqual(payload["sample_lowering"]["alloc_wrappers"], 0)


if __name__ == "__main__":
    unittest.main()
