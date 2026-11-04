"""Evidence and revision lifecycle tests for the model-independent discovery workspace."""

import json
from pathlib import Path
import tempfile
import unittest

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.discovery import DiscoveryWorkspace
from kuttaka_runtime.store import Journal


def encoded(value):
    return json.dumps({'result': value}, sort_keys=True, separators=(',', ':'))


class FakeExecutor:
    """Interpret named fixtures; never executes candidate source in the host."""

    def __init__(self, predictions=None, *, fail=False):
        self.predictions = predictions or {}
        self.fail = fail
        self.jobs = []

    def __call__(self, job):
        self.jobs.append(job)
        if self.fail:
            return {'status': 'timeout', 'exit_code': None, 'output': ''}
        source = job['source']
        case = job['input']
        prediction = self.predictions[source](case)
        return {'status': 'completed', 'exit_code': 0, 'output': encoded(prediction)}


class DiscoveryWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.journal = Journal(root / 'journal.jsonl')
        self.memory = ArtifactMemory(root / 'memory')
        self.oracle_calls = []
        self.callback_event_kinds = []
        journal_path = root / 'journal.jsonl'
        self.executor = FakeExecutor({
            'identity': lambda case: {'y': case['input']['x']},
            'overfit': lambda case: {'y': 0 if case['input']['x'] in (0, 1) else -1},
            'wrong': lambda case: {'y': 0},
        })
        def observe(value):
            self.oracle_calls.append(value)
            self.callback_event_kinds.extend(e['kind'] for e in Journal.read(journal_path))
            return {'y': value['x']}
        observe.__doc__ = 'Input {x: integer}; returns {y: integer}.'
        self.workspace = DiscoveryWorkspace(
            self.memory, self.journal, self.executor, {'observe': observe},
            context={'goal': 'infer y from x'}, max_observations=8, max_executions=16)

    def tearDown(self):
        self.journal.close()
        self.memory.close()
        self.tmp.cleanup()

    def measure(self, x, representation=None):
        return self.workspace.tools()['discover_measure']({
            'representation': representation, 'tool': 'observe', 'input': {'x': x}})

    def represent(self, source, *, parent=None, based_on=None, name='candidate'):
        return self.workspace.tools()['discover_represent']({
            'name': name, 'source': source, 'parent': parent,
            'based_on': [] if based_on is None else based_on})

    def test_observations_before_candidate_are_only_retrospective_and_cannot_be_adopted(self):
        observed = self.measure(2)
        candidate = self.represent('identity', based_on=[observed['evidence']])
        assessed = self.workspace.tools()['discover_assess']({'representation': candidate['representation']})
        adopted = self.workspace.tools()['discover_adopt']({'representation': candidate['representation']})
        self.assertEqual(assessed['status'], 'retrospective_fit')
        self.assertFalse(adopted['adopted'])
        self.assertIsNone(self.workspace.active)
        self.assertEqual(len(self.oracle_calls), 1)

    def test_prediction_is_journaled_before_oracle_callback(self):
        candidate = self.represent('identity')
        self.measure(3, candidate['representation'])
        self.assertIn('discovery_prediction', self.callback_event_kinds)
        kinds = [event['kind'] for event in Journal.read(Path(self.tmp.name) / 'journal.jsonl')]
        self.assertLess(kinds.index('discovery_prediction'), kinds.index('discovery_observation'))

    def test_overfitting_revision_fails_novel_probe_and_does_not_replace_active_parent(self):
        first = self.represent('identity')
        first_probe = self.measure(0, first['representation'])
        self.assertEqual(self.workspace.tools()['discover_adopt']({'representation': first['representation']})['status'], 'supported')
        child = self.represent('overfit', parent=first['representation'], based_on=[first_probe['evidence']])
        self.measure(8, child['representation'])
        assessed = self.workspace.tools()['discover_assess']({'representation': child['representation']})
        adopted = self.workspace.tools()['discover_adopt']({'representation': child['representation']})
        self.assertEqual(assessed['status'], 'contradicted')
        self.assertFalse(adopted['adopted'])
        self.assertIsNone(self.workspace.active)
        parent_status = self.workspace.tools()['discover_assess']({'representation': first['representation']})
        self.assertEqual(parent_status['status'], 'supported')
        restored = self.workspace.tools()['discover_adopt']({'representation': first['representation']})
        self.assertTrue(restored['adopted'])
        self.assertEqual(self.workspace.active, first['representation'])

    def test_assessment_replays_historical_failure_even_if_candidate_omits_it(self):
        failed = self.measure(4)
        candidate = self.represent('wrong', based_on=[])
        assessed = self.workspace.tools()['discover_assess']({'representation': candidate['representation']})
        self.assertEqual(assessed['status'], 'contradicted')
        self.assertEqual(len(assessed['mismatches']), 1)
        self.assertEqual(failed['observation'], {'y': 4})

    def test_duplicate_probe_uses_cache_without_adding_fresh_evidence(self):
        candidate = self.represent('identity')
        first = self.measure(5, candidate['representation'])
        repeated = self.measure(5, candidate['representation'])
        assessed = self.workspace.tools()['discover_assess']({'representation': candidate['representation']})
        self.assertEqual(len(self.oracle_calls), 1)
        self.assertFalse(first['cached'])
        self.assertTrue(repeated['cached'])
        self.assertEqual(repeated['evidence'], first['evidence'])
        self.assertEqual(assessed['status'], 'supported')
        self.assertEqual(assessed['fresh_matches'], 1)

    def test_executor_failure_is_incomplete_not_a_mathematical_contradiction(self):
        failed_executor = FakeExecutor(fail=True)
        workspace = DiscoveryWorkspace(self.memory, self.journal, failed_executor,
            {'observe': lambda value: {'y': value['x']}}, context={},
            max_observations=4, max_executions=4)
        candidate = workspace.tools()['discover_represent']({'name': 'failure-case',
            'source': 'identity', 'parent': None, 'based_on': []})
        result = workspace.tools()['discover_measure']({'representation': candidate['representation'],
                                                         'tool': 'observe', 'input': {'x': 6}})
        assessed = workspace.tools()['discover_assess']({'representation': candidate['representation']})
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(assessed['status'], 'incomplete')
        self.assertIsNone(workspace.active)

    def test_unknown_evidence_and_tool_reject_before_observation_or_execution(self):
        with self.assertRaises(ValueError):
            self.represent('identity', based_on=['0' * 64])
        candidate = self.represent('identity')
        jobs_before = len(self.executor.jobs)
        with self.assertRaises(ValueError):
            self.workspace.tools()['discover_measure']({'representation': candidate['representation'],
                                                         'tool': 'missing', 'input': {'x': 1}})
        self.assertEqual(len(self.oracle_calls), 0)
        self.assertEqual(len(self.executor.jobs), jobs_before)

    def test_budget_exhaustion_does_not_reset_when_candidate_is_revised(self):
        observed = []
        workspace = DiscoveryWorkspace(self.memory, self.journal, self.executor,
            {'observe': lambda value: observed.append(value) or {'y': value['x']}}, context={},
            max_observations=1, max_executions=1)
        one = workspace.tools()['discover_measure']({'representation': None, 'tool': 'observe', 'input': {'x': 1}})
        candidate = workspace.tools()['discover_represent']({'name': 'after-budget', 'source': 'identity',
                                                              'parent': None, 'based_on': [one['evidence']]})
        result = workspace.tools()['discover_measure']({'representation': candidate['representation'],
                                                         'tool': 'observe', 'input': {'x': 2}})
        assessed = workspace.tools()['discover_assess']({'representation': candidate['representation']})
        self.assertIn(result['status'], ('incomplete', 'budget_exhausted'))
        self.assertEqual(assessed['status'], 'retrospective_fit')
        self.assertEqual(len(observed), 1)


if __name__ == '__main__':
    unittest.main()
