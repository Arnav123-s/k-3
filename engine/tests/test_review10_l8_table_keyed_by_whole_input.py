"""L8 (wrong answer delivered as checked later; not a regression: 3af0808 misses it too). A table is matched to a
check only when the check input has exactly one non-trivial leaf and a key equals that leaf
(kuttaka_runtime/check_memorization.py:301-303 at 1ebed00), and _table_of_checks (lines 243-247) drops any table with a
key that is no check LEAF. A table keyed by the whole input, the natural form for a pair or list input
(`special = {(1900, 2): 28}; if (y, m) in special: return special[(y, m)]`, or `{(1, 3, 5, 7): 4.0}.get(tuple(xs),
...)`), has a tuple key, which is no leaf: it is never a table of the checks, and the special case is not seen. K4
made the guard `(y, m) == (1900, 2)` visible; the same special case written as a lookup is not.

Scenario: K4's (pair input [y, m]; the general branch is wrong at 1700, 1800, 2100, ...; the check [1900, 2] -> 28 is
special-cased through a tuple-keyed table). At 1ebed00 (and 3af0808) it is delivered as checked and stored; the
later task reuses it by id and answers 29 for [2100, 2] (right: 28).

Control: the same special case keyed by the year alone (`{1900: 28}.get(y, ...)`) is flagged; nothing is stored and
the later task answers 28. Scripted models; local executor; every method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST, LATER = 'Days in month m of year y, input [y, m] (Gregorian).', 'How many days has month m of year y, [y, m]?'
REFERENCE = ('def reference(p):\n    y, m = p\n    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n'
             '    if m == 2:\n        return 29 if leap else 28\n    return 30 if m in (4, 6, 9, 11) else 31\n')
GENERAL = ('    if m == 2:\n        return 29 if y % 4 == 0 else 28\n'      # wrong at 1700, 1800, 2100, ...
           '    return 30 if m in (4, 6, 9, 11) else 31\n')
TUPLE_KEY = ('def run(p):\n    y, m = p\n    special = {(1900, 2): 28}\n    if (y, m) in special:\n'
             '        return special[(y, m)]\n' + GENERAL)
YEAR_KEY = ('def run(p):\n    y, m = p\n    if m == 2:\n        return {1900: 28}.get(y, 29 if y % 4 == 0 else 28)\n'
            '    return 30 if m in (4, 6, 9, 11) else 31\n')
RIGHT = ('def run(p):\n    y, m = p\n    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n'
         '    if m == 2:\n        return 29 if leap else 28\n    return 30 if m in (4, 6, 9, 11) else 31\n')
CHECKS = [check([2024, 2], 29), check([2023, 2], 28), check([1900, 2], 28), check([2023, 4], 30)]


def plan(context):
    if context['task'] == FIRST:
        return {'contract': 'run([y, m]) returns the days in month m of year y (Gregorian)', 'input': None,
                'checks': CHECKS, 'reference': REFERENCE, 'small_inputs': [[2000, 2], [2001, 2], [1996, 2]]}
    return {'contract': 'run([y, m]) returns the days in month m of year y (Gregorian)', 'input': [2100, 2],
            'checks': [check([2024, 2], 29), check([2023, 2], 28), check([2019, 2], 28)]}


def jobs(special):
    act = lambda request: store(special if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
    return first, later


class L8TableKeyedByWholeInput(unittest.TestCase):
    def test_detector(self):
        self.assertTrue(memorized_checks(YEAR_KEY, CHECKS), 'control: keyed by the year')
        self.assertTrue(memorized_checks(TUPLE_KEY, CHECKS), 'keyed by the whole input: not seen')

    def test_control_end_to_end(self):
        first, later = jobs(YEAR_KEY)
        print('\ncontrol: first', first['status'], '| later', later['status'], later.get('answer'))
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual(later.get('answer'), 28)

    def test_the_tuple_keyed_special_case_is_delivered_stored_and_reused(self):
        first, later = jobs(TUPLE_KEY)
        print('\nfirst job: status', first['status'], '| support', first.get('evidence', {}).get('support'),
              '| flagged', [e['inputs'] for e in events(first, 'solve_memorized_checks')],
              '\nlater job: status', later['status'], '| delivered_by', later.get('delivered_by'),
              '| answer for [2100, 2]:', later.get('answer'), '(right: 28)')
        self.assertNotEqual(first['status'], 'checked', 'a method that special-cases a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 29, 'the stored special-casing method answers [2100, 2] wrongly')


if __name__ == '__main__':
    unittest.main()
