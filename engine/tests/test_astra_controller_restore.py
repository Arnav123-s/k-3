"""Controller replay is state reconstruction, not effect resumption."""

import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.discovery import DiscoveryWorkspace
from kuttaka_runtime.goals import GoalLedger
from kuttaka_runtime.store import canonical, digest


class MemoryJournal:
    def __init__(self, events=None):
        self.events = list(events or [])

    def append(self, kind, body):
        body = json.loads(canonical(body))
        event = {'seq': len(self.events), 'kind': kind, 'body': body,
                 'prev': self.events[-1]['hash'] if self.events else ''}
        event['hash'] = digest(event)
        self.events.append(event)
        return event['hash']


class ControllerRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = ArtifactMemory(Path(self.tmp.name) / 'memory')
        self.addCleanup(self.memory.close)

    def test_goal_ledger_split_replay_preserves_original_signal_and_authority(self):
        journal = MemoryJournal()
        ledger = GoalLedger('Make a useful table', journal)
        original_evidence = ledger.request_evidence
        clarification = ledger.record_signal('Use three columns', source='user')
        first = ledger.propose({'description': 'table with three columns',
            'success_criteria': ['three columns'], 'unknowns': [], 'tradeoffs': [],
            'parent': None, 'based_on': [original_evidence]})['goal']
        ledger.authorize(first, user_evidence=original_evidence)
        second = ledger.propose({'description': 'table with three requested columns',
            'success_criteria': ['three columns'], 'unknowns': ['row count'],
            'tradeoffs': [], 'parent': first, 'based_on': [clarification]})['goal']
        ledger.authorize(second, user_evidence=clarification)

        restored_journal = MemoryJournal(journal.events)
        restored = GoalLedger.restore('Make a useful table', restored_journal, journal.events)
        self.assertEqual(restored.request_evidence, original_evidence)
        self.assertEqual(restored.status({}), ledger.status({}))
        self.assertEqual(len(restored_journal.events), len(journal.events))
        self.assertEqual(restored.journal.events, journal.events)  # no replay writes

    def _tools(self, calls):
        def observe(value):
            """Return twice the supplied integer."""
            calls.append(('observe', value))
            return value * 2
        return {'double': observe}

    def _executor(self, calls):
        def execute(job):
            calls.append(('execute', job['input']))
            value = job['input']['input']
            result = value * 2 if job['source'] == 'correct' else -1
            return {'status': 'completed', 'exit_code': 0,
                    'output': json.dumps({'result': result})}
        return execute

    def test_discovery_restore_replays_cache_retrospective_fit_and_fresh_adoption(self):
        events = MemoryJournal()
        effects = []
        tools = self._tools(effects)
        executor = self._executor(effects)
        workspace = DiscoveryWorkspace(self.memory, events, executor, tools,
                                        context={'unit': 'fixture'}, max_observations=5,
                                        max_executions=12)
        old = workspace.measure({'representation': None, 'tool': 'double', 'input': 3})
        rid = workspace.represent({'name': 'doubling', 'source': 'correct',
                                   'parent': None, 'based_on': [old['evidence']]})['representation']
        self.assertEqual(workspace.assess({'representation': rid})['status'], 'retrospective_fit')
        fresh = workspace.measure({'representation': rid, 'tool': 'double', 'input': 4})
        self.assertEqual(fresh['status'], 'prediction_matched')
        repeated = workspace.measure({'representation': rid, 'tool': 'double', 'input': 4})
        self.assertTrue(repeated['cached'])
        self.assertEqual(workspace.assess({'representation': rid})['fresh_matches'], 1)
        self.assertTrue(workspace.adopt({'representation': rid})['adopted'])
        count_before = len(effects)

        restored_journal = MemoryJournal(events.events)
        restored = DiscoveryWorkspace.restore(self.memory, restored_journal,
            self._executor(effects), self._tools(effects), context={'unit': 'fixture'},
            events=events.events)
        self.assertEqual(len(effects), count_before, 'restore must not execute callbacks or candidates')
        self.assertEqual(restored_journal.events, events.events, 'restore must not append events')
        self.assertEqual(restored._cache, workspace._cache)
        self.assertEqual(restored._evidence, workspace._evidence)
        self.assertEqual(restored.observations, workspace.observations)
        self.assertEqual(restored.executions, workspace.executions)
        self.assertEqual(restored.active, rid)
        self.assertEqual(restored.assess({'representation': rid}),
                         workspace.assess({'representation': rid}))

    def test_discovery_restores_failed_candidate_case_and_cached_probes_without_credit(self):
        events = MemoryJournal()
        effects = []
        tools = self._tools(effects)
        workspace = DiscoveryWorkspace(self.memory, events, self._executor(effects), tools,
                                        context={}, max_observations=4, max_executions=8)
        rid = workspace.represent({'name': 'wrong', 'source': 'wrong', 'parent': None,
                                   'based_on': []})['representation']
        observed = workspace.measure({'representation': rid, 'tool': 'double', 'input': 2})
        self.assertEqual(observed['status'], 'contradicted')
        cached = workspace.measure({'representation': None, 'tool': 'double', 'input': 2})
        self.assertTrue(cached['cached'])
        self.assertEqual(workspace.observations, 1)
        before = len(effects)

        restored_journal = MemoryJournal(events.events)
        restored = DiscoveryWorkspace.restore(self.memory, restored_journal,
            self._executor(effects), self._tools(effects), context={}, events=events.events)
        self.assertEqual(len(effects), before)
        self.assertEqual(restored.assess({'representation': rid})['status'], 'contradicted')
        self.assertEqual(restored._candidates[rid]['fresh'], set())
        self.assertEqual(restored.observations, 1)
        self.assertEqual(restored.executions, 1)

    def test_discovery_retains_failed_execution_case_and_cumulative_budget(self):
        events = MemoryJournal()
        effects = []
        tools = self._tools(effects)
        def failing_executor(job):
            effects.append(('failed-execute', job['input']))
            raise RuntimeError('fixture failure')
        workspace = DiscoveryWorkspace(self.memory, events, failing_executor, tools,
                                        context={}, max_observations=3, max_executions=3)
        rid = workspace.represent({'name': 'candidate', 'source': 'correct',
                                   'parent': None, 'based_on': []})['representation']
        self.assertEqual(workspace.measure({'representation': rid, 'tool': 'double',
                                            'input': 7})['status'], 'incomplete')
        before = len(effects)
        restored_journal = MemoryJournal(events.events)
        restored = DiscoveryWorkspace.restore(self.memory, restored_journal,
            self._executor(effects), self._tools(effects), context={}, events=events.events)
        self.assertEqual(len(effects), before)
        self.assertEqual(restored.executions, 1)
        self.assertIn('{"input":7,"tool":"double"}', restored._candidates[rid]['failed_cases'])
        self.assertEqual(restored.assess({'representation': rid})['status'], 'incomplete')
        self.assertEqual(restored.executions, 1)  # no silent retry
        retried = restored.measure({'representation': rid, 'tool': 'double', 'input': 7})
        self.assertEqual(retried['status'], 'prediction_matched')
        self.assertEqual(restored._candidates[rid]['failed_cases'], {})
        self.assertEqual(restored.assess({'representation': rid})['fresh_matches'], 1)
        restored_again = DiscoveryWorkspace.restore(self.memory, MemoryJournal(restored_journal.events),
            self._executor(effects), self._tools(effects), context={}, events=restored_journal.events)
        self.assertEqual(restored_again.executions, 2)
        self.assertEqual(restored_again._candidates[rid]['failed_cases'], {})
        self.assertEqual(restored_again._candidates[rid]['fresh'], restored._candidates[rid]['fresh'])

    def _adopted_workspace(self, effects):
        journal = MemoryJournal()
        def observe(value):
            effects.append(('observe', value))
            return {'y': value['x']}
        observe.__doc__ = 'Return the input x as y.'
        def execute(job):
            effects.append(('execute', job['input']))
            value = job['input']['input']['x']
            result = value if job['source'] == 'identity' else 0
            return {'status': 'completed', 'exit_code': 0,
                    'output': json.dumps({'result': {'y': result}})}
        workspace = DiscoveryWorkspace(self.memory, journal, execute, {'observe': observe},
                                       context={'fixture': True}, max_observations=5,
                                       max_executions=12)
        candidate = workspace.represent({'name': 'identity', 'source': 'identity',
            'parent': None, 'based_on': []})['representation']
        workspace.measure({'representation': candidate, 'tool': 'observe', 'input': {'x': 1}})
        self.assertTrue(workspace.adopt({'representation': candidate})['adopted'])
        return workspace, journal, candidate, observe, execute

    def test_restore_replays_adopted_candidate_suspension_before_measurement_result(self):
        effects = []
        workspace, journal, candidate, observe, execute = self._adopted_workspace(effects)
        # New unpredicted evidence suspends the active candidate before the result event.
        workspace.measure({'representation': None, 'tool': 'observe', 'input': {'x': 4}})
        self.assertIsNone(workspace.active)
        restored = DiscoveryWorkspace.restore(self.memory, MemoryJournal(journal.events),
            execute, {'observe': observe}, context={'fixture': True}, events=journal.events)
        self.assertIsNone(restored.active)
        self.assertEqual(restored._evidence, workspace._evidence)
        self.assertEqual(restored._candidates[candidate]['checks'],
                         workspace._candidates[candidate]['checks'])

    def test_other_candidate_probe_suspends_adopted_candidate_on_restore(self):
        effects = []
        workspace, journal, adopted, observe, execute = self._adopted_workspace(effects)
        other = workspace.represent({'name': 'other', 'source': 'identity',
            'parent': adopted, 'based_on': []})['representation']
        self.assertNotEqual(other, adopted)
        workspace.measure({'representation': other, 'tool': 'observe', 'input': {'x': 6}})
        self.assertIsNone(workspace.active)
        restored = DiscoveryWorkspace.restore(self.memory, MemoryJournal(journal.events),
            execute, {'observe': observe}, context={'fixture': True}, events=journal.events)
        self.assertIsNone(restored.active)
        self.assertEqual(restored._candidates[other]['fresh'], workspace._candidates[other]['fresh'])

    def test_restore_rejects_context_contract_tampering_and_artifact_mismatch(self):
        events = MemoryJournal()
        effects = []
        tools = self._tools(effects)
        workspace = DiscoveryWorkspace(self.memory, events, self._executor(effects), tools, context={})
        workspace.represent({'name': 'x', 'source': 'correct', 'parent': None, 'based_on': []})
        for context, other_tools in (({'changed': True}, tools),
                                     ({}, {'double': lambda value: value})):
            with self.subTest(context=context), self.assertRaises(ValueError):
                DiscoveryWorkspace.restore(self.memory, MemoryJournal(events.events),
                    self._executor(effects), other_tools, context=context, events=events.events)

        bad = json.loads(json.dumps(events.events))
        representation = next(event for event in bad if event['kind'] == 'discovery_representation')
        representation['body']['payload']['body']['source'] = 'forged'
        # Rehash the edited suffix to ensure artifact identity, rather than only journal hash,
        # detects the mismatch.
        previous = ''
        for index, event in enumerate(bad):
            event['seq'], event['prev'] = index, previous
            event.pop('hash', None)
            event['hash'] = digest(event)
            previous = event['hash']
        with self.assertRaises(ValueError):
            DiscoveryWorkspace.restore(self.memory, MemoryJournal(bad), self._executor(effects),
                                       tools, context={}, events=bad)

    def test_restore_rejects_pending_external_prediction_or_measurement(self):
        effects = []
        tools = self._tools(effects)
        events = MemoryJournal()
        workspace = DiscoveryWorkspace(self.memory, events, self._executor(effects), tools, context={})
        rid = workspace.represent({'name': 'x', 'source': 'correct', 'parent': None, 'based_on': []})['representation']
        events.append('discovery_prediction_attempt', {'representation': rid,
            'case': {'tool': 'double', 'input': 1}, 'mode': 'prospective', 'number': 1})
        with self.assertRaisesRegex(ValueError, 'pending prediction'):
            DiscoveryWorkspace.restore(self.memory, MemoryJournal(events.events),
                self._executor(effects), tools, context={}, events=events.events)

        measurement_events = MemoryJournal()
        measuring = DiscoveryWorkspace(self.memory, measurement_events, self._executor(effects),
                                       tools, context={})
        measurement_events.append('discovery_measurement_attempt', {
            'case': {'tool': 'double', 'input': 1}, 'number': 1,
            'representation': None, 'prediction_receipt': None})
        with self.assertRaisesRegex(ValueError, 'pending measurement'):
            DiscoveryWorkspace.restore(self.memory, MemoryJournal(measurement_events.events),
                self._executor(effects), tools, context={}, events=measurement_events.events)

    def test_legacy_unversioned_discovery_prefix_is_not_restored(self):
        events = MemoryJournal()
        effects = []
        tools = self._tools(effects)
        DiscoveryWorkspace(self.memory, events, self._executor(effects), tools, context={})
        legacy = json.loads(json.dumps(events.events))
        legacy[0]['body'].pop('version')
        previous = ''
        for index, event in enumerate(legacy):
            event['seq'], event['prev'] = index, previous
            event.pop('hash', None)
            event['hash'] = digest(event)
            previous = event['hash']
        with self.assertRaisesRegex(ValueError, 'unsupported discovery journal version'):
            DiscoveryWorkspace.restore(self.memory, MemoryJournal(legacy), self._executor(effects),
                                       tools, context={}, events=legacy)


if __name__ == '__main__':
    unittest.main()
