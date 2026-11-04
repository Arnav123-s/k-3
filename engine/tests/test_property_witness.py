"""A property supports a check only if it can tell the check's value from a near miss of it (ERDŐS-979-d: a verify
that tested only that the output had the keys n and representations accepted the placeholder {"n": 0, ...}, and the
check was labelled property-supported).

Scenario: run(n) returns n*(n+1)/2; the planner's hand check 3 -> 7 is wrong (6); no reference.
  - a property that accepts every integer: no check is labelled property, so the hand checks are derived again
    blindly; the auditor disputes 3 -> 7, the right method is delivered, and 3 returns as 3 -> 6;
Control: a property that computes the value keeps its support labels. near_misses is the helper checked as a Kuttaka task.
Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime import pramana
from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + ... + n.'
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'
SHAPE_ONLY = 'def verify(input, output):\n    return isinstance(output, int)\n'
COMPUTES = 'def verify(input, output):\n    return output == input * (input + 1) // 2\n'


def solve(verify, hand=7):
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': None, 'verify': verify,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(3, hand), check(5, 15)]}
    audit = lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]}
    m = Metered(Model(plan, lambda r: store(RIGHT), audit=audit), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    result = solver.solve(TASK)
    return result, events(result, 'solve_given_conditions')[0]


class PropertyWitness(unittest.TestCase):
    def test_a_shape_only_property_supports_nothing(self):
        result, settled = solve(SHAPE_ONLY)
        self.assertNotIn('property', settled['support'].values(), 'a property blind to near misses gave support')
        self.assertEqual(settled.get('property_blind'), 5)
        self.assertEqual(result['status'], 'checked')
        self.assertIn({'input': 3, 'expected': 6}, result['checks'])

    def test_control_a_property_that_computes_keeps_its_support(self):
        result, settled = solve(COMPUTES, hand=6)
        self.assertEqual(set(settled['support'].values()), {'property'})
        self.assertEqual(result['status'], 'checked')

    def test_near_misses_is_the_checked_helper(self):
        self.assertEqual(pramana.near_misses({'n': 0, 'representations': []}), [{'n': 1, 'representations': []},
                                                                                {'n': -1, 'representations': []}])
        self.assertEqual(pramana.near_misses([1, True]), [[2, True], [0, True], [1, False]])
        self.assertEqual(pramana.near_misses(None), [])
        self.assertEqual(len(pramana.near_misses(list(range(6)))), 8)


if __name__ == '__main__':
    unittest.main()
