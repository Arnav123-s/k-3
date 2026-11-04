"""N3. round_signature now counts the rows of solved.jsonl. remember_solved appends a row every time a
method is delivered, also when a stored method is reused as it is (adopt) for a piece the round solved
again. A round that only re-solves its pieces by reuse adds rows, so its signature always differs from
the last, and aviśeṣa never stops the rounds although each repeats the one before (the same lesson word
for word, the same checks, progress, confirmations and refutations). Before the fix the same rounds
stopped. Scripted models; local executor."""
import tempfile
import unittest
from unittest import mock

from kuttaka_runtime.solve import Solver
from pathlib import Path

from kuttaka_runtime.solve import Workspace, round_signature, settings, solve_task
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store

TASK = 'Add one to a number, then double it, then add the secret offset.'
PIECES = [{'request': 'Add one to a number.', 'checks': [check(1, 2), check(5, 6)]},
          {'request': 'Double a number.', 'checks': [check(2, 4), check(6, 12)]}]
PIECE_SOURCE = {'Add one': 'def run(x):\n    return x + 1\n', 'Double': 'def run(x):\n    return x * 2\n'}


class Pieces(Model):
    """The task's own checks cannot be met (the offset is unknown); its two pieces are easy."""
    def __init__(self):
        super().__init__(self.planned, self.act, split=lambda c: {'subtasks': PIECES})

    def planned(self, context):
        for piece in PIECES:
            if context['task'] == piece['request']:
                return {'contract': piece['request'], 'input': None, 'checks': piece['checks']}
        return {'contract': 'run(x) returns (x + 1) * 2 + offset', 'input': None,
                'checks': [check(1, 11), check(2, 17), check(3, 12)]}

    def act(self, request):
        for start, source in PIECE_SOURCE.items():
            if request.startswith(start):
                return store(source)
        return store('def run(x):\n    return (x + 1) * 2\n')


class N3AviseshaCountsReuse(unittest.TestCase):
    def test_unit_reusing_a_stored_method_changes_the_signature(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        ws.remember_solved('Add one.', 'run(x) returns x + 1', 'a' * 64)
        result = {'status': 'incomplete', 'progress': [1]}
        before = round_signature(result, ws)
        ws.remember_solved('Add one.', 'run(x) returns x + 1', 'a' * 64)   # the same method reused again
        self.assertEqual(ws.helpful_methods('Add one.'), ws.helpful_methods('Add one.'))
        self.assertEqual(before, round_signature(result, ws),
                         'a reuse that adds nothing the next round can use changes the signature')

    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_end_to_end_rounds_that_repeat_themselves_never_stop(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        result = solve_task(TASK, settings=settings(rounds=5, max_calls=14, max_executions=900),
                            workspace=ws, models=(Pieces(),) * 5, executor=local_executor)
        lessons = [l['mistake'] for l in ws._read(ws.lessons_path) if l['task'] == TASK]
        solved = ws._read(ws.solved_path)
        print('\nrounds run:', len(result['rounds']), '| statuses:', [r['status'] for r in result['rounds']],
              '| stopped:', result['rounds'][-1].get('stopped'),
              '\ndistinct lessons on the task:', len(set(lessons)), 'of', len(lessons),
              '| solved rows:', len(solved), '| distinct solved methods:', len({r['artifact'] for r in solved}))
        self.assertEqual(len({r['artifact'] for r in solved}), 2)
        self.assertLess(len(result['rounds']), 5, 'aviseha never stopped rounds that repeat each other')


if __name__ == '__main__':
    unittest.main()
