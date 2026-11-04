"""T3 (completion lost: a right method never delivered; regression vs e7cb51d). `_close` (kuttaka_runtime/solve.py:
366-367) compares two values exactly when `float(x).is_integer()` holds for both: meant for counts ("an off-by-one on
275"), it also holds for any float that happens to be whole (94672800.0 seconds, every float above 2**53). A hand's
rounding of such a float is then a disagreement. At a set-aside check (`_test_set_aside`, solve.py:969) the right
method's value is not "close" to the blind value, so the check is a three-way split: the planner's rounded hand
value returns (solve.py:971-973) and every later attempt, the right method included, fails it exactly.

Scenario: seconds in n Julian years (365.25 days) as a float. Hand checks 0 -> 0.0, 1 -> 31557600.0, 2 -> 63115200.0,
and 3 -> 9.47e7 (a hand's rounding; right 94672800.0). The blind auditor is exact on 0, 1, 2 and writes five
significant digits at 3: 9.4673e7 (a hand's rounding too).
Requested input 10 (right 315576000.0).
  4c803be: 9.4673e7 vs the method's 94672800.0, both whole: exact, a third value; 9.47e7 returns; not delivered.
  e7cb51d: within 0.5%: the check is corrected to the method's value (two derivations); 315576000.0 delivered.
Control: the tropical year (365.2422 days): the same roundings, but the method's value 94670778.24 is a fraction, so
the tolerance applies and the right answer is delivered (only the accident of a whole float differs).
Scripted models; local executor; every method is written here. Run: python3 <this file> from the repo root."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store


def solve(days):
    task = 'Seconds: run(n) returns the number of seconds in n years of %s days, as a float.' % days
    year = days * 86400.0
    checks = [check(0, 0.0), check(1, year), check(2, 2 * year), check(3, 9.47e7)]   # 3: a hand's rounding
    plan = lambda c: {'contract': 'run(n) returns n * %s * 86400.0 (a float) for an integer n >= 0' % days,
                      'input': 10, 'checks': checks}
    audit = lambda c: {'expected': [n * year if n < 3 else float('%.5g' % (n * year)) for n in c['inputs']]}  # 5 digits at 3
    right = 'def run(n):\n    return n * %s * 86400.0\n' % days
    solver, _ = make_solver(Model(plan, lambda r: store(right), audit=audit), ws=Workspace(Path(tempfile.mkdtemp())),
                            max_calls=30)
    result = solver.solve(task)
    print('\n%s days: %s, answer %r (right %r) | set aside tested %s' % (
        days, result['status'], result.get('answer'), 10 * year,
        [(e['restored'], e['corrected'], e.get('not_run')) for e in events(result, 'solve_set_aside_tested')]),
        file=sys.stderr)
    return result, 10 * year


class T3AWholeFloatIsNotACount(unittest.TestCase):
    def test_control_fractional_value(self):
        result, right = solve(365.2422)
        self.assertEqual((result['status'], result.get('answer')), ('checked', right))

    def test_whole_float_value(self):
        result, right = solve(365.25)
        self.assertEqual((result['status'], result.get('answer')), ('checked', right),
                         'a hand rounding of a whole float is taken for an off-by-one: the right method is not delivered')


if __name__ == '__main__':
    unittest.main()
