from __future__ import annotations
import io
import json
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import effect_replay_sandbox as ers


class TestAlgebraicEffectReplaySandbox(unittest.TestCase):
    def test_parse_deterministic_header_seeded_default_and_absent(self) -> None:
        self.assertEqual(ers.parse_deterministic_header("deterministic(seed: 0x42) { task f() }"), 0x42)
        self.assertEqual(ers.parse_deterministic_header("deterministic(seed: 100) { }"), 100)
        self.assertEqual(ers.parse_deterministic_header("deterministic { }"), 0)
        self.assertIsNone(ers.parse_deterministic_header("fn main() { }"))

    def test_audit_deterministic_source_rejects_ambient_nondeterminism_hyb_e6020(self) -> None:
        impure = "\n".join([
            "deterministic(seed: 0x42) {",
            "    let t = time.Now()",
            "    let r = rand.Intn(10)",
            "    let f = os.ReadFile(\"a.txt\")",
            "    let m = make(map[string]int)",
            "    for k, v in m { }",
            "    let d = net /* inline */ .\n        Dial(\"tcp\", \"127.0.0.1:80\")",
            "}",
        ])
        diags = ers.audit_deterministic_source(impure)
        self.assertEqual([d.code for d in diags], ["HYB-E6020"] * 5)
        self.assertEqual([d.line for d in diags], [2, 3, 4, 6, 7])
        self.assertIn("wall-clock", diags[0].message)
        self.assertIn("entropy", diags[1].message)
        self.assertIn("OS/network", diags[2].message)
        self.assertIn("map iteration", diags[3].message)

    def test_audit_deterministic_source_admits_pure_concurrent_and_comments(self) -> None:
        pure = "\n".join([
            "deterministic(seed: 0x42) {",
            "    // time.Now() and let items = make(map[string]int) in comments must not trigger",
            "    /* multiline block comment with os.Open(\"x\") and rand.Intn(5) */",
            "    let jobs = make(chan int, 4)",
            "    let items = []int{1, 2, 3}",
            "    for idx, v in items { time.sleep(10) }",
            "}",
        ])
        self.assertEqual(ers.audit_deterministic_source(pure), [])

    def test_splitmix64_reproducibility_and_seed_sensitivity(self) -> None:
        a, b, c = ers.SplitMix64(0x42), ers.SplitMix64(0x42), ers.SplitMix64(0x43)
        seq_a = [a.next_u64() for _ in range(16)]
        self.assertEqual(seq_a, [b.next_u64() for _ in range(16)])
        self.assertNotEqual(seq_a, [c.next_u64() for _ in range(16)])
        self.assertEqual(a.choice_index(1), 0)
        self.assertTrue(0 <= a.choice_index(7) < 7)

    def test_worker_pool_and_pipeline_replay_certificates_and_jitter_invariance(self) -> None:
        wp0 = ers.run_worker_pool_sandbox(0x42, jitter=0)
        wp1 = ers.run_worker_pool_sandbox(0x42, jitter=99991)
        wp_alt = ers.run_worker_pool_sandbox(0x43, jitter=0)
        self.assertEqual(wp0.trace_sha256, "4e8e0ae5a2b34b08966ee63b7e9cb1fef7b3b4a845a82ff30fa02bd06c68e338")
        self.assertEqual(wp0.trace_sha256, wp1.trace_sha256)
        self.assertEqual(wp0.events, wp1.events)
        self.assertEqual(wp0.result, [2, 2, 4, 6, 8, 10, 12, 18])
        self.assertEqual(wp0.lambda_steps, 54)
        self.assertIsNone(wp0.deadlock)
        self.assertNotEqual(wp0.trace_sha256, wp_alt.trace_sha256)

        pb0 = ers.run_pipeline_backpressure_sandbox(0x42, jitter=0)
        pb1 = ers.run_pipeline_backpressure_sandbox(0x42, jitter=777)
        self.assertEqual(pb0.trace_sha256, "6fe8a234305a15e3271ceb44c5ff1727845ea0ff4bd62a61f48b94a60ccb2e54")
        self.assertEqual(pb0.trace_sha256, pb1.trace_sha256)
        self.assertEqual(pb0.result, 204)
        self.assertEqual(pb0.lambda_steps, 49)

    def test_deadlock_witness_hyb_e6021_and_closed_channel_send_hyb_e6022(self) -> None:
        dl = ers.run_deadlock_sandbox(0x42)
        self.assertIsNotNone(dl.deadlock)
        assert dl.deadlock is not None
        self.assertEqual(dl.deadlock.code, "HYB-E6021")
        self.assertEqual(dl.deadlock.lambda_step, 3)
        self.assertEqual(dl.deadlock.blocked_tasks, (("main", "recv", "ch_a"), ("peer#1", "recv", "ch_b")))

        sb = ers.DeterministicSandbox(0x42)
        sb.make_chan("closed_ch", 1)
        def bad_main():
            yield ers.ChanSend("closed_ch", 42)
            yield ers.ChanClose("closed_ch")
            self.assertEqual((yield ers.ChanRecv("closed_ch")), (42, True))
            self.assertEqual((yield ers.ChanRecv("closed_ch")), (None, False))
            yield ers.ChanSend("closed_ch", 7)
        with self.assertRaisesRegex(RuntimeError, "HYB-E6022"):
            sb.run(bad_main)
        def bad_select():
            return (yield ers.Select((ers.ChanSend("closed_ch", 9),), timeout_ticks=5))
        with self.assertRaisesRegex(RuntimeError, "HYB-E6022"):
            sb.run(bad_select)

    def test_select_timeout_and_wakeup_semantics(self) -> None:
        sb = ers.DeterministicSandbox(0x42)
        sb.make_chan("slow", 0)
        def main_timeout():
            return (yield ers.Select((ers.ChanRecv("slow"),), timeout_ticks=15))
        cert = sb.run(main_timeout)
        self.assertEqual(cert.result, (-1, (None, False)))
        self.assertEqual(cert.virtual_time, 15)

        sb2 = ers.DeterministicSandbox(0x42)
        sb2.make_chan("fast", 0)
        def sender():
            yield ers.Sleep(5)
            yield ers.ChanSend("fast", 99)
        def main_wake():
            yield ers.Spawn("sender", sender)
            return (yield ers.Select((ers.ChanRecv("fast"),), timeout_ticks=20))
        cert2 = sb2.run(main_wake)
        self.assertEqual(cert2.result, (0, (99, True)))
        self.assertEqual(cert2.virtual_time, 5)

    def test_verify_replay_invariance_and_live_corpus_cli(self) -> None:
        rep = ers.verify_replay_invariance(0x42, runs=8)
        self.assertEqual(rep["replay_identical_ratio"], 1.0)
        self.assertTrue(rep["seed_sensitivity_verified"])
        self.assertEqual(rep["deadlock_witness_code"], "HYB-E6021")

        repo_root = Path(__file__).resolve().parents[3]
        corp = ers.audit_live_corpus(repo_root)
        self.assertGreaterEqual(corp["total_hy_files"], 130)
        self.assertGreaterEqual(corp["concurrent_files"], 80)
        self.assertGreaterEqual(corp["impure_rejected_files"], 4)

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ers.main(["--check", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["issue"], 957)
        self.assertEqual(payload["replay"]["replay_identical_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
