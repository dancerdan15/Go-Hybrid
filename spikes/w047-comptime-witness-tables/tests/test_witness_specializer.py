from __future__ import annotations
import io, json, sys, unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import witness_specializer as ws  # noqa: E402


class WitnessSpecializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = ws.ContractSpec("ResetCloser", (ws.MethodSig("close_into", "move", ("ref",), "int"),))

    def test_verify_witness_contract_accepts_exact_ownership_match(self) -> None:
        impl = ws.TypeImpl("ShardChan", True, self.contract.methods)
        self.assertEqual(ws.verify_witness_contract(impl, self.contract), ())

    def test_verify_witness_contract_rejects_missing_method_and_mode_mismatch(self) -> None:
        diags_missing = ws.verify_witness_contract(ws.TypeImpl("EmptyChan", True, ()), self.contract)
        self.assertEqual(len(diags_missing), 1)
        self.assertEqual(diags_missing[0].code, ws.DIAG_WITNESS_SIGNATURE_MISMATCH)
        self.assertIn("missing required contract method", diags_missing[0].detail)

        wrong_mode = ws.TypeImpl("BadRefChan", True, (ws.MethodSig("close_into", "ref", ("ref",), "int"),))
        diags_mode = ws.verify_witness_contract(wrong_mode, self.contract)
        self.assertEqual(len(diags_mode), 1)
        self.assertEqual(diags_mode[0].code, ws.DIAG_WITNESS_SIGNATURE_MISMATCH)
        self.assertIn("ownership/type mismatch", diags_mode[0].detail)

    def test_verify_witness_contract_rejects_boxed_affine_interface(self) -> None:
        boxed = ws.TypeImpl("AffineChan", True, self.contract.methods, box_as_interface=True)
        diags = ws.verify_witness_contract(boxed, self.contract)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].code, ws.DIAG_WITNESS_SIGNATURE_MISMATCH)
        self.assertIn("cannot be boxed into a dynamic interface", diags[0].detail)

    def test_specialize_witness_call_inline_devirt_vs_static_vtable(self) -> None:
        impl_a = ws.TypeImpl("ShardChan", True, self.contract.methods, size_bytes=24)
        impl_b = ws.TypeImpl("PipeChan", True, self.contract.methods, size_bytes=32)
        impl_c = ws.TypeImpl("RingChan", True, self.contract.methods, size_bytes=16)

        inline_plan = ws.specialize_witness_call("drain_pair", self.contract, (impl_a, impl_b))
        self.assertEqual(inline_plan.strategy, "inline_devirt")
        self.assertEqual(inline_plan.heap_alloc_bytes_per_call, 0)
        self.assertEqual(inline_plan.interface_boxing_bytes_saved, 56)
        self.assertEqual(inline_plan.witness_bodies, 2)
        self.assertEqual(inline_plan.diagnostics, ())
        self.assertIn("inline devirtualized", inline_plan.emitted_go_snippet)

        vtable_plan = ws.specialize_witness_call("drain_all", self.contract, (impl_a, impl_b, impl_c))
        self.assertEqual(vtable_plan.strategy, "static_witness_table")
        self.assertEqual(vtable_plan.heap_alloc_bytes_per_call, 0)
        self.assertEqual(vtable_plan.interface_boxing_bytes_saved, 72)
        self.assertEqual(vtable_plan.monomorphized_bodies, 3)
        self.assertEqual(vtable_plan.witness_bodies, 1)
        self.assertIn("const static vtable", vtable_plan.emitted_go_snippet)

    def test_r1_linear_close_right_and_send_after_close(self) -> None:
        non_owner = ws.verify_deadlock_witness(
            "non_owner", [ws.ProtocolOp("make_chan", "main", "ch"), ws.ProtocolOp("close", "worker", "ch")]
        )
        self.assertFalse(non_owner.accepted)
        self.assertEqual(non_owner.diagnostics[0].code, ws.DIAG_CLOSE_LINEARITY_VIOLATION)

        unjoined_sender = ws.verify_deadlock_witness(
            "unjoined_sender",
            [ws.ProtocolOp("make_chan", "main", "ch"), ws.ProtocolOp("spawn", "main", "s1", "ch"), ws.ProtocolOp("close", "main", "ch")],
        )
        self.assertFalse(unjoined_sender.accepted)
        self.assertEqual(unjoined_sender.diagnostics[0].code, ws.DIAG_CLOSE_LINEARITY_VIOLATION)

        send_closed = ws.verify_deadlock_witness(
            "send_closed",
            [ws.ProtocolOp("make_chan", "main", "ch"), ws.ProtocolOp("close", "main", "ch"), ws.ProtocolOp("send", "main", "ch")],
        )
        self.assertFalse(send_closed.accepted)
        self.assertEqual(send_closed.diagnostics[0].code, ws.DIAG_CLOSE_LINEARITY_VIOLATION)

    def test_r2_acyclic_spawn_dag_and_orphan_child_detection(self) -> None:
        orphan = ws.verify_deadlock_witness("orphan", [ws.ProtocolOp("spawn", "main", "child"), ws.ProtocolOp("return", "main")])
        self.assertFalse(orphan.accepted)
        self.assertEqual(orphan.diagnostics[0].code, ws.DIAG_SPAWN_DAG_VIOLATION)

        cycle = ws.verify_deadlock_witness(
            "cycle3",
            [ws.ProtocolOp("wait_task", "t1", "t2"), ws.ProtocolOp("wait_task", "t2", "t3"), ws.ProtocolOp("wait_task", "t3", "t1")],
        )
        self.assertFalse(cycle.accepted)
        self.assertTrue(any(d.code == ws.DIAG_SPAWN_DAG_VIOLATION for d in cycle.diagnostics))

    def test_r3_blocking_recv_modes_and_livelock_falsifier(self) -> None:
        bare_strict = ws.verify_deadlock_witness(
            "bare_strict", [ws.ProtocolOp("make_chan", "main", "ch"), ws.ProtocolOp("bare_recv", "main", "ch")], enforce_r3_bare_recv=True
        )
        self.assertFalse(bare_strict.accepted)
        self.assertEqual(bare_strict.diagnostics[0].code, ws.DIAG_UNBOUNDED_RECEIVE)

        bare_relaxed = ws.verify_deadlock_witness(
            "bare_relaxed", [ws.ProtocolOp("make_chan", "main", "ch"), ws.ProtocolOp("bare_recv", "main", "ch")], enforce_r3_bare_recv=False
        )
        self.assertTrue(bare_relaxed.accepted)

        spin = ws.verify_deadlock_witness(
            "spin_default", [ws.ProtocolOp("make_chan", "main", "ch"), ws.ProtocolOp("select_recv", "main", "ch", "spin_default")]
        )
        self.assertTrue(spin.accepted)
        self.assertTrue(spin.livelock_risk)

    def test_six_and_six_corpus_and_live_repo_audit(self) -> None:
        corpus = ws.run_six_and_six_corpus()
        self.assertEqual(len(corpus), 12)
        for name, verdict in corpus.items():
            self.assertEqual(verdict.accepted, name.startswith("accept_"), msg=name)

        audit = ws.audit_live_repo(Path(__file__).resolve().parents[3])
        self.assertEqual(audit["corpus_total"], 126)
        self.assertEqual(audit["corpus_accept_total"], 38)
        self.assertTrue(audit["corpus_15_preserved_under_r1"])
        self.assertEqual(audit["r3_strict_bare_recv_reject_count"], 15)
        self.assertTrue(audit["guarantees_row16_no_claim"])

    def test_cli_check_and_json_modes(self) -> None:
        buf_check, buf_json = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_check):
            self.assertEqual(ws.main(["--check"]), 0)
        self.assertIn("6/6 accept, 6/6 reject, 0B boxing, 15/38 R3 bare-recv impact", buf_check.getvalue())

        with redirect_stdout(buf_json):
            self.assertEqual(ws.main(["--json"]), 0)
        payload = json.loads(buf_json.getvalue())
        self.assertTrue(payload["six_and_six_ok"])
        self.assertEqual(payload["witness_plan"]["heap_alloc_bytes_per_call"], 0)


if __name__ == "__main__":
    unittest.main()
