"""Where the reference and a blind re-derivation each reject the method's value, though they disagree with each
other, the method is unsupported there (W4-n h-r4-collide: on one particle starting at the wall moving left, the
method said [0.0, 5.0], the reference [-2.5, 7.5], the auditor [2.5, 7.5]; nothing was confirmed, the method stood
as checked, and it failed a hidden case).

No value has two voices, so nothing becomes a check. The method is asked for again once, shown the input and both
values. The split does not bar delivery: in W4-l h-c4-puzzle it stood over a right method.

Scenario: run(n) returns n*(n+1)/2; W says 800 at 40; the reference is wrong only at 40 (810); the auditor is right.
  - an actor that fixes the method when shown the dispute: the right method is checked;
  - an actor that keeps W: asked once, and W is delivered as before.
Controls: an auditor that agrees with W (two voices
to one), one within a hand's rounding of W, and one that gives no value (null, no voice) are not a dispute. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + ... + n.'
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'
W = 'def run(n):\n    return 800 if n == 40 else n * (n + 1) // 2\n'
REFERENCE = 'def reference(n):\n    return 810 if n == 40 else sum(range(n + 1))\n'


def solve(fixes, at_40):
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': None, 'reference': REFERENCE,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)], 'small_inputs': [40]}
    act = lambda request: store(RIGHT if fixes and 'agrees with neither' in request else W)
    audit = lambda c: {'expected': [at_40 if n == 40 else n * (n + 1) // 2 for n in c['inputs']]}
    m = Metered(Model(plan, act, audit=audit), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK)


def source(result):
    return result.get('source') or (result.get('unverified') or {}).get('artifact')


class AgainstBothTests(unittest.TestCase):
    def test_shown_the_dispute_the_method_is_fixed_and_checked(self):
        result = solve(True, 820)
        self.assertIn('against both', result['strategies'])
        self.assertEqual((result['status'], result.get('source')), ('checked', RIGHT))

    def test_a_method_kept_is_asked_once_and_delivered_as_before(self):
        result = solve(False, 820)
        self.assertEqual(result['strategies'].count('against both'), 1)
        self.assertEqual((result['status'], result.get('source')), ('checked', W))

    def test_control_an_auditor_with_the_method_leaves_it_checked(self):
        result = solve(False, 800)
        self.assertEqual((result['status'], result.get('source')), ('checked', W))
        self.assertNotIn('against both', result['strategies'])

    def test_control_an_auditor_within_rounding_of_the_method_agrees_with_it(self):
        result = solve(False, 800.5)
        self.assertNotIn('against both', result['strategies'])

    def test_control_an_auditor_with_no_value_is_no_voice(self):
        result = solve(False, None)
        self.assertEqual((result['status'], result.get('source')), ('checked', W))


if __name__ == '__main__':
    unittest.main()
