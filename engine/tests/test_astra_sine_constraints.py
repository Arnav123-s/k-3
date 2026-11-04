"""Exact numerical controls for modern sine-generator hypotheses."""

import importlib.util
from fractions import Fraction
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "docs/research/predictive-state/sine_constraints.py"
SPEC = importlib.util.spec_from_file_location("sine_constraints", MODULE_PATH)
sine_constraints = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sine_constraints)


class SineConstraintTests(unittest.TestCase):
    def test_run_checks_derives_certificates_from_reconstruct_py(self):
        result = sine_constraints.run_checks()
        self.assertEqual(result["source"]["differences"], list(sine_constraints._source_table()))
        self.assertEqual(result["source"]["cumulative_values"][5], 1315)
        self.assertEqual(result["source"]["cumulative_values"][17], 3177)
        nearest = result["constant_coefficient_recurrences"]["nearest_half_up"]
        self.assertFalse(nearest["coefficient_feasible"])
        self.assertEqual(nearest["lower_witness"]["transition"], 8)
        self.assertEqual(nearest["upper_witness"]["transition"], 6)
        self.assertEqual(nearest["intersection"]["lower"], {"numerator": 5, "denominator": 1146})
        self.assertEqual(nearest["intersection"]["upper"], {"numerator": 11, "denominator": 2630})
        self.assertFalse(result["constant_coefficient_plus_phase"]["coefficient_feasible"])
        self.assertEqual(result["constant_coefficient_plus_phase"]["lower_pair"]["transition_indices"], [8, 7])
        self.assertEqual(result["constant_coefficient_plus_phase"]["upper_pair"]["transition_indices"], [8, 12])
        self.assertTrue(result["single_radius_sampled_sine"]["full_model_rejected"])
        self.assertGreater(result["single_radius_sampled_sine"]["exact_comparison"]["squared_difference"], 0)
        self.assertEqual(result["single_radius_sampled_sine"]["exact_comparison"]["lower_bound_relation"],
                         "greater")

    def test_floor_and_ceil_models_are_checked_with_their_own_boundaries(self):
        result = sine_constraints.run_checks()["constant_coefficient_recurrences"]
        self.assertFalse(result["floor"]["coefficient_feasible"])
        self.assertEqual(result["floor"]["lower_witness"]["transition"], 8)
        self.assertEqual(result["floor"]["upper_witness"]["transition"], 21)
        self.assertTrue(result["floor"]["intersection"]["lower_inclusive"])
        self.assertFalse(result["floor"]["intersection"]["upper_inclusive"])
        self.assertFalse(result["ceil"]["coefficient_feasible"])
        self.assertEqual(result["ceil"]["lower_witness"]["transition"], 22)
        self.assertEqual(result["ceil"]["upper_witness"]["transition"], 6)
        self.assertFalse(result["ceil"]["intersection"]["lower_inclusive"])
        self.assertTrue(result["ceil"]["intersection"]["upper_inclusive"])

    def test_generated_positive_controls_are_accepted(self):
        controls = sine_constraints.run_checks()["positive_controls"]
        for rounding in ("nearest_half_up", "floor", "ceil"):
            with self.subTest(rounding=rounding):
                self.assertTrue(controls[rounding]["coefficient_check"])
        self.assertTrue(controls["affine_nearest"]["check"])
        self.assertTrue(controls["affine_unbounded"]["check"]["coefficient_feasible"])
        self.assertIsNone(controls["affine_unbounded"]["check"]["coefficient_interval"]["upper"])
        self.assertTrue(controls["affine_strict_zero_lower"]["check"]["coefficient_feasible"])
        self.assertTrue(controls["affine_strict_zero_lower"]["check"]["coefficient_interval"]["lower_strict"])
        self.assertFalse(controls["affine_inconsistent"]["check"]["coefficient_feasible"])
        self.assertEqual(controls["affine_inconsistent"]["check"]["coefficient_interval"]["upper"],
                         {"numerator": 0, "denominator": 1})
        self.assertTrue(controls["rational_radius"]["check"])
        # Ties at the lower boundary are included by round-half-up.
        tie_table = sine_constraints._generate(2, 1, Fraction(1, 2), "nearest_half_up")
        self.assertEqual(tie_table, [1, 0])
        self.assertTrue(sine_constraints.coefficient_feasibility(
            tie_table, "nearest_half_up")["coefficient_feasible"])
        # Equality is impossible when the lower boundary is included and the
        # nearest-rounding upper boundary is excluded.
        self.assertFalse(sine_constraints._intervals_intersect(
            Fraction(3), Fraction(3), True, False))

    def test_affine_checker_rejects_noninteger_or_negative_data(self):
        for bad in ([1, -1], [True, 0], [0, 1]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sine_constraints.affine_nearest_feasibility(bad)

    def test_certificate_uses_input_values_not_a_fixed_claim(self):
        source = list(sine_constraints._source_table())
        original = sine_constraints.single_radius_radical_certificate(source)
        changed = list(source)
        changed[17] += 2
        revised = sine_constraints.single_radius_radical_certificate(changed)
        self.assertEqual(original["source_values"]["J_18"], sum(source[:18]))
        self.assertEqual(revised["source_values"]["J_18"], sum(changed[:18]))
        self.assertNotEqual(original["exact_comparison"]["squared_difference"],
                            revised["exact_comparison"]["squared_difference"])


if __name__ == "__main__":
    unittest.main()
