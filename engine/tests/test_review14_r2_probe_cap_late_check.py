"""R2 (wrong answer delivered as checked, stored, reused by id; not a regression vs d5dc8ce: Q1 is fixed only for a
check early in the list). The inputs around each check are added in one pool capped at MAX_PROBES = 32
(check_substitution.py:56, 273-279 at d330165): first every check input, then sibling probes, then made, near and
scaled inputs interleaved check by check (lines 297-310). With eight ordinary checks the pool is full before the
last checks get any input of their own, so `around` holds nothing for them (lines 376-384) and the question about
the method around a literal there (solve.py:1802-1808) is only about the check's value: a right auditor confirms
the correct check, and the wrong method that patches it stands.

Scenario: day of the year of [year, month, day] (the R1 rule on the numeric form, where the question works), with
eight correct hand checks; the one leap check [2024, 3, 1] -> 61 is patched with a named literal by a rule that
knows no leap years. Requested input [2028, 3, 1] (right 61). Leap check last: delivered as checked, 60; a later task
reuses it by id for [2032, 12, 31] and answers 365 (right 366).
Control: the same eight checks with the leap check first: the question reaches [2025, 3, 1], [4048, 3, 1], ..., the
patch is disputed, and the general rule is delivered: 61.
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime import check_substitution
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Day of the year: run(date) returns the day of the year (1 to 366) of a Gregorian [year, month, day].'
LATER = 'Day of the year of a Gregorian [year, month, day]: what is run(date) for this date?'
CUM = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
HEAD = 'CUM = %r\nLEAP_MARCH_FIRST = 61\n\n\ndef run(date):\n    year, month, day = date\n' % (CUM,)
PATCHED = HEAD + ('    if year == 2024 and month == 3 and day == 1:\n        return LEAP_MARCH_FIRST\n'
                  '    return CUM[month - 1] + day\n')   # wrong: every other leap year is a day short from March on
RIGHT = HEAD + ('    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n'
                '    return CUM[month - 1] + day + (1 if leap and month > 2 else 0)\n')
ORDINARY = [([2023, 1, 15], 15), ([2023, 3, 1], 60), ([2023, 12, 31], 365), ([2022, 2, 10], 41), ([2021, 6, 30], 181),
            ([2019, 9, 9], 252), ([2018, 11, 5], 309)]
LEAP = ([2024, 3, 1], 61)


def right(date):
    year, month, day = date
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return CUM[month - 1] + day + (1 if leap and month > 2 else 0)


def auditor(context):   # right everywhere it can read the input
    out = []
    for v in context['inputs']:
        try:
            out.append(right(v))
        except Exception:
            out.append(None)
    return {'expected': out}


def checks(leap_last):
    pairs = ORDINARY + [LEAP] if leap_last else [LEAP] + ORDINARY
    return [check(i, e) for i, e in pairs]


def jobs(leap_last):
    first_checks = checks(leap_last)

    def plan(context):
        if context['task'] == TASK:
            return {'contract': 'run(date) returns the day of the year, 1 to 366', 'input': [2028, 3, 1],
                    'checks': first_checks}
        return {'contract': 'run(date) returns the day of the year, 1 to 366', 'input': [2032, 12, 31],
                'checks': first_checks[:2] if not leap_last else first_checks[:2]}

    def act(request):   # the patch first; the general rule once told a literal was rejected
        return store(RIGHT if 'literally' in request else PATCHED)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act, audit=auditor), ws=ws, max_calls=16)[0].solve(TASK)
    later = make_solver(Model(plan, act, audit=auditor), ws=ws, max_calls=16)[0].solve(LATER)
    print('\nleap check %s: first %s answer %r (right 61) | questions %s | later %s by %s, answer %r (right 366)' % (
        'last' if leap_last else 'first', first['status'], first.get('answer'),
        [(e.get('outcome'), e.get('inputs')) for e in events(first, 'solve_literal_questioned')],
        later['status'], later.get('delivered_by'), later.get('answer')), file=sys.stderr)
    return first, later


class R2ProbeCapLateCheck(unittest.TestCase):
    def test_precondition_no_probe_of_its_own(self):
        # Adapted after the fix: the finding's precondition (the last check had only its own input, 1) is what the
        # fix removes; every check now gets its share of inputs in turn, so the last one has inputs of its own.
        prepared = check_substitution.prepare({'': PATCHED}, checks(True), [])
        self.assertGreaterEqual(prepared['origin'].count(len(ORDINARY)), 3)

    def test_control_leap_check_first(self):
        first, later = jobs(False)
        self.assertEqual((first['status'], first.get('answer')), ('checked', 61))
        self.assertEqual(later.get('answer'), 366)

    def test_leap_check_last(self):
        first, later = jobs(True)
        self.assertNotEqual(first.get('answer'), 60, 'the patched method is delivered as checked and answers 60')
        self.assertNotEqual(later.get('answer'), 365, 'a later task reuses it by id and answers 365')


if __name__ == '__main__':
    unittest.main()
