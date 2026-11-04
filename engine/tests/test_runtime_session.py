"""Session retention and restore integration using deterministic fake executors."""

import json
from pathlib import Path
import tempfile
import unittest

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.programs import ProgramTask
from kuttaka_runtime.session import InvestigationSession
from kuttaka_runtime.store import Journal
from kuttaka_runtime.investigators import InvestigatorBudget, run_policy


SOURCE = 'def run(value): return {"y": value["input"]["x"] * 2}'
PROVENANCE = {'producer': 'fixture-model', 'phase': 'development'}
CONTEXT = {'domain': 'authored-fixture'}


class FixtureExecutor:
    """Choose authored outputs by source label and input shape; never exec source."""
    def __init__(self, log=None):
        self.calls = [] if log is None else log

    def __call__(self, job):
        self.calls.append(json.loads(json.dumps(job)))
        value = job['input']
        if 'tool' in value:
            x = value['input']['x']
            result = {'y': 2 * x}
        else:
            x = value['input']['x']
            result = {'y': 2 * x}
        return {'status': 'completed', 'exit_code': 0,
                'output': json.dumps({'result': result})}


class PolicyFixtureExecutor:
    def __call__(self, job):
        action = {'kind': 'experiment', 'tool': 'discover_measure',
            'input': {'representation': job['input']['representation'],
                      'tool': 'observe', 'input': {'x': 5}},
            'predictions': [{'hypothesis': 'the fixture scales x by two', 'outcome': {'y': 10}}]}
        return {'status': 'completed', 'exit_code': 0,
                'output': json.dumps({'result': {'state': {'phase': 1}, 'action': action}})}


