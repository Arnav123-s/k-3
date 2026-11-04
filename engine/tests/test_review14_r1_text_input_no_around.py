"""R1 (wrong answer delivered as checked, stored, reused by id; not a regression vs d5dc8ce: the eleventh review's Q1
is fixed for numeric inputs only). The question about the method around a literal (kuttaka_runtime/solve.py:
1802-1808, check_substitution.py:296-310, 376-384 at d330165) asks about inputs "made from" the check: its numbers
moved (near_inputs: numbers only), its numbers doubled and tripled (ints only), and its leaves replaced by the
source's own constants of the same type. A check whose input is TEXT (a date "2024-03-01", a code, a word) gets
no near or scaled input, and the source's text constants ("-", a format) are no valid input: the method raises
there and they are dropped. So `around` is empty, and the question is only about the check's value: a right
auditor confirms the correct check, and the wrong method that patches it stands (with user examples, or checks
the reference or two derivations support, no question is asked at all).

Scenario: day of the year of a "YYYY-MM-DD" date. The model's rule knows no leap years and patches the one leap
check with a named literal (the text reading misses it; the substitution test finds it). All checks are correct.
Requested input "2028-03-01" (right 61). At d330165 the patched method is delivered as checked and answers 60; a
later task asking for "2032-12-31" reuses it by id and answers 365 (right 366).
Control: the same rule on the numeric form [2024, 3, 1] (a list): the question reaches [2024, 3, 2], [4048, 3, 1]
and the like, the patch is disputed and set aside, and the general rule is delivered: 61.
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Day of the year: run(date) returns the day of the year (1 to 366) of a Gregorian date.'
LATER = 'Day of the year of a Gregorian date: what is run(date) for this date?'
CUM = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
PARSE = {'text': '    year, month, day = (int(x) for x in date.split("-"))\n',
         'list': '    year, month, day = date\n'}
LEAP = '    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n'


def method(form, patched):
    head = 'CUM = %r\nLEAP_MARCH_FIRST = 61\n\n\ndef run(date):\n' % (CUM,) + PARSE[form]
    if patched:   # wrong: every other leap year is a day short from March on
        return head + ('    if year == 2024 and month == 3 and day == 1:\n        return LEAP_MARCH_FIRST\n'
                       '    return CUM[month - 1] + day\n')
    return head + LEAP + '    return CUM[month - 1] + day + (1 if leap and month > 2 else 0)\n'


def right(date):
    year, month, day = (int(x) for x in date.split('-')) if isinstance(date, str) else date
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


def as_form(form, text):
    return text if form == 'text' else [int(x) for x in text.split('-')]


def jobs(form, user=False):
    checks = [check(as_form(form, d), e) for d, e in
              [('2023-01-15', 15), ('2023-03-01', 60), ('2024-03-01', 61), ('2023-12-31', 365)]]

    def plan(context):
        if context['task'] == TASK:
            return {'contract': 'run(date) returns the day of the year, 1 to 366', 'input': as_form(form, '2028-03-01'),
                    'checks': checks}
        return {'contract': 'run(date) returns the day of the year, 1 to 366', 'input': as_form(form, '2032-12-31'),
                'checks': [checks[0], checks[1]]}

    def act(request):   # the patch first; the general rule once told a literal was rejected
        return store(method(form, patched='literally' not in request))
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act, audit=auditor), ws=ws, max_calls=16)[0].solve(
        TASK, examples=checks if user else ())
    later = make_solver(Model(plan, act, audit=auditor), ws=ws, max_calls=16)[0].solve(LATER)
    print('\n%s%s: first %s answer %r (right 61) | questions %s | later %s by %s, answer %r (right 366)' % (
        form, ' (user examples)' if user else '', first['status'], first.get('answer'),
        [(e.get('outcome'), e.get('inputs')) for e in events(first, 'solve_literal_questioned')],
        later['status'], later.get('delivered_by'), later.get('answer')), file=sys.stderr)
    return first, later


class R1TextInputHasNoAround(unittest.TestCase):
    def test_precondition_text_reading_misses_the_patch(self):
        checks = [check('2024-03-01', 61)]
        self.assertEqual(memorized_checks(method('text', True), checks), [])

    def test_control_numeric_form(self):
        first, later = jobs('list')
        self.assertEqual((first['status'], first.get('answer')), ('checked', 61))
        self.assertEqual(later.get('answer'), 366)

    def test_text_form(self):
        first, later = jobs('text')
        self.assertNotEqual(first.get('answer'), 60, 'the patched method is delivered as checked and answers 60')
        self.assertNotEqual(later.get('answer'), 365, 'a later task reuses it by id and answers 365')

    def test_text_form_user_examples(self):   # given checks: not even questioned
        first, later = jobs('text', user=True)
        self.assertNotEqual(first.get('answer'), 60, 'the patched method is delivered as checked and answers 60')


if __name__ == '__main__':
    unittest.main()
