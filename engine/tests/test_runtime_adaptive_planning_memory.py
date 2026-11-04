"""Goal-relative planning-view persistence controls; no evaluator data."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from kuttaka_runtime.adaptive_planning_memory import AdaptivePlanningMemory
from kuttaka_runtime.artifacts import ArtifactMemory


def goal_sensitive_spec():
    return {
        'variables': ['done', 'red'],
        'initial': {'done': False, 'red': False},
        'goal': 'done',
        'actions': {
            'finish': {'guard': True, 'cost': 1, 'assign': {'done': True}},
            'toggle_red': {'guard': True, 'cost': 0, 'assign': {'red': True}},
        },
    }


class AdaptivePlanningMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = ArtifactMemory(Path(self.temp.name) / 'memory')
        self.addCleanup(self.memory.close)
        self.context = {'world_version': 'fixture-v1'}
        self.wrapper = AdaptivePlanningMemory(self.memory,
            provenance={'attribution': 'authored test fixture'})

    def test_old_goal_plan_reuses_view_and_changed_goal_recompiles_dropped_dimension(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True,
                                      preserve='goal_cost')
        self.assertEqual(result['audit']['retained_variables'], ['done'])
        self.assertEqual(result['audit']['removed_actions'], ['toggle_red'])
        self.assertGreater(result['bytes']['source_spec_bytes'], 0)
        self.assertGreater(result['bytes']['view_record_bytes'], 0)

        with mock.patch('kuttaka_runtime.adaptive_planning_memory.compile_factored',
                        side_effect=AssertionError('same binding must reuse')):
            old_goal = self.wrapper.plan(result['view'], goal='done', context=self.context)
        self.assertTrue(old_goal['reused'])
        self.assertEqual(old_goal['plan']['actions'], ['finish'])
        self.assertEqual(old_goal['plan']['cost'], 1)

        revised = self.wrapper.plan(result['view'], goal='red', context=self.context)
        self.assertFalse(revised['reused'])
        self.assertTrue(revised['recompiled_for_goal'])
        self.assertNotEqual(revised['view'], result['view'])
        self.assertEqual(revised['audit']['retained_variables'], ['red'])
        self.assertEqual(revised['audit']['removed_actions'], ['finish'])
        self.assertEqual(revised['plan']['actions'], ['toggle_red'])
        self.assertEqual(revised['plan']['cost'], 0)
        self.assertEqual(self.memory.get(revised['view'])['dependencies'], [result['source']])

    def test_caller_mutation_cannot_change_persisted_source_or_context(self):
        spec = goal_sensitive_spec()
        context = dict(self.context)
        result = self.wrapper.compile(spec, context=context, complete=True)
        spec['goal'] = 'red'
        spec['initial']['done'] = True
        spec['actions'].clear()
        context['world_version'] = 'mutated'
        self.assertEqual(self.memory.get(result['source'])['body']['spec'], goal_sensitive_spec())
        reused = self.wrapper.plan(result['view'], goal='done', context=self.context)
        self.assertTrue(reused['reused'])
        self.assertEqual(reused['plan']['actions'], ['finish'])

    def test_source_and_view_survive_fresh_memory_process(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        source_id, view_id = result['source'], result['view']
        self.memory.close()
        self.memory = ArtifactMemory(Path(self.temp.name) / 'memory')
        self.addCleanup(self.memory.close)
        restored = AdaptivePlanningMemory(self.memory)
        old = restored.plan(view_id, goal='done', context=self.context)
        self.assertTrue(old['reused'])
        changed = restored.plan(view_id, goal='red', context=self.context)
        self.assertEqual(changed['source'], source_id)
        self.assertEqual(changed['plan']['actions'], ['toggle_red'])

    def test_changed_context_refuses_reuse_or_recompilation(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        refusal = self.wrapper.plan(result['view'], goal='red',
                                    context={'world_version': 'different'})
        self.assertEqual(refusal['status'], 'context_mismatch')
        self.assertEqual(refusal['view'], result['view'])

    def test_missing_retained_source_refuses_changed_goal(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        # Simulate an incomplete legacy/imported store: the view survived but
        # its source blob/index entry is absent.
        self.memory._records.pop(result['source'])
        refusal = self.wrapper.plan(result['view'], goal='red', context=self.context)
        self.assertEqual(refusal['status'], 'source_unavailable')

    def test_invalidating_source_invalidates_view_and_blocks_goal_recompile(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        self.memory.invalidate(result['source'], 'source model contradicted',
                               ['fixture-counterevidence'], scope=result['scope'])
        self.assertEqual(self.memory.status(result['view'], result['scope']), 'invalidated')
        refusal = self.wrapper.plan(result['view'], goal='red', context=self.context)
        self.assertEqual(refusal['status'], 'invalidated_view')

    def test_global_source_invalidation_also_blocks_cached_view(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        self.memory.invalidate(result['source'], 'source model withdrawn', ['global-counterevidence'])
        refusal = self.wrapper.plan(result['view'], goal='done', context=self.context)
        self.assertEqual(refusal['status'], 'invalidated_view')

    def test_recompiling_same_source_does_not_clear_scoped_invalidation(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        self.memory.invalidate(result['source'], 'source contradicted', ['scoped-evidence'],
                               scope=result['scope'])
        with self.assertRaisesRegex(ValueError, 'host revalidation is required'):
            self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        self.assertEqual(self.memory.status(result['source'], result['scope']), 'invalidated')

    def test_recompiling_same_source_does_not_clear_global_invalidation(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        self.memory.invalidate(result['source'], 'source withdrawn', ['global-evidence'])
        with self.assertRaisesRegex(ValueError, 'host revalidation is required'):
            self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        self.assertEqual(self.memory.status(result['source'], result['scope']), 'invalidated')

    def test_recompiling_cached_view_does_not_clear_direct_view_invalidation(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        self.memory.invalidate(result['view'], 'view counterexample', ['view-evidence'],
                               scope=result['scope'])
        with self.assertRaisesRegex(ValueError, 'cached view is invalidated'):
            self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        self.assertEqual(self.memory.status(result['view'], result['scope']), 'invalidated')

    def test_unqualified_forged_view_cannot_claim_host_compiler_output(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        original = self.memory.get(result['view'])
        forged_body = original['body']
        forged_body['record'] = {'invented': 'incorrect cached plan'}
        forged = dict(original, body=forged_body)
        forged_id = self.memory.put(forged)
        refusal = self.wrapper.plan(forged_id, goal='done', context=self.context)
        self.assertEqual(refusal, {'status': 'view_unqualified', 'view': forged_id})

    def test_view_digest_must_match_actual_retained_source(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True)
        original = self.memory.get(result['view'])
        forged_body = original['body']
        forged_body['source_digest'] = '0' * 64
        forged_body['binding'] = self.wrapper._binding(
            result['source'], forged_body['source_digest'], forged_body['goal'],
            forged_body['context'], forged_body['preservation_envelope']['preserve'],
            forged_body['compiler']['max_relevant'])
        forged_id = self.memory.put(dict(original, body=forged_body))
        refusal = self.wrapper.plan(forged_id, goal='done', context=self.context)
        self.assertEqual(refusal['status'], 'source_unavailable')

    def test_preservation_or_compiler_ceiling_change_creates_new_bound_view(self):
        result = self.wrapper.compile(goal_sensitive_spec(), context=self.context, complete=True,
                                      preserve='goal_cost', max_relevant=4)
        revised = self.wrapper.plan(result['view'], goal='done', context=self.context,
                                    preserve='behavior', max_relevant=4)
        self.assertFalse(revised['reused'])
        self.assertEqual(revised['audit']['preservation'], 'behavior')
        self.assertEqual(revised['audit']['removed_actions'], [])
        self.assertEqual(revised['plan']['cost'], 1)


if __name__ == '__main__':
    unittest.main()
