"""G5 (low). The tie count in rival() (solve.py:2038-2045 at 9fc29a0) gives the auditor's voice to both sides when
same_value's 1e-9 relative tolerance accepts it against the method and against the rival, while the two methods
differ by just over the tolerance: 2 == 2 with no reference at all, and the requested answer is void although all
three values agree to the suite's own scoring tolerance with the method's. Control: the auditor equal to the
method exactly (no tie) delivers. Scripted models; local executor; every method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import check
from tests.test_solve_pramana import Rewriting

RIVAL = 'def run(x):\n    if x < 1:\n        return -1.0\n    return x * 20.5 * (1 + 1.8e-9 if x == 40 else 1)\n'
METHOD = 'def run(x):\n    return x * 20.5\n'


def solve(mid):
    plan = lambda c: {'contract': 'run(x) returns 20.5 * x', 'input': 40,
                      'checks': [check(0, 0.0), check(1, 20.5), check(2, 41.0)]}
    audit = lambda c: {'expected': [x * 20.5 * ((1 + 0.9e-9) if (mid and x == 40) else 1) for x in c['inputs']]}
    m = Metered(Rewriting(plan, RIVAL, METHOD, audit=audit), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve('Scale by 20.5.')


class G5ToleranceTie(unittest.TestCase):
    def test_control_auditor_equal_to_the_method(self):
        self.assertEqual(solve(mid=False)['status'], 'checked')

    def test_auditor_within_tolerance_of_both_voids_without_a_reference(self):
        result = solve(mid=True)
        print('\nstatus:', result['status'], '|', result.get('reason'))
        self.assertEqual(result['status'], 'checked', 'a tolerance overlap counts as two against two')


if __name__ == '__main__':
    unittest.main()
