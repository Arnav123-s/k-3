"""K11 (crash; not a regression: 11e0ea9 fails earlier, near 600 arms). memorized_checks catches only SyntaxError from
ast.parse (check_memorization.py:157-160 at 3af0808); _guarded_values.conditional (lines 104-110) recurses once per
arm of a conditional-expression chain. A valid method returning a chain of about 1000 arms (a lookup written as
`a if n == x else b if n == y else ...`, which Python parses and compiles) raises RecursionError out of the detector,
and _memorized (solve.py:1748-1775) and rival() (solve.py:2022) do not catch it, so the solve job raises instead of
judging the method. Control: 900 arms are judged. Pure detector calls plus compile(); no execution."""
import unittest

from kuttaka_runtime.check_memorization import memorized_checks


def chain(arms):
    return 'def run(n):\n    return ' + ' '.join('%d if n == %d else' % (i * 7 + 100, i + 3) for i in range(arms)) + ' 0\n'


class K11DetectorRecursion(unittest.TestCase):
    def test_control_900_arms_are_judged(self):
        self.assertIsInstance(memorized_checks(chain(900), [{'input': 5, 'expected': 114}]), list)

    def test_1000_arms_crash_the_detector(self):
        source = chain(1000)
        compile(source, '<method>', 'exec')   # a valid method
        try:
            found = memorized_checks(source, [{'input': 5, 'expected': 114}])
        except RecursionError as error:
            self.fail('memorized_checks raised %s on a valid 1000-arm conditional chain' % type(error).__name__)
        self.assertIsInstance(found, list)


if __name__ == '__main__':
    unittest.main()
