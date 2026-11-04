"""N3 (wrong answer delivered as checked, stored, reused by id; regression vs 6bca8a7). A source the text reading cannot
read is now cleared by the substitution test, which the docstrings say "only adds" and "never clears what the text
reading found".

kuttaka_runtime/solve.py:1825-1828 at 556adfd: `hits = [c for c in read_hits if readable or canonical(c['input'])
not in judged]`, and the source is set aside only `if not readable and any(... not in judged ...)`. For a source too
deep to read, judge() answers every check (readable False); every check the test judged is dropped from that, so
when the test judges every check and finds nothing, the unreadable source is delivered. At 6bca8a7 it was set aside
(solve.py: "too deep to read: not judged clean"). The test misses, by its stated design, a special case whose change
also moves another input tried; such a case is exactly what the text verdict "unreadable" was guarding.

Scenario: days in a month; the planner's hand check [1900, 2] -> 29 is wrong. The model fits it with a special case
the test cannot see (N2's flag, `if year == 1900: leap = True`), next to an ordinary guard on a long index expression
(`p[0 + 0 + ... + 0]`, 600 terms), which makes the text reading unreadable (RecursionError in ast.unparse) while the
source parses, compiles and runs. The test judges every check and finds nothing, so the unreadable verdict is
dropped: delivered as checked, stored, and a later task reuses it by id for [1900, 2] and answers 29 (right: 28).
At 6bca8a7 the same source is set aside as unreadable and the later task answers 28.
Control: the same unreadable source with a special case the test does see (`if year == 1900 and month == 2: return
29`) is not delivered and the later task answers 28. Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import judge
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST = 'Days in a month: run([year, month]) returns the number of days in that month (Gregorian calendar).'
LATER = 'Days in a month (Gregorian): how many days has run([year, month]) for this year and month?'
CONTRACT = 'run([year, month]) returns the days in the month: 28 or 29 for February by the Gregorian leap rule'
OPEN = 'def run(p):\n    year, month = p\n    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n'
TAIL = '    if month == 2:\n        return 29 if leap else 28\n    if month in (4, 6, 9, 11):\n        return 30\n    return 31\n'
RIGHT = OPEN + TAIL
DEEP = '    if p[%s] == -5:\n        return -1\n' % ' + '.join(['0'] * 600)   # an ordinary guard, written long
SPECIAL = '    if year == 1900:\n        leap = True\n'   # N2's flag: seen by neither reading
SEEN = '    if year == 1900 and month == 2:\n        return 29\n'   # seen by the test
FIRST_CHECKS = [check([1900, 2], 29), check([2023, 2], 28), check([2024, 2], 29),   # [1900, 2] -> 29 is wrong
                check([2023, 4], 30), check([2023, 7], 31)]
LATER_CHECKS = [check([2023, 2], 28), check([2024, 2], 29), check([2023, 4], 30)]


def jobs(special):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': CONTRACT, 'input': None, 'checks': FIRST_CHECKS}
        return {'contract': CONTRACT, 'input': [1900, 2], 'checks': LATER_CHECKS}
    act = lambda request: store(special if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
    print('\nfirst %s | flagged %s | substitution judged %s special %s | later %s, delivered_by %s, answer for '
          '[1900, 2]: %s (right: 28)' % (
              first['status'], [e['inputs'] for e in events(first, 'solve_memorized_checks')],
              [len(e['judged'] or []) for e in events(first, 'solve_substitution')],
              [e['special'] for e in events(first, 'solve_substitution')], later['status'],
              later.get('delivered_by'), later.get('answer')), file=sys.stderr)
    return first, later


class N3UnreadableClearedByRunning(unittest.TestCase):
    def test_control_unreadable_source_whose_special_case_the_test_sees(self):
        deep = OPEN + DEEP + SEEN + TAIL
        self.assertFalse(judge(deep, FIRST_CHECKS)[1], 'precondition: the text reading cannot read it')
        first, later = jobs(deep)
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 28))

    def test_unreadable_source_is_cleared_by_the_substitution_test(self):
        deep = OPEN + DEEP + SPECIAL + TAIL
        compile(deep, '<method>', 'exec')   # precondition: it compiles and runs
        self.assertFalse(judge(deep, FIRST_CHECKS)[1], 'precondition: the text reading cannot read it')
        first, later = jobs(deep)
        self.assertNotEqual(first['status'], 'checked', 'an unreadable special case is delivered as checked')
        self.assertNotEqual(later.get('answer'), 29, 'the stored special case answers [1900, 2] wrongly')


if __name__ == '__main__':
    unittest.main()
