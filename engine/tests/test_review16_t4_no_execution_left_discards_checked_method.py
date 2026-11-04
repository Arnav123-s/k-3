"""T4 (completion lost: a checked right method discarded; regression vs e7cb51d). `_test_set_aside`
(kuttaka_runtime/solve.py:957-960, 964-966, 994) now treats "not run because three or fewer executions are left" like
a raise: the set-aside hand check returns and True is returned, so the loop (solve.py:1737-1744) throws away the method
that passed every check it faced and asks for a new attempt, which, with three executions or fewer, cannot even be
qualified on the checks (one execution per check). At e7cb51d the method was kept (the check stayed aside) and
delivered. The stated reason ("three values have no majority"; "the method must face it in a new attempt") does not
hold here: nothing contradicts the method, and no attempt can face anything with the executions left.

Scenario: the digit-sum task of tests/test_blind_derivation.py (hand check 99 -> 19 is wrong; the auditor is right;
the model's method is right); the job's execution limit leaves three executions when the method has passed.
Control: a generous execution limit: the method runs there, the check is corrected, 18 is delivered.
Scripted models; local executor; every method is written here. Run: python3 <this file> from the repo root."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Digit sum: run(n) returns the sum of the decimal digits of n.'
RIGHT = 'def run(n):\n    return sum(int(d) for d in str(n))\n'
CHECKS = [check(12, 3), check(305, 8), check(99, 19), check(4096, 19)]   # 99 -> 19 is wrong (18)


def solve(max_executions):
    plan = lambda c: {'contract': 'run(n) returns the sum of the decimal digits of n >= 0', 'input': 99,
                      'checks': CHECKS}
    audit = lambda c: {'expected': [sum(int(d) for d in str(n)) for n in c['inputs']]}
    solver, _ = make_solver(Model(plan, lambda r: store(RIGHT), audit=audit), ws=Workspace(Path(tempfile.mkdtemp())),
                            max_calls=20, max_executions=max_executions)
    result = solver.solve(TASK)
    print('\nexecution limit %d: %s, answer %r (right 18), reason %r | set aside tested %s' % (
        max_executions, result['status'], result.get('answer'), result.get('reason'),
        [(e['restored'], e['corrected'], e.get('not_run')) for e in events(result, 'solve_set_aside_tested')]),
        file=sys.stderr)
    return result


class T4NoExecutionLeftDiscardsTheCheckedMethod(unittest.TestCase):
    def test_control_executions_to_spare(self):
        result = solve(256)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 18))

    def test_three_executions_left(self):
        result = solve(6)   # 3 to qualify the method on the three checks left, 3 for what follows
        self.assertEqual((result['status'], result.get('answer')), ('checked', 18),
                         'the method that passed every check it faced is discarded for an attempt that cannot run')


if __name__ == '__main__':
    unittest.main()
