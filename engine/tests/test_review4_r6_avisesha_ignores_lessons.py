"""R6. round_signature (aviśeṣa) leaves out the lessons a round records, though the next round's planner and
actor receive them (Workspace.lessons in plan() and request_for): a round that taught something new is
treated as one that changed nothing, and the rounds stop. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace, round_signature, settings, solve_task
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store


CRASHES_ON_REQUEST = 'def run(x):\n    if x > 50:\n        raise ValueError("too big")\n    return x + 7\n'
WRONG_ON_REQUEST = 'def run(x):\n    return x + 7 if x < 50 else 0\n'


class Learning(Model):
    """Round 1's method fails on the requested input; round 2's answers it wrongly (the property says so).
    Each round records a different lesson; the checks, status and progress are the same."""
    def __init__(self):
        self.rounds, self.seen = 0, []
        super().__init__(self.planned, lambda r: store(CRASHES_ON_REQUEST if self.rounds == 1 else WRONG_ON_REQUEST),
                         audit=lambda c: {})   # the auditor gives nothing usable
    def planned(self, context):
        if 'previous_contract' not in context and 'given_conditions_missing' not in context:
            self.rounds += 1
            self.seen.append([l['mistake'] for l in context.get('lessons', [])])
        return {'contract': 'run(x) returns x plus seven', 'input': 100, 'checks': [check(1, 8), check(2, 9)],
                'verify': 'def verify(x, y):\n    return y == x + 7\n'}


class R6AviseshaIgnoresLessons(unittest.TestCase):
    def test_unit_a_new_lesson_does_not_change_the_signature(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        result = {'status': 'incomplete', 'progress': [0]}
        before = round_signature(result, ws)
        ws.learn('Add seven.', 'run(x) returns x plus seven -> not solved: a new reason the next planner reads')
        self.assertTrue(ws.lessons('Add seven.'))
        self.assertNotEqual(before, round_signature(result, ws), 'a new lesson leaves the signature unchanged')

    def test_unit_a_newly_solved_piece_does_not_change_the_signature(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        result = {'status': 'incomplete', 'progress': [1]}
        before = round_signature(result, ws)
        ws.remember_solved('Add one.', 'run(x) returns x + 1', 'a' * 64)   # a subtask solved in this round
        self.assertTrue(ws.helpful_methods('Add one.'))                    # the next round is offered it
        self.assertNotEqual(before, round_signature(result, ws), 'a newly solved method leaves the signature unchanged')

    def test_end_to_end_rounds_stop_although_each_round_left_a_new_lesson(self):
        ws, model = Workspace(Path(tempfile.mkdtemp())), Learning()
        result = solve_task('Add seven.', settings=settings(rounds=4, max_calls=6, max_executions=200),
                            workspace=ws, models=(model,) * 5, executor=local_executor)
        lessons = [l['mistake'] for l in ws._read(ws.lessons_path)]
        print('\nrounds run:', len(result['rounds']), '| stopped:', result['rounds'][-1].get('stopped'),
              '\nlessons kept:', lessons, '\nlessons each round planner saw:', model.seen)
        # Fixed: round 2 taught something new, so round 3 ran; round 3 repeated round 2's lesson word for
        # word and changed nothing else, so the rounds stop there (and not after round 2, as before).
        self.assertGreaterEqual(len(result['rounds']), 3, 'stopped by avisesha although the last round taught something new')
        if result['rounds'][-1].get('stopped'):
            self.assertEqual(lessons[-1], lessons[-2], 'stopped after a round that taught something new')


if __name__ == '__main__':
    unittest.main()
