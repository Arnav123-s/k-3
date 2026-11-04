import copy
import random
import unittest

from kuttaka_runtime.recurrence import (
    FORMAT, MAX_ORDER, MAX_PREDICTION_STEPS, MAX_SAMPLES, fit_recurrence, predict_recurrence,
    verify_recurrence,
)


class RationalRecurrenceTests(unittest.TestCase):
    def test_square_sequence_has_unique_minimal_order_three_fit(self):
        values = [n * n for n in range(6)]
        result = fit_recurrence(values, max_order=5)
        self.assertEqual(result["format"], FORMAT)
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["order"], 3)
        self.assertEqual(result["coefficients"], ["1", "-3", "3"])
        self.assertEqual([w["order"] for w in result["lower_order_witnesses"]], [1, 2])
        self.assertEqual([w["dot_b"] for w in result["lower_order_witnesses"]], ["1", "8"])
        self.assertEqual(len(result["full_rank_witness"]["minor_rows"]), 3)
        self.assertNotEqual(result["full_rank_witness"]["determinant"], "0")
        self.assertTrue(verify_recurrence(values, result))

    def test_rational_oscillator_has_unique_order_two_recurrence(self):
        values = [0, 1, 1, 0, -1, -1, 0, 1]
        result = fit_recurrence(values, max_order=4)
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["order"], 2)
        self.assertEqual(result["coefficients"], ["-1", "1"])
        self.assertTrue(verify_recurrence(values, result))

    def test_exact_rational_values_are_supported(self):
        values = ["1/2", "1", "2", "4"]
        result = fit_recurrence(values, max_order=2)
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["order"], 1)
        self.assertEqual(result["coefficients"], ["2"])
        self.assertTrue(verify_recurrence(values, result))

    def test_dependent_prefix_returns_particular_and_nullvector(self):
        nonzero = fit_recurrence([0, 1, 0], max_order=2)
        self.assertEqual(nonzero["status"], "underdetermined")
        self.assertEqual(nonzero["order"], 2)
        self.assertEqual(nonzero["lower_order_witnesses"][0]["order"], 1)
        self.assertTrue(verify_recurrence([0, 1, 0], nonzero))

        values = [0, 0, 0, 0]
        result = fit_recurrence(values)
        self.assertEqual(result["status"], "underdetermined")
        self.assertEqual(result["order"], 1)  # positive-order convention includes zero sequence
        self.assertIsNone(result["coefficients"])
        self.assertEqual(result["particular"], ["0"])
        self.assertEqual(result["nullvector"], ["1"])
        self.assertTrue(verify_recurrence(values, result))
        with self.assertRaises(ValueError):
            predict_recurrence(result, values)

    def test_one_sample_is_explicitly_insufficient_not_order_zero(self):
        result = fit_recurrence([7])
        self.assertEqual(result["status"], "underdetermined")
        self.assertEqual(result["order"], 1)
        self.assertEqual(result["search_limit"], 0)
        self.assertTrue(verify_recurrence([7], result))

    def test_no_fit_has_independent_left_null_rejections(self):
        values = [1, 0, 0, 1]
        result = fit_recurrence(values, max_order=2)
        self.assertEqual(result["status"], "no_fit")
        self.assertIsNone(result["order"])
        self.assertEqual([w["order"] for w in result["lower_order_witnesses"]], [1, 2])
        self.assertTrue(all(w["dot_b"] != "0" for w in result["lower_order_witnesses"]))
        self.assertTrue(verify_recurrence(values, result))
        altered = copy.deepcopy(result)
        altered["lower_order_witnesses"][0]["dot_b"] = "0"
        self.assertFalse(verify_recurrence(values, altered))

        corrupted_square = [0, 1, 4, 9, 17, 25]
        rejected = fit_recurrence(corrupted_square, max_order=2)
        self.assertEqual(rejected["status"], "no_fit")
        self.assertTrue(verify_recurrence(corrupted_square, rejected))

    def test_corrupted_training_and_tampered_rank_witness_are_rejected(self):
        values = [n * n for n in range(6)]
        result = fit_recurrence(values, max_order=3)
        corrupted = list(values)
        corrupted[-1] += 1
        self.assertFalse(verify_recurrence(corrupted, result))

        altered = copy.deepcopy(result)
        altered["full_rank_witness"]["determinant"] = "0"
        self.assertFalse(verify_recurrence(values, altered))
        altered = copy.deepcopy(result)
        altered["lower_order_witnesses"][1]["lambda"][0] = "0"
        self.assertFalse(verify_recurrence(values, altered))

    def test_prediction_is_conditional_and_accepts_new_consistent_initial_prefix(self):
        result = fit_recurrence([n * n for n in range(6)], max_order=3)
        forecast = predict_recurrence(result, ["1", "3", "6"], steps=2)
        self.assertEqual(forecast["values"], ["10", "15"])
        self.assertIn("conditional", forecast["interpretation"])
        with self.assertRaisesRegex(ValueError, "contradicts"):
            predict_recurrence(result, [0, 1, 4, 9, 17])
        with self.assertRaises(ValueError):
            predict_recurrence(result, [0, 1, 4], steps=MAX_PREDICTION_STEPS + 1)

    def test_float_bool_and_noncanonical_rational_inputs_are_rejected(self):
        for values in ([1.0, 2], [True, 2], ["2/4", "1"], ["1/0", "2"], [],
                       [0] * (MAX_SAMPLES + 1), [1 << 513, 1]):
            with self.subTest(values=str(values[:3])):
                with self.assertRaises(ValueError):
                    fit_recurrence(values)
        for max_order in (True, 0, MAX_ORDER + 1, 1.0):
            with self.subTest(max_order=max_order), self.assertRaises(ValueError):
                fit_recurrence([1, 2, 3], max_order=max_order)

    def test_predict_refuses_tampered_fit_certificate(self):
        result = fit_recurrence([0, 1, 1, 0, -1], max_order=2)
        result["coefficients"][0] = "0"
        with self.assertRaisesRegex(ValueError, "certificate"):
            predict_recurrence(result, [0, 1])

    def test_large_exact_certificate_round_trips_under_its_own_limits(self):
        rng = random.Random(12)
        values = [rng.getrandbits(510) for _ in range(16)]
        fit = fit_recurrence(values, max_order=8)
        self.assertEqual(fit['status'], 'unique')
        self.assertTrue(any(len(w['dot_b']) > 1024 for w in fit['lower_order_witnesses']))
        self.assertTrue(verify_recurrence(values, fit))

    def test_certificate_metadata_does_not_upgrade_its_claim(self):
        fit = fit_recurrence([7])
        fit['order'] = True
        self.assertFalse(verify_recurrence([7], fit))
        fit = fit_recurrence([1, 2, 4])
        fit['interpretation'] = 'proven infinite law'
        self.assertFalse(verify_recurrence([1, 2, 4], fit))


if __name__ == "__main__":
    unittest.main()
