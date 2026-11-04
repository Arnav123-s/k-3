"""Program composition integration controls using trusted fake executors only."""

import json
from pathlib import Path
import tempfile
import unittest

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.programs import ProgramTask, ProgramTools
from kuttaka_runtime.runtime import Budget, investigate
from kuttaka_runtime.store import Journal


PROVENANCE = {'model': 'authored-test', 'phase': 'development'}


class Scripted:
    def __init__(self, *actions):
        self.actions = iter(actions)

    def propose(self, context):
        return next(self.actions)


class ProgramCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.memory = ArtifactMemory(root / 'memory')
        self.addCleanup(self.memory.close)
        self.journal_path = root / 'journal.jsonl'
        self.journal = Journal(self.journal_path)
        self.addCleanup(self.journal.close)

    def tools(self, checks, executor, request='solve root task', max_executions=64):
        task = ProgramTask(request, checks)
        return ProgramTools(task, self.memory, self.journal, executor,
                            max_executions=max_executions, provenance=PROVENANCE)

    @staticmethod
    def response(result, **metadata):
        return {'status': 'completed', 'exit_code': 0,
                'output': json.dumps({'result': result}), **metadata}

    def remember(self, tools, name, source, uses=None):
        request = {'name': name, 'description': name + ' fixture', 'source': source}
        if uses is not None:
            request['uses'] = uses
        return tools.remember_method(request)['artifact']

    def test_instrumented_receipt_is_accepted_journaled_and_unknown_metadata_rejected(self):
        telemetry = {
            'container_name': 'astra-exec-test',
            'timings': {'stdin_complete_seconds': 0.01, 'first_output_seconds': 0.02,
                        'process_exit_seconds': 0.03, 'process_exit_forced': False,
                        'total_wall_seconds': 0.04},
            'inspect_state': None,
            'cleanup': {'status': 'completed', 'return_code': 0, 'timed_out': False,
                        'elapsed_seconds': 0.01, 'stdout': '', 'stderr': ''},
            'dependency_calls': [],
        }
        tools = self.tools([{'input': 4, 'expected': 4}],
                           lambda job: self.response(job['input'], **telemetry))
        aid = self.remember(tools, 'identity', 'def run(value): return value')
        validated = tools.validate_method({'artifact': aid})
        self.assertEqual(validated['status'], 'passed')
        events = Journal.read(self.journal_path)
        metadata = next(event['body']['metadata'] for event in events
                        if event['kind'] == 'program_execution_metadata')
        self.assertEqual(metadata, telemetry)

        malformed = self.tools([{'input': 1, 'expected': 1}],
                               lambda job: {**self.response(job['input']), 'future': True})
        bad_aid = self.remember(malformed, 'unknown-metadata', 'def run(value): return value')
        self.assertEqual(malformed.validate_method({'artifact': bad_aid})['status'], 'incomplete')

    def test_only_explicit_declared_dependency_calls_are_attributed(self):
        calls = []
        base = self.tools([{'input': 5, 'expected': 5}], lambda job: self.response(5))
        unused = self.remember(base, 'unused', 'def run(value): return value')
        def executor(job):
            calls.append(job)
            return self.response(job['input'], dependency_calls=[])
        composite_tools = self.tools([{'input': 5, 'expected': 5}], executor)
        root = self.remember(composite_tools, 'root', 'def run(value): return value', [unused])
        self.assertEqual(composite_tools.validate_method({'artifact': root})['status'], 'passed')
        self.assertEqual(calls[0]['libraries'], {unused: 'def run(value): return value'})
        result = next(e['body'] for e in Journal.read(self.journal_path)
                      if e['kind'] == 'program_execution_result')
        self.assertEqual(result['dependency_calls'], [])

        declared_tools = self.tools([{'input': 5, 'expected': 6}],
            lambda job: self.response(6, dependency_calls=[unused]))
        caller = self.remember(declared_tools, 'caller', 'def run(value): return value', [unused])
        self.assertEqual(declared_tools.validate_method({'artifact': caller})['status'], 'passed')
        result = [e['body'] for e in Journal.read(self.journal_path)
                  if e['kind'] == 'program_execution_result'][-1]
        self.assertEqual(result['dependency_calls'], [unused])

        undeclared = self.tools([{'input': 1, 'expected': 1}],
            lambda job: self.response(1, dependency_calls=['f' * 64]))
        candidate = self.remember(undeclared, 'undeclared-caller', 'def run(value): return value')
        self.assertEqual(undeclared.validate_method({'artifact': candidate})['status'], 'incomplete')

    def test_composite_child_gets_component_only_status_and_is_still_checked_independently(self):
        executor_calls = []
        child_source = 'def run(value): return value + 1'
        def executor(job):
            executor_calls.append(job)
            # Root composes correctly on 4, but child alone cannot satisfy root expected 5.
            if job.get('libraries'):
                result = job['libraries'][child]
                return self.response(5, dependency_calls=[child])
            return self.response(4 if job['source'] == child_source else 5)
        tools = self.tools([{'input': 4, 'expected': 5}], executor)
        child = self.remember(tools, 'increment', child_source)
        root = self.remember(tools, 'composed', 'def run(value): return 5', [child])
        self.assertEqual(tools.validate_method({'artifact': root})['status'], 'passed')
        catalog = tools.find_methods({'query': '', 'limit': 10})['methods']
        child_row = next(item for item in catalog if item['id'] == child)
        root_row = next(item for item in catalog if item['id'] == root)
        self.assertEqual(child_row['status'], 'component_only')
        self.assertEqual(child_row['qualified_scopes'], 0)
        self.assertEqual(child_row['component_scopes'], 1)
        self.assertEqual(root_row['qualified_scopes'], 1)

        # This second validation must invoke the child source as the top-level candidate;
        # it fails the root task's expected result and does not promote component evidence.
        result = tools.validate_method({'artifact': child})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['counterexample_checks'], [0])
        self.assertEqual(executor_calls[-1]['source'], child_source)
        self.assertEqual(tools.memory.status(child, tools.task.scope_id), 'invalidated')

    def test_exact_nested_dependency_sources_are_sent_within_closure_cap(self):
        executor_jobs = []
        tools = self.tools([{'input': 1, 'expected': 1}],
                           lambda job: (executor_jobs.append(job) or self.response(1)))
        previous = None
        expected_sources = {}
        for index in range(16):
            source = f'def run(value): return value + {index}'
            uses = [] if previous is None else [previous]
            previous = self.remember(tools, f'layer-{index}', source, uses)
            expected_sources[previous] = source
        outer = self.remember(tools, 'outer', 'def run(value): return value', [previous])
        self.assertEqual(tools.validate_method({'artifact': outer})['status'], 'passed')
        libraries = executor_jobs[0]['libraries']
        self.assertEqual(len(libraries), 16)
        self.assertLessEqual(len(libraries), 16)
        self.assertEqual(libraries, expected_sources)

    def test_tool_input_rejection_consumes_budget_and_preserves_feedback_for_correction(self):
        tools = self.tools([{'input': 2, 'expected': 2}],
                           lambda job: self.response(job['input']))
        actions = [
            {'kind': 'experiment', 'tool': 'remember_method', 'input': {},
             'predictions': [{'hypothesis': 'store', 'outcome': 'stored'}]},
            {'kind': 'experiment', 'tool': 'remember_method',
             'input': {'name': 'identity', 'description': 'returns input',
                       'source': 'def run(value): return value'},
             'predictions': [{'hypothesis': 'store', 'outcome': 'stored'}]},
        ]
        result = investigate('store a method', Scripted(*actions), tools.tools(),
                             lambda request, answer: True, self.journal,
                             budget=Budget(model_calls=2, tool_calls=2))
        events = Journal.read(self.journal_path)
        rejection = next(e for e in events if e['kind'] == 'tool_error')
        observation = next(e['body'] for e in events if e['kind'] == 'observation')
        self.assertEqual(rejection['body']['type'], 'ToolInputError')
        self.assertEqual(observation['observation']['status'], 'tool_rejection')
        self.assertIn('remember_method requires name', observation['observation']['feedback'])
        self.assertEqual(result.tool_calls, 2)
        self.assertEqual(result.status, 'incomplete')  # model budget ended after corrected storage
        self.assertEqual(len([e for e in events if e['kind'] == 'proposal']), 2)
        self.assertEqual(len([e for e in events if e['kind'] == 'method_remembered']), 1)


if __name__ == '__main__':
    unittest.main()
