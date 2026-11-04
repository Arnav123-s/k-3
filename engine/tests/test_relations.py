"""Two positions of the gnomon (Gaṇitapāda 16): an unknown fixed by comparing two observations, the second made after
a known change, with no value worked out by anyone. Golapāda 13 tells one moment from four cities, and the four must
agree; IV 1 and 27 use symmetry. Kuttaka checked a method only against values somebody worked out (the hand, a
reference, a property). A relation between two runs needs none (metamorphic testing).

Scenario: the sum of a list. Every check starts with 0, so a method that adds the first element twice passes them all.
The plan states that reversing the list leaves the sum unchanged. Run on an input and on its reverse, the method
breaks the relation; it is shown the pairs and fixed. Controls: a right method is not asked again; a relation that a
pair of checks contradicts is discarded and asks nothing; with no relation stated nothing is run (the old outcome)."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

RIGHT = 'def run(x):\n    return sum(x)\n'
FIRST_TWICE = 'def run(x):\n    return sum(x) + x[0] if x else 0\n'
REVERSAL = ('def moves(x):\n    return [list(reversed(x))]\n\n\n'
            'def agree(x, y, m, z):\n    return y == z\n')
DOUBLES = ('def moves(x):\n    return [list(reversed(x))]\n\n\n'      # wrong: reversing does not double the sum
           'def agree(x, y, m, z):\n    return z == 2 * y\n')
CHECKS = [check([0, 1, 2], 3), check([0, 5], 5), check([0], 0), check([], 0)]
PAIRED = CHECKS + [check([1, 2], 3), check([2, 1], 3)]   # the reverse of a check is a check: the sums are equal
SUM_PAIRS = RIGHT.replace('sum(x)', 'sum(x) + 0')


def solve(first, relations=REVERSAL, checks=CHECKS):
    requests = []

    def act(request):
        requests.append(request)
        return store(RIGHT if 'breaks a relation' in request or 'break it' in request else first)
    plan = lambda c: {'contract': 'run(list) returns the sum of the numbers', 'input': None, 'checks': checks,
                      'relations': relations}
    m = Metered(Model(plan, act, audit=lambda c: {}), {'used': 0, 'limit': 10})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                    max_executions=256, say=lambda _: None)
    return solver.solve('Add up the numbers.'), requests


class TwoPositionsOfTheGnomon(unittest.TestCase):
    def test_a_method_that_breaks_the_stated_relation_is_shown_the_pairs_and_fixed(self):
        result, requests = solve(FIRST_TWICE)
        self.assertEqual((result['status'], result['source']), ('checked', RIGHT))   # old code: FIRST_TWICE
        self.assertIn('relation', result['strategies'])
        self.assertEqual(result['relations'], 'held')
        self.assertEqual(len(requests), 2)
        broken = [e for e in events(result, 'solve_relation_test') if e['outcome'] == 'broken']
        self.assertTrue(broken and broken[0]['broken'])

    def test_control_a_right_method_is_not_asked_again(self):
        result, requests = solve(RIGHT)
        self.assertEqual((result['status'], len(requests)), ('checked', 1))

    def test_a_right_method_is_labelled_as_holding_the_relation(self):
        self.assertEqual(solve(RIGHT)[0]['relations'], 'held')

    def test_control_a_relation_a_pair_of_checks_contradicts_asks_nothing(self):
        result, requests = solve(SUM_PAIRS, relations=DOUBLES, checks=PAIRED)
        self.assertEqual((result['status'], len(requests)), ('checked', 1))

    def test_a_relation_a_pair_of_checks_contradicts_is_discarded(self):
        result, _ = solve(SUM_PAIRS, relations=DOUBLES, checks=PAIRED)
        self.assertEqual(result['relations'], 'discarded')
        self.assertTrue([e for e in events(result, 'solve_relation_test') if e['outcome'] == 'discarded'])

    def test_control_with_no_relation_stated_nothing_is_run(self):
        result, requests = solve(FIRST_TWICE, relations=None)
        self.assertEqual((result['status'], result['source'], len(requests)), ('checked', FIRST_TWICE, 1))
        self.assertEqual(events(result, 'solve_relation_test'), [])
        self.assertEqual(result.get('relations', 'not_asked'), 'not_asked')


if __name__ == '__main__':
    unittest.main()
