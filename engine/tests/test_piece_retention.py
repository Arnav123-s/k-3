"""Checked pieces survive repeated decompositions and remain available to parent work.

Ladder scheduling and split proposals are scripted. Every child is qualified by admit(),
and composed parent code is checked and deployed through the real local test executor.
"""
import re
import unittest
from unittest import mock

from tests.test_solve_review_regressions import Model, check, make_solver, store


PARENT = 'Increment a number, then double the result.'
INCREMENT = 'Increment input.'
DOUBLE = 'Double input.'
CHECKS = [check(-1, 0), check(2, 6), check(4, 10)]


def run_retention(*, final_step='repair', repeat=False, wrong_composition=False):
    requests, split_offers, pieces = [], [], []
    rejected = []

    def plan(context):
        return {'contract': 'run(n) returns 2*(n+1)', 'input': 5, 'checks': CHECKS}

    def actor(request):
        requests.append(request)
        available = {label: artifact for artifact, label in re.findall(
            r'^([0-9a-f]{64}): (Increment input\.|Double input\.)$', request, re.M)}
        if not all(label in available for label in (INCREMENT, DOUBLE)):
            # A real partial: right at -1, wrong at 2 and 4. The actor cannot recover child IDs
            # from the fixture or memory: only the methods exposed in the current request count.
            return store('def run(n):\n    return n + 1\n')
        first, second = available[INCREMENT], available[DOUBLE]
        if wrong_composition:
            first, second = second, first
        source = ('def run(n):\n'
                  f'    first, second = use("{first}"), use("{second}")\n'
                  '    return second(first(n))\n')
        return store(source, uses=[first, second])

    solver, workspace = make_solver(Model(plan, actor), max_calls=40)

    def split(prompt, spec, lessons, outcome, partial, request, offered, depth, indent, *, retained_parts=()):
        split_offers.append(list(offered))
        number = len(split_offers)
        if number == 1:
            label, source, examples = INCREMENT, 'def run(n):\n    return n + 1\n', [check(3, 4), check(8, 9)]
        elif number == 2:
            label, source, examples = DOUBLE, 'def run(n):\n    return n * 2\n', [check(3, 6), check(8, 16)]
        else:
            # A later split can rediscover the same piece and also leave an unverified candidate.
            failed = solver.admit('An incorrect piece.', [check(3, 6)],
                                  'def run(n):\n    return n * 3\n', 'incorrect piece')
            assert failed['status'] == 'incomplete'
            bad_piece = dict(failed, task='An incorrect piece.', artifact=failed['candidate'])
            rejected.append(bad_piece)
            return [pieces[0], bad_piece], 'scripted third split', outcome
        qualified = solver.admit(label, examples, source, label)
        assert qualified['status'] == 'checked', qualified
        piece = dict(qualified, task=label)
        pieces.append(piece)
        # The parent remains partial until its later repair/replan actually composes the pieces.
        return [piece], 'scripted split', outcome

    ladder = iter(['split', 'split'] + (['split'] if repeat else []) + [final_step])
    with mock.patch.object(solver, '_next_step', side_effect=lambda *args: next(ladder, None)), \
            mock.patch.object(solver, 'split', side_effect=split):
        result = solver.solve(PARENT, CHECKS)
    return result, workspace, requests, split_offers, pieces, rejected


def run_cross_split_assembly(*, retain=True, max_executions=256):
    """The actor solves either helper, but cannot implement the parent or any glue."""
    split_requests, parent_requests = [], []

    def child_checks(task):
        return [check(3, 4), check(8, 9)] if task == INCREMENT else [check(3, 6), check(8, 16)]

    def plan(context):
        if context['task'] == PARENT:
            return {'contract': 'run(n) returns 2*(n+1)', 'input': 5, 'checks': CHECKS}
        return {'contract': context['task'], 'input': None, 'checks': child_checks(context['task'])}

    def actor(request):
        if request.startswith(PARENT):
            parent_requests.append(request)
            return {'kind': 'unresolved', 'reason': 'can solve helpers but cannot write the whole task or glue'}
        assert request.startswith((INCREMENT, DOUBLE)), request
        return store('def run(n):\n    return n + 1\n' if request.startswith(INCREMENT)
                     else 'def run(n):\n    return n * 2\n')

    def propose_split(context):
        split_requests.append(context)
        task = INCREMENT if len(split_requests) == 1 else DOUBLE
        return {'subtasks': [{'request': task, 'checks': child_checks(task)}]}

    solver, workspace = make_solver(Model(plan, actor, split=propose_split), max_calls=30,
                                   max_executions=max_executions)
    real_split = solver.split

    def split(*args, **kwargs):
        if not retain:
            # Negative control restores the old behavior: only this split's pieces can compose.
            kwargs['retained_parts'] = ()
        return real_split(*args, **kwargs)

    ladder = iter(['split', 'split'])
    with mock.patch.object(solver, '_next_step', side_effect=lambda *args: next(ladder, None)), \
            mock.patch.object(solver, 'split', side_effect=split):
        result = solver.solve(PARENT, CHECKS)
    return result, workspace, parent_requests, split_requests


