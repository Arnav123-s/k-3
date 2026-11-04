"""H3. Since G1 (solve.py:1719-1731 at 11e0ea9) every check is looked for, whatever supports it, and any hit keeps
the method from delivery. memorized_checks (check_memorization.py:84-115) counts a guard as testing a check's input
whenever any constant in the if-test equals an input leaf, so general rules are flagged: `if n % 15 == 0: return
"FizzBuzz"` (check 15 -> "FizzBuzz"), `if m in (12, 1, 2): return "winter"` (check 1 -> "winter"), `if n == 0:
return "zero"` (check 0 -> "zero"; the `if n == 0: return 1` kind of edge case the module promises to allow, with
a string). On FizzBuzz with every check supported by the reference, the correct method is set aside twice and the
task ends incomplete; at 9fc29a0 the reference-supported checks were exempt and it was delivered.

Control: the same task, the model writing the same function as string accumulation, is delivered.
Scripted models; local executor; every method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

NATURAL = ('def run(n):\n    if n % 15 == 0:\n        return "FizzBuzz"\n    if n % 3 == 0:\n        return "Fizz"\n'
           '    if n % 5 == 0:\n        return "Buzz"\n    return str(n)\n')
ACCUMULATED = ('def run(n):\n    out = ""\n    if n % 3 == 0:\n        out += "Fizz"\n    if n % 5 == 0:\n'
               '        out += "Buzz"\n    return out or str(n)\n')
REFERENCE = ('def reference(n):\n    words = [w for d, w in ((3, "Fizz"), (5, "Buzz")) if n % d == 0]\n'
             '    return "".join(words) or str(n)\n')


def solve(source, reference=True, ws=None):
    plan = lambda c: dict({'contract': 'run(n) returns "Fizz", "Buzz", "FizzBuzz" or str(n) for n >= 1', 'input': 30,
                           'checks': [check(3, 'Fizz'), check(5, 'Buzz'), check(15, 'FizzBuzz'), check(7, '7')]},
                          **({'reference': REFERENCE, 'small_inputs': [9, 10, 11, 45]} if reference else {}))
    solver, ws = make_solver(Model(plan, lambda r: store(source)), ws=ws, max_calls=20)
    return solver.solve('FizzBuzz: run(n) for one positive integer n.')


class H3GeneralGuardFlagged(unittest.TestCase):
    def test_detector_flags_general_rules(self):
        cases = [(NATURAL, [check(15, 'FizzBuzz'), check(3, 'Fizz')]),
                 ('def run(m):\n    if m in (12, 1, 2):\n        return "winter"\n    if m in (3, 4, 5):\n'
                  '        return "spring"\n    if m in (6, 7, 8):\n        return "summer"\n    return "autumn"\n',
                  [check(1, 'winter'), check(7, 'summer')]),
                 ('def run(n):\n    if n == 0:\n        return "zero"\n    return words(n)\n', [check(0, 'zero')])]
        for source, checks in cases:
            print('\nflagged:', memorized_checks(source, checks))
        self.assertEqual([memorized_checks(s, c) for s, c in cases], [[], [], []],
                         'a general rule (modulus, a month set, n == 0 -> "zero") is read as a memorized check')

    def test_control_accumulating_fizzbuzz_is_delivered(self):
        result = solve(ACCUMULATED)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 'FizzBuzz'))

    def test_natural_fizzbuzz_with_every_check_reference_supported(self):
        result = solve(NATURAL)
        found = events(result, 'solve_memorized_checks')
        print('\nnatural FizzBuzz: status', result['status'], '|', result.get('reason'),
              '| support', result.get('evidence', {}).get('support'),
              '\n  flagged inputs per round:', [e['inputs'] for e in found], '| culprits:', [e['culprits'] for e in found])
        self.assertEqual(result['status'], 'checked', 'a correct, general FizzBuzz is never delivered')

    def test_without_a_reference_the_correct_method_is_refuted_for_every_task(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        result = solve(NATURAL, reference=False, ws=ws)
        reasons = [r['reason'] for r in ws._read(ws.refuted_path)]
        print('\nno reference: status', result['status'], '| refuted:', reasons[:1])
        self.assertFalse(reasons, 'a correct, general FizzBuzz is refuted by id for every task')


if __name__ == '__main__':
    unittest.main()
