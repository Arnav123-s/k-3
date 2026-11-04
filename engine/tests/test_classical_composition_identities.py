"""Bounded exact arithmetic checks for the classical norm identities."""

import unittest
from math import isqrt

from research.kuttaka_method.composition_identities import (
    MAX_INTEGER_BITS,
    chakravala_step,
    compose,
    norm,
    pell_exit,
)


class ClassicalCompositionIdentityTests(unittest.TestCase):
    def test_norm_and_standard_composition_are_exact(self) -> None:
        N = 2
        left = (3, 2, 1)
        right = (3, 2, 1)
        result = compose(N, left, right)
        self.assertEqual(result, (17, 12, 1))
        self.assertEqual(result[0] ** 2 - N * result[1] ** 2, 1)

        conjugated = compose(N, left, right, conjugate=True)
        self.assertEqual(conjugated, (1, 0, 1))
        self.assertEqual(conjugated[0] ** 2 - N * conjugated[1] ** 2, 1)

    def test_claimed_coordinate_mismatch_has_counterexample(self) -> None:
        N = 2
        left = (3, 2, 1)
        right = (3, 2, 1)
        a, b, _ = left
        c, d, _ = right
        wrong_pair = (a * d + b * c, b * d + N * a * c)
        self.assertEqual(wrong_pair, (12, 22))
        self.assertEqual(wrong_pair[0] ** 2 - N * wrong_pair[1] ** 2, -824)
        correct = compose(N, left, right)
        self.assertEqual(correct[:2], (17, 12))
        self.assertEqual(correct[0] ** 2 - N * correct[1] ** 2, correct[2])

    def test_bounded_small_grid_composition_identity(self) -> None:
        # This finite sweep is corroboration, not a proof of the identity.
        coordinates = range(-2, 3)
        for N in range(-2, 7):
            triples = [(a, b, a * a - N * b * b) for a in coordinates for b in coordinates]
            for left in triples:
                for right in triples:
                    for conjugate in (False, True):
                        with self.subTest(N=N, left=left, right=right, conjugate=conjugate):
                            out_a, out_b, out_k = compose(N, left, right, conjugate=conjugate)
                            self.assertEqual(out_a * out_a - N * out_b * out_b, out_k)
                            self.assertEqual(out_k, left[2] * right[2])

    def test_pell_exit_formulas_cover_signs_and_parities(self) -> None:
        cases = [
            (2, (1, 0, 1), (1, 0)),       # already a trivial Pell solution
            (2, (1, 1, -1), (3, 2)),      # square a norm -1 solution
            (2, (2, 1, 2), (3, 2)),       # norm +2 formula
            (2, (0, 1, -2), (1, 0)),      # norm -2 can yield y = 0
            (5, (3, 1, 4), (9, 4)),       # odd-coordinate +4, cube
            (13, (3, 1, -4), (649, 180)),# odd-coordinate -4, cube then square
            (5, (18, 8, 4), (9, 4)),      # even-coordinate +4, divide by two
            (5, (4, 2, -4), (9, 4)),      # even-coordinate -4, divide then square
            (8, (2, 1, -4), (3, 1)),      # mixed parity, N divisible by four
            (12, (4, 1, 4), (7, 2)),      # mixed parity, positive norm
        ]
        for N, triple, expected in cases:
            with self.subTest(N=N, triple=triple):
                x, y = pell_exit(N, triple)
                self.assertEqual((x, y), expected)
                self.assertEqual(x * x - N * y * y, 1)

    def test_bounded_small_grid_pell_exits_substitute_exactly(self) -> None:
        for N in range(2, 16):
            if isqrt(N) ** 2 == N:
                continue
            for a in range(-6, 7):
                for b in range(-6, 7):
                    k = a * a - N * b * b
                    if k in (-4, -2, -1, 1, 2, 4):
                        with self.subTest(N=N, a=a, b=b, k=k):
                            x, y = pell_exit(N, (a, b, k))
                            self.assertEqual(x * x - N * y * y, 1)

    def test_classical_N61_step_is_exact_and_abs_k_increases(self) -> None:
        start = (8, 1, 3)
        end = chakravala_step(61, start, 7)
        self.assertEqual(end, (39, 5, -4))
        self.assertEqual(norm(61, end[0], end[1]), end[2])
        self.assertGreater(abs(end[2]), abs(start[2]))

    def test_self_composition_can_grow_nonpell_norm(self) -> None:
        start = (8, 1, 3)
        end = compose(61, start, start)
        self.assertEqual(end, (125, 16, 9))
        self.assertEqual(norm(61, end[0], end[1]), 9)
        self.assertGreater(abs(end[2]), abs(start[2]))

    def test_step_validates_norm_divisions_and_zero_k(self) -> None:
        with self.assertRaisesRegex(ValueError, "declares norm"):
            chakravala_step(61, (8, 1, 2), 7)
        with self.assertRaisesRegex(ValueError, "not divisible"):
            chakravala_step(61, (8, 1, 3), 2)
        with self.assertRaisesRegex(ValueError, r"a \+ m\*b"):
            chakravala_step(-3, (-15, -15, 900), 3)
        with self.assertRaisesRegex(ValueError, "nonzero"):
            chakravala_step(2, (0, 0, 0), 1)

    def test_pell_exit_domain_and_norm_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive nonsquare"):
            pell_exit(4, (1, 0, 1))
        with self.assertRaisesRegex(ValueError, "positive nonsquare"):
            pell_exit(-2, (1, 0, 1))
        with self.assertRaisesRegex(ValueError, "supports only"):
            pell_exit(2, (3, 1, 7))
        with self.assertRaisesRegex(ValueError, "declares norm"):
            pell_exit(2, (3, 2, 2))

    def test_integer_validation_rejects_booleans_and_bounds_size(self) -> None:
        with self.assertRaisesRegex(TypeError, "bool is not accepted"):
            norm(True, 1, 0)
        with self.assertRaisesRegex(TypeError, "bool is not accepted"):
            compose(2, (True, 0, 1), (1, 0, 1))
        with self.assertRaisesRegex(ValueError, "bit limit"):
            norm(1, 1 << MAX_INTEGER_BITS, 0)


if __name__ == "__main__":
    unittest.main()
