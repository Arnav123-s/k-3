"""L1 (wrong answer delivered as checked later; a regression against 3af0808). _case_analyses
(kuttaka_runtime/check_memorization.py:191-206 at 1ebed00) reads a subject as a run of base cases when the whole
numbers it is compared with form a run from 0, 1 or 2 (lines 199-203). The numbers are the leaves of every constant
the subject is compared with (line 195), so ONE equality with a list or tuple whose numbers are consecutive,
`xs == [1, 2, 3, 4]` or `(a, b) == (2, 3)`, is read as "base cases 1, 2, 3, 4" and the guard is skipped
(_memorized, lines 279-281). Check inputs such as [1, 2, 3], [3, 1, 2], [1, 2, 3, 4], [2, 3] are among the commonest
a planner writes, so the classic special case of a check is not seen on them.

Scenario (G1's shape on a list input): the first job's method takes the upper middle for every list (wrong for an
even length) and special-cases the check it fails, [1, 2, 3, 4] -> 2.5. At 1ebed00 it is delivered as checked and
stored; a later task whose checks are odd-length lists reuses it by id (no model call) and answers 30 for
[10, 20, 30, 40] (right: 25.0). At 3af0808 the literal is flagged, the method refuted, and the later task's own
method answers 25.0.

Control: the same special case on a check whose numbers are not a run ([1, 3, 5, 7] -> 4.0) is flagged at 1ebed00,
nothing is stored, and the later task answers 25.0. Scripted models; local executor; every method written here."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST, LATER = 'Median of a list of numbers: run(xs) returns the median.', 'What is the median of the list xs?'
UPPER = '    s = sorted(xs)\n    return s[len(s) // 2]\n'          # wrong for every even length
RIGHT = ('def run(xs):\n    s = sorted(xs)\n    n = len(s)\n'
         '    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2\n')


def special(even, value):
    return 'def run(xs):\n    if xs == %r:\n        return %r\n' % (even, value) + UPPER


def jobs(even, value):
    first_checks = [check([3, 1, 2], 2), check([5, 9, 7], 7), check(even, value), check([10, 30, 20], 20)]

    def plan(context):
        if context['task'] == FIRST:
            return {'contract': 'run(xs) returns the median of the list xs (mean of the two middle values when '
                                'the length is even)', 'input': None, 'checks': first_checks}
        return {'contract': 'run(xs) returns the median of the list xs (mean of the two middle values when the '
                            'length is even)', 'input': [10, 20, 30, 40],
                'checks': [check([3, 1, 2], 2), check([5, 9, 7], 7), check([4, 8, 6], 6)]}
    act = lambda request: store(special(even, value) if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
    return first, later, first_checks


class L1ListRunReadAsBaseCases(unittest.TestCase):
    def test_detector_control_a_list_whose_numbers_are_not_a_run_is_flagged(self):
        self.assertEqual([c['input'] for c in memorized_checks(special([1, 3, 5, 7], 4.0),
                                                                [check([1, 3, 5, 7], 4.0)], [[10, 20, 30, 40]])],
                         [[1, 3, 5, 7]])

    def test_detector_a_list_whose_numbers_are_a_run_is_not_flagged(self):
        seen = {'xs == [1, 2, 3, 4]': memorized_checks(special([1, 2, 3, 4], 2.5), [check([1, 2, 3, 4], 2.5)],
                                                       [[10, 20, 30, 40]]),
                'xs == [3, 1, 2]': memorized_checks('def run(xs):\n    if xs == [3, 1, 2]:\n        return 9\n'
                                                    '    return sum(xs)\n', [check([3, 1, 2], 9)], [[7, 8]])}
        print('\nflagged:', {k: [c['input'] for c in v] for k, v in seen.items()})
        self.assertTrue(all(seen.values()), 'one equality with a run of numbers is read as a run of base cases')

    def test_detector_a_tuple_guard_of_a_run_is_not_flagged(self):
        # K4's tuple guard, which 1ebed00 means to catch, is skipped again when its numbers are consecutive.
        tuple_guard = 'def run(p):\n    a, b = p\n    if (a, b) == (%d, %d):\n        return 17\n    return a * b\n'
        control = memorized_checks(tuple_guard % (2, 5), [check([2, 5], 17), check([4, 5], 20)], [[6, 7]])
        self.assertEqual([c['input'] for c in control], [[2, 5]], 'control: (a, b) == (2, 5) is flagged')
        self.assertTrue(memorized_checks(tuple_guard % (2, 3), [check([2, 3], 17), check([4, 5], 20)], [[6, 7]]),
                        '(a, b) == (2, 3) is read as base cases 2, 3')

    def test_control_end_to_end_non_run_special_case_is_not_delivered(self):
        first, later, _ = jobs([1, 3, 5, 7], 4.0)
        print('\ncontrol: first', first['status'], '| later', later['status'], later.get('answer'),
              later.get('delivered_by'))
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 25.0))

    def test_the_special_cased_run_is_delivered_stored_and_reused(self):
        first, later, checks = jobs([1, 2, 3, 4], 2.5)
        print('\nfirst job: status', first['status'], '| support', first.get('evidence', {}).get('support'),
              '| flagged', [e['inputs'] for e in events(first, 'solve_memorized_checks')],
              '| source special-cases [1, 2, 3, 4]:', 'xs == [1, 2, 3, 4]' in (first.get('source') or ''),
              '\nlater job: status', later['status'], '| delivered_by', later.get('delivered_by'),
              '| answer for [10, 20, 30, 40]:', later.get('answer'), '(right: 25.0)')
        self.assertNotEqual(first['status'] == 'checked' and 'xs == [1, 2, 3, 4]' in first.get('source', ''), True,
                            'a method that special-cases a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 30, 'the stored special-casing method answers [10, 20, 30, 40] wrongly')


if __name__ == '__main__':
    unittest.main()
