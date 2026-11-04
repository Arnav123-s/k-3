"""T2 (wrong answer delivered as checked / completion lost; regression vs e7cb51d). `_derive_blind`
(kuttaka_runtime/solve.py:902) now compares the blind value with the hand value by `_close` (0.5% for fractions)
instead of `same_value`. A hand check rounded by the planner (1/3 written 0.333) is within 0.5% of the blind
value (0.3333), so it is "agreed" and enters the attempt unchanged; checks are compared exactly in the attempt
(programs.py:554, canonical JSON), so the right method 1/n fails it. The audit step (solve.py:1455) corrects only
when the auditor's value equals the method's exactly (0.3333 != 0.3333333333333333), so it does not help; the
repair step shows the model "expected 0.333", and a model that fits the check (round to 3 places: the h-m1-digits
signature) passes every check. Nothing records a voice at the requested input, so 0.143 is delivered as checked
for 7 (right 0.14285714285714285).
  e7cb51d: 0.333 vs 0.3333 is a dispute; the check is set aside; the right method, written without it, is close to
           the blind value there, so the check is corrected to 1/3 (two derivations); 1/7 is delivered.
Control: the planner writes 1/3 in full; the right method passes; 1/7 is delivered.
Scripted models; local executor; every method is written here. Run: python3 -m unittest <this file> from the repo."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Reciprocal: run(n) returns 1/n as a float.'
RIGHT = 'def run(n):\n    return 1 / n\n'
FITTED = 'def run(n):\n    return round(1 / n, 3)\n'   # what a model writes to fit "expected 0.333"


def solve(third):
    checks = [check(1, 1.0), check(2, 0.5), check(4, 0.25), check(3, third)]
    plan = lambda c: {'contract': 'run(n) returns 1 / n as a float, for an integer n >= 1', 'input': 7,
                      'checks': checks}
    audit = lambda c: {'expected': [round(1 / n, 4) for n in c['inputs']]}   # a hand derivation: 4 places

    def act(request):
        return store(FITTED if 'Failing checks' in request else RIGHT)
    solver, _ = make_solver(Model(plan, act, audit=audit), ws=Workspace(Path(tempfile.mkdtemp())), max_calls=30)
    result = solver.solve(TASK)
    print('\nhand %r at 3: %s, answer %r (right %r) | blind %s | strategies %s' % (
        third, result['status'], result.get('answer'), 1 / 7,
        [(e.get('outcome'), e.get('agreed'), e.get('disputed')) for e in events(result, 'solve_blind_derivation')],
        result.get('strategies')), file=sys.stderr)
    return result


class T2BlindToleranceKeepsARoundedHandCheck(unittest.TestCase):
    def test_control_hand_value_in_full(self):
        result = solve(1 / 3)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 1 / 7))

    def test_rounded_hand_value(self):
        result = solve(0.333)
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 0.143),
                            'a rounded hand check agreed "within rounding" steers the method; 0.143 delivered as checked')
        self.assertEqual((result['status'], result.get('answer')), ('checked', 1 / 7),
                         'the model alone (1/n) answers 1/7')


if __name__ == '__main__':
    unittest.main()
