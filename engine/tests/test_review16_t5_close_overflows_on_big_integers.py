"""T5 (crash; regression at this call site vs e7cb51d). `_close` (kuttaka_runtime/solve.py:366) calls
`float(said).is_integer()`, which raises OverflowError for an int beyond float range (about 1.8e308). `_derive_blind`
(solve.py:902) now compares the blind value with the hand value by `_close` instead of `same_value` (which catches
OverflowError, solve.py:253-256), so a hand check with a big integer that the auditor disputes stops the whole job:
nothing in `Solver.solve` or `solve_task` catches it (solve.py:1502-1509, 3337-3347). The same `_close` is reached by
`_test_set_aside` (solve.py:969) and `_question_literals` (solve.py:2138) with the method's own values, which may be
big integers (2**n, factorials); there the old `_close` overflowed too (`5e-3 * int`), so those two are not new.

Scenario: run(n) returns 2**n; hand checks 0 -> 1, 1 -> 2, 10 -> 1024 and 1100 -> 2**1100 + 1 (a slip); the auditor
is right (2**1100). Requested input 20.
  e7cb51d: 1100 is disputed and set aside; the right method corrects it; 1048576 is delivered.
Control: the slip on 10 (1025): small integers; delivered.
Scripted models; local executor; every method is written here. Run: python3 <this file> from the repo root."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace, _close
from tests.test_solve_review_regressions import Model, check, make_solver, store

TASK = 'Powers of two: run(n) returns 2 to the power n, exactly.'
RIGHT = 'def run(n):\n    return 2 ** n\n'


def solve(slip_at):
    checks = [check(0, 1), check(1, 2), check(10, 1024 + (slip_at == 10)), check(1100, 2 ** 1100 + (slip_at == 1100))]
    plan = lambda c: {'contract': 'run(n) returns 2**n as an exact integer, n >= 0', 'input': 20, 'checks': checks}
    audit = lambda c: {'expected': [2 ** n for n in c['inputs']]}
    solver, _ = make_solver(Model(plan, lambda r: store(RIGHT), audit=audit), ws=Workspace(Path(tempfile.mkdtemp())),
                            max_calls=20)
    try:
        result = solver.solve(TASK)
    except Exception as error:
        print('\nslip at %d: %s: %s' % (slip_at, type(error).__name__, error), file=sys.stderr)
        raise
    print('\nslip at %d: %s, answer %r' % (slip_at, result['status'], result.get('answer')), file=sys.stderr)
    return result


class T5CloseOverflowsOnBigIntegers(unittest.TestCase):
    def test_control_small_slip(self):
        self.assertEqual(solve(10).get('answer'), 2 ** 20)

    def test_big_slip(self):
        self.assertEqual(solve(1100).get('answer'), 2 ** 20, 'the job stops with OverflowError')

    def test_close_itself(self):
        self.assertFalse(_close(2 ** 1100, 2 ** 1100 + 1))


if __name__ == '__main__':
    unittest.main()