class CheckedPieceRetention(unittest.TestCase):
    def assert_checked_composition(self, result, requests, split_offers, pieces):
        self.assertEqual(result['status'], 'checked', result.get('reason'))
        self.assertEqual(result['answer'], 12)
        ids = {piece['artifact'] for piece in pieces}
        self.assertEqual(set(result['reused']), ids)
        self.assertEqual({piece['artifact'] for piece in result['subtasks']}, ids)
        self.assertEqual(len(result['subtasks']), 2)
        self.assertTrue(any(pieces[0]['artifact'] in note for note in split_offers[1]))
        self.assertTrue(all(artifact in requests[-1] for artifact in ids))
        self.assertEqual(result['checks'], CHECKS)

    def test_two_splits_keep_both_checked_pieces_for_later_parent_repair(self):
        result, _, requests, split_offers, pieces, _ = run_retention()
        self.assert_checked_composition(result, requests, split_offers, pieces)
        self.assertIn('repair', result['strategies'])
        self.assertIn('Best attempt so far', requests[-1])

    def test_parent_replan_still_receives_every_checked_piece(self):
        result, _, requests, split_offers, pieces, _ = run_retention(final_step='replan')
        self.assert_checked_composition(result, requests, split_offers, pieces)
        self.assertIn('replan', result['strategies'])

    def test_repeated_piece_is_deduplicated_and_unchecked_candidate_is_not_offered(self):
        result, _, requests, split_offers, pieces, rejected = run_retention(repeat=True)
        self.assertEqual(result['status'], 'checked', result.get('reason'))
        self.assertEqual(result['answer'], 12)
        checked = [piece for piece in result['subtasks'] if piece['status'] == 'checked']
        self.assertEqual(len(checked), 2)
        self.assertEqual({piece['artifact'] for piece in checked}, {piece['artifact'] for piece in pieces})
        self.assertEqual(len(result['subtasks']), 3)
        self.assertEqual(len(rejected), 1)
        self.assertNotIn(rejected[0]['artifact'], requests[-1])
        self.assertEqual(set(result['reused']), {piece['artifact'] for piece in pieces})
        self.assertTrue(all(any(piece['artifact'] in note for note in split_offers[2]) for piece in pieces))

    def test_wrong_composition_cannot_inherit_the_checked_status_of_its_parts(self):
        result, workspace, requests, _, pieces, _ = run_retention(wrong_composition=True)
        self.assertEqual(len(pieces), 2)
        self.assertTrue(all(piece['status'] == 'checked' for piece in pieces))
        self.assertTrue(all(piece['artifact'] in requests[-1] for piece in pieces))
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('answer', result)
        self.assertEqual(result['checks'], CHECKS)
        self.assertFalse(workspace._read(workspace.solved_path))


class CrossSplitMechanicalAssembly(unittest.TestCase):
    def test_separate_splits_compose_without_parent_or_glue_code_from_actor(self):
        result, _, parent_requests, splits = run_cross_split_assembly()
        self.assertEqual(result['status'], 'checked', result.get('reason'))
        self.assertEqual(result['answer'], 12)
        self.assertEqual(result['delivered_by'], 'assembly')
        self.assertEqual(result['strategies'], ['split', 'split'])
        self.assertEqual(len(splits), 2)
        self.assertEqual({p['task'] for p in result['subtasks']}, {INCREMENT, DOUBLE})
        self.assertTrue(all(p['status'] == 'checked' for p in result['subtasks']))
        self.assertEqual(set(result['reused']), {p['artifact'] for p in result['subtasks']})
        self.assertEqual(result['checks'], CHECKS)
        # One initial whole-task attempt and one failed glue attempt after the first piece;
        # combining the second piece needs no further actor call.
        self.assertEqual(len(parent_requests), 2)

    def test_dropping_previous_split_piece_leaves_the_same_actor_unsolved(self):
        result, workspace, parent_requests, splits = run_cross_split_assembly(retain=False)
        self.assertEqual(len(splits), 2)
        self.assertEqual(len(result['subtasks']), 2)
        self.assertTrue(all(p['status'] == 'checked' for p in result['subtasks']))
        self.assertNotEqual(result['status'], 'checked')
        self.assertNotIn('answer', result)
        self.assertEqual(len(parent_requests), 3)
        self.assertFalse(any(row['task'] == PARENT for row in workspace._read(workspace.solved_path)))


if __name__ == '__main__':
    unittest.main()
