from __future__ import annotations
import sys
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comptime_evaluator import (  # noqa: E402
    F32_MAX,
    F64_MAX,
    MAX_SHIFT_BITS,
    ComptimeEnv,
    ComptimeError,
    ScalarType,
    TypedVal,
    UntypedBool,
    UntypedComplex,
    UntypedFloat,
    UntypedInt,
    UntypedString,
    _run_self_check,
    ieee754_round,
    parse_int_literal,
)


class ComptimeEvaluatorTest(unittest.TestCase):
    def assert_err(self, env: ComptimeEnv, expr: str, code: str, ty: str | None = None) -> ComptimeError:
        with self.assertRaises(ComptimeError) as ctx:
            env.eval_expr(expr, ty=ty)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_arbitrary_precision_shifts_and_e5004(self) -> None:
        env = ComptimeEnv()
        v = env.eval_expr("(1 << 200) >> 195")
        self.assertEqual(v, UntypedInt(32))
        self.assertEqual(v.to_go_literal(), "32")
        self.assertEqual(env.eval_expr("1 << 500 >> 495"), UntypedInt(32))
        self.assertEqual(env.eval_expr("((1 << 500) + (1 << 250)) >> 250"), UntypedInt((1 << 250) + 1))
        self.assertEqual(env.eval_expr("1.0 << 10.0"), UntypedInt(1024))
        self.assertEqual(MAX_SHIFT_BITS, 4096)
        self.assertEqual(parse_int_literal("0x1_00"), 256)
        self.assertEqual(parse_int_literal("0o7_7"), 63)
        self.assertEqual(parse_int_literal("0b1010_0101"), 165)
        self.assert_err(env, "1 << 10_000_000", "HYB-E5001")
        self.assert_err(env, "(1 << 3000) << 2000", "HYB-E5001")
        self.assert_err(env, "1 << -1", "HYB-E5004")
        self.assert_err(env, "16 >> -2", "HYB-E5004")
        self.assert_err(env, "1 << 2.5", "HYB-E5004")
        self.assert_err(env, "2.5 << 1", "HYB-E5004")

    def test_exact_rational_float_folding_and_e5002(self) -> None:
        env = ComptimeEnv()
        self.assertEqual(env.eval_expr("1e30 + 1 - 1e30"), UntypedFloat(Fraction(1)))
        self.assertEqual(env.eval_expr("100.0 / (1e30 + 1.0 - 1e30)"), UntypedFloat(Fraction(100)))
        self.assertEqual(env.eval_expr("0.1 + 0.2 == 0.3"), UntypedBool(True))
        self.assertEqual(env.eval_expr("0x1.8p+1 - 0x1.0p-2"), UntypedFloat(Fraction(11, 4)))
        self.assertEqual(env.eval_expr("-7 / 2"), UntypedInt(-3))
        self.assertEqual(env.eval_expr("-7 % 2"), UntypedInt(-1))
        self.assertEqual(env.eval_expr("real(complex(3, 4) * (1 - 2i))"), UntypedFloat(Fraction(11)))
        self.assertEqual(env.eval_expr("imag(complex(3, 4) / 2i)"), UntypedFloat(Fraction(-3, 2)))
        self.assert_err(env, "100.0 / (1e30 - 1e30)", "HYB-E5002")
        self.assert_err(env, "42 / 0", "HYB-E5002")
        self.assert_err(env, "42 % 0", "HYB-E5002")
        self.assert_err(env, "complex(1, 2) / 0i", "HYB-E5002")

    def test_ieee754_f32_f64_round_ties_to_even_and_bounds(self) -> None:
        env = ComptimeEnv()
        self.assertEqual(env.eval_expr("f32((1 << 128) - (1 << 104))"), TypedVal(ScalarType.F32, F32_MAX))
        self.assertEqual(env.eval_expr("f32(-((1 << 128) - (1 << 104)))"), TypedVal(ScalarType.F32, -F32_MAX))
        self.assertEqual(env.eval_expr("f32(0x1.fffffep+127)"), TypedVal(ScalarType.F32, F32_MAX))
        self.assertEqual(env.eval_expr("f32(-0x1.fffffep+127)"), TypedVal(ScalarType.F32, -F32_MAX))
        self.assertEqual(env.eval_expr("f64((1 << 1024) - (1 << 971))"), TypedVal(ScalarType.F64, F64_MAX))
        self.assert_err(env, "f32((1 << 128) - (1 << 104) + 1)", "HYB-E5001")
        self.assert_err(env, "f32(-((1 << 128) - (1 << 104) + (1 << 104)))", "HYB-E5001")
        self.assert_err(env, "f32(1 << 128)", "HYB-E5001")
        self.assert_err(env, "f64((1 << 1024) - (1 << 971) + 1)", "HYB-E5001")
        self.assert_err(env, "1e10000000", "HYB-E5001")
        self.assert_err(env, "0x1p10000000", "HYB-E5001")
        ulp23, half_ulp23 = Fraction(1, 1 << 23), Fraction(1, 1 << 24)
        self.assertEqual(ieee754_round(Fraction(1) + half_ulp23, 24, -149), Fraction(1))
        self.assertEqual(ieee754_round(Fraction(1) + ulp23 + half_ulp23, 24, -149), Fraction(1) + 2 * ulp23)
        self.assertEqual(ieee754_round(-Fraction(1) - half_ulp23, 24, -149), -Fraction(1))
        self.assertEqual(ieee754_round(-Fraction(1) - ulp23 - half_ulp23, 24, -149), -(Fraction(1) + 2 * ulp23))
        self.assertEqual(ieee754_round(-Fraction(1, 1 << 150), 24, -149), Fraction(0))
        self.assertEqual(ieee754_round(-Fraction(3, 1 << 150), 24, -149), -Fraction(2, 1 << 149))

    def test_explicit_conversions_truncation_overflow_and_hy_emit_wrapping(self) -> None:
        env = ComptimeEnv()
        self.assertEqual(env.eval_expr("int(3.0)"), TypedVal(ScalarType.INT, 3))
        self.assertEqual(env.eval_expr("int(complex(7, 0))"), TypedVal(ScalarType.INT, 7))
        self.assertEqual(env.eval_expr("u8((1 << 200) >> 195)"), TypedVal(ScalarType.U8, 32))
        self.assertEqual(env.eval_expr("~u8(0)"), TypedVal(ScalarType.U8, 255))
        self.assertEqual(env.eval_expr("string(65)"), TypedVal(ScalarType.STRING, "A"))
        self.assertEqual(env.eval_expr("string(0xD800)"), TypedVal(ScalarType.STRING, "\ufffd"))
        self.assertEqual(env.eval_expr('len("h\\u00e9llo" + "!")'), UntypedInt(7))
        self.assert_err(env, "~2.5", "HYB-E5003")
        self.assert_err(env, "int(2.5)", "HYB-E5003")
        self.assert_err(env, "u8(3.14)", "HYB-E5003")
        self.assert_err(env, "int(complex(3, 1))", "HYB-E5003")
        self.assert_err(env, "f64(2 + 3i)", "HYB-E5003")
        self.assert_err(env, "u8(300)", "HYB-E5001")
        self.assert_err(env, "i8(128)", "HYB-E5001")
        self.assert_err(env, "i64(1 << 63)", "HYB-E5001")
        self.assert_err(env, "string(1 << 512)", "HYB-E5001")
        emit_env = ComptimeEnv(wrap_sub64=True)
        u8_wrapped = emit_env.eval_expr("u8(300)")
        self.assertEqual(u8_wrapped, TypedVal(ScalarType.U8, 44))
        self.assertEqual(u8_wrapped.to_go_literal(), "uint8(44)")
        self.assertEqual(emit_env.eval_expr("i8(200)"), TypedVal(ScalarType.I8, -56))
        self.assert_err(emit_env, "i64(1 << 63)", "HYB-E5001")

    def test_comptime_env_resolution_cycles_nonconst_and_gas_limit(self) -> None:
        env = ComptimeEnv({"A": "B * 2", "B": "C + 3", "C": ("5", "i32"), "D": "A + B", "S": '"go" + "-hybrid"'})
        res = env.resolve_all()
        self.assertEqual(res.diagnostics, [])
        self.assertEqual(res.values["A"], TypedVal(ScalarType.I32, 16))
        self.assertEqual(res.values["D"], TypedVal(ScalarType.I32, 24))
        self.assertEqual(res.emitted_go["A"], "int32(16)")
        self.assertEqual(res.values["S"], UntypedString("go-hybrid"))
        cyc_env = ComptimeEnv({"X": "Y + 1", "Y": "Z + 1", "Z": "X + 1"})
        err = self.assert_err(cyc_env, "X", "HYB-E5005")
        self.assertIn("X -> Y -> Z -> X", err.message)
        self.assert_err(env, "foo(1)", "HYB-E6003")
        self.assert_err(env, "UNKNOWN_CONST + 1", "HYB-E6003")
        self.assert_err(env, "", "HYB-E6003")
        self.assert_err(env, "1 + * 2", "HYB-E6003")
        self.assert_err(ComptimeEnv(max_steps=4), "1 + 2 + 3 + 4 + 5", "HYB-E5005")
        self.assert_err(ComptimeEnv(max_steps=16), "1 << 2048", "HYB-E5005")
        self.assertEqual(_run_self_check()["verified_cases"], 11)


if __name__ == "__main__":
    unittest.main()
