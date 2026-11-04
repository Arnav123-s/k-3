"""Bound malformed JSON replies without executing or repairing their text.

The nested prediction below is an authored generic fixture, not evaluation
output. These scripted tests make no model calls and execute no candidate code.
"""

import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime import Budget, Journal, investigate
from kuttaka_runtime.models import ReplayModel, parse_action
from kuttaka_runtime.runtime import ToolInputError
from kuttaka_runtime.solve import Metered, Solver, Workspace


# Deliberately missing the closing prediction-list and outer-object delimiters.
MALFORMED = ('{"kind":"experiment","tool":"find_methods",'
             '"input":{"query":"generic procedure","limit":4},'
             '"predictions":[{"hypothesis":"a method may match",'
             '"outcome":{"methods":[{"name":"candidate","interface":'
             '{"examples":[{"input":1,"output":2}]}}]}}')
BAD = parse_action(MALFORMED)
FIND = {'kind': 'experiment', 'tool': 'find_methods',
        'input': {'query': 'generic procedure', 'limit': 4},
        'predictions': [{'hypothesis': 'a method may match', 'outcome': 'a method list'}]}
ANSWER = {'kind': 'answer', 'answer': 42}
REQUEST = 'Inspect the catalog, then propose the answer for the supplied checker.'


class Replies:
    identity = {'model': 'scripted-json-recovery'}

    def __init__(self, actions, *, finish='stop'):
        self.actions = iter(actions)
        self.contexts = []
        self.last_usage = {'input_tokens': 2, 'output_tokens': 3}
        if finish is not None:
            self.last_usage['finish_reason'] = finish

    def propose(self, context):
        self.contexts.append(context)
        return next(self.actions)


class CorrectiveActor(Replies):
    def __init__(self):
        super().__init__([])

    def propose(self, context):
        self.contexts.append(context)
        history = context['history']
        if history and history[-1]['observation'].get('methods') == []:
            return ANSWER
        if history and 'not valid JSON' in history[-1]['observation'].get('feedback', ''):
            return FIND
        return BAD


class InvalidJsonLimitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.serial = 0

    def run_model(self, model, *, calls=8, tool_calls=8, **options):
        self.serial += 1
        path = self.root / (str(self.serial) + '.jsonl')
        tool_inputs, checked = [], []

        def find(request):
            """Input: query string and integer limit. Returns a method list."""
            tool_inputs.append(request)
            if not isinstance(request, dict):
                raise ToolInputError('query and limit are required')
            return {'methods': [], 'retrieval': 'empty'}

        def checker(request, answer):
            checked.append((request, answer))
            return answer == 42

        with Journal(path) as journal:
            result = investigate(REQUEST, model, {'find_methods': find}, checker, journal,
                                 Budget(calls, tool_calls), **options)
        return result, Journal.read(path), path, tool_inputs, checked

    def test_eight_nested_syntax_failures_stop_after_two_without_any_tool(self):
        baseline = self.run_model(Replies([BAD] * 8))
        model = Replies([BAD] * 8)
        result, events, _, tools, checked = self.run_model(
            model, invalid_json_limit=2, detailed_response_feedback=True)
        self.assertEqual((baseline[0].status, baseline[0].model_calls, baseline[0].tool_calls),
                         ('incomplete', 8, 0))
        self.assertEqual((result.status, result.model_calls, result.tool_calls), ('incomplete', 2, 0))
        self.assertEqual(result.reason, 'repeated invalid JSON replies')
        self.assertEqual((tools, checked), ([], []))
        self.assertEqual([event['body']['action'] for event in events if event['kind'] == 'model_reply'],
                         [BAD, BAD])
        self.assertEqual(model.contexts, [event['body'] for event in events if event['kind'] == 'model_context'])
        self.assertEqual(events[0]['body']['runtime_options']['invalid_json_limit'], 2)

    def test_syntax_guidance_allows_real_tool_and_original_checker_within_same_budget(self):
        control = self.run_model(CorrectiveActor(), calls=3, invalid_json_limit=2)
        result, events, _, tools, checked = self.run_model(
            CorrectiveActor(), calls=3, invalid_json_limit=2, detailed_response_feedback=True)
        self.assertEqual((control[0].status, control[0].model_calls, control[0].tool_calls),
                         ('incomplete', 2, 0))
        self.assertEqual((result.status, result.model_calls, result.tool_calls), ('checked', 3, 2))
        self.assertEqual(tools, [FIND['input']])
        self.assertEqual(checked, [(REQUEST, 42)])
        feedback = next(event['body']['feedback'] for event in events if event['kind'] == 'action_rejected')
        self.assertIn('short string outcome', feedback)
        self.assertNotIn(MALFORMED, feedback)

    def test_omitted_and_none_preserve_default_journal_and_feedback(self):
        omitted = self.run_model(Replies([BAD] * 3), calls=3)
        explicit_none = self.run_model(Replies([BAD] * 3), calls=3, invalid_json_limit=None)
        self.assertEqual(omitted[:2], explicit_none[:2])
        self.assertNotIn('runtime_options', omitted[1][0]['body'])
        feedback = next(event['body']['feedback'] for event in omitted[1] if event['kind'] == 'action_rejected')
        self.assertEqual(feedback, 'Use kind experiment, answer, or unresolved and follow that action schema.')

    def test_hard_model_and_checker_budgets_remain_binding(self):
        for calls in (0, 1, 2):
            with self.subTest(calls=calls):
                result, _, _, tools, checked = self.run_model(
                    Replies([BAD] * 8), calls=calls, invalid_json_limit=2, detailed_response_feedback=True)
                self.assertEqual(result.model_calls, calls)
                self.assertEqual((tools, checked), ([], []))
        result, _, _, tools, checked = self.run_model(
            CorrectiveActor(), calls=3, tool_calls=1, invalid_json_limit=2, detailed_response_feedback=True)
        self.assertEqual((result.status, result.model_calls, result.tool_calls), ('incomplete', 3, 1))
        self.assertEqual(tools, [FIND['input']])
        self.assertEqual(checked, [])

    def test_a_valid_action_resets_the_syntax_failure_streak(self):
        result, _, _, tools, checked = self.run_model(
            Replies([BAD, FIND, BAD, ANSWER]), invalid_json_limit=2)
        self.assertEqual((result.status, result.model_calls), ('checked', 4))
        self.assertEqual(tools, [FIND['input']])
        self.assertEqual(checked, [(REQUEST, 42)])

    def test_different_syntax_errors_still_share_the_bound(self):
        other_bad = parse_action('{"kind":"experiment",}')
        result, _, _, tools, _ = self.run_model(Replies([BAD, other_bad, ANSWER]), invalid_json_limit=2)
        self.assertEqual((result.status, result.model_calls), ('incomplete', 2))
        self.assertEqual(tools, [])

    def test_empty_unknown_truncated_and_valid_nonobject_replies_are_not_counted(self):
        for action, finish in ((parse_action(''), 'stop'), (BAD, None), (BAD, 'vendor-private'),
                               (BAD, 'length'), (parse_action('[]'), 'stop'),
                               (parse_action('42'), 'stop'), (parse_action('null'), 'stop'), ({}, 'stop')):
            with self.subTest(action=action, finish=finish):
                result, _, _, tools, checked = self.run_model(
                    Replies([action] * 4, finish=finish), calls=4,
                    invalid_json_limit=2, detailed_response_feedback=True)
                self.assertEqual((result.status, result.model_calls), ('incomplete', 4))
                self.assertEqual(result.reason, 'model budget exhausted')
                self.assertEqual((tools, checked), ([], []))

    def test_truncated_parser_receipt_cannot_prove_long_valid_json_is_malformed(self):
        for value in (['a' * 2100], 'a' * 2100):
            with self.subTest(kind=type(value).__name__):
                action = parse_action(json.dumps(value))
                self.assertEqual(len(action['raw_reply']), 2000)
                result, events, _, tools, checked = self.run_model(
                    Replies([action] * 4), calls=4, invalid_json_limit=2, detailed_response_feedback=True)
                self.assertEqual((result.status, result.model_calls), ('incomplete', 4))
                self.assertEqual(result.reason, 'model budget exhausted')
                self.assertEqual((tools, checked), ([], []))
                feedback = [event['body']['feedback'] for event in events if event['kind'] == 'action_rejected']
                self.assertTrue(all('not valid JSON' not in item for item in feedback))

    def test_tool_rejections_are_not_syntax_errors_and_reset_the_streak(self):
        rejected = dict(FIND, input='not the documented object')
        result, events, _, tools, checked = self.run_model(
            Replies([BAD, rejected, BAD, ANSWER]), invalid_json_limit=2)
        self.assertEqual((result.status, result.model_calls), ('checked', 4))
        self.assertEqual(tools, [rejected['input']])
        self.assertEqual(checked, [(REQUEST, 42)])
        self.assertTrue(any(event['kind'] == 'observation' and
                            event['body']['observation'].get('status') == 'tool_rejection'
                            for event in events if isinstance(event['body'].get('observation'), dict)))

    def test_replay_is_exact_and_wrong_limit_is_rejected_before_any_call(self):
        options = {'invalid_json_limit': 2, 'detailed_response_feedback': True}
        original, events, path, _, _ = self.run_model(CorrectiveActor(), **options)
        replayed, replay_events, _, _, _ = self.run_model(ReplayModel(path), **options)
        self.assertEqual(original, replayed)
        self.assertEqual(events, replay_events)
        for limit in (None, 1, 3):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'runtime options do not match'):
                self.run_model(ReplayModel(path), invalid_json_limit=limit, detailed_response_feedback=True)
            self.assertEqual(Journal.read(self.root / (str(self.serial) + '.jsonl')), [])

    def test_limit_requires_a_positive_integer_or_none(self):
        for limit in (0, -1, True, False, 1.5, '2'):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'invalid_json_limit'):
                self.run_model(Replies([ANSWER]), invalid_json_limit=limit)
            self.assertEqual(Journal.read(self.root / (str(self.serial) + '.jsonl')), [])

    def test_solver_opts_in_without_spending_its_full_attempt_or_executing_source(self):
        model = Replies([BAD] * 8)
        counter = {'used': 0, 'limit': 8}
        metered = Metered(model, counter)

        def forbidden_executor(_job):
            self.fail('malformed JSON must not execute candidate source')

        solver = Solver(Workspace(self.root / 'workspace'), metered, metered,
                        forbidden_executor, say=lambda _: None)
        result = solver.attempt('Construct a generic method', [{'input': 1, 'expected': 2}])
        events = Journal.read(Path(result['run']) / 'events.jsonl')
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['reason'], 'repeated invalid JSON replies')
        self.assertEqual(counter['used'], 2)
        self.assertEqual(solver._execution_used, 0)
        self.assertEqual(next(event['body'] for event in events if event['kind'] == 'start')
                         ['runtime_options']['invalid_json_limit'], 2)


if __name__ == '__main__':
    unittest.main()
