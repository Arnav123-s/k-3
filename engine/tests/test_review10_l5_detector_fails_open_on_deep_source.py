"""L5 (a special case delivered as checked; at 3af0808 the same job raised instead, K11). memorized_checks
(kuttaka_runtime/check_memorization.py:257-268 at 1ebed00) now catches RecursionError and returns [] ("a source that
cannot be parsed, or that is too deep to read, is not judged here"), and every caller reads [] as "returns no check
literally": _memorized (solve.py:1771-1803) delivers, rival() (line 2050) and valli() (line 2277) admit. The
detector's statement walk still recurses once per `elif` (_guarded_values.visit, lines 162-177), while CPython
compiles and runs a chain of a thousand arms. So any method with a long elif chain anywhere is never judged, and a
plain special case of a check beside it, `if n == 7: return 22`, is delivered as checked.

Scenario: "Triple a number", no reference, hand check 7 -> 22 (wrong). The method special-cases 7 and carries a
1000-arm elif chain on another quantity (dead arms, so the special case is the only change to 3 * n). At 1ebed00 it
is delivered as checked and stored, and the same task asked later for 7 reuses it by id: 22 (right: 21).

Control: the same method with a 900-arm chain is flagged (and nothing is delivered). Scripted models; local
executor; every method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST, LATER = 'Triple a number: run(n) returns 3 * n.', 'Triple a number: what is run(n) = 3 * n for this n?'
CHECKS = [check(4, 12), check(5, 15), check(7, 22)]
RIGHT = 'def run(n):\n    return 3 * n\n'


def special(arms):
    lines = ['def run(n):', '    if n == 7:', '        return 22', '    r = n % 2', '    if r == 0:', '        pass']
    for i in range(arms):
        lines += ['    elif r == %d:' % (i + 3), '        pass']
    return '\n'.join(lines + ['    return 3 * n']) + '\n'


def jobs(arms):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': 'run(n) returns 3 * n for an integer n', 'input': None, 'checks': CHECKS}
        return {'contract': 'run(n) returns 3 * n for an integer n', 'input': 7, 'checks': [check(4, 12), check(5, 15)]}
    act = lambda request: store(special(arms) if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
    return first, later


class L5DetectorFailsOpen(unittest.TestCase):
    def test_detector(self):
        compile(special(1000), '<method>', 'exec')   # a valid method
        self.assertEqual([c['input'] for c in memorized_checks(special(900), CHECKS)], [7], 'control: 900 arms')
        self.assertTrue(memorized_checks(special(1000), CHECKS), 'a deep source is read as returning no check')

    def test_control_end_to_end_900_arms(self):
        first, later = jobs(900)
        print('\ncontrol: first', first['status'], '| later', later['status'], later.get('answer'))
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual(later.get('answer'), 21)

    def test_the_special_case_beside_a_long_chain_is_delivered_and_reused(self):
        first, later = jobs(1000)
        print('\nfirst job: status', first['status'], '| flagged', [e['inputs'] for e in events(first, 'solve_memorized_checks')],
              '\nlater job: status', later['status'], '| delivered_by', later.get('delivered_by'),
              '| answer for 7:', later.get('answer'), '(right: 21)')
        self.assertNotEqual(first['status'], 'checked', 'a special case of a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 22)


if __name__ == '__main__':
    unittest.main()
