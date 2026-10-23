#!/usr/bin/env python3
"""Unit and integration tests for W-032 (#950) Certified Task-Graph Acyclicity."""
from __future__ import annotations

import io, json, sys, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from task_graph_certifier import (  # noqa: E402
    CORPUS_FIXTURES,
    TaskSpec,
    audit_repo,
    benchmark_postmortems,
    build_wait_graph,
    certify_task_graph,
    find_canonical_cycle,
    kahn_topological_epochs,
    main,
    parse_hy_task_graph,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class TaskGraphCertifierTests(unittest.TestCase):
    def test_dining_philosophers_cycle_rejected_hyb_e7001(self) -> None:
        tasks = [
            TaskSpec("phil_a", sends=("fork_ab",), recvs=("fork_ca",)),
            TaskSpec("phil_b", sends=("fork_bc",), recvs=("fork_ab",)),
            TaskSpec("phil_c", sends=("fork_ca",), recvs=("fork_bc",)),
        ]
        res = certify_task_graph("dining_philosophers", tasks)
        self.assertEqual(res.verdict, "REJECT-CYCLE")
        self.assertEqual(res.diagnostic, "HYB-E7001")
        self.assertEqual(res.cycle_witness, ("phil_a", "phil_c", "phil_b", "phil_a"))

    def test_unbounded_dynamic_spawn_rejected_hyb_e7002(self) -> None:
        tasks = [
            TaskSpec("spawner", sends=("jobs",), dynamic_spawn=True),
            TaskSpec("worker", recvs=("jobs",)),
        ]
        res = certify_task_graph("unbounded_spawn", tasks)
        self.assertEqual(res.verdict, "REJECT-UNBOUNDED")
        self.assertEqual(res.diagnostic, "HYB-E7002")
        self.assertEqual(res.cycle_witness, ())

    def test_wait_graph_orphan_and_epoch_inversion_edges(self) -> None:
        tasks = [
            TaskSpec("sender", sends=("unbuf_ping",)),
            TaskSpec("waiter", joins=("target",), explicit_epoch=1),
            TaskSpec("target", explicit_epoch=2),
        ]
        adj, edges = build_wait_graph(tasks)
        kinds = {e.kind for e in edges}
        self.assertTrue({"orphan_send", "join", "epoch"}.issubset(kinds))
        self.assertEqual(find_canonical_cycle([t.name for t in tasks], adj), ("sender", "sender"))

    def test_kahn_topological_epochs_monotonicity(self) -> None:
        tasks = [
            TaskSpec("stage1", sends=("c1",)),
            TaskSpec("stage2", sends=("c2",), recvs=("c1",)),
            TaskSpec("sink", recvs=("c2",), joins=("stage2",)),
        ]
        adj, _ = build_wait_graph(tasks)
        order, epochs = kahn_topological_epochs([t.name for t in tasks], adj)
        self.assertEqual(order, ("stage1", "stage2", "sink"))
        self.assertEqual(epochs, {"stage1": 0, "stage2": 1, "sink": 2})
        for src, wait_edges in adj.items():
            for edge in wait_edges:
                self.assertGreater(epochs[src], epochs[edge.dst])

    def test_close_phase_transition_accepts_move_send_clean_pattern(self) -> None:
        hy_src = """
        fn consumer(batches <-chan int, acks chan<- int) { for b in batches { acks <- b } }
        fn main() {
            batches := make(chan int, 2)
            acks := make(chan int, 2)
            t := task consumer(batches, acks)
            batches <- 1
            close(batches)
            <-acks
            task.wait(t)
        }
        """
        res = parse_hy_task_graph(hy_src, "move_send_clean_inline")
        self.assertEqual(res.verdict, "ACCEPT-CERTIFIED")
        self.assertIsNone(res.diagnostic)
        self.assertEqual(res.epochs, {"main@send": 0, "consumer": 1, "main@join": 2})

    def test_postmortem_suite_recall_and_positive_controls(self) -> None:
        verdicts = benchmark_postmortems()
        self.assertEqual(len(verdicts), 8)
        for dead in verdicts[:6]:
            self.assertEqual((dead.verdict, dead.diagnostic), ("REJECT-CYCLE", "HYB-E7001"), dead.case_id)
            self.assertGreaterEqual(len(dead.cycle_witness), 2, dead.case_id)
        for ctrl in verdicts[6:]:
            self.assertEqual((ctrl.verdict, ctrl.diagnostic), ("ACCEPT-CERTIFIED", None), ctrl.case_id)

    def test_audit_repo_and_cli_check_json(self) -> None:
        report = audit_repo(REPO_ROOT)
        self.assertTrue(report["row16_baseline_no_claim"] and report["falsifiers_passed"])
        self.assertEqual((report["corpus_certified"], report["cycles_caught"], report["controls_accepted"]), (len(CORPUS_FIXTURES), 6, 2))
        self.assertEqual(report["select_false_positives"], 0)
        self.assertLessEqual(report["annotation_lines_per_50_loc"], 1.0)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--repo-root", str(REPO_ROOT), "--check", "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(buf.getvalue())["falsifiers_passed"])
        missing = audit_repo(REPO_ROOT / "__nonexistent_w032_dir__")
        self.assertFalse(missing["row16_baseline_no_claim"] or missing["falsifiers_passed"])

    def test_select_unbuffered_receiver_and_crlf_utf8_resilience(self) -> None:
        tasks = [TaskSpec("supervisor", sends=("unbuf_jobs",)), TaskSpec("worker", recvs=("unbuf_jobs", "ctx_done"), select_nonblocking=True)]
        self.assertEqual(certify_task_graph("select_unbuf", tasks, {"unbuf_jobs"}).verdict, "ACCEPT-CERTIFIED")
        crlf_src = "// UTF-8: Árvíztűrő 🚀\r\nfn w() {}\r\nfn main() {\r\n t := task w()\r\n task.wait(t)\r\n}\r\n"
        res = parse_hy_task_graph(crlf_src, "crlf_handle_join")
        self.assertEqual((res.verdict, res.epochs), ("ACCEPT-CERTIFIED", {"w": 0, "main": 1}))


if __name__ == "__main__":
    unittest.main()
