"""Independent controls for the authored polynomial-method constructor."""

import copy
import unittest

from experiments.astra_method_construction.compiler import construct, solve, validate


def horner(coefficients, x):
    """Independent polynomial evaluator used by certificate checks."""
    value = 0
    for coefficient in reversed(coefficients):
        value = value * x + coefficient
    return value


def binary_search_floor_root(coefficients, target):
    """Independent reference for the largest nonnegative x with P(x) <= target."""
    if target < coefficients[0]:
        return None
    low, high = 0, 1
    while horner(coefficients, high) <= target:
        low, high = high, high * 2
    while high - low > 1:
        middle = (low + high) // 2
        if horner(coefficients, middle) <= target:
            low = middle
        else:
            high = middle
    return low


class MethodConstructionTests(unittest.TestCase):
    def assert_certificate(self, coefficients, target, result):
        self.assertEqual(result['status'], 'solved')
        root = result['root']
        value = horner(coefficients, root)
        next_value = horner(coefficients, root + 1)
        self.assertEqual(root, binary_search_floor_root(coefficients, target))
        self.assertEqual(result['remainder'], target - value)
        self.assertEqual(result['next_gap'], next_value - target)
        self.assertGreaterEqual(result['remainder'], 0)
        self.assertGreater(result['next_gap'], 0)
        self.assertEqual(value + result['remainder'], target)
        self.assertLess(target, next_value)

    def test_constructed_methods_match_independent_binary_search(self):
        cases = [
            ([0, 1], [0, 1, 2, 9, 10, 127]),
            ([3, 2], [2, 3, 4, 17, 99]),
            ([0, 0, 1], [0, 1, 2, 8, 81, 1000]),
            ([5, 1, 3], [4, 5, 6, 17, 300, 10_000]),
            ([1, 0, 2, 0, 1], [0, 1, 2, 100, 12_345]),
            ([7, 3, 2, 1, 4, 1, 2, 1, 1], [7, 8, 100, 100_000]),
        ]
        for coefficients, targets in cases:
            with self.subTest(coefficients=coefficients):
                record = construct(coefficients)
                self.assertTrue(validate(record))
                for target in targets:
                    if target < coefficients[0]:
                        self.assertEqual(solve(record, target)['status'], 'no_solution')
                    else:
                        self.assert_certificate(coefficients, target, solve(record, target))

    def test_radix_changes_construction_steps_not_the_certified_answer(self):
        coefficients = [2, 1, 0, 1]
        target = 987_654_321
        results = [solve(construct(coefficients, radix), target) for radix in (2, 3, 10, 16)]
        for result in results:
            self.assert_certificate(coefficients, target, result)
        self.assertEqual(len({result['root'] for result in results}), 1)
        self.assertEqual(len({result['remainder'] for result in results}), 1)
        self.assertEqual(len({result['next_gap'] for result in results}), 1)

    def test_large_coefficients_and_1024_bit_target(self):
        coefficients = [1 << 255, (1 << 200) + 17, 3]
        record = construct(coefficients, radix=2)
        target = (1 << 1023) + (1 << 300) + 123
        self.assertLessEqual(target.bit_length(), 1024)
        result = solve(record, target)
        self.assert_certificate(coefficients, target, result)

    def test_nonmonotone_or_out_of_contract_inputs_are_rejected(self):
        for coefficients in ([], [1], [1, 0], [0, -1], [0, True], [0, 1.0],
                             [0] * 9 + [1], [0, 1 << 256]):
            with self.subTest(coefficients=coefficients):
                with self.assertRaises(ValueError):
                    construct(coefficients)
        for radix in (True, 1, 17, 2.0):
            with self.subTest(radix=radix):
                with self.assertRaises(ValueError):
                    construct([0, 1], radix)
        valid = construct([1, 1])
        for target in (-1, True, 1 << 1024):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    solve(valid, target)

    def test_zero_step_budget_returns_a_valid_partial_invariant(self):
        coefficients = [4, 2, 1]
        target = 1_000_000
        result = solve(construct(coefficients), target, max_steps=0)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['steps'], 0)
        self.assertEqual(result['trace'], [])
        self.assertEqual(horner(coefficients, result['partial_root'])
                         + result['partial_remainder'], target)

    def test_mid_search_budget_exhaustion_preserves_partial_invariant(self):
        coefficients = [4, 2, 1]
        target = 1_234
        result = solve(construct(coefficients), target, max_steps=5)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['steps'], 5)
        self.assertTrue(any(row['kind'] == 'digit' for row in result['trace']))
        self.assertEqual(horner(coefficients, result['partial_root'])
                         + result['partial_remainder'], target)
        self.assertGreaterEqual(result['partial_remainder'], 0)

    def test_altered_delta_term_is_rejected_by_validation_and_solve(self):
        record = construct([3, 2, 1])
        altered = copy.deepcopy(record)
        altered['increment'][0]['coefficient'] += 1
        with self.assertRaises(ValueError):
            validate(altered)
        with self.assertRaises(ValueError):
            solve(altered, 100)

    def test_record_schema_and_duplicate_terms_are_rejected(self):
        record = construct([0, 1, 1])
        extra = copy.deepcopy(record)
        extra['cached_root'] = 5
        with self.assertRaises(ValueError):
            validate(extra)
        duplicate = copy.deepcopy(record)
        duplicate['increment'].append(copy.deepcopy(duplicate['increment'][0]))
        with self.assertRaises(ValueError):
            validate(duplicate)


if __name__ == '__main__':
    unittest.main()
