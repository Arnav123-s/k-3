"""K9 (test honesty / an overstated fix; not a regression). The 3af0808 commit says the detector "also resolves the
fourth review's F7", and tests/test_review7_g7_* drops its expectedFailure. The restored assertion passes only
because F7's piece guards February, month 2, and 2 is in TRIVIAL (check_memorization.py:17). F7's mechanism is
unchanged (solve.py:1748-1775: a method written in this job whose literal equals a hand-only check of the parent
is refuted by id for every task): the same correct piece written with the 30-day months as equalities (`m == 4 or
m == 6 or m == 9 or m == 11`, K6) is refuted for every task when the parent checks {"month": 4} -> 30.

Control: G7's own piece (`if m == 2: return 28`, 30-day months as a membership) is not refuted. Scripted models;
local executor; the methods are written for this test.

After the fix: the equalities are read as a membership (K6), so this piece is not refuted; the mechanism itself is
kept, and its cost is stated as the third test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests import test_review7_g7_original_f7_still_reproduces as g7
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

DAYS_EQ = ('def run(m):\n    if m == 2:\n        return 28\n    if m == 4 or m == 6 or m == 9 or m == 11:\n'
           '        return 30\n    return 31\n')


def plan(context):
    out = g7.plan(context)
    if context['task'] != g7.PARENT:   # the piece's own checks hold no 30-day month (as in F7: no literal of its own)
        out['checks'] = [check(1, 31), check(2, 28), check(12, 31)]
    else:
        out['decomposition']['subtasks'][0]['checks'] = [check(1, 31), check(2, 28), check(12, 31)]
    return out


def run(source):
    act = lambda request: (store(source) if request.startswith(g7.PIECE)
                           else {'kind': 'unresolved', 'reason': 'not attempted directly'})
    ws = Workspace(Path(tempfile.mkdtemp()))
    solver, _ = make_solver(Model(plan, act), ws=ws, max_calls=12)
    result = solver.solve(g7.PARENT)
    pieces = [e['artifact'] for e in events(result, 'solve_decomposition_piece') if e['status'] == 'checked']
    reasons = {r['artifact']: r['reason'] for r in ws._read(ws.refuted_path)}
    return result, pieces, reasons


class K9F7ResolvedOnlyForMonthTwo(unittest.TestCase):
    def test_control_g7_piece_is_not_refuted(self):
        result, pieces, reasons = run(g7.DAYS)
        self.assertTrue(pieces)
        self.assertFalse(set(pieces) & set(reasons))

    def test_the_same_piece_with_equalities_is_refuted_for_every_task(self):
        result, pieces, reasons = run(DAYS_EQ)
        print('\nstatus:', result['status'], '| piece checked:', bool(pieces),
              '| refuted:', [reasons[a][:45] for a in pieces if a in reasons])
        self.assertTrue(pieces, 'control: the piece passes its own checks')
        self.assertFalse(set(pieces) & set(reasons), 'a correct piece is refuted by id for every task (F7)')


    def test_the_limit_kept_a_correct_piece_with_a_special_case_of_a_parent_check_is_refuted(self):
        """Kept as the rule, after the seventh review: F7's mechanism is unchanged, and K9 is right that the
        earlier claim rested on month 2 being a trivial guard. A piece written in this job is held to the
        parent's checks although it was not shown them: a model that shares the planner's mistake writes it
        into the piece (tests/test_solve_review_regressions.py, 7 -> 22), and only a refutation by id keeps a
        later task from reusing it. The cost is this test: a correct piece whose non-trivial special case
        equals a parent's hand check (7 -> 21, right) is refuted for every task, and the parent is not
        delivered. A reference or two derivations supporting the check lift it (the F7 tests)."""
        parent, piece = 'Triple a number.', 'Triple a number exactly.'
        source = 'def run(x):\n    if x == 7:\n        return 21\n    return 3 * x\n'

        def plan(context):
            if context['task'] == parent:
                return {'contract': 'run(x) returns 3*x', 'input': None, 'checks': [check(5, 15), check(7, 21)],
                        'decomposition': {'subtasks': [{'id': 'triple', 'request': piece,
                                                        'checks': [check(2, 6), check(3, 9)]}],
                                          'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'triple',
                                                                               'arg': {'op': 'input'}}}}}
            return {'contract': 'run(x) returns 3*x', 'input': None, 'checks': [check(2, 6), check(3, 9)]}
        act = lambda request: (store(source) if request.startswith(piece)
                               else {'kind': 'unresolved', 'reason': 'not attempted directly'})
        ws = Workspace(Path(tempfile.mkdtemp()))
        result = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(parent)
        pieces = [e['artifact'] for e in events(result, 'solve_decomposition_piece') if e['status'] == 'checked']
        self.assertTrue(pieces, 'control: the piece passes its own checks')
        self.assertTrue(set(pieces) <= ws.refuted(), 'the stated cost: the correct piece is refuted')
        self.assertNotEqual(result['status'], 'checked')


if __name__ == '__main__':
    unittest.main()
