"""Fresh-process-style continuation controls; no source is executed in host."""

import json
from pathlib import Path
import tempfile
import unittest

from kuttaka_runtime.investigators import (
    InvestigatorBudget, run_investigation, run_policy,
)
from kuttaka_runtime.store import Journal


def output(value):
    return json.dumps({'result': value}, separators=(',', ':'))


class Model:
    def __init__(self, identity='resume-model-v1', *replies):
        self.identity = identity
        self.max_tokens = 64
        self.replies = iter(replies)
        self.calls = []
        self.last_response = {'status': 'ok'}
        # Unknown usage is intentional and must remain unknown after resumption.
        self.last_usage = {}

    def propose(self, context):
        self.calls.append(context)
        return next(self.replies)


class PolicyExecutor:
    contexts = []
    created = 0

    def __init__(self):
        type(self).created += 1

    def __call__(self, job):
        context = job['input']
        type(self).contexts.append(context)
        phase = (context['state'] or {}).get('phase', 0)
        if phase == 0:
            result = {'state': {'phase': 1}, 'action': {
                'kind': 'experiment', 'tool': 'probe', 'input': 'first',
                'predictions': [{'hypothesis': 'first', 'outcome': 1}]}}
        elif phase == 1:
            result = {'state': {'phase': 2}, 'action': {
                'kind': 'revise', 'representation': {'encoding': 'revised'},
                'reason': 'retain a distinguishing feature'}}
        elif phase == 2:
            result = {'state': {'phase': 3}, 'action': {
                'kind': 'model', 'instruction': 'request another probe'}}
        elif phase == 3:
            assert context['model_reply'] == {
                'kind': 'experiment', 'tool': 'probe', 'input': 'second',
                'predictions': [{'hypothesis': 'second', 'outcome': 2}]}
            result = {'state': {'phase': 4}, 'action': {
                'kind': 'experiment', 'tool': 'probe', 'input': 'second',
                'predictions': [{'hypothesis': 'second', 'outcome': 2}]}}
        else:
            result = {'state': {'phase': 5}, 'action': {'kind': 'answer', 'answer': 9}}
        return {'status': 'completed', 'exit_code': 0, 'output': output(result)}


class InvestigatorResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'events.jsonl'
        PolicyExecutor.contexts = []
        PolicyExecutor.created = 0
        self.seen = []

    def probe(self, value):
        self.seen.append(value)
        return {'value': 1 if value == 'first' else 2}

    def tools(self, doc='Probe a fixture input.'):
        def callback(value):
            return self.probe(value)
        callback.__doc__ = doc
        return {'probe': callback}

    @staticmethod
    def model_reply():
        return {'kind': 'experiment', 'tool': 'probe', 'input': 'second',
                'predictions': [{'hypothesis': 'second', 'outcome': 2}]}

    def start_policy(self, *, goal='find fixture answer', policy='def run(input): return {}',
                     model=None, tools=None, budget=None, resume_context={'case': 'fixed'}, pause=3):
        model = model or Model('resume-model-v1', self.model_reply())
        tools = tools or self.tools()
        budget = budget or InvestigatorBudget(model_calls=4, tool_calls=4,
                                               policy_executions=8, max_steps=8)
        with Journal(self.path) as journal:
            result = run_policy(goal, policy, model, tools, lambda goal, answer: answer == 9,
                                journal, PolicyExecutor, budget,
                                pause_after_steps=pause, resume_context=resume_context)
        return result, model, budget

    def resume_policy(self, model, budget, *, goal='find fixture answer',
                      policy='def run(input): return {}', tools=None,
                      resume_context={'case': 'fixed'}, pause=1):
        events = Journal.read(self.path)
        journal = Journal.resume(self.path, expected_head=events[-1]['hash'])
        try:
            return run_policy(goal, policy, model, tools or self.tools(),
                              lambda goal, answer: answer == 9, journal, PolicyExecutor,
                              budget, pause_after_steps=pause, resume=True,
                              resume_context=resume_context)
        finally:
            journal.close()

    def test_policy_resume_preserves_checkpoint_and_continues_without_duplicate_effects(self):
        result, model, budget = self.start_policy()
        self.assertEqual(result.status, 'paused')
        self.assertEqual((result.model_calls, result.tool_calls,
                          result.policy_executions, result.steps), (1, 1, 3, 3))
        first = Journal.read(self.path)
        checkpoint = first[-1]['body']['cursor']
        self.assertEqual(checkpoint['state'], {'phase': 3})
        self.assertEqual(checkpoint['representation'], {'encoding': 'revised'})
        self.assertEqual(checkpoint['history'][0]['observation'], {'value': 1})
        self.assertEqual(checkpoint['previous_model_reply'], self.model_reply())
        self.assertFalse(checkpoint['token_known'])
        self.assertEqual(self.seen, ['first'])

        model2 = Model('resume-model-v1')
        resumed = self.resume_policy(model2, budget)
        self.assertEqual(resumed.status, 'paused')
        self.assertEqual((resumed.model_calls, resumed.tool_calls,
                          resumed.policy_executions, resumed.steps), (1, 2, 4, 4))
        self.assertEqual(resumed.model_input_tokens, None)
        self.assertFalse(resumed.model_tokens_known)
        self.assertEqual(self.seen, ['first', 'second'])
        resumed_events = Journal.read(self.path)
        contexts = [e['body']['input'] for e in resumed_events if e['kind'] == 'policy_attempt']
        self.assertEqual(contexts[3]['state'], {'phase': 3})
        self.assertEqual(contexts[3]['representation'], {'encoding': 'revised'})
        self.assertEqual(contexts[3]['history'][0]['observation'], {'value': 1})
        self.assertEqual(contexts[3]['model_reply'], self.model_reply())

        model3 = Model('resume-model-v1')
        final = self.resume_policy(model3, budget, pause=1)
        self.assertEqual(final.status, 'checked')
        self.assertEqual((final.model_calls, final.tool_calls, final.policy_executions,
                          final.steps), (1, 3, 5, 5))
        self.assertEqual(self.seen, ['first', 'second'])
        self.assertEqual(model.calls.__len__(), 1)
        self.assertEqual(model2.calls, [])
        self.assertEqual(model3.calls, [])

    def test_changed_resume_contract_and_increased_budget_reject(self):
        changes = [
            {'goal': 'different goal'},
            {'policy': 'def run(input): return {"different": True}'},
            {'model': Model('different-model')},
            {'tools': self.tools('Changed probe semantics.')},
            {'resume_context': {'case': 'changed'}},
            {'budget': InvestigatorBudget(model_calls=5, tool_calls=4,
                policy_executions=8, max_steps=8)},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                _, base_model, base_budget = self.start_policy()
                model = change.get('model', Model('resume-model-v1'))
                budget = change.get('budget', base_budget)
                events = Journal.read(self.path)
                journal = Journal.resume(self.path, expected_head=events[-1]['hash'])
                try:
                    with self.assertRaises(ValueError):
                        run_policy(change.get('goal', 'find fixture answer'),
                            change.get('policy', 'def run(input): return {}'), model,
                            change.get('tools', self.tools()), lambda goal, answer: answer == 9,
                            journal, PolicyExecutor, budget, pause_after_steps=1, resume=True,
                            resume_context=change.get('resume_context', {'case': 'fixed'}))
                finally:
                    journal.close()

    def test_resume_rejects_modified_tail_and_terminal_run(self):
        self.start_policy()
        events = Journal.read(self.path)
        journal = Journal.resume(self.path, expected_head=events[-1]['hash'])
        journal.append('investigator_tool_attempt', {'number': 99, 'kind': 'experiment'})
        journal.close()
        events = Journal.read(self.path)
        resumed_journal = Journal.resume(self.path, expected_head=events[-1]['hash'])
        try:
            with self.assertRaisesRegex(ValueError, 'explicit pause'):
                run_policy('find fixture answer', 'def run(input): return {}',
                    Model('resume-model-v1'), self.tools(), lambda goal, answer: True,
                    resumed_journal, PolicyExecutor, InvestigatorBudget(), resume=True,
                    resume_context={'case': 'fixed'})
        finally:
            resumed_journal.close()

        terminal_path = Path(self.tmp.name) / 'terminal.jsonl'
        with Journal(terminal_path) as terminal_journal:
            result = run_investigation('terminal', Model('terminal-model',
                {'kind': 'answer', 'answer': 9}), self.tools(), lambda goal, answer: True,
                terminal_journal,
                InvestigatorBudget(model_calls=1, tool_calls=2, policy_executions=0, max_steps=2))
        self.assertEqual(result.status, 'checked')
        end = Journal.read(terminal_path)
        continued = Journal.resume(terminal_path, expected_head=end[-1]['hash'])
        try:
            with self.assertRaises(ValueError):
                run_investigation('terminal', Model('terminal-model'), self.tools(),
                    lambda goal, answer: True, continued,
                    InvestigatorBudget(model_calls=1, tool_calls=2, policy_executions=0, max_steps=2),
                    resume=True)
        finally:
            continued.close()

    def test_direct_model_mode_resumes_after_tool_observation(self):
        model = Model('direct-v1',
            {'kind': 'experiment', 'tool': 'probe', 'input': 'first',
             'predictions': [{'hypothesis': 'one', 'outcome': 1}]},
            {'kind': 'answer', 'answer': 9})
        budget = InvestigatorBudget(model_calls=3, tool_calls=3, policy_executions=0, max_steps=4)
        with Journal(self.path) as journal:
            paused = run_investigation('direct goal', model, self.tools(),
                lambda goal, answer: answer == 9, journal, budget,
                pause_after_steps=1, resume_context={'v': 1})
        self.assertEqual(paused.status, 'paused')
        self.assertEqual(self.seen, ['first'])
        model2 = Model('direct-v1', {'kind': 'answer', 'answer': 9})
        events = Journal.read(self.path)
        resumed_journal = Journal.resume(self.path, expected_head=events[-1]['hash'])
        try:
            result = run_investigation('direct goal', model2, self.tools(),
                lambda goal, answer: answer == 9, resumed_journal, budget,
                resume=True, resume_context={'v': 1})
        finally:
            resumed_journal.close()
        self.assertEqual(result.status, 'checked')
        self.assertEqual((result.model_calls, result.tool_calls, result.steps), (2, 2, 2))
        self.assertEqual(self.seen, ['first'])
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(model2.calls), 1)


if __name__ == '__main__':
    unittest.main()
