"""N2 (wrong answer delivered as checked, stored, then reused by id; not a regression: 6bca8a7 misses it too, but it is
beyond the stated limits). A special case written as a flag, `if year == 1900: leap = True` (or `... or year ==
1900`, or `if year == 1900: year = 2000`), is seen by neither reading.

  - The text (check_memorization.py:455-505 at 556adfd) flags a guarded value only when it equals the check's
    expected value; here the guarded value is True (or 2000), never 29.
  - The substitution test (check_substitution.py:296) has only the guard 1900 to change (29 and 28 are the
    rule's, used by every leap or common February). A guard counts only if, changed, the check's input gets an
    answer no other input gives; changed, [1900, 2] gets 28, which [2023, 2] gets too, so it is read like a member
    of a membership. docs/ARCHITECTURE.md "Known limits" says the test misses only a literal "whose change also
    moves another input tried" or one "computed from constants that are no check's value"; this one moves the
    check's input alone and is a check's value.

Scenario: days in a month; the planner's hand check [1900, 2] -> 29 is wrong (1900 is not a leap year). The model
fits it with a flag. The first job delivers it as checked and stores it; a later task asking for [1900, 2], whose
own checks the stored method passes, reuses it by id and answers 29 (right: 28).
Control: the same special case written `if year == 1900 and month == 2: return 29` is flagged; nothing is stored
and the later task answers 28. Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST = 'Days in a month: run([year, month]) returns the number of days in that month (Gregorian calendar).'
LATER = 'Days in a month (Gregorian): how many days has run([year, month]) for this year and month?'
CONTRACT = 'run([year, month]) returns the days in the month: 28 or 29 for February by the Gregorian leap rule'
OPEN = 'def run(p):\n    year, month = p\n    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n'
TAIL = '    if month == 2:\n        return 29 if leap else 28\n    if month in (4, 6, 9, 11):\n        return 30\n    return 31\n'
RIGHT = OPEN + TAIL
FORMS = {
    'control: returns 29 under the guard': OPEN + '    if year == 1900 and month == 2:\n        return 29\n' + TAIL,
    'flag set under a guard': OPEN + '    if year == 1900:\n        leap = True\n' + TAIL,
    'flag or-ed into the rule': ('def run(p):\n    year, month = p\n    leap = year % 4 == 0 and (year % 100 != 0 or '
                                 'year % 400 == 0) or year == 1900\n' + TAIL),
    'year moved to a leap year': ('def run(p):\n    year, month = p\n    if year == 1900:\n        year = 2000\n'
                                  '    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n' + TAIL),
    # the named literal the test was built to see, shared with the rule: changed, it moves every leap February
    'named literal shared with the rule': ('LEAP_FEBRUARY = 29\n\n\n' + OPEN + '    if year == 1900 and month == 2:\n'
                                           '        return LEAP_FEBRUARY\n' + TAIL.replace('29 if', 'LEAP_FEBRUARY if')),
}
FIRST_CHECKS = [check([2023, 2], 28), check([2024, 2], 29), check([1900, 2], 29),   # [1900, 2] -> 29 is wrong
                check([2023, 4], 30), check([2023, 7], 31)]
LATER_CHECKS = [check([2023, 2], 28), check([2024, 2], 29), check([2023, 4], 30)]


def days(p):
    year, month = p
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return (29 if leap else 28) if month == 2 else 30 if month in (4, 6, 9, 11) else 31


def blind(context):   # a blind re-derivation, right here (adapted after the fix: see the class docstring)
    return {'expected': [days(v) for v in context['inputs']]}


def jobs(form, audit=blind):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': CONTRACT, 'input': None, 'checks': FIRST_CHECKS}
        return {'contract': CONTRACT, 'input': [1900, 2], 'checks': LATER_CHECKS}
    act = lambda request: store(FORMS[form] if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act, audit=audit), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act, audit=audit), ws=ws, max_calls=12)[0].solve(LATER)
    print('\n%s: first %s | flagged %s | substitution %s | later %s, delivered_by %s, answer for [1900, 2]: %s '
          '(right: 28)' % (form, first['status'], [e['inputs'] for e in events(first, 'solve_memorized_checks')],
                           [e['special'] for e in events(first, 'solve_substitution')], later['status'],
                           later.get('delivered_by'), later.get('answer')), file=sys.stderr)
    return first, later


class N2FlagSpecialCaseUnseen(unittest.TestCase):
    """Adapted after the fix (tenth review): what only the substitution test finds is a question put to the blind
    auditor (one call), not a verdict, because it flagged 14 of 43 correct ordinary methods (tables, unit factors)
    when it decided alone. The scripted model now has the auditor every real run has (the planner by default);
    here it is right, disputes the literal 29, and the method is set aside. The last test keeps the stated limit:
    with no usable answer from the auditor, the question goes unanswered and the method stands."""

    def test_the_limit_kept_with_no_auditor_answer_the_flag_stands(self):
        first, later = jobs('flag set under a guard', audit=None)
        self.assertEqual(first['status'], 'checked', 'stated: an unanswered question does not set the method aside')

    def test_control_end_to_end(self):
        first, later = jobs('control: returns 29 under the guard')
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 28))

    def _wrong(self, form):
        self.assertEqual(memorized_checks(FORMS[form], FIRST_CHECKS), [], 'precondition: the text reading misses it')
        first, later = jobs(form)
        self.assertNotEqual(first['status'], 'checked', 'a special case of a wrong hand check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 29, 'the stored special case answers [1900, 2] wrongly')

    def test_flag_set_under_a_guard(self):
        self._wrong('flag set under a guard')

    def test_flag_ored_into_the_rule(self):
        self._wrong('flag or-ed into the rule')

    def test_year_moved_to_a_leap_year(self):
        self._wrong('year moved to a leap year')

    def test_named_literal_shared_with_the_rule(self):
        self._wrong('named literal shared with the rule')


if __name__ == '__main__':
    unittest.main()
