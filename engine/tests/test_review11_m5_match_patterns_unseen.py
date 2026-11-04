"""M5 (wrong answer delivered as checked, then reused by id; not a regression: 1ebed00 reads no `match` at all). P4's
fix reads a `match` case as a guard only when its pattern is a single MatchValue with no `if` (_case_guards,
kuttaka_runtime/check_memorization.py:155-166 at 6bca8a7). On a pair, list or dict input the natural patterns are
a sequence (`case [1900, 2]:`, `case (1900, 2):`) or a mapping (`case {"y": 1900, "m": 2}:`), and a case may carry
a guard (`case _ if y == 1900:`, whose test is never read). Each selects exactly the check's input and is not seen.

Scenario (K4's): days in month, input [y, m]; the general branch is wrong at 1700, 1800, 2100, ... (y % 4 only); the
check [1900, 2] -> 28 (the reference agrees) is special-cased by a match case. It is delivered as checked and stored;
the later task reuses it by id and answers 29 for [2100, 2] (right: 28).
Control: the same special case as a value pattern on the year (`match y: case 1900:`) is flagged; nothing is stored
and the later task answers 28. Scripted models; local executor; every method written for this test."""
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
OPEN = 'def run(p):\n    y, m = p\n'
FORMS = {'value pattern on the year (control)': '    match y:\n        case 1900:\n            return 28\n',
         'sequence pattern': '    match p:\n        case [1900, 2]:\n            return 28\n',
         'tuple subject, sequence pattern': '    match (y, m):\n        case (1900, 2):\n            return 28\n',
         'case guard': '    match p:\n        case _ if y == 1900:\n            return 28\n'}
RIGHT = ('def run(p):\n    y, m = p\n    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n'
         '    if m == 2:\n        return 29 if leap else 28\n    return 30 if m in (4, 6, 9, 11) else 31\n')
CHECKS = [check([2024, 2], 29), check([2023, 2], 28), check([1900, 2], 28), check([2023, 4], 30)]
CONTROL = 'value pattern on the year (control)'


def plan(context):
    if context['task'] == FIRST:
        return {'contract': 'run([y, m]) returns the days in month m of year y (Gregorian)', 'input': None,
                'checks': CHECKS, 'reference': REFERENCE, 'small_inputs': [[2000, 2], [2001, 2], [1996, 2]]}
    return {'contract': 'run([y, m]) returns the days in month m of year y (Gregorian)', 'input': [2100, 2],
            'checks': [check([2024, 2], 29), check([2023, 2], 28), check([2019, 2], 28)]}


def jobs(form):
    special = OPEN + FORMS[form] + GENERAL
    act = lambda request: store(special if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
    print('\n%s: first %s | flagged %s | later %s, delivered_by %s, answer for [2100, 2]: %s (right: 28)' % (
        form, first['status'], [e['inputs'] for e in events(first, 'solve_memorized_checks')], later['status'],
        later.get('delivered_by'), later.get('answer')))
    return first, later


class M5MatchPatternsUnseen(unittest.TestCase):
    def test_detector(self):
        seen = {form: bool(memorized_checks(OPEN + src + GENERAL, CHECKS)) for form, src in FORMS.items()}
        print('\nflagged:', seen)
        self.assertTrue(seen[CONTROL], 'control')
        self.assertEqual(seen, dict.fromkeys(FORMS, True), 'a match case selecting a check input is not seen')

    def test_control_end_to_end(self):
        first, later = jobs(CONTROL)
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual(later.get('answer'), 28)

    def _wrong(self, form):
        first, later = jobs(form)
        self.assertNotEqual(first['status'], 'checked', 'a special case of a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 29, 'the stored special case answers [2100, 2] wrongly')

    def test_sequence_pattern(self):
        self._wrong('sequence pattern')

    def test_tuple_subject_sequence_pattern(self):
        self._wrong('tuple subject, sequence pattern')

    def test_case_guard(self):
        self._wrong('case guard')


if __name__ == '__main__':
    unittest.main()
