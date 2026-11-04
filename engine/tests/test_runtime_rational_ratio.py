import copy
import math
import unittest
from fractions import Fraction

from kuttaka_runtime.rational_ratio import fit_ratio, predict_ratio, verify_ratio


def ramanujan_kernel(k):
    """Factorial kernel in Ramanujan's 1914 summand, without its linear weight."""
    return Fraction(math.factorial(4 * k), math.factorial(k) ** 4 * 396 ** (4 * k))


def ramanujan_term(k):
    return ramanujan_kernel(k) * (1103 + 26390 * k)


def qstr(value):
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


class RationalRatioTests(unittest.TestCase):
    def test_geometric_unique_degree_zero_and_conditional_prediction(self):
        train = [3 * (2 ** n) for n in range(7)]
        result = fit_ratio(train)
        self.assertEqual((result["status"], result["degree"]), ("unique", 0))
        self.assertEqual(result["coefficients"], ["2"])
        self.assertTrue(verify_ratio(train, result))
        self.assertEqual(predict_ratio(result, 6, train[-1], 2)["values"], ["384", "768"])
        with self.assertRaises(ValueError):
            predict_ratio(result, 6, 1, 1)  # seed contradicts a retained training value

    def test_factorial_kernel_is_degree_three_with_three_withheld_terms(self):
        train = [ramanujan_kernel(k) for k in range(12)]
        heldout = [ramanujan_kernel(k) for k in range(12, 15)]
        result = fit_ratio([qstr(x) for x in train], max_degree=4)
        self.assertEqual((result["status"], result["degree"]), ("unique", 3))
        self.assertTrue(verify_ratio([qstr(x) for x in train], result))
        forecast = predict_ratio(result, 11, qstr(train[-1]), 3)
        self.assertEqual(forecast["values"], [qstr(x) for x in heldout])
        self.assertIn("conditional", forecast["interpretation"])

    def test_full_ramanujan_summand_degree_four_and_three_withheld_terms(self):
        train = [ramanujan_term(k) for k in range(12)]
        heldout = [ramanujan_term(k) for k in range(12, 15)]
        strings = [qstr(x) for x in train]
        result = fit_ratio(strings, max_degree=4)
        self.assertEqual((result["status"], result["degree"]), ("unique", 4))
        self.assertTrue(verify_ratio(strings, result))
        forecast = predict_ratio(result, 11, qstr(train[-1]), 3)
        self.assertEqual(forecast["values"], [qstr(x) for x in heldout])
        self.assertEqual(len(heldout), 3)

    def test_underdetermined_returns_particular_and_nullvector(self):
        result = fit_ratio([1, 2, 7], max_degree=2)
        self.assertEqual(result["status"], "underdetermined")
        self.assertIsNotNone(result["particular"])
        self.assertIsNotNone(result["nullvector"])
        self.assertTrue(verify_ratio([1, 2, 7], result))

    def test_inconsistency_witness_and_tampering(self):
        values = [1, 1, 3, 7, 15, 31, 63, 128]
        result = fit_ratio(values, max_degree=1)
        self.assertEqual(result["status"], "no_fit")
        self.assertTrue(verify_ratio(values, result))
        changed = copy.deepcopy(result)
        changed["degree_rejections"][0]["dot_b"] = "0"
        self.assertFalse(verify_ratio(values, changed))

    def test_corrupted_training_does_not_validate_or_predict(self):
        values = [1, 2, 4, 8, 16, 32, 64]
        result = fit_ratio(values)
        self.assertEqual(result["status"], "unique")
        self.assertFalse(verify_ratio(values[:-1] + [65], result))
        with self.assertRaises(ValueError):
            predict_ratio(result, 6, 65, 1)

    def test_zero_denominator_candidate_is_rejected(self):
        # Q=(1-n/2)(1+n), P=(1-n/2)(1+2n); at n=2 both sides
        # vanish, so the observed transition is not an admissible ratio step.
        values = [1, 1, Fraction(3, 2), 5, Fraction(35, 4), Fraction(63, 4), Fraction(231, 8)]
        strings = [qstr(x) for x in values]
        result = fit_ratio(strings, max_degree=2)
        self.assertEqual(result["status"], "no_fit")
        self.assertTrue(any(r["kind"] == "denominator_zero" for r in result["degree_rejections"]))
        self.assertTrue(verify_ratio(strings, result))

    def test_limits_and_malformed_values(self):
        for bad in ([1.0, 2], [True, 2], [], ["2/4", 1], ["1/0", 1], ["+1", 2]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fit_ratio(bad)
        with self.assertRaises(ValueError):
            fit_ratio([1], max_degree=5)
        with self.assertRaises(ValueError):
            fit_ratio([1 << 513, 1])
        with self.assertRaises(ValueError):
            predict_ratio(fit_ratio([1, 2, 4]), 0, 1, 513)


if __name__ == "__main__":
    unittest.main()
