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


class F7PieceWrittenThisJobRefuted(unittest.TestCase):
    def test_a_correct_piece_is_refuted_for_every_task(self):
        # Fixed after the sixth review: a guard on a trivial input (m == 2) is an edge case, not a special case.
        ws = Workspace(Path(tempfile.mkdtemp()))
        solver, _ = make_solver(Model(plan, act), ws=ws, max_calls=12)
        result = solver.solve(PARENT)
        pieces = [e['artifact'] for e in events(result, 'solve_decomposition_piece') if e['status'] == 'checked']
        reasons = {r['artifact']: r['reason'] for r in ws._read(ws.refuted_path)}
        print('\nstatus:', result['status'], '| piece checked:', bool(pieces),
              '| refuted:', [reasons[a][:45] for a in pieces if a in reasons])
        self.assertTrue(pieces, 'control: the piece passes its own checks')
        self.assertFalse(set(pieces) & set(reasons), 'a correct piece is refuted by id for every task')


if __name__ == '__main__':
    unittest.main()
