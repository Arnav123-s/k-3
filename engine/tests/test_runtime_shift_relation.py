import copy
from fractions import Fraction
import unittest
from unittest.mock import patch

from kuttaka_runtime.shift_relation import (
    fit_shift_relation,
    predict_shift_relation,
    verify_shift_relation,
)


def qstr(value):
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def mixed_sequence(count=12):
    values = [1, 1]
    for n in range(count - 2):
        values.append((n + 1) * values[-1] + values[-2])
    return values


class PolynomialShiftRelationTests(unittest.TestCase):
    def test_constant_coefficient_recurrence_slice(self):
        values = [n * n for n in range(8)]
        result = fit_shift_relation(values, order=3, degree=0)
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["coefficients"], [["1"], ["-3"], ["3"], ["-1"]])
        self.assertTrue(result["predictive"])
        self.assertTrue(verify_shift_relation(values, result))
        forecast = predict_shift_relation(result, values, steps=2)
        self.assertEqual(forecast["values"], ["64", "81"])

    def test_rational_ratio_slice(self):
        values = [1, 2, 4, 8, 16, 32, 64]
        result = fit_shift_relation(values, order=1, degree=0)
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["coefficients"], [["1"], ["-1/2"]])
        self.assertEqual(predict_shift_relation(result, values, steps=2)["values"], ["128", "256"])

    def test_mixed_order_and_degree_unique_relation(self):
        values = mixed_sequence(8)
        result = fit_shift_relation(values, order=2, degree=1)
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["coefficients"], [["1", "0"], ["1", "1"], ["-1", "0"]])
        self.assertEqual(result["rank"], 5)
        self.assertTrue(verify_shift_relation(values, result))
        self.assertEqual(predict_shift_relation(result, values, steps=2)["values"],
                         [str(x) for x in mixed_sequence(10)[8:]])

    def test_absolute_index_origin_is_used(self):
        whole = mixed_sequence(14)
        origin, values = 5, whole[5:13]
        result = fit_shift_relation(values, order=2, degree=1, start_index=origin)
        self.assertEqual(result["status"], "unique")
        self.assertTrue(verify_shift_relation(values, result))
        self.assertEqual(predict_shift_relation(result, values, start_index=origin, steps=1)["values"],
                         [str(whole[13])])
        wrong_origin = fit_shift_relation(values, order=2, degree=1, start_index=0)
        self.assertNotEqual(wrong_origin["coefficients"], result["coefficients"])

    def test_undersampled_and_all_zero_are_fully_underdetermined(self):
        for values in ([1, 2], [0] * 8):
            result = fit_shift_relation(values, order=2, degree=1)
            self.assertEqual(result["status"], "underdetermined")
            self.assertEqual(len(result["nullspace_basis"]), 6)
            self.assertEqual(result["rank"], 0 if values[0] == 0 else 0)
            self.assertTrue(verify_shift_relation(values, result))
            with self.assertRaises(ValueError):
                predict_shift_relation(result, values, steps=1)

    def test_no_fit_full_rank_certificate(self):
        values = [1, 2, 4, 8, 17, 35]
        result = fit_shift_relation(values, order=2, degree=0)
        self.assertEqual(result["status"], "no_fit")
        self.assertEqual(result["nullspace_basis"], [])
        self.assertTrue(verify_shift_relation(values, result))

    def test_past_singularity_requires_observed_seed_but_not_permanent_rejection(self):
        # At n>=1, x[n+1]=(n+1)/n*x[n], hence x[n]=n for n>=1.
        # The equation at n=0 remains valid but P_1(0)=0, so it does not update.
        values = [0, 1, 2, 3, 4, 5, 6]
        result = fit_shift_relation(values, order=1, degree=1)
        self.assertEqual(result["status"], "unique")
        self.assertTrue(result["predictive"])
        self.assertEqual(result['observed_singular_indices'], [0])
        self.assertTrue(verify_shift_relation(values, result))
        self.assertEqual(predict_shift_relation(result, values, steps=2)['values'], ['7', '8'])
        with self.assertRaises(ValueError):
            predict_shift_relation(result, [0], steps=1)
        with self.assertRaisesRegex(ValueError, 'index 0'):
            predict_shift_relation(result, [0], steps=1, continuation=False)
        new = predict_shift_relation(result, [0, 2, 4], steps=1, continuation=False)
        self.assertEqual(new['values'], ['6'])
        self.assertEqual(new['execution_mode'], 'new_indexed_seeds')

    def test_identified_but_zero_highest_shift_polynomial(self):
        # This finite sparse prefix admits a relation that cannot update the
        # highest shift. A unique relation need not be an executable predictor.
        values = [0, 0, -1, 3, 0, 0, -4]
        result = fit_shift_relation(values, order=2, degree=1)
        self.assertEqual(result["status"], "unique")
        self.assertFalse(result["predictive"])
        self.assertIn("identically zero", result["predictive_reason"])
        self.assertTrue(verify_shift_relation(values, result))

    def test_tampered_basis_or_rank_certificate_is_rejected(self):
        values = mixed_sequence(8)
        result = fit_shift_relation(values, order=2, degree=1)
        changed = copy.deepcopy(result)
        changed["nullspace_basis"][0][0] = "0"
        self.assertFalse(verify_shift_relation(values, changed))
        changed = copy.deepcopy(result)
        changed["rank_minor"]["determinant"] = "0"
        self.assertFalse(verify_shift_relation(values, changed))

    def test_unbounded_certificate_basis_is_rejected_before_parsing(self):
        values = mixed_sequence(8)
        fit = fit_shift_relation(values, order=2, degree=1)
        fit['nullspace_basis'] *= 100
        with patch('kuttaka_runtime.shift_relation._parse_result_fraction', side_effect=AssertionError):
            self.assertFalse(verify_shift_relation(values, fit))

    def test_prefix_must_satisfy_relation_and_future_pole_is_checked(self):
        fit = fit_shift_relation([1, 2, 4, 8, 16], order=1, degree=0)
        with self.assertRaises(ValueError):
            predict_shift_relation(fit, [1, 3], start_index=0, steps=1)
        # The fitted leading polynomial is n-7. It is nonzero throughout the
        # observed training transitions, then blocks a requested future step.
        seq = [Fraction(1)]
        for n in range(7):
            seq.append(-seq[-1] / Fraction(n - 7))
        fitted = fit_shift_relation([qstr(v) for v in seq[:7]], order=1, degree=1)
        self.assertEqual(fitted["status"], "unique")
        self.assertTrue(fitted["predictive"])
        with self.assertRaisesRegex(ValueError, "index 7"):
            predict_shift_relation(fitted, [qstr(v) for v in seq], start_index=0, steps=1)

    def test_invalid_inputs_bounds_and_work_budget(self):
        for values in ([1.0, 2], [True, 2], [], ["2/4", 1]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                fit_shift_relation(values, order=1, degree=0)
        for order, degree, origin in ((0, 0, 0), (5, 0, 0), (1, 5, 0), (1, 0, True), (1, 0, 1_000_001)):
            with self.subTest(order=order, degree=degree, origin=origin), self.assertRaises(ValueError):
                fit_shift_relation([1, 2, 4], order=order, degree=degree, start_index=origin)
        with patch("kuttaka_runtime.shift_relation.MAX_STEPS", 0):
            with self.assertRaisesRegex(ValueError, "budget"):
                fit_shift_relation([1, 2, 4, 8], order=1, degree=0)

    def test_large_exact_rational_round_trip(self):
        seed = 1 << 400
        values = [seed, 2 * seed, 4 * seed, 8 * seed, 16 * seed]
        strings = [qstr(Fraction(v)) for v in values]
        result = fit_shift_relation(strings, order=1, degree=0)
        self.assertTrue(verify_shift_relation(strings, result))
        self.assertEqual(predict_shift_relation(result, strings, steps=1)["values"], [str(32 * seed)])


if __name__ == "__main__":
    unittest.main()
