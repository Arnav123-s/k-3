"""The thirteenth review's plausible findings, fixed with the confirmed ones (no reproduction was sent; these are the
builder's tests of the rules as fixed): S1/S2 whole numbers compared exactly, floats within a hand's rounding; S10
agreement counted once per derivation, dissent each time heard. Pure function calls."""
import unittest

from kuttaka_runtime.solve import _close, _heard


class Rules(unittest.TestCase):
    def test_whole_numbers_exactly_fractions_within_rounding(self):
        self.assertFalse(_close(275, 274))
        self.assertFalse(_close(1228, 1230))
        self.assertFalse(_close(275.0, 274))
        self.assertTrue(_close(6.66667, 6.666666666666667))
        self.assertTrue(_close([1, 6.66667], [1, 6.666666666666667]))
        self.assertFalse(_close([1, 6.66667], [2, 6.666666666666667]))

    def test_agreement_once_dissent_each_time(self):
        heard = _heard([('auditor', 800, None), ('auditor', 800, None), ('auditor', 820, None),
                        ('auditor', 820, None)], 800)
        self.assertEqual([v for _, v, _ in heard], [800, 820, 820])


if __name__ == '__main__':
    unittest.main()
