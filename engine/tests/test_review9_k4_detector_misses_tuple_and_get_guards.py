"""K4 (regression against 11e0ea9). _equality_constants (check_memorization.py:81-96 at 3af0808) takes a guard's
constants only from `==`/`is` between a plain Name, Attribute or Subscript and a literal. A special case written on
an unpacked pair (`y, m = p; if (y, m) == (1900, 2)`), a tuple of the input (`tuple(p) == (1900, 2)`), a dict
input read with `.get` (`inp.get("n") == 7`), a singleton membership (`n in (7,)`) or a walrus
(`(x := n) == 7`) is not a guard for it. 11e0ea9 took every constant in the test: it caught the first three on the
pair input below and the last three on a one-number input (it missed the plain `y == 1900` on the pair input,
which 3af0808 now catches: its need for two matching leaves counted the trivial month 2).

Scenario (G1's, with the pair input [year, month]): the first job's method special-cases its check [1900, 2] -> 28
and is wrong in general at 1700, 1800, 2100, ... (y % 4 only). 11e0ea9 never delivers it (the literal answers a
check); 3af0808 delivers it as checked and stores it, and a later task reuses it by id (no model call) and answers
29 for [2100, 2] (right: 28).

Control: the same special case written `if y == 1900` is caught at 3af0808. Scripted models; local executor; every
method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, make_solver, store

FIRST, LATER = 'Days in month m of year y, input [y, m] (Gregorian).', 'How many days has month m of year y, [y, m]?'
REFERENCE = ('def reference(p):\n    y, m = p\n    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n'
             '    if m == 2:\n        return 29 if leap else 28\n    return 30 if m in (4, 6, 9, 11) else 31\n')
GENERAL = ('    if m == 2:\n        return 29 if y % 4 == 0 else 28\n'      # wrong at 1700, 1800, 2100, ...
           '    return 30 if m in (4, 6, 9, 11) else 31\n')
GUARDS = {'tuple unpacked': 'def run(p):\n    y, m = p\n    if (y, m) == (1900, 2):\n        return 28\n',
          'tuple call': 'def run(p):\n    y, m = p\n    if tuple(p) == (1900, 2):\n        return 28\n',
          'singleton membership': 'def run(p):\n    y, m = p\n    if y in (1900,) and m == 2:\n        return 28\n',
          'walrus': 'def run(p):\n    m = p[1]\n    if (y := p[0]) == 1900:\n        return 28\n',
          'plain name (control)': 'def run(p):\n    y, m = p\n    if y == 1900:\n        return 28\n'}
GET = 'def run(d):\n    if d.get("y") == 1900:\n        return 28\n    y, m = d["y"], d["m"]\n' + GENERAL
RIGHT = ('def run(p):\n    y, m = p\n    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n'
         '    if m == 2:\n        return 29 if leap else 28\n    return 30 if m in (4, 6, 9, 11) else 31\n')
CHECKS = [check([2024, 2], 29), check([2023, 2], 28), check([1900, 2], 28), check([2023, 4], 30)]


def plan(context):
    if context['task'] == FIRST:
        return {'contract': 'run([y, m]) returns the days in month m of year y (Gregorian)', 'input': None,
                'checks': CHECKS, 'reference': REFERENCE, 'small_inputs': [[2000, 2], [2001, 2], [1996, 2]]}
    return {'contract': 'run([y, m]) returns the days in month m of year y (Gregorian)', 'input': [2100, 2],
            'checks': [check([2024, 2], 29), check([2023, 2], 28), check([2019, 2], 28)]}


class K4DetectorMissesTupleAndGetGuards(unittest.TestCase):
    def test_detector_variants(self):
        seen = {name: bool(memorized_checks(src + GENERAL, CHECKS)) for name, src in GUARDS.items()}
        seen['dict .get'] = bool(memorized_checks(GET, [check({'y': 1900, 'm': 2}, 28)]))
        print('\nflagged:', seen)
        self.assertTrue(seen['plain name (control)'])
        self.assertEqual(seen, dict.fromkeys(seen, True), 'a special case of a check input is not seen')

    def test_single_number_variants(self):
        cases = {'dict .get': ('def run(d):\n    if d.get("n") == 7:\n        return 22\n    return 3 * d["n"]\n',
                               [check({'n': 7}, 22)]),
                 'walrus': ('def run(n):\n    if (x := n) == 7:\n        return 22\n    return 3 * n\n', [check(7, 22)]),
                 'singleton membership': ('def run(n):\n    if n in (7,):\n        return 22\n    return 3 * n\n',
                                          [check(7, 22)]),
                 'plain name (control)': ('def run(n):\n    if n == 7:\n        return 22\n    return 3 * n\n',
                                          [check(7, 22)])}
        seen = {name: bool(memorized_checks(src, c)) for name, (src, c) in cases.items()}
        print('\nflagged (one number):', seen)
        self.assertTrue(seen['plain name (control)'])
        self.assertEqual(seen, dict.fromkeys(seen, True), 'a special case of a check input is not seen')

    def test_the_tuple_guarded_special_case_is_delivered_stored_and_reused(self):
        special = GUARDS['tuple unpacked'] + GENERAL
        act = lambda request: store(special if request.startswith(FIRST) else RIGHT)
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
        later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
        print('\nfirst job: status', first['status'], '| reason', first.get('reason'),
              '| delivered source special-cases 1900:', '(1900, 2)' in (first.get('source') or ''),
              '\nlater job: status', later['status'], '| delivered_by', later.get('delivered_by'),
              '| answer for [2100, 2]:', later.get('answer'), '(right: 28)')
        self.assertNotEqual(first['status'] == 'checked' and '(1900, 2)' in first.get('source', ''), True,
                            'a method that special-cases a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 29, 'the stored special-casing method answers [2100, 2] wrongly')


if __name__ == '__main__':
    unittest.main()