class ModelThatMustNotRun:
    identity = 'session-fixture-model'
    max_tokens = 64

    def __init__(self):
        self.calls = 0

    def propose(self, context):
        self.calls += 1
        raise AssertionError('policy fixture should not request the model')


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.memory_path = self.root / 'memory'
        self.journal_path = self.root / 'journal.jsonl'
        self.memory = ArtifactMemory(self.memory_path)
        self.addCleanup(self.memory.close)
        self.journal = Journal(self.journal_path)
        self.addCleanup(self.journal.close)
        self.executor_calls = []
        self.executor = FixtureExecutor(self.executor_calls)
        self.observation_calls = []
        self.tools = self.make_tools()
        self.task = self.make_task()
        self.session = self.make_session()

    def make_tools(self, doc='Observe a fixture point {x}; return {y: 2*x}.'):
        def observe(value):
            self.observation_calls.append(json.loads(json.dumps(value)))
            return {'y': 2 * value['x']}
        observe.__doc__ = doc
        return {'observe': observe}

    @staticmethod
    def make_task(request='infer y from x', expected=6):
        return ProgramTask(request, [{'input': {'tool': 'observe', 'input': {'x': 3}},
                                      'expected': {'y': expected}}], context=CONTEXT)

    def make_session(self, *, task=None, context=CONTEXT, provenance=PROVENANCE,
                     tools=None, max_observations=8, max_predictions=16,
                     max_program_executions=8):
        return InvestigationSession(task or self.task, self.memory, self.journal,
            self.executor, self.tools if tools is None else tools, context=context,
            provenance=provenance, max_observations=max_observations,
            max_predictions=max_predictions, max_program_executions=max_program_executions)

    def prepare_supported_method(self):
        original = self.session.goals.request_evidence
        goal = self.session.goals.propose({'description': 'infer y from x',
            'success_criteria': ['return y equal to twice x'], 'unknowns': [], 'tradeoffs': [],
            'parent': None, 'based_on': [original]})['goal']
        self.session.goals.authorize(goal, user_evidence=original)
        representation = self.session.discovery.represent({'name': 'double x', 'source': SOURCE,
            'parent': None, 'based_on': []})['representation']
        observed = self.session.discovery.measure({'representation': representation,
            'tool': 'observe', 'input': {'x': 3}})
        self.assertEqual(observed['status'], 'prediction_matched')
        self.assertTrue(self.session.discovery.adopt({'representation': representation})['adopted'])
        retained = self.session.retain_representation({'representation': representation,
            'name': 'double x', 'description': 'Predicts y for an observe input.'})
        method = retained['artifact']
        stored = self.memory.get(method)
        self.assertEqual(stored['body']['source'], SOURCE)
        self.assertEqual(stored['provenance']['derivation']['representation'], representation)
        self.assertEqual(self.memory.status(method, self.task.scope_id), 'proposed')
        self.assertEqual(self.session.programs.validate_method({'artifact': method})['status'], 'passed')
        self.assertEqual(self.memory.status(method, self.task.scope_id), 'qualified')
        return goal, representation, method

    def _checkpoint_with_active_session(self, *, pending_program=False, invalidate_method=False):
        goal, representation, method = self.prepare_supported_method()
        if invalidate_method:
            def wrong_method(job):
                self.executor_calls.append(json.loads(json.dumps(job)))
                return {'status': 'completed', 'exit_code': 0,
                        'output': json.dumps({'result': {'y': -1}})}
            self.session.programs.executor = wrong_method
            self.assertEqual(self.session.programs.validate_method({'artifact': method})['status'], 'failed')
            self.assertEqual(self.memory.status(method, self.task.scope_id), 'invalidated')
            self.session.programs.executor = self.executor
        if pending_program:
            self.journal.append('program_execution_precommit', {'artifact': method,
                'input': {'tool': 'observe', 'input': {'x': 99}}, 'scope': self.task.scope_id,
                'purpose': 'probe', 'check_index': None, 'attempt': 2, 'uses': []})
        model = ModelThatMustNotRun()
        result = run_policy('infer y from x', 'def run(input): return {}', model,
            self.session.tools(), self.session.checker, self.journal, PolicyFixtureExecutor,
            InvestigatorBudget(model_calls=2, tool_calls=3, policy_executions=4, max_steps=4),
            representation=representation, pause_after_steps=1,
            resume_context=self.session.binding)
        self.assertEqual(result.status, 'paused')
        self.assertEqual(model.calls, 0)
        return goal, representation, method

    def test_retention_keeps_support_and_source_until_independent_task_validation(self):
        _, representation, method = self.prepare_supported_method()
        self.assertEqual(self.session.discovery.active, representation)
        self.assertEqual(self.memory.get(method)['body']['source'], SOURCE)
        qualification = self.memory.qualifications(method)
        self.assertEqual(qualification[0]['checker'], 'kuttaka-program-check-json-v1')
        self.assertEqual(qualification[0]['scope'], self.task.scope_id)
        self.assertEqual(self.session.usage(), {'observations': 1, 'predictions': 1,
                                               'program_executions': 1})

    def test_restore_after_clean_investigator_pause_preserves_all_session_state(self):
        goal, representation, method = self._checkpoint_with_active_session()
        before_usage = self.session.usage()
        before_effects = (len(self.executor_calls), len(self.observation_calls))
        prior_goal_status = self.session.goals.status({})
        prior_cache = dict(self.session.discovery._cache)
        events = Journal.read(self.journal_path)
        head = events[-1]['hash']
        self.journal.close()
        self.memory.close()

        self.memory = ArtifactMemory(self.memory_path)
        self.addCleanup(self.memory.close)
        self.journal = Journal.resume(self.journal_path, expected_head=head)
        self.addCleanup(self.journal.close)
        restored_executor = FixtureExecutor(self.executor_calls)
        restored_tools = self.make_tools()
        restored = InvestigationSession.restore(self.task, self.memory, self.journal,
            restored_executor, restored_tools, context=CONTEXT, provenance=PROVENANCE,
            max_observations=8, max_predictions=16, max_program_executions=8)
        self.assertEqual(restored.goals.status({}), prior_goal_status)
        self.assertEqual(restored.goals.status({})['active'], goal)
        self.assertEqual(restored.discovery.active, representation)
        self.assertEqual(restored.discovery._cache, prior_cache)
        self.assertEqual(restored.usage(), before_usage | {'observations': 2, 'predictions': 2})
        self.assertEqual((len(self.executor_calls), len(self.observation_calls)), before_effects)

        # Local qualification is re-established only for the current scope and source.
        calls_before_check = len(self.executor_calls)
        checked = restored.checker(self.task.request, {'artifact': method})
        self.assertTrue(checked, restored.programs.checker_feedback(self.task.request,
            {'artifact': method}))
        self.assertEqual(len(self.executor_calls), calls_before_check)
        self.assertEqual(restored.programs.execution_attempts, 1)

    def test_changed_session_task_context_limits_provenance_or_tool_contract_rejects(self):
        self._checkpoint_with_active_session()
        head = Journal.read(self.journal_path)[-1]['hash']
        variants = [
            {'task': self.make_task(request='different request')},
            {'task': self.make_task(expected=99)},
            {'context': {'changed': True}},
            {'max_observations': 9},
            {'max_predictions': 17},
            {'max_program_executions': 9},
            {'provenance': {'producer': 'different', 'phase': 'development'}},
            {'tools': self.make_tools('Changed tool semantics.')},
        ]
        self.journal.close()
        for variant in variants:
            with self.subTest(variant=variant):
                journal = Journal.resume(self.journal_path, expected_head=head)
                try:
                    with self.assertRaises(ValueError):
                        InvestigationSession.restore(variant.get('task', self.task), self.memory,
                            journal, FixtureExecutor(self.executor_calls),
                            variant.get('tools', self.tools), context=variant.get('context', CONTEXT),
                            provenance=variant.get('provenance', PROVENANCE),
                            max_observations=variant.get('max_observations', 8),
                            max_predictions=variant.get('max_predictions', 16),
                            max_program_executions=variant.get('max_program_executions', 8))
                finally:
                    journal.close()

    def test_restore_rejects_pending_program_precommit(self):
        self._checkpoint_with_active_session(pending_program=True)
        events = Journal.read(self.journal_path)
        head = events[-1]['hash']
        self.journal.close()
        journal = Journal.resume(self.journal_path, expected_head=head)
        try:
            with self.assertRaisesRegex(ValueError, 'unfinished or over-budget'):
                InvestigationSession.restore(self.task, self.memory, journal,
                    FixtureExecutor(self.executor_calls), self.tools, context=CONTEXT,
                    provenance=PROVENANCE, max_observations=8, max_predictions=16,
                    max_program_executions=8)
        finally:
            journal.close()

    def test_earlier_qualification_does_not_restore_after_later_counterexample(self):
        _, _, method = self._checkpoint_with_active_session(invalidate_method=True)
        head = Journal.read(self.journal_path)[-1]['hash']
        self.journal.close()
        journal = Journal.resume(self.journal_path, expected_head=head)
        try:
            restored = InvestigationSession.restore(self.task, self.memory, journal,
                FixtureExecutor(self.executor_calls), self.tools, context=CONTEXT,
                provenance=PROVENANCE, max_observations=8, max_predictions=16,
                max_program_executions=8)
            self.assertEqual(self.memory.status(method, self.task.scope_id), 'invalidated')
            self.assertNotIn(method, restored.programs._locally_qualified)
            attempts = restored.programs.execution_attempts
            checked = restored.checker(self.task.request, {'artifact': method})
            self.assertTrue(checked, restored.programs.checker_feedback(self.task.request,
                {'artifact': method}))
            self.assertEqual(restored.programs.execution_attempts, attempts + 1)
        finally:
            journal.close()


if __name__ == '__main__':
    unittest.main()
