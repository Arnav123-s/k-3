"""T6 (journal mislabel; the H4 report reads it). `_test_set_aside` (kuttaka_runtime/solve.py:961-983) never appends
to `left`, so the event field `three_way` is always empty, and a three-way split (the method ran and gave a third
value, solve.py:971-973) is journalled under `not_run`, beside methods that raised or were not run. W4-g/W4-h's
H4 reports "the solve_set_aside_tested events": read as written, every three-way split is a method that did not run.

Scenario: digit sum, hand check 99 -> 19 (wrong), blind auditor 17 at 99 (wrong too), the right method answers 18.
Control: a method that raises at 99 is journalled under not_run (right).
Scripted models; local executor; every method is written here. Run: python3 <this file> from the repo root."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Digit sum: run(n) returns the sum of the decimal digits of n.'
RIGHT = 'def run(n):\n    return sum(int(d) for d in str(n))\n'
RAISES = 'def run(n):\n    if n > 50 and n % 11 == 0:\n        raise ValueError(n)\n    return sum(int(d) for d in str(n))\n'
CHECKS = [check(12, 3), check(305, 8), check(99, 19), check(4096, 19)]


def first_test(source):
    plan = lambda c: {'contract': 'run(n) returns the sum of the decimal digits of n >= 0', 'input': 12,
                      'checks': CHECKS}
    audit = lambda c: {'expected': [17 if n == 99 else sum(int(d) for d in str(n)) for n in c['inputs']]}
    solver, _ = make_solver(Model(plan, lambda r: store(source), audit=audit), ws=Workspace(Path(tempfile.mkdtemp())),
                            max_calls=20)
    return events(solver.solve(TASK), 'solve_set_aside_tested')[0]


class T6ThreeWayJournalledAsNotRun(unittest.TestCase):
    def test_control_a_method_that_raises(self):
        event = first_test(RAISES)
        self.assertEqual(event.get('not_run'), [99])

    def test_a_method_that_ran_and_gave_a_third_value(self):
        event = first_test(RIGHT)
        self.assertEqual((event.get('three_way'), event.get('not_run')), ([99], []),
                         'a three-way split is journalled as "not run"; three_way is always empty')


if __name__ == '__main__':
    unittest.main()
