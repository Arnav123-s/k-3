"""Broker controls using a scripted executor; no host source execution."""

import json
from pathlib import Path
import tempfile
import unittest

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.programs import ProgramTask, ProgramTools, QUALIFIER_ID
from kuttaka_runtime.store import Journal


SOURCE = 'def run(input):\n    return input'
PROVENANCE = {'model': 'test-model', 'phase': 'development'}


class FakeExecutor:
    def __init__(self, journal_path=None, response=None):
        self.journal_path = journal_path
        self.response = response or (lambda job: {
            'status': 'completed', 'exit_code': 0,
            'output': json.dumps({'result': job['input']}, allow_nan=False)})
        self.calls = []

    def __call__(self, job):
        self.calls.append(json.loads(json.dumps(job)))
        if self.journal_path is not None:
            events = Journal.read(self.journal_path)
            self.assert_precommitted(events, job)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response(job) if callable(self.response) else self.response

    @staticmethod
    def assert_precommitted(events, job):
        event = events[-1]
        if event['kind'] != 'program_execution_precommit':
            raise AssertionError('executor called before durable execution precommit')
        if event['body']['input'] != job['input']:
            raise AssertionError('precommit input differs from executor input')


class ProgramToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.memory = ArtifactMemory(root / 'memory')
        self.addCleanup(self.memory.close)
        self.journal_path = root / 'journal.jsonl'
        self.journal = Journal(self.journal_path)
        self.addCleanup(self.journal.close)
        self.task = ProgramTask.from_dict({
            'request': 'Store an identity method',
            'checks': [{'input': 1, 'expected': 1}, {'input': {'x': 2}, 'expected': {'x': 2}}],
            'context': {'units': 'abstract'}, 'input': ['deployment'],
        })
        self.executor = FakeExecutor(self.journal_path)
        self.tools = ProgramTools(self.task, self.memory, self.journal,
                                  self.executor, provenance=PROVENANCE)

    def remember(self, tools=None):
        tools = tools or self.tools
        return tools.remember_method({'name': 'identity', 'description': 'Returns its input',
                                      'source': SOURCE})['artifact']

    def test_manifest_snapshot_scope_and_validation(self):
        manifest = {'request': 'Do work', 'checks': [{'input': 2, 'expected': 2}],
                    'context': {'unit': 'u'}, 'input': 8}
        task = ProgramTask.from_dict(manifest)
        scope = task.scope_id
        manifest['checks'][0]['expected'] = 0
        manifest['context']['unit'] = 'changed'
        manifest['input'] = 100
        self.assertEqual(task.checks, [{'input': 2, 'expected': 2}])
        self.assertEqual(task.context, {'unit': 'u'})
        self.assertEqual(task.input, 8)
        changed_input = ProgramTask.from_dict({
            'request': 'Do work', 'checks': [{'input': 2, 'expected': 2}],
            'context': {'unit': 'u'}, 'input': 9})
        self.assertEqual(changed_input.scope_id, scope)
        with self.assertRaises(AttributeError):
            task._request = 'mutate'
        for bad in (
            {'request': '', 'checks': [{'input': 1, 'expected': 1}]},
            {'request': 'x', 'checks': []},
            {'request': 'x', 'checks': [{'input': 1, 'expected': 1, 'extra': 2}]},
            {'request': 'x', 'checks': [{'input': float('nan'), 'expected': 1}]},
            {'request': 'x', 'checks': [{'input': 1, 'expected': 1}], 'unknown': 1},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ProgramTask.from_dict(bad)
        with self.assertRaises(ValueError):
            ProgramTask.from_dict({'request': 'x', 'checks': [
                {'input': 1, 'expected': 1}] * 65})

    def test_search_fallback_exposes_candidates_without_qualifying_them(self):
        self.assertEqual(self.tools.find_methods({'query': 'unknown', 'limit': 1}),
                         {'methods': [], 'retrieval': 'empty'})
        artifact = self.remember()
        result = self.tools.find_methods({'query': 'unrelated vocabulary', 'limit': 1})
        self.assertEqual(result['retrieval'], 'unfiltered_catalog')
        self.assertEqual([m['id'] for m in result['methods']], [artifact])
        self.assertEqual(result['methods'][0]['status'], 'proposed')
        self.assertEqual(self.memory.status(artifact, self.task.scope_id), 'proposed')
        self.assertEqual(self.executor.calls, [])
        self.assertEqual(self.tools.find_methods({'query': 'identity', 'limit': 1})['retrieval'],
                         'query_matches')
        self.assertEqual(self.tools.find_methods({'query': '', 'limit': 1})['retrieval'], 'catalog')

    def test_remembered_source_has_provenance_and_no_user_qualification(self):
        artifact = self.remember()
        payload = self.memory.get(artifact)
        self.assertEqual(payload['kind'], 'method')
        self.assertEqual(payload['body'], {'language': 'python', 'name': 'identity',
                                           'description': 'Returns its input', 'source': SOURCE})
        self.assertEqual(payload['provenance'], PROVENANCE)
        self.assertEqual(payload['dependencies'], [])
        self.assertEqual(self.memory.status(artifact, self.task.scope_id), 'proposed')
        self.assertEqual(self.journal_path.read_text().count('method_remembered'), 1)
        self.assertEqual(self.tools.inspect_method({'artifact': artifact})['artifact'], artifact)

    def test_probe_precommits_exact_input_and_counts_attempt(self):
        artifact = self.remember()
        result = self.tools.probe_method({'artifact': artifact, 'input': {'nested': [True, 2]}})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result'], {'nested': [True, 2]})
        self.assertEqual(self.tools.execution_attempts, 1)
        events = Journal.read(self.journal_path)
        proposal = next(event for event in events if event['kind'] == 'program_execution_precommit')
        self.assertEqual(proposal['body']['artifact'], artifact)
        self.assertEqual(proposal['body']['input'], {'nested': [True, 2]})

    def test_executor_diagnostics_are_bounded_and_returned_for_repair(self):
        artifact = self.remember()
        self.executor.response = {
            'status': 'completed', 'exit_code': 0,
            'output': '{"result":1}', 'diagnostics': 'candidate printed a note'}
        result = self.tools.probe_method({'artifact': artifact, 'input': 1})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['diagnostics'], 'candidate printed a note')
        execution_event = [e for e in Journal.read(self.journal_path)
                           if e['kind'] == 'program_execution_result'][-1]
        self.assertEqual(execution_event['body']['diagnostics'], 'candidate printed a note')

        self.executor.response = {
            'status': 'completed', 'exit_code': 0, 'output': '{"result":1}',
            'diagnostics': 'x' * 40000}
        bounded = self.tools.probe_method({'artifact': artifact, 'input': 1})
        self.assertLessEqual(len(bounded['diagnostics'].encode('utf-8')), 32768)

        self.executor.response = {
            'status': 'completed', 'exit_code': 1, 'output': '',
            'diagnostics': 'SyntaxError: invalid syntax'}
        failed = self.tools.validate_method({'artifact': artifact})
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['failed_checks'][0]['check_index'], 0)
        self.assertEqual(failed['failed_checks'][0]['input'], 1)
        self.assertEqual(failed['failed_checks'][0]['expected'], 1)
        self.assertEqual(failed['failed_checks'][0]['diagnostics'],
                         'SyntaxError: invalid syntax')

        self.executor.response = {
            'status': 'invalid_output', 'exit_code': 0, 'output': 'bad',
            'diagnostics': 'runner rejected output'}
        malformed = self.tools.probe_method({'artifact': artifact, 'input': 1})
        self.assertEqual(malformed['status'], 'candidate_error')
        self.assertEqual(malformed['diagnostics'], 'runner rejected output')

    def test_docker_startup_exit_is_incomplete_not_a_counterexample(self):
        artifact = self.remember()
        self.assertEqual(self.tools.validate_method({'artifact': artifact})['status'], 'passed')
        self.executor.response = {'status': 'completed', 'exit_code': 125,
                                  'output': '', 'diagnostics': 'docker daemon unavailable'}
        result = self.tools.validate_method({'artifact': artifact})
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('counterexample_checks', result)
        self.assertEqual(self.memory.status(artifact, self.task.scope_id), 'qualified')

    def test_checker_feedback_returns_counterexamples_then_clears_on_success(self):
        artifact = self.remember()
        self.executor.response = lambda job: {
            'status': 'completed', 'exit_code': 0, 'output': '{"result":"wrong"}'}
        self.assertFalse(self.tools.checker(self.task.request, {'artifact': artifact}))
        feedback = self.tools.checker_feedback(self.task.request, {'artifact': artifact})
        self.assertEqual(feedback['failed_checks'][0], {
            'check_index': 0, 'input': 1, 'expected': 1, 'actual': 'wrong'})
        feedback['failed_checks'].clear()
        self.assertEqual(len(self.tools.checker_feedback(self.task.request, {})['failed_checks']), 2)
        self.executor.response = lambda job: {
            'status': 'completed', 'exit_code': 0, 'output': json.dumps({'result': job['input']})}
        self.assertTrue(self.tools.checker(self.task.request, {'artifact': artifact}))
        self.assertIsNone(self.tools.checker_feedback(self.task.request, {'artifact': artifact}))

    def test_checker_feedback_preserves_infrastructure_failure(self):
        artifact = self.remember()
        self.executor.response = RuntimeError('private executor detail')
        self.assertFalse(self.tools.checker(self.task.request, {'artifact': artifact}))
        feedback = self.tools.checker_feedback(self.task.request, {'artifact': artifact})
        self.assertEqual(feedback['status'], 'incomplete')
        self.assertEqual(feedback['infrastructure_checks'], [0, 1])
        self.assertNotIn('failed_checks', feedback)
        self.assertNotIn('private executor detail', json.dumps(feedback))

    def test_checker_requires_qualified_dependency_closure(self):
        dependency = self.memory.put({
            'kind': 'representation', 'body': {'name': 'base'}, 'dependencies': [],
            'provenance': PROVENANCE, 'applicability': {}})
        self.memory.qualify(dependency, self.task.scope_id, 'base-checker', ['base-evidence'])
        method = self.memory.put({
            'kind': 'method',
            'body': {'language': 'python', 'name': 'dependent', 'description': 'test',
                     'source': SOURCE},
            'dependencies': [dependency], 'provenance': PROVENANCE,
            'applicability': {}})
        self.assertTrue(self.tools.checker(self.task.request, {'artifact': method}))
        self.assertEqual(self.memory.status(method, self.task.scope_id), 'qualified')
        # Directly make the dependency unusable in scope; the final checker must
        # inspect the qualification closure, even if the method's local status says qualified.
        self.memory.invalidate(dependency, 'dependency failed', ['dependency-counterexample'],
                               scope=self.task.scope_id)
        self.assertFalse(self.tools.checker(self.task.request, {'artifact': method}))

    def test_all_checks_qualify_and_checker_reuses_local_attestation(self):
        artifact = self.remember()
        answer = {'artifact': artifact}
        self.assertTrue(self.tools.checker(self.task.request, answer))
        self.assertEqual(self.tools.execution_attempts, 2)
        self.assertEqual(self.memory.status(artifact, self.task.scope_id), 'qualified')
        qualify = [e for e in self.memory._events if e['kind'] == 'qualify'][-1]
        self.assertEqual(qualify['body']['checker'], QUALIFIER_ID)
        self.assertEqual(len(qualify['body']['evidence']), 2)
        self.assertTrue(self.tools.checker(self.task.request, answer))
        self.assertEqual(self.tools.execution_attempts, 2)
        self.assertFalse(self.tools.checker('different request', answer))
        self.assertFalse(self.tools.checker(self.task.request, {'artifact': artifact, 'extra': 1}))

    def test_new_tools_instance_does_not_trust_opaque_existing_attestation(self):
        artifact = self.remember()
        self.assertEqual(self.tools.validate_method({'artifact': artifact})['status'], 'passed')
        journal2_path = Path(self.tmp.name) / 'journal2.jsonl'
        with Journal(journal2_path) as journal2:
            limited_executor = FakeExecutor(journal2_path)
            second = ProgramTools(self.task, self.memory, journal2, limited_executor,
                                  max_executions=1, provenance=PROVENANCE)
            self.assertFalse(second.checker(self.task.request, {'artifact': artifact}))
            self.assertEqual(second.execution_attempts, 1)
            self.assertEqual(self.memory.status(artifact, self.task.scope_id), 'qualified')

    def test_wrong_output_is_counterexample_and_scoped_invalidation(self):
        artifact = self.remember()
        self.assertEqual(self.tools.validate_method({'artifact': artifact})['status'], 'passed')
        other_scope = 'other-task-scope'
        self.memory.qualify(artifact, other_scope, 'other-checker', ['other-evidence'])
        self.executor.response = lambda job: {
            'status': 'completed', 'exit_code': 0,
            'output': json.dumps({'result': 'wrong'})}
        result = self.tools.validate_method({'artifact': artifact})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['counterexample_checks'], [0, 1])
        self.assertEqual(self.memory.status(artifact, self.task.scope_id), 'invalidated')
        self.assertEqual(self.memory.status(artifact, other_scope), 'qualified')

    def test_executor_failure_is_incomplete_not_a_counterexample(self):
        artifact = self.remember()
        self.executor.response = RuntimeError('secret details')
        result = self.tools.validate_method({'artifact': artifact})
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('counterexample_checks', result)
        self.assertEqual(self.memory.status(artifact, self.task.scope_id), 'proposed')
        rows = Journal.read(self.journal_path)
        self.assertTrue(any(e['kind'] == 'program_execution_result'
                            and e['body'].get('error_type') == 'RuntimeError' for e in rows))
        self.assertNotIn('secret details', self.journal_path.read_text())

    def test_budget_counts_failed_calls_and_never_qualifies_partial_checks(self):
        artifact = self.remember()
        self.executor.response = RuntimeError('failure')
        self.tools.max_executions = 1
        result = self.tools.validate_method({'artifact': artifact})
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['checks_completed'], 1)
        self.assertEqual(self.tools.execution_attempts, 1)
        probe = self.tools.probe_method({'artifact': artifact, 'input': 9})
        self.assertEqual(probe['status'], 'budget_exhausted')
        self.assertEqual(self.tools.execution_attempts, 1)
        self.assertEqual(self.memory.status(artifact, self.task.scope_id), 'proposed')

    def test_known_counterexample_is_not_erased_by_later_budget_exhaustion(self):
        artifact = self.remember()
        self.assertEqual(self.tools.validate_method({'artifact': artifact})['status'], 'passed')
        self.executor.response = lambda job: {
            'status': 'completed', 'exit_code': 0, 'output': '{"result":"wrong"}'}
        self.tools.max_executions = self.tools.execution_attempts + 1
        result = self.tools.validate_method({'artifact': artifact})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['counterexample_checks'], [0])
        self.assertEqual(self.memory.status(artifact, self.task.scope_id), 'invalidated')

    def test_bad_executor_output_is_counterexample_not_accepted_json(self):
        artifact = self.remember()
        outputs = ['noise\n{"result":1}', '{"result":1,"result":1}',
                   '{"result":NaN}', '{"wrong":1}', '1\n2']
        for output in outputs:
            with self.subTest(output=output):
                self.executor.response = lambda job, output=output: {
                    'status': 'completed', 'exit_code': 0, 'output': output}
                result = self.tools.validate_method({'artifact': artifact})
                self.assertEqual(result['status'], 'failed')
                self.assertIn(0, result['counterexample_checks'])
                # Clear scoped invalidation by making the artifact proposed again
                # only through an independent store instance is not supported; each
                # repetition remains a valid failure observation.

    def test_tool_catalog_and_malformed_tool_arguments(self):
        artifact = self.remember()
        self.assertEqual(self.tools.find_methods({'query': 'IDENTITY', 'limit': 5})['methods'][0]['id'],
                         artifact)
        names = set(self.tools.tools())
        self.assertEqual(names, {'remember_method', 'inspect_method', 'find_methods',
                                 'probe_method', 'validate_method'})
        for call, argument in ((self.tools.remember_method, {}),
                               (self.tools.inspect_method, {'artifact': artifact, 'extra': 1}),
                               (self.tools.find_methods, {'query': 'x', 'limit': True}),
                               (self.tools.probe_method, {'artifact': artifact}),
                               (self.tools.validate_method, {'artifact': artifact, 'expected': 1})):
            with self.subTest(call=call.__name__), self.assertRaises(ValueError):
                call(argument)

    def test_provenance_is_required_even_though_null_is_valid_json(self):
        with self.assertRaises(ValueError):
            ProgramTools(self.task, self.memory, self.journal, self.executor)
        nullable = ProgramTools(self.task, self.memory, self.journal, self.executor,
                                provenance=None)
        self.assertIsNone(nullable.provenance)


if __name__ == '__main__':
    unittest.main()
