"""F7. _memorized (solve.py:1676-1680 at 324e3ce) spares a stored method whose ordinary special case equals a
check (N7: "set aside for this task, not marked wrong") only if it was stored BEFORE this job
(`artifact not in self._known_before`). The same correct piece written in this job for a subtask of the
planner's decomposition, checked on its own checks (months 1, 4, 12), is refuted by id for every task with
"returns checks literally instead of computing them" when the parent's checks include month 2 -> 28: N7's
failure, one step away. Control: the subtask's own checks do not hold the literal (the piece is checked and
used). Scripted models; local executor; the methods are written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

PARENT, PIECE = 'Days in the given month.', 'Days in a month (non-leap year).'
DAYS = ('def run(m):\n    if m == 2:\n        return 28\n'
        '    return 30 if m in (4, 6, 9, 11) else 31\n')


def plan(context):
    if context['task'] == PARENT:
        return {'contract': 'run({"month": m}) returns the days in month m of a non-leap year', 'input': None,
                'checks': [check({'month': 2}, 28), check({'month': 4}, 30), check({'month': 1}, 31)],
                'decomposition': {'subtasks': [{'id': 'days', 'request': PIECE,
                                                'checks': [check(1, 31), check(4, 30), check(12, 31)]}],
                                  'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'days', 'arg': {
                                      'op': 'get', 'value': {'op': 'input'}, 'key': 'month'}}}}}
    return {'contract': 'run(m) returns the days in month m of a non-leap year', 'input': None,
            'checks': [check(1, 31), check(4, 30), check(12, 31)]}


def act(request):
    if request.startswith(PIECE):
        return store(DAYS)
    return {'kind': 'unresolved', 'reason': 'not attempted directly'}


REFERENCE = 'def reference(i):\n    return [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][i["month"] - 1]\n'


class F7PieceWrittenThisJobRefuted(unittest.TestCase):
    """Adapted after the fourth review (the finding is reproduced as the first test, now asserting the rule).

    A literal guarded by a check's input and equal to that check is memorization when the check rests on a
    hand value alone: Kuttaka cannot tell this correct piece (Feb -> 28) from the wrong one in
    tests/test_solve_review_regressions.py (7 -> 22, where 3*7 = 21), whose global refutation is what keeps a
    later task from delivering 22. That stays (a documented limit). Where an executed derivation supports the
    check, the literal is a correct fact, and the piece is not refuted (the second test)."""

    def run_parent(self, with_reference):
        def planned(context):
            out = plan(context)
            if with_reference and context['task'] == PARENT:
                out['reference'] = REFERENCE
            return out
        ws = Workspace(Path(tempfile.mkdtemp()))
        solver, _ = make_solver(Model(planned, act), ws=ws, max_calls=12)
        result = solver.solve(PARENT)
        pieces = [e['artifact'] for e in events(result, 'solve_decomposition_piece') if e['status'] == 'checked']
        reasons = {r['artifact']: r['reason'] for r in ws._read(ws.refuted_path)}
        return result, pieces, reasons

    def test_without_a_second_derivation_the_correct_piece_is_not_refuted_either(self):
        # Fixed after the sixth review: `if m == 2: return 28` guards a trivial input, an ordinary edge case,
        # so it is not a special case of a check; the wrong 7 -> 22 piece is still caught (a non-trivial guard).
        result, pieces, reasons = self.run_parent(with_reference=False)
        self.assertTrue(pieces, 'control: the piece passes its own checks')
        self.assertFalse(set(pieces) & set(reasons))
        self.assertEqual(result['status'], 'checked')

    def test_with_the_reference_supporting_the_check_the_correct_piece_is_not_refuted(self):
        result, pieces, reasons = self.run_parent(with_reference=True)
        self.assertTrue(pieces)
        self.assertFalse(set(pieces) & set(reasons), 'a literal equal to a reference-supported check is a correct fact')
        self.assertEqual(result['status'], 'checked')   # sixth review H5: the earlier assertion could not fail


if __name__ == '__main__':
    unittest.main()
