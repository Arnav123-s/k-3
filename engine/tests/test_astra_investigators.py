"""Runtime contract tests with a fake isolated executor and fake model."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from kuttaka_runtime.investigators import (
    InvestigatorBudget, POLICY_CONTRACT, export_investigator,
    DIRECT_CONTRACT, import_investigator, investigator_payload, run_investigation, run_policy,
)
from kuttaka_runtime.execution import PythonExecutor
from kuttaka_runtime.runtime import TransportFailure
from kuttaka_runtime.store import Journal


def encoded(value):
    return json.dumps({'result': value}, separators=(',', ':'))


class FakeExecutor:
    created = 0

    def __init__(self):
        type(self).created += 1

    def __call__(self, job):
        context = job['input']
        step = context['state']['step'] if context['state'] else 0
        if step == 0:
            result = {'state': {'step': 1}, 'action': {
                'kind': 'revise', 'representation': {'encoding': 'two states'},
                'reason': 'the initial encoding merges two outcomes'}}
        elif step == 1:
            result = {'state': {'step': 2}, 'action': {
                'kind': 'experiment', 'tool': 'probe', 'input': 'case',
                'predictions': [{'hypothesis': 'case has value 7', 'outcome': 7}]}}
        else:
            result = {'state': {'step': 3}, 'action': {'kind': 'answer', 'answer': 7}}
        return {'status': 'completed', 'exit_code': 0, 'output': encoded(result)}


class Model:
    identity = {'model': 'test-model'}
    max_tokens = 64

    def __init__(self, reply=None, usage=None):
        self.reply = reply or {'kind': 'answer', 'answer': 7}
        self.last_usage = {} if usage is None else usage
        self.last_response = {'status': 200, 'body': 'private raw receipt'}
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        return self.reply


class InvestigatorTests(unittest.TestCase):
    def test_policy_revises_representation_then_uses_shared_tool_and_checker(self):
        FakeExecutor.created = 0
        seen = []
        def probe(value):
            seen.append(value)
            return {'value': 7}
        probe.__doc__ = 'Probe one case. Input is a string.'
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_policy('Find the value', 'def run(input): return {}', Model(),
                                {'probe': probe}, lambda goal, answer: answer == 7,
                                journal, FakeExecutor, InvestigatorBudget())
            self.assertEqual((result.status, result.answer), ('checked', 7))
            self.assertEqual((result.model_calls, result.tool_calls,
                              result.policy_executions, result.steps), (0, 2, 3, 3))
            self.assertEqual(FakeExecutor.created, 3)
            self.assertEqual(seen, ['case'])
            events = Journal.read(Path(tmp) / 'log')
            revision = next(e['body'] for e in events if e['kind'] == 'representation_revision')
            self.assertEqual(revision['before'], None)
            self.assertEqual(revision['after'], {'encoding': 'two states'})
            policy_inputs = [e['body']['input'] for e in events if e['kind'] == 'policy_attempt']
            self.assertEqual(set(policy_inputs[0]), {
                'goal', 'tools', 'history', 'remaining', 'state', 'representation', 'model_reply'})
            self.assertEqual(policy_inputs[1]['representation'], {'encoding': 'two states'})
            self.assertEqual(policy_inputs[2]['history'][-1]['observation'], {'value': 7})

    def test_model_action_receipt_raw_reply_and_unknown_usage_are_recorded(self):
        class ModelPolicyExecutor:
            def __init__(self):
                self.inputs = []
            def __call__(self, job):
                self.inputs.append(job['input'])
                ctx = job['input']
                if ctx['model_reply'] is None:
                    action = {'kind': 'model', 'instruction': 'answer the goal'}
                else:
                    self.assert_reply = ctx['model_reply']
                    action = {'kind': 'answer', 'answer': 7}
                return {'status': 'completed', 'exit_code': 0,
                        'output': encoded({'state': {'seen': True}, 'action': action})}
        executor = ModelPolicyExecutor()
        model = Model(usage={'input_tokens': None, 'output_tokens': None})
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_policy('Find value', 'pass', model, {},
                                lambda goal, answer: answer == 7, journal,
                                lambda: executor, InvestigatorBudget(max_steps=4))
            self.assertEqual(result.status, 'checked')
            self.assertEqual(result.model_calls, 1)
            self.assertIsNone(result.model_input_tokens)
            self.assertFalse(result.model_tokens_known)
            self.assertEqual(executor.assert_reply, {'kind': 'answer', 'answer': 7})
            model_context = model.contexts[0]
            self.assertEqual(model_context['policy_instruction'], 'answer the goal')
            events = Journal.read(Path(tmp) / 'log')
            receipt = next(e['body'] for e in events if e['kind'] == 'model_response')
            self.assertEqual(receipt['receipt']['body'], 'private raw receipt')

    def test_bad_policy_action_is_feedback_only_and_bounded_by_policy_steps(self):
        class InvalidExecutor:
            def __init__(self):
                pass
            def __call__(self, job):
                return {'status': 'completed', 'exit_code': 0, 'output': encoded({'state': None,
                        'action': {'kind': 'experiment', 'tool': 'missing', 'input': None,
                                   'predictions': []}})}
        model = Model()
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_policy('goal', 'x', model, {}, lambda *_: True, journal,
                                InvalidExecutor, InvestigatorBudget(max_steps=2))
            self.assertEqual((result.status, result.model_calls, result.policy_executions),
                             ('incomplete', 0, 2))
            self.assertTrue(any(e['kind'] == 'action_rejected' for e in Journal.read(Path(tmp) / 'log')))

    def test_false_final_answer_is_terminal_without_corrective_checker_feedback(self):
        calls = []
        class AnswerExecutor:
            def __call__(self, job):
                return {'status': 'completed', 'exit_code': 0, 'output': encoded({'state': None,
                        'action': {'kind': 'answer', 'answer': 'wrong'}})}
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_policy('goal', 'x', Model(), {},
                                lambda *_: calls.append('checked') or False, journal,
                                AnswerExecutor, InvestigatorBudget())
            self.assertEqual(result.status, 'failed')
            self.assertEqual(calls, ['checked'])
            self.assertEqual(result.tool_calls, 1)

    def test_failed_policy_execution_keeps_bounded_executor_receipt(self):
        class TimedOutExecutor:
            def __call__(self, job):
                return {'status': 'timeout', 'exit_code': -9, 'output': '',
                        'diagnostics': 'bounded executor diagnostic'}
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_policy('goal', 'x', Model(), {}, lambda *_: True, journal,
                                TimedOutExecutor, InvestigatorBudget())
            events = Journal.read(Path(tmp) / 'log')
            failure = next(e['body']['receipt'] for e in events
                           if e['kind'] == 'policy_execution_failure')
            self.assertEqual(result.reason, 'policy execution failure')
            self.assertEqual((failure['status'], failure['exit_code']), ('timeout', -9))
            self.assertEqual(failure['diagnostics'], 'bounded executor diagnostic')

    def test_direct_baseline_uses_same_checker_and_token_ledger(self):
        model = Model({'kind': 'answer', 'answer': 7}, {'input_tokens': 12, 'output_tokens': 4})
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_investigation('goal', model, {}, lambda _goal, answer: answer == 7,
                                       journal, InvestigatorBudget())
        self.assertEqual(result.status, 'checked')
        self.assertEqual((result.model_calls, result.tool_calls, result.model_input_tokens,
                          result.model_output_tokens, result.model_tokens_known), (1, 1, 12, 4, True))
        self.assertEqual(model.contexts[0]['instruction'], DIRECT_CONTRACT)

    def test_configured_reported_token_limit_fails_closed_without_usage(self):
        checked = []
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_investigation('goal', Model(), {},
                lambda *_: checked.append(True) or True, journal,
                InvestigatorBudget(total_model_tokens=100))
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(result.reason, 'cannot verify reported model token budget')
        self.assertFalse(result.model_tokens_known)
        self.assertEqual(checked, [])

    def test_nonzero_policy_exit_cannot_deliver_even_valid_json_action(self):
        class FailedExecutor:
            def __call__(self, job):
                return {'status': 'completed', 'exit_code': 1, 'output': encoded({
                    'state': None, 'action': {'kind': 'answer', 'answer': 7}})}
        checked = []
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_policy('goal', 'x', Model(), {},
                lambda *_: checked.append(True) or True, journal, FailedExecutor)
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(checked, [])

    def test_policy_requires_exact_integer_zero_exit_code(self):
        for receipt in (
                {'status': 'completed', 'output': encoded({'state': None,
                 'action': {'kind': 'answer', 'answer': 7}})},
                {'status': 'completed', 'exit_code': False, 'output': encoded({'state': None,
                 'action': {'kind': 'answer', 'answer': 7}})}):
            with self.subTest(receipt=receipt):
                checked = []
                class MalformedExecutor:
                    def __call__(self, job):
                        return receipt
                with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
                    result = run_policy('goal', 'x', Model(), {},
                        lambda *_: checked.append(True) or True, journal, MalformedExecutor)
                    events = Journal.read(Path(tmp) / 'log')
                self.assertEqual(result.status, 'incomplete')
                self.assertEqual(checked, [])
                failure = next(e['body']['receipt'] for e in events
                               if e['kind'] == 'policy_execution_failure')
                self.assertEqual(failure.get('exit_code'), receipt.get('exit_code'))
                self.assertNotIn('policy_result', [e['kind'] for e in events])

    def test_policy_rejects_duplicate_nested_action_keys(self):
        class DuplicateKeyExecutor:
            def __call__(self, job):
                return {'status': 'completed', 'exit_code': 0,
                        'output': '{"result":{"state":null,"action":{"kind":"answer",'
                                  '"answer":"wrong","answer":7}}}'}
        checked = []
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_policy('goal', 'x', Model(), {},
                lambda *_: checked.append(True) or True, journal, DuplicateKeyExecutor)
            events = Journal.read(Path(tmp) / 'log')
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(checked, [])
        self.assertTrue(any(e['kind'] == 'policy_execution_failure' for e in events))
        self.assertFalse(any(e['kind'] == 'policy_result' for e in events))

    def test_direct_malformed_none_is_rejected_without_crashing(self):
        class MalformedModel(Model):
            def propose(self, context):
                self.contexts.append(context)
                return None
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_investigation('goal', MalformedModel(), {}, lambda *_: True, journal,
                InvestigatorBudget(model_calls=1, max_steps=1))
        self.assertEqual((result.status, result.model_calls), ('incomplete', 1))

    def test_model_timeout_marks_usage_unknown(self):
        class TimedOutModel(Model):
            last_usage = {}
            last_response = None
            def propose(self, context):
                raise TimeoutError('private transport details')
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_investigation('goal', TimedOutModel(), {}, lambda *_: True, journal,
                                       InvestigatorBudget())
            events = Journal.read(Path(tmp) / 'log')
        self.assertEqual(result.status, 'incomplete')
        self.assertFalse(result.model_tokens_known)
        self.assertIsNone(result.model_input_tokens)
        self.assertNotIn('private transport details', json.dumps(events))

    def test_transport_failures_keep_safe_kind_status_and_unknown_usage_in_direct_and_policy(self):
        class TimedOutModel(Model):
            def __init__(self):
                super().__init__()
                self.last_response = None
                self.last_usage = {}
            def propose(self, context):
                raise TransportFailure("request_timeout")
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'direct') as journal:
            direct = run_investigation('goal', TimedOutModel(), {}, lambda *_: True,
                                       journal, InvestigatorBudget())
            direct_events = Journal.read(Path(tmp) / 'direct')
        self.assertEqual(direct.status, 'incomplete')
        self.assertEqual(direct.model_calls, 1)
        self.assertFalse(direct.model_tokens_known)
        direct_response = next(e['body'] for e in direct_events if e['kind'] == 'model_response')
        self.assertIsNone(direct_response['receipt'])
        self.assertEqual(direct_response['usage'], {'input_tokens': None, 'output_tokens': None})
        direct_error = next(e['body'] for e in direct_events if e['kind'] == 'model_error')
        self.assertEqual(direct_error, {'type': 'TransportFailure',
                                       'kind': 'request_timeout', 'status': None})

        class RequestModel:
            identity = {'model': 'test-model'}
            max_tokens = 64
            last_response = {'status': 500, 'body': 'safe bounded provider receipt'}
            last_usage = {}
            def propose(self, context):
                raise TransportFailure('provider_http_error', status=500)
        class ModelActionExecutor:
            def __call__(self, job):
                return {'status': 'completed', 'exit_code': 0,
                        'output': encoded({'state': None, 'action': {
                            'kind': 'model', 'instruction': 'continue the investigation'}})}
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'policy') as journal:
            policy = run_policy('goal', 'x', RequestModel(), {}, lambda *_: True,
                                journal, ModelActionExecutor, InvestigatorBudget())
            policy_events = Journal.read(Path(tmp) / 'policy')
        self.assertEqual(policy.status, 'incomplete')
        self.assertEqual(policy.model_calls, 1)
        self.assertFalse(policy.model_tokens_known)
        policy_response = next(e['body'] for e in policy_events if e['kind'] == 'model_response')
        self.assertEqual(policy_response['receipt']['status'], 500)
        self.assertEqual(policy_response['usage'], {'input_tokens': None, 'output_tokens': None})
        policy_error = next(e['body'] for e in policy_events if e['kind'] == 'model_error')
        self.assertEqual(policy_error, {'type': 'TransportFailure',
                                       'kind': 'provider_http_error', 'status': 500})
        self.assertNotIn('private', json.dumps(direct_events + policy_events))

    def test_non_boolean_checker_result_fails_closed(self):
        model = Model({'kind': 'answer', 'answer': 7}, {'input_tokens': 1, 'output_tokens': 1})
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_investigation('goal', model, {}, lambda *_: 1, journal,
                                       InvestigatorBudget())
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(result.tool_calls, 1)

    def test_zero_budgets_stop_before_model_or_tool_side_effects(self):
        model = Model()
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_investigation('goal', model, {}, lambda *_: True, journal,
                InvestigatorBudget(model_calls=0, tool_calls=0, policy_executions=0,
                                   max_steps=0, max_response_tokens=0))
        self.assertEqual((result.status, result.model_calls, result.tool_calls),
                         ('incomplete', 0, 0))
        self.assertEqual(model.contexts, [])

    def test_artifact_export_import_is_minimal_and_hash_pinned(self):
        source = 'def run(input):\n    return {"state": None, "action": {"kind": "unresolved", "reason": "unknown"}}'
        payload = investigator_payload(source, name='small', description='generic policy')
        hash_ref = '1' * 64
        payload['provenance']['origin_hash'] = hash_ref
        restored = import_investigator(export_investigator(payload))
        self.assertEqual(restored, payload)
        self.assertEqual(restored['dependencies'], [])
        self.assertEqual(set(restored), {'kind', 'body', 'dependencies', 'provenance', 'applicability'})
        self.assertNotIn('qualification', json.dumps(restored))
        bad = dict(payload)
        bad['provenance'] = {'source_sha256': '0' * 64}
        with self.assertRaises(ValueError):
            import_investigator(bad)
        with self.assertRaises(ValueError):
            import_investigator({**payload, 'dependencies': [hash_ref]})
        with self.assertRaises(ValueError):
            export_investigator({**payload, 'dependencies': [hash_ref]})

    @unittest.skipUnless(os.environ.get('ASTRA_DOCKER_TESTS'),
                         'set ASTRA_DOCKER_TESTS=1 to run the explicit Docker runtime fixture')
    def test_real_docker_executes_human_written_policy_fixture(self):
        source = '''def run(input):
    if input["state"] is None:
        return {"state": {"asked": True}, "action": {"kind": "experiment", "tool": "probe", "input": {}, "predictions": [{"hypothesis": "the fixture returns seven", "outcome": {"value": 7}}]}}
    return {"state": input["state"], "action": {"kind": "answer", "answer": 7}}
'''
        def probe(_):
            """Return a fixed runtime fixture value."""
            return {'value': 7}
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            result = run_policy('Read the fixture value', source, Model(), {'probe': probe},
                lambda _goal, answer: answer == 7, journal,
                executor=lambda: PythonExecutor(timeout=10),
                budget=InvestigatorBudget(model_calls=0, tool_calls=2, max_steps=4,
                                          policy_executions=4))
        self.assertEqual((result.status, result.answer, result.model_calls, result.tool_calls),
                         ('checked', 7, 0, 2))


if __name__ == '__main__':
    unittest.main()
