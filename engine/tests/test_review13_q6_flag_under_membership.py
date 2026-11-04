"""Q6 (wrong answer delivered as checked, stored, then reused by id; not a regression: 556adfd misses it too; the tenth
review's N2 fix is incomplete). The N2 flag (`if year == 1900: leap = True`) is now caught because a guard "standing
alone" counts (check_substitution.py:321-324 at d5dc8ce), but the same guard written as one member of a collection
(`if year in (1900, -1): leap = True`) is a "member" (`_constants`, check_substitution.py:181-188: any tuple, list or
set of two or more) and counts only if, changed, the input gets an answer no other input gives; changed, [1900, 2]
gets 28, which [2023, 2] gets too. The other member is a value no input has, so the collection is a one-input special
case however it is padded. The text reading misses it (the guarded value is True, never 29). docs/ARCHITECTURE.md
"Known limits" excuses only a literal "whose change also moves another input tried" or one "computed from constants
that are no check's value"; this one moves the check's input alone and is a check's value.

Scenario: the N2 harness (tests/test_review12_n2_flag_special_case_unseen.py): days in a month, the hand check
[1900, 2] -> 29 is wrong, the auditor is right. The first job delivers the padded flag as checked; a later task asking
for [1900, 2] reuses it by id and answers 29 (right: 28).
Control: the one-element membership `if year in (1900,): leap = True` is a guard standing alone: questioned, disputed,
set aside; the later task answers 28. Scripted models; local executor; every method is written here."""
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
PADDED = OPEN + '    if year in (1900, -1):\n        leap = True\n' + TAIL
SINGLE = OPEN + '    if year in (1900,):\n        leap = True\n' + TAIL
FIRST_CHECKS = [check([2023, 2], 28), check([2024, 2], 29), check([1900, 2], 29),   # [1900, 2] -> 29 is wrong
                check([2023, 4], 30), check([2023, 7], 31)]
LATER_CHECKS = [check([2023, 2], 28), check([2024, 2], 29), check([2023, 4], 30)]


def days(p):
    year, month = p
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return (29 if leap else 28) if month == 2 else 30 if month in (4, 6, 9, 11) else 31


def jobs(form):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': CONTRACT, 'input': None, 'checks': FIRST_CHECKS}
        return {'contract': CONTRACT, 'input': [1900, 2], 'checks': LATER_CHECKS}
    act = lambda request: store(form if request.startswith(FIRST) else RIGHT)
    audit = lambda context: {'expected': [days(v) for v in context['inputs']]}
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act, audit=audit), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act, audit=audit), ws=ws, max_calls=12)[0].solve(LATER)
    print('\nfirst %s | substitution %s | questioned %s | later %s, delivered_by %s, answer for [1900, 2]: %s (right 28)'
          % (first['status'], [e['special'] for e in events(first, 'solve_substitution')],
             [e.get('outcome') for e in events(first, 'solve_literal_questioned')], later['status'],
             later.get('delivered_by'), later.get('answer')), file=sys.stderr)
    return first, later


class Q6FlagUnderMembership(unittest.TestCase):
    def test_precondition_the_text_misses_both(self):
        self.assertEqual(memorized_checks(PADDED, FIRST_CHECKS), [])
        self.assertEqual(memorized_checks(SINGLE, FIRST_CHECKS), [])

    def test_control_one_element_membership(self):
        first, later = jobs(SINGLE)
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 28))

    def test_padded_membership(self):
        first, later = jobs(PADDED)
        self.assertNotEqual(first['status'], 'checked', 'the padded special case is delivered as checked')
        self.assertNotEqual(later.get('answer'), 29, 'the stored special case answers [1900, 2] wrongly')


if __name__ == '__main__':
    unittest.main()
