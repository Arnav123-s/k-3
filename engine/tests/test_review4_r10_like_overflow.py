"""R10. _like (number form of a revised check) calls float() on an int when the hand value was a float; an
int beyond float range (a factorial, a power) raises OverflowError, which nothing catches: solve() crashes
in _settle_given. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace, _like
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store


class R10LikeOverflow(unittest.TestCase):
    def test_unit(self):
        self.assertEqual(_like(1.0, 10 ** 400), 10 ** 400)

    def test_solve_crashes_when_a_float_hand_value_meets_a_big_int_reference(self):
        big = 2 ** 1100   # the planner wrote 2 ** 1100 as a float it could not represent: 1e308
        plan = lambda c: {'contract': 'run(k) returns 2 ** k exactly', 'input': None,
                          'checks': [check(3, 8), check(4, 16), check(1100, 1e308)],
                          'reference': 'def reference(k):\n    return 2 ** k\n'}
        model = Model(plan, lambda r: store('def run(k):\n    return 2 ** k\n'),
                      audit=lambda c: {'expected': [2 ** k for k in c['inputs']]})
        m = Metered(model, {'used': 0, 'limit': 10})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                        sketcher=m, max_executions=64, say=lambda _: None)
        try:
            result = solver.solve('Powers of two, exactly.')
        except OverflowError as error:
            self.fail(f'solve() raised OverflowError: {error}')
        self.assertIn(check(1100, big), result['checks'])


if __name__ == '__main__':
    unittest.main()
