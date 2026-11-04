"""The vallī step is tested itself, not only through the checks (the builder's first-hand run, 2026-11-02: a kuṭṭaka
reduction with a sign error in reduce, wrong on about half of all inputs above the bottom, was delivered as checked,
because every check sat at the bottom, where the reference answers, and the differential's inputs, made from the
checks, reached the step twice without showing it).

On each check input and planner small input where reduce gives strictly smaller parts, combine(input, the
reference's answers for the parts) must equal the reference's answer for the input: the kuṭṭaka's invariant at each
division. A step that fails it is sent back to the splitter with its counterexample.

Scenario: run(n) returns 0 + 1 + ... + n; small(n) is n <= 3, and every check is at n <= 3.
  - combine off by one (answers[0] + n - 1): the step is refuted on the checks; a splitter that then gives the
    right step is checked; one that repeats the wrong step never has it delivered as checked;
Controls: the right step holds on every testable input and is checked, as before; without a reference (a base
only) the step test does not run and the method is checked as before. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

RIGHT = ('def measure(n):\n    return n\n\n\ndef small(n):\n    return n <= 3\n\n\n'
         'def reduce(n):\n    return [n - 1]\n\n\ndef combine(n, answers):\n    return answers[0] + n\n')
OFF_BY_ONE = RIGHT.replace('return answers[0] + n\n', 'return answers[0] + n - 1\n')
REFERENCE = 'def reference(n):\n    return sum(range(n + 1))\n'


def solve(reductions, reference=REFERENCE):
    replies = iter(reductions)
    last = [reductions[-1]]

    def split(context):
        last[0] = next(replies, last[0])
        return {'reduction': last[0]}
    plan = lambda c: {'contract': 'run(n) returns 0 + 1 + ... + n', 'input': None, 'reference': reference,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(3, 6)]}
    model = Model(plan, lambda request: store('def run(n):\n    return n\n'), split=split)   # wrong on 2 and 3
    m = Metered(model, {'used': 0, 'limit': 30})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve('Sum of the whole numbers up to n.')


def outcomes(result):
    return [e['outcome'] for e in events(result, 'solve_valli_step')]


class ValliStepTests(unittest.TestCase):
    def test_a_wrong_step_is_refuted_and_the_corrected_one_is_checked(self):
        result = solve([OFF_BY_ONE, RIGHT])
        self.assertEqual(outcomes(result)[:2], ['refuted', 'held'])
        refuted = events(result, 'solve_valli_step')[0]['counterexamples'][0]
        self.assertEqual((refuted['input'], refuted['combined'], refuted['direct']), (1, 0, 1))
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['delivered_by'], 'valli')

    def test_a_wrong_step_given_again_is_never_delivered_as_checked(self):
        result = solve([OFF_BY_ONE])
        self.assertIn('refuted', outcomes(result))
        self.assertNotEqual((result['status'], result.get('delivered_by')), ('checked', 'valli'),
                            'a step wrong above the bottom passes checks that all sit at the bottom')

    def test_control_the_right_step_holds_and_is_checked(self):
        result = solve([RIGHT])
        self.assertIn(outcomes(result), ([], ['held']))   # [] before the step test existed
        self.assertEqual((result['status'], result['delivered_by']), ('checked', 'valli'))

    def test_control_without_a_reference_the_step_test_does_not_run(self):
        result = solve([RIGHT + '\n\ndef base(n):\n    return n * (n + 1) // 2\n'], reference=None)
        self.assertIn(outcomes(result), ([], ['not_run']))
        self.assertEqual((result['status'], result['delivered_by']), ('checked', 'valli'))


if __name__ == '__main__':
    unittest.main()
