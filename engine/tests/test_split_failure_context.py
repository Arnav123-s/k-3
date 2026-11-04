"""A weak whole-task actor can solve checked pieces when the splitter sees its actual failure.

All programs and model replies are test-written; no endpoint or suite task data is used.
"""
import copy
import tempfile
import unittest
from unittest import mock

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_mechanical_composition import (
    ComponentActor, ScriptedPlanner, SQUARE, TOTAL, check, decomposition,
)


PARENT = 'sum squares list'
CHECKS = [check([1, 2], 5), check([2, 3], 13)]


class Replies:
    identity = {'model': 'test-splitter'}
    last_usage = {}

    def __init__(self, *replies):
        self.replies = list(replies)
        self.contexts = []

    def propose(self, context):
        self.contexts.append(copy.deepcopy(context))
        return copy.deepcopy(self.replies[min(len(self.contexts) - 1, len(self.replies) - 1)])


class FailureAwareSplitter(Replies):
    """Proposes the missing square stage only when actual zero-pass feedback is available."""
    def __init__(self):
        super().__init__({})

    def propose(self, context):
        self.contexts.append(copy.deepcopy(context))
        partial = context.get('partial_attempt')
        if (context.get('checks') == CHECKS and context.get('input') == [3, 4] and partial
                and partial['passed_checks'] == []
                and [c.get('got') for c in partial['failed_checks']] == [3, 5]):
            return decomposition()
        return {'subtasks': []}


def make_solver(splitter, *, parent_source=TOTAL, max_calls=40):
    planner = ScriptedPlanner(parent_checks=CHECKS, parent_input=[3, 4])
    sources = {'square each value': SQUARE, 'sum values': TOTAL}
    if parent_source is not None:
        sources[PARENT] = parent_source
    actor = ComponentActor(sources, unresolved_parent=parent_source is None)
    counter = {'used': 0, 'limit': max_calls}
    solver = Solver(Workspace(tempfile.mkdtemp()), Metered(planner, counter), Metered(actor, counter),
                    local_executor, splitter=Metered(splitter, counter), say=lambda _: None)
    return solver, actor, counter


class SplitFailureContext(unittest.TestCase):
    def test_zero_pass_failure_splits_into_checked_pieces_and_returns_the_answer(self):
        splitter = FailureAwareSplitter()
        solver, actor, counter = make_solver(splitter)
        result = solver.solve(PARENT)
        self.assertEqual((result['status'], result['answer']), ('checked', 25))
        self.assertEqual(result['checks'], CHECKS)
        self.assertEqual([p['status'] for p in result['subtasks']], ['checked', 'checked'])
        self.assertEqual(set(result['reused']), {p['artifact'] for p in result['subtasks']})
        self.assertEqual(result['composition']['model_glue_calls'], 0)
        self.assertGreater(actor.parent_calls, 0, 'the whole-task attempt failed before reduction')
        self.assertLessEqual(counter['used'], counter['limit'])
        feedback = splitter.contexts[0]
        self.assertEqual(feedback['partial_attempt']['source'], TOTAL)
        self.assertEqual(feedback['partial_attempt']['passed_checks'], [])
        self.assertEqual(feedback['checks'], CHECKS)

    def test_control_hiding_zero_pass_feedback_leaves_the_same_actor_unsolved(self):
        splitter = FailureAwareSplitter()
        solver, _, counter = make_solver(splitter)
        ask = solver._ask

        def old_context(model, context, *args, **kwargs):
            if model is solver.splitter:
                context = dict(context)
                for key in ('checks', 'input'):
                    context.pop(key, None)
                if not (context.get('partial_attempt') or {}).get('passed_checks'):
                    context.pop('partial_attempt', None)
            return ask(model, context, *args, **kwargs)

        with mock.patch.object(solver, '_ask', side_effect=old_context):
            result = solver.solve(PARENT)
        self.assertNotEqual(result['status'], 'checked')
        self.assertFalse(result.get('subtasks'))
        self.assertLessEqual(counter['used'], counter['limit'])

    def test_corrected_recipe_assembles_before_another_whole_task_attempt(self):
        invalid = decomposition()
        invalid['recipe']['output']['op'] = 'apply'
        splitter = Replies(invalid, decomposition())
        solver, _, counter = make_solver(splitter, parent_source=None)
        result = solver.solve(PARENT)
        self.assertEqual((result['status'], result['answer']), ('checked', 25))
        self.assertEqual(result['composition']['model_glue_calls'], 0)
        self.assertEqual(len(splitter.contexts), 2)
        self.assertNotIn('previous_reply_rejected', splitter.contexts[0])
        self.assertIn('unsupported recipe operation: apply', splitter.contexts[1]['previous_reply_rejected'])
        self.assertEqual(splitter.contexts[1]['previous_recipe'], invalid['recipe'])
        self.assertEqual(splitter.contexts[1]['previous_subtasks'], invalid['subtasks'])
        self.assertEqual(len(result['subtasks']), 2)
        self.assertLessEqual(counter['used'], counter['limit'])


