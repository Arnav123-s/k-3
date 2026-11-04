"""Q1 (wrong answer delivered as checked; regression vs 556adfd). A WRONG method that special-cases a CORRECT check in a
form only the substitution test sees is delivered, because what that test finds is now only a question about the
check's VALUE (kuttaka_runtime/solve.py:1855-1860, 1791-1815 at d5dc8ce):

  - a hand check the substitution test finds is put to the blind auditor; a right auditor confirms the right value,
    so the method "stands" (`_question_literals` returns no dispute), although the special case is exactly what the
    guard exists to stop: "a literal that answers a check is never delivered, whatever supports the check (a method
    special-casing even a correct check may be wrong where no check reaches)" (solve.py:1831-1834);
  - a check the user gave, or the reference or two derivations support, is not even questioned (solve.py:1858);
    the test's find is dropped.

Scenario: days in a month. The model's rule ignores leap years and patches the one leap check with a named literal
(`LEAP_FEBRUARY = 29` under `if year == 2024`), which the text reading misses and the substitution test finds.
All checks are correct. The requested input is [2028, 2] (right: 29). At d5dc8ce the patched method is delivered as
checked and answers 28. At 556adfd the substitution test's hit set it aside, the next attempt wrote the general rule,
and the answer is 29.
The same holds with user examples. (With a correct reference the find is dropped too, but the differential test runs
the reference on the requested input and refutes the method there; it protects only where the reference runs.)
Control: the same patch written `if year == 2024 and month == 2: return 29` is read by the text and set aside at
d5dc8ce too; the answer is 29.
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Days in a month: what is run([year, month]), the number of days in that month (Gregorian calendar)?'
CONTRACT = 'run([year, month]) returns the days in the month: 28 or 29 for February by the Gregorian leap rule'
TAIL = '    if month in (4, 6, 9, 11):\n        return 30\n    return 31\n'
RIGHT = ('def run(p):\n    year, month = p\n    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n'
         '    if month == 2:\n        return 29 if leap else 28\n' + TAIL)
PATCHED = ('LEAP_FEBRUARY = 29\n\n\ndef run(p):\n    year, month = p\n    if month == 2:\n        if year == 2024:\n'
           '            return LEAP_FEBRUARY\n        return 28\n' + TAIL)   # wrong: every other leap year gets 28
CONTROL = ('def run(p):\n    year, month = p\n    if year == 2024 and month == 2:\n        return 29\n'
           '    if month == 2:\n        return 28\n' + TAIL)
CHECKS = [check([2023, 2], 28), check([2024, 2], 29), check([2023, 4], 30), check([2023, 7], 31)]   # all correct


def days(p):
    year, month = p
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return (29 if leap else 28) if month == 2 else 30 if month in (4, 6, 9, 11) else 31


def right_auditor(context):
    return {'expected': [days(v) for v in context['inputs']]}


REFERENCE = RIGHT.replace('def run(p)', 'def reference(p)')
SMALL = [[2023, 1], [2021, 2], [2023, 12], [2023, 6]]   # no other leap February among them


def solve(first, examples=(), reference=False):
    def plan(context):
        return dict({'contract': CONTRACT, 'input': [2028, 2], 'checks': CHECKS},
                    **({'reference': REFERENCE, 'small_inputs': SMALL} if reference else {}))

    def act(request):   # the patched method first; the general rule once told a literal was rejected
        return store(RIGHT if 'literally' in request else first)
    solver, ws = make_solver(Model(plan, act, audit=right_auditor), ws=Workspace(Path(tempfile.mkdtemp())),
                             max_calls=16)
    result = solver.solve(TASK, examples=list(examples))
    print('\n%s: %s, answer for [2028, 2]: %s (right 29) | questioned %s | flagged %s' % (
        'user examples' if examples else 'reference-supported checks' if reference else 'hand checks', result['status'], result.get('answer'),
        [e.get('outcome') for e in events(result, 'solve_literal_questioned')],
        [e['inputs'] for e in events(result, 'solve_memorized_checks')]), file=sys.stderr)
    return result


class Q1PatchedCorrectCheckDelivered(unittest.TestCase):
    def test_precondition_the_text_misses_the_patch(self):
        self.assertEqual(memorized_checks(PATCHED, CHECKS), [])
        self.assertTrue(memorized_checks(CONTROL, CHECKS))

    def test_control_the_text_reads_the_patch(self):
        result = solve(CONTROL)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 29))

    def test_hand_checks_right_auditor(self):
        result = solve(PATCHED)
        self.assertNotEqual(result.get('answer'), 28, 'the patched method is delivered and answers 28 for [2028, 2]')

    def test_user_examples_not_even_questioned(self):
        result = solve(PATCHED, examples=CHECKS)
        self.assertNotEqual(result.get('answer'), 28, 'the patched method is delivered and answers 28 for [2028, 2]')


if __name__ == '__main__':
    unittest.main()
