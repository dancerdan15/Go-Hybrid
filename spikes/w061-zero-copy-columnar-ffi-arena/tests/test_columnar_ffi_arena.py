"""Unit and verification tests for Spike W-061 (#1292): Zero-Copy Columnar Tape FFI Arena."""
from __future__ import annotations
from pathlib import Path
import struct, sys, unittest

SPIKE_DIR = Path(__file__).resolve().parents[1]
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

from columnar_ffi_arena import (  # noqa: E402
    HYB_E6201, HYB_E6202, HYB_E6203, TAG_ARRAY_END, TAG_ARRAY_START, TAG_BOOL,
    TAG_INT64, TAG_NULL, TAG_OBJECT_END, TAG_OBJECT_START, TAG_STRING,
    ArenaSafetyError, ColumnarArena, ColumnarTape, TapeEntry,
    compare_handle_per_elem_vs_columnar_tape, run_self_check, verify_batch_view_program,
)


class ColumnarTapeTests(unittest.TestCase):
    def test_tape_entry_pack_unpack_binary_64bit_words(self) -> None:
        cases = [
            TapeEntry(TAG_NULL), TapeEntry(TAG_BOOL, payload=1),
            TapeEntry(TAG_INT64, payload=(-42) & ((1 << 56) - 1)),
            TapeEntry(TAG_STRING, offset=128, length=37),
            TapeEntry(TAG_ARRAY_START, count=5, jump_idx=19), TapeEntry(TAG_ARRAY_END, jump_idx=2),
            TapeEntry(TAG_OBJECT_START, count=3, jump_idx=14), TapeEntry(TAG_OBJECT_END, jump_idx=1),
        ]
        for entry in cases:
            raw = entry.pack_bytes()
            self.assertEqual(len(raw), 8)
            decoded = TapeEntry.unpack_u64(struct.unpack("<Q", raw)[0])
            self.assertEqual((decoded.tag, decoded.offset, decoded.length), (entry.tag, entry.offset, entry.length))
            self.assertEqual((decoded.count, decoded.jump_idx), (entry.count, entry.jump_idx))
        self.assertTrue(TapeEntry.unpack_u64(cases[1].pack_u64()).as_bool())
        self.assertEqual(TapeEntry.unpack_u64(cases[2].pack_u64()).as_int64(), -42)

    def test_columnar_tape_o1_skip_value_and_utf8_string_slab(self) -> None:
        doc = {
            "service": "hyrt-Ω",
            "records": [{"id": 10, "tags": ["a", "b", "c"]}, {"id": -7, "pi": 3.5, "ok": False}],
            "empty": None,
        }
        tape = ColumnarTape.from_document(doc)
        self.assertEqual((tape.ffi_crossings, tape.cstring_allocs), (1, 0))
        decoded, end_idx = tape.decode_value(0)
        self.assertEqual(end_idx, len(tape))
        self.assertEqual(decoded["service"], "hyrt-Ω")
        self.assertEqual(decoded["records"][0]["tags"], ["a", "b", "c"])
        self.assertEqual(decoded["records"][1]["id"], -7)
        self.assertAlmostEqual(decoded["records"][1]["pi"], 3.5, places=6)
        self.assertIsNone(decoded["empty"])
        self.assertEqual(tape.skip_value(0), len(tape))
        self.assertEqual((tape.entry_at(1).tag, tape.read_string(1), tape.read_string(2)), (TAG_STRING, "service", "hyrt-Ω"))
        self.assertEqual(tape.entry_at(4).tag, TAG_ARRAY_START)
        self.assertEqual(tape.read_string(tape.skip_value(4)), "empty")


class ArenaEpochAndVerifierTests(unittest.TestCase):
    def test_hyb_e6201_rejects_reset_or_free_while_pinned_and_allows_after_release(self) -> None:
        arena = ColumnarArena({"items": [1, 2, 3]})
        with arena.open_batch_view() as view:
            self.assertEqual(view.read(), {"items": [1, 2, 3]})
            for fn in (arena.reset, arena.free, lambda: arena.ingest([4, 5])):
                with self.assertRaises(ArenaSafetyError) as ctx:
                    fn()
                self.assertEqual(ctx.exception.code, HYB_E6201)
        arena.reset()
        self.assertEqual(arena.epoch, 2)
        arena.ingest({"items": [99]})
        with arena.open_batch_view() as view2:
            self.assertEqual(view2.read(), {"items": [99]})
        arena.free()
        self.assertTrue(arena.freed)
        bad_prog = verify_batch_view_program([{"op": "PinBatchView", "view": "v0"}, {"op": "ArenaReset"}])
        self.assertEqual((bad_prog.ok, [d.code for d in bad_prog.diagnostics]), (False, [HYB_E6201]))
        ok_prog = verify_batch_view_program([
            {"op": "PinBatchView", "view": "v0"}, {"op": "ReadView", "view": "v0"},
            {"op": "ReleaseBatchView", "view": "v0"}, {"op": "ArenaReset"},
        ])
        self.assertEqual((ok_prog.ok, ok_prog.diagnostics), (True, []))

    def test_hyb_e6202_rejects_lexical_scope_escape(self) -> None:
        arena = ColumnarArena([1, 2])
        with arena.open_batch_view() as view:
            with self.assertRaises(ArenaSafetyError) as ctx:
                view.escape_to_task()
            self.assertEqual(ctx.exception.code, HYB_E6202)
        for op_name in ("EscapeViewToTask", "StoreViewGlobal", "ReturnViewFromScope"):
            rep = verify_batch_view_program([{"op": "PinBatchView", "view": "v1"}, {"op": op_name, "view": "v1"}])
            self.assertEqual((rep.ok, rep.diagnostics[0].code), (False, HYB_E6202))

    def test_hyb_e6203_rejects_stale_epoch_read_and_mutation(self) -> None:
        arena = ColumnarArena({"k": "v"})
        view = arena.open_batch_view()
        with self.assertRaises(ArenaSafetyError) as mut_ctx:
            view.mutate("k", "v2")
        self.assertEqual(mut_ctx.exception.code, HYB_E6203)
        view.release()
        arena.reset()
        with self.assertRaises(ArenaSafetyError) as stale_ctx:
            view.read()
        self.assertEqual(stale_ctx.exception.code, HYB_E6203)
        rep = verify_batch_view_program([
            {"op": "PinBatchView", "view": "v0", "view_epoch": 1},
            {"op": "ReleaseBatchView", "view": "v0"},
            {"op": "ArenaReset"},
            {"op": "ReadView", "view": "v0"},
        ])
        self.assertEqual((rep.ok, [d.code for d in rep.diagnostics]), (False, [HYB_E6203]))

    def test_compare_handle_per_elem_vs_columnar_tape_and_self_check(self) -> None:
        for n in (10, 256, 1000):
            cmp_res = compare_handle_per_elem_vs_columnar_tape(n)
            self.assertEqual((cmp_res.legacy_ffi_calls, cmp_res.legacy_mutex_locks, cmp_res.legacy_handle_slots), (2 * n, n, n))
            self.assertEqual((cmp_res.columnar_ffi_calls, cmp_res.columnar_arena_buffers), (1, 1))
            self.assertEqual((cmp_res.columnar_mutex_locks, cmp_res.columnar_handle_slots, cmp_res.columnar_cstring_allocs), (0, 0, 0))
        check = run_self_check()
        self.assertEqual((check["status"], check["verified_codes"]), ("PASS", [HYB_E6201, HYB_E6202, HYB_E6203]))


if __name__ == "__main__":
    unittest.main()