class SplitReplyValidation(unittest.TestCase):
    def split(self, splitter, *, offered=(), max_calls=20):
        solver, _, counter = make_solver(splitter, max_calls=max_calls)
        spec = solver.plan(PARENT, [])
        solved = []

        def piece(sub, *args):
            solved.append(sub)
            return {'status': 'incomplete', 'task': sub['request'], 'artifact': None}

        with mock.patch.object(solver, '_solve_piece', side_effect=piece):
            result = solver.split(PARENT, spec, [], {'reason': 'no working method'}, None,
                                  PARENT, list(offered), 0, '')
        return result, solved, counter

    def test_invalid_subtask_containers_are_rejected_without_crashing(self):
        for invalid in (7, 'not a list', {}, True, None):
            with self.subTest(value=invalid):
                splitter = Replies({'subtasks': invalid}, decomposition())
                _, solved, _ = self.split(splitter)
                self.assertEqual(len(splitter.contexts), 2)
                self.assertIn('subtasks must be a list', splitter.contexts[1]['previous_reply_rejected'])
                self.assertEqual(len(solved), 2)

    def test_malformed_or_conflicting_checks_do_not_leak_a_partial_plan(self):
        good = decomposition()['subtasks'][0]
        invalid_parts = [None, {'request': '', 'checks': [check(1, 1)]},
                         {'request': 'bad', 'checks': [check(1, 1), check(1, 2)]},
                         {'request': 'bad', 'checks': 'not checks'}]
        for invalid in invalid_parts:
            with self.subTest(value=invalid):
                splitter = Replies({'subtasks': [good, invalid]})
                _, solved, _ = self.split(splitter)
                self.assertEqual(solved, [])
                self.assertEqual(len(splitter.contexts), 2)
                self.assertIn('previous_reply_rejected', splitter.contexts[1])

    def test_overlong_plan_is_not_silently_truncated(self):
        splitter = Replies({'subtasks': [decomposition()['subtasks'][0]] * 5})
        _, solved, _ = self.split(splitter)
        self.assertEqual(solved, [])
        self.assertIn('at most four', splitter.contexts[1]['previous_reply_rejected'])

    def test_twice_invalid_recipe_keeps_valid_pieces_after_bounded_retry(self):
        invalid = decomposition()
        invalid['recipe']['output']['op'] = 'apply'
        splitter = Replies(invalid)
        _, solved, _ = self.split(splitter)
        self.assertEqual(len(splitter.contexts), 2)
        self.assertEqual(solved, invalid['subtasks'])

    def test_available_checked_pieces_are_offered_by_id(self):
        offered = ['a' * 64 + ': previously checked square function']
        splitter = Replies({'subtasks': []})
        self.split(splitter, offered=offered)
        self.assertEqual(splitter.contexts[0]['available_methods'], offered)
        self.assertEqual(splitter.contexts[0]['checks'], CHECKS)
        self.assertEqual(splitter.contexts[0]['input'], [3, 4])

    def test_retry_does_not_cross_shared_call_ceiling(self):
        splitter = Replies({'subtasks': 7})
        _, solved, counter = self.split(splitter, max_calls=2)  # one plan, one split call
        self.assertEqual(len(splitter.contexts), 1)
        self.assertEqual(solved, [])
        self.assertEqual(counter['used'], counter['limit'])


if __name__ == '__main__':
    unittest.main()
