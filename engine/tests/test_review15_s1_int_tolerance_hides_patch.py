"""S1 (wrong answer delivered as checked; regression vs d330165). _question_literals compares the auditor with the
method's own answers around a literal by _close (kuttaka_runtime/solve.py:338-350, used at 2099-2100), which allows
0.5% relative on INTEGERS too. A wrong rule patched at one check (the leap-year patch the code comments name) is
off by one around the check; with answers above 200 an off-by-one is within 0.5%, so the question "confirms" the
patched method and it is delivered. At d330165 (same_value) the same question disputes it and the general rule
is delivered.

Scenario: day of the year of [year, month, day]. The model's rule knows no leap years and patches the leap check
[2024, 10, 1] -> 275 with a named constant (the text reading misses it; the substitution test finds it). All checks
and the auditor are right. Requested input [2028, 11, 15] (right 320; the patched rule answers 319).
Control: the same patch at [2024, 3, 1] -> 61 (values near 60: an off-by-one is 1.6%): disputed, 320 delivered.
Scripted models; local executor; every method is written here. Run: python3 <this file> [repo root]."""
import os
import sys
import tempfile
import unittest
from pathlib import Path


from kuttaka_runtime.check_memorization import memorized_checks   # noqa: E402
from kuttaka_runtime.solve import Workspace   # noqa: E402
from tests.test_solve_review_regressions import Model, check, events, make_solver, store   # noqa: E402

TASK = 'Day of the year: run(date) returns the day of the year (1 to 366) of a Gregorian date [year, month, day].'
CUM = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def right(date):
    year, month, day = date
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return CUM[month - 1] + day + (1 if leap and month > 2 else 0)


def method(patched, at):
    head = 'CUM = %r\nLEAP_DAY_VALUE = %d\n\n\ndef run(date):\n    year, month, day = date\n' % (CUM, right(at))
    if patched:   # wrong: no leap years, one leap check patched
        return head + ('    if year == %d and month == %d and day == %d:\n        return LEAP_DAY_VALUE\n' % tuple(at)
                       + '    return CUM[month - 1] + day\n')
    return head + ('    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n'
                   '    return CUM[month - 1] + day + (1 if leap and month > 2 else 0)\n')


def auditor(context):   # right wherever it can read the input
    out = []
    for v in context['inputs']:
        try:
            out.append(right(v))
        except Exception:
            out.append(None)
    return {'expected': out}


def job(at):
    checks = [check([2023, 1, 15], 15), check([2023, 3, 1], 60), check(list(at), right(at)), check([2023, 12, 31], 365)]
    plan = lambda c: {'contract': 'run(date) returns the day of the year, 1 to 366', 'input': [2028, 11, 15],
                      'checks': checks}
    act = lambda request: store(method('literally' not in request, at))   # the patch first; the rule once told
    ws = Workspace(Path(tempfile.mkdtemp()))
    solver, _ = make_solver(Model(plan, act, audit=auditor), ws=ws, max_calls=16)
    result = solver.solve(TASK)
    print('\npatch at %s: %s, answer %r (right 320) | question %s' % (
        at, result['status'], result.get('answer'),
        [(e.get('outcome'), e.get('auditor')) for e in events(result, 'solve_literal_questioned')]), file=sys.stderr)
    return result, checks


class S1IntegerToleranceHidesPatch(unittest.TestCase):
    def test_precondition_text_reading_misses_the_patch(self):
        self.assertEqual(memorized_checks(method(True, (2024, 10, 1)), [check([2024, 10, 1], 275)]), [])

    def test_control_patch_at_small_values_is_disputed(self):
        result, _ = job((2024, 3, 1))
        self.assertEqual((result['status'], result.get('answer')), ('checked', 320))

    def test_patch_at_values_above_200_is_confirmed_and_delivered(self):
        result, _ = job((2024, 10, 1))
        self.assertNotEqual(result.get('answer'), 319, 'the patched (no-leap-year) method is delivered as checked')


if __name__ == '__main__':
    unittest.main()
