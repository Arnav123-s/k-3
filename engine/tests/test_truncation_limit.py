"""Replies cut at the token limit end an attempt after two in a row (found from W2-b's h-r2-turning journals).

There, one attempt spent five replies of 16384 tokens each (about 20 minutes apiece), every one cut at the
limit before any action, after being told only "Use kind experiment, answer, or unresolved". The model is
now told its reply was cut and to keep the reasoning short. After two such replies in a row the attempt
ends as incomplete, and the solver's ladder moves on (here: the hand check is questioned, then the method
is written). Scripted models only; only test-written code runs, in the local executor.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime import Budget, Journal, investigate
from kuttaka_runtime.solve import TRUNCATION_LIMIT
from tests.test_solve_review_regressions import Model, check, make_solver, store

CUT = {'kind': 'invalid', 'raw_reply': ''}
ANSWER = {'kind': 'answer', 'answer': 42}
PROBE = {'kind': 'experiment', 'tool': 'probe', 'input': {}, 'predictions': [{'hypothesis': 'h', 'outcome': 1}]}


class Replies:
    identity = {'model': 'scripted'}

    def __init__(self, *steps):
        self.steps = iter(steps)   # (action, finish_reason)
        self.contexts = []
        self.last_usage = {}

    def propose(self, context):
        self.contexts.append(context)
        action, finish = next(self.steps)
        self.last_usage = {'input_tokens': 1, 'output_tokens': 16384 if finish == 'length' else 10,
                           'finish_reason': finish}
        return action


def run(model, calls=6, **options):
    path = Path(tempfile.mkdtemp()) / 'run.jsonl'
    with Journal(path) as journal:
        result = investigate('Find the answer', model, {'probe': lambda _input: {'ok': True}},
                             lambda _request, answer: answer == 42, journal, Budget(calls, 6), **options)
    return result, Journal.read(path)


class TheRuntimeLimit(unittest.TestCase):
    def test_two_cut_replies_in_a_row_end_the_run(self):
        model = Replies(*[(CUT, 'length')] * 6)
        result, events = run(model, truncation_limit=2, detailed_response_feedback=True)
        self.assertEqual((result.status, result.model_calls), ('incomplete', 2))
        self.assertEqual(result.reason, 'replies cut at the token limit')
        told = model.contexts[1]['history'][-1]['observation']['feedback']
        self.assertIn('token limit', told)
        self.assertIn('Keep the reasoning short', told)
        self.assertEqual(events[0]['body']['runtime_options']['truncation_limit'], 2)

    def test_control_without_the_limit_every_call_is_spent(self):
        result, _ = run(Replies(*[(CUT, 'length')] * 6))
        self.assertEqual((result.status, result.model_calls), ('incomplete', 6))
        self.assertEqual(result.reason, 'model budget exhausted')

    def test_cut_replies_that_are_not_in_a_row_do_not_end_the_run(self):
        steps = [(CUT, 'length'), (PROBE, 'stop'), (CUT, 'length'), (ANSWER, 'stop')]
        result, _ = run(Replies(*steps), truncation_limit=2)
        self.assertEqual((result.status, result.model_calls), ('checked', 4))

    def test_empty_replies_that_were_not_cut_are_not_truncations(self):
        result, _ = run(Replies(*[(CUT, 'stop')] * 6), truncation_limit=2)
        self.assertEqual((result.status, result.model_calls), ('incomplete', 6))

    def test_the_limit_is_validated(self):
        for bad in (0, -1, True, 1.5, '2'):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                run(Replies((ANSWER, 'stop')), truncation_limit=bad)


RULE = 'def run(n):\n    return 3 * n\n'


class Truncating(Model):
    """The actor reasons past the limit for its whole first attempt, then writes the general method."""
    def __init__(self, plan):
        super().__init__(plan, lambda request: store(RULE))
        self.attempts, self.actor_calls = 0, 0
        self.last_usage = {'input_tokens': 1, 'output_tokens': 1, 'finish_reason': 'stop'}

    def propose(self, context):
        if 'request' in context and 'history' in context:
            self.actor_calls += 1
            self.attempts += not context['history']
            if self.attempts == 1:
                self.last_usage = {'input_tokens': 1, 'output_tokens': 16384, 'finish_reason': 'length'}
                return CUT
        self.last_usage = {'input_tokens': 1, 'output_tokens': 1, 'finish_reason': 'stop'}
        return super().propose(context)


PLAN = lambda context: {'contract': 'run(n) returns 3n', 'input': None,
                        'checks': [check(1, 3), check(2, 6), check(4, 12)]}


class InTheSolver(unittest.TestCase):
    def test_a_truncating_attempt_ends_after_two_replies_and_the_task_is_still_solved(self):
        model = Truncating(PLAN)
        solver, _ = make_solver(model)
        result = solver.solve('Triple a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(model.actor_calls, TRUNCATION_LIMIT + 2)   # two cut replies, then store and answer
        self.assertEqual(TRUNCATION_LIMIT, 2)

    def test_control_without_the_limit_the_attempt_spends_its_whole_budget(self):
        model = Truncating(PLAN)
        solver, _ = make_solver(model)
        with mock.patch('kuttaka_runtime.solve.TRUNCATION_LIMIT', None):
            result = solver.solve('Triple a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(model.actor_calls, solver.attempt_calls + 2)


if __name__ == '__main__':
    unittest.main()
