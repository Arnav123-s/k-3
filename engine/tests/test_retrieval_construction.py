"""Engineered recovery after retrieval has no direct fit; no model service is called.

All candidate sources below are authored test fixtures, executed only through
the test executor. These controls establish host behavior, not model capability.
"""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime.programs import ProgramTools
from kuttaka_runtime.solve import Metered, ProgressActor, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_evidence_integration import ScriptedModel, find_action, remember_action


REQUEST = 'Construct a general increment procedure: run(x) returns x+1.'
CHECKS = [{'input': 2, 'expected': 3}, {'input': 7, 'expected': 8}]
SOURCE = 'def run(x):\n    return x + 1\n'


def constructing_actor(source=SOURCE):
    """This weak fixture constructs only after the explicit host guidance."""
    def reply(context):
        history = context['history']
        last = history[-1]['observation'] if history else {}
        if isinstance(last, dict) and 'suggested_next_step' in last:
            return remember_action(source, 'increment', 'General increment procedure')
        if isinstance(last, dict) and last.get('artifact'):
            return {'kind': 'answer', 'answer': {'artifact': last['artifact']}}
        if last is False:
            return {'kind': 'unresolved', 'reason': 'candidate failed the real checker'}
        return find_action()
    return ScriptedModel(reply)


class RetrievalConstructionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace_number = 0

    def solver(self, actor, *, calls=8, attempt_calls=8, tool_calls=48, workspace=None):
        self.workspace_number += 1
        workspace = workspace or Workspace(self.root / str(self.workspace_number))
        counter = {'used': 0, 'limit': calls}
        metered = Metered(actor, counter)
        return Solver(workspace, metered, metered, local_executor,
                      attempt_calls=attempt_calls, tool_calls=tool_calls, say=lambda _: None)

    def attempt(self, solver, request=REQUEST, checks=CHECKS):
        result = solver.attempt(request, checks)
        events = Journal.read(Path(result['run']) / 'events.jsonl')
        return result, events

    def guidance(self, events):
        return [event['body'] for event in events
                if event['kind'] == 'solve_retrieval_construction_guidance']

    def test_empty_retrieval_enables_construction_with_no_extra_calls_or_false_context(self):
        baseline_actor = constructing_actor()
        baseline = self.solver(baseline_actor, calls=4)
        with mock.patch.object(ProgressActor, 'observe_retrieval',
                               side_effect=lambda request, observation: observation):
            failed, _ = self.attempt(baseline)
        self.assertEqual(failed['status'], 'unresolved')
        self.assertEqual(baseline_actor.calls, 2)

        actor = constructing_actor()
        solver = self.solver(actor, calls=4)
        result, events = self.attempt(solver)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(actor.calls, 3)
        self.assertEqual(solver.actor.counter['used'], 3)
        self.assertGreater(solver._execution_used, 0)
        self.assertEqual(actor.contexts, [event['body'] for event in events
                                          if event['kind'] == 'model_context'])
        self.assertTrue(all(context['request'] == result['task'].request for context in actor.contexts))
        self.assertEqual(actor.contexts[0]['tools']['find_methods'], ProgramTools.find_methods_c3.__doc__)
        guidance = self.guidance(events)
        self.assertEqual(len(guidance), 1)
        self.assertEqual(guidance[0]['tool_observation'], {'methods': [], 'retrieval': 'empty'})
        next_observation = actor.contexts[1]['history'][-1]['observation']
        self.assertEqual(next_observation['methods'], [])
        self.assertEqual(next_observation['retrieval'], 'empty')
        self.assertEqual(next_observation['suggested_next_step'], guidance[0]['host_guidance'])
        self.assertEqual(guidance[0]['host_guidance']['attribution'], 'engineered solver guidance')
        replies = [event['body']['action'] for event in events if event['kind'] == 'model_reply']
        self.assertEqual(replies[1]['input']['source'], SOURCE)
        self.assertTrue(any(event['kind'] == 'observation' and event['body']['observation'] is True
                            for event in events))

    def test_ignored_guidance_keeps_existing_identical_retrieval_stop_and_raw_reply(self):
        actor = ScriptedModel(find_action())
        solver = self.solver(actor, calls=8)
        result, events = self.attempt(solver)
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(actor.calls, 2)
        self.assertEqual(len(self.guidance(events)), 1)
        interventions = [event['body'] for event in events if event['kind'] == 'solve_stall_intervention']
        self.assertEqual(interventions[0]['model_action'], find_action())
        self.assertEqual(interventions[0]['host_action']['kind'], 'unresolved')
        self.assertIsNone(result['artifact'])

    def test_nonfitting_checked_method_enables_checked_composition_with_same_budget(self):
        seed = self.solver(constructing_actor())
        stored, _ = self.attempt(seed)
        self.assertEqual(stored['status'], 'checked')
        request = 'Construct a general two-step increment procedure: run(x) returns x+2.'
        checks = [{'input': 2, 'expected': 4}, {'input': 7, 'expected': 9}]

        def compose(context):
            history = context['history']
            last = history[-1]['observation'] if history else {}
            if isinstance(last, dict) and 'suggested_next_step' in last:
                artifact = last['methods'][0]['id']
                source = 'def run(x):\n    return use("%s")(use("%s")(x))\n' % (artifact, artifact)
                action = remember_action(source, 'increment twice', 'Compose two increments')
                action['input']['uses'] = [artifact]
                return action
            if isinstance(last, dict) and last.get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': last['artifact']}}
            action = find_action()
            action['input']['query'] = 'increment'
            return action

        baseline_actor = ScriptedModel(compose)
        baseline = self.solver(baseline_actor, workspace=seed.ws, calls=4)
        with mock.patch.object(ProgressActor, 'observe_retrieval',
                               side_effect=lambda request, observation: observation):
            failed, baseline_events = self.attempt(baseline, request, checks)
        self.assertEqual(failed['status'], 'unresolved')
        self.assertEqual(baseline_actor.calls, 2)
        self.assertIsNone(failed['artifact'])
        self.assertEqual(self.guidance(baseline_events), [])
        self.assertFalse(any(event['kind'] == 'method_remembered' for event in baseline_events))

        actor = ScriptedModel(compose)
        solver = self.solver(actor, workspace=seed.ws, calls=4)
        result, events = self.attempt(solver, request, checks)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(actor.calls, 3)
        self.assertEqual(solver.actor.counter['used'], 3)
        self.assertEqual(solver.actor.counter['limit'], 4)
        self.assertEqual(result['task'].checks, checks)
        self.assertEqual(actor.contexts, [event['body'] for event in events
                                          if event['kind'] == 'model_context'])
        guidance = self.guidance(events)
        self.assertEqual(len(guidance), 1)
        raw_observation = guidance[0]['tool_observation']
        self.assertEqual(raw_observation['retrieval'], 'ranked')
        self.assertEqual(len(raw_observation['methods']), 1)
        self.assertEqual(raw_observation['methods'][0]['id'], stored['artifact'])
        self.assertIs(raw_observation['methods'][0]['fits_this_task'], False)
        self.assertNotIn('suggested_next_step', raw_observation)
        self.assertEqual(actor.contexts[1]['history'][-1]['observation'],
                         {**raw_observation, 'suggested_next_step': guidance[0]['host_guidance']})
        self.assertIn('adapter', guidance[0]['host_guidance']['instruction'])
        self.assertIn('compose', guidance[0]['host_guidance']['instruction'])
        self.assertEqual(guidance[0]['host_guidance']['attribution'], 'engineered solver guidance')
        replies = [event['body']['action'] for event in events if event['kind'] == 'model_reply']
        self.assertEqual(replies[1]['input']['uses'], [stored['artifact']])
        self.assertTrue(any(event['kind'] == 'observation' and event['body']['observation'] is True
                            for event in events))

    def test_only_explicitly_false_ranked_metadata_allows_no_fit_guidance(self):
        false_entry = {'id': 'nonfitting', 'fits_this_task': False}
        observations = [
            {'methods': [false_entry, {'fits_this_task': value}], 'retrieval': 'ranked'}
            for value in (True, None, 0, '', 'false', [], {})
        ] + [
            {'methods': [false_entry, {}], 'retrieval': 'ranked'},
            {'methods': [false_entry, None], 'retrieval': 'ranked'},
            {'methods': [false_entry, 'method'], 'retrieval': 'ranked'},
            {'methods': [false_entry], 'retrieval': 'empty'},
            {'methods': [], 'retrieval': 'ranked'},
            {'methods': [false_entry]},
            {'methods': {'fits_this_task': False}, 'retrieval': 'ranked'},
            {'retrieval': 'ranked'},
            None,
        ] + [
            {'methods': [false_entry], 'retrieval': 'ranked', 'status': status}
            for status in ('tool_rejection', 'infrastructure_error', None)
        ]
        for observation in observations:
            with self.subTest(observation=observation):
                journal = mock.Mock()
                actor = ProgressActor(None, journal, {})
                before = copy.deepcopy(observation)
                result = actor.observe_retrieval({'query': 'fixture'}, observation)
                self.assertIs(result, observation)
                self.assertEqual(observation, before)
                self.assertFalse(actor.construction_guided)
                journal.append.assert_not_called()

    def test_empty_and_no_fit_guidance_share_one_gate_and_preserve_raw_observations(self):
        observations = [
            {'methods': [], 'retrieval': 'empty'},
            {'methods': [{'id': 'a', 'fits_this_task': False},
                         {'id': 'b', 'fits_this_task': False}], 'retrieval': 'ranked'},
        ]
        for first in observations:
            with self.subTest(first=first):
                journal = mock.Mock()
                actor = ProgressActor(None, journal, {})
                before = copy.deepcopy(first)
                result = actor.observe_retrieval({'query': 'first'}, first)
                self.assertEqual(first, before)
                self.assertEqual({key: value for key, value in result.items()
                                  if key != 'suggested_next_step'}, first)
                for subsequent in observations:
                    self.assertIs(actor.observe_retrieval({'query': 'changed'}, subsequent), subsequent)
                journal.append.assert_called_once()
                self.assertEqual(journal.append.call_args.args[1]['tool_observation'], before)

    def test_changed_empty_queries_remain_bounded_with_only_one_guidance(self):
        def reply(context):
            action = find_action()
            action['input']['query'] = 'query' + str(len(context['history']))
            return action
        actor = ScriptedModel(reply)
        solver = self.solver(actor, calls=3)
        result, events = self.attempt(solver)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(actor.calls, 3)
        self.assertEqual(solver.actor.counter['used'], 3)
        self.assertEqual(len(self.guidance(events)), 1)

    def test_relevant_checked_retrieval_remains_available_without_guidance(self):
        seed = self.solver(constructing_actor())
        stored, _ = self.attempt(seed)
        self.assertEqual(stored['status'], 'checked')

        def reuse(context):
            if not context['history']:
                action = find_action()
                action['input']['query'] = 'increment'
                # A model prediction is never used to decide whether retrieval was empty.
                action['predictions'][0]['outcome'] = {'methods': [], 'retrieval': 'empty'}
                return action
            methods = context['history'][-1]['observation']['methods']
            return {'kind': 'answer', 'answer': {'artifact': methods[0]['id']}}
        actor = ScriptedModel(reuse)
        solver = self.solver(actor, workspace=seed.ws)
        result, events = self.attempt(solver)
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['artifact'], stored['artifact'])
        self.assertEqual(self.guidance(events), [])
        self.assertNotIn('suggested_next_step', actor.contexts[1]['history'][-1]['observation'])
        self.assertIs(actor.contexts[1]['history'][-1]['observation']['methods'][0]['fits_this_task'], True)

    def test_tool_rejection_is_not_an_empty_retrieval(self):
        def reply(context):
            if context['history']:
                return {'kind': 'unresolved', 'reason': 'malformed request'}
            action = find_action()
            action['input']['limit'] = 0
            action['predictions'][0]['outcome'] = {'methods': [], 'retrieval': 'empty'}
            return action
        actor = ScriptedModel(reply)
        result, events = self.attempt(self.solver(actor))
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(self.guidance(events), [])
        self.assertEqual(actor.contexts[1]['history'][-1]['observation']['status'], 'tool_rejection')

    def test_infrastructure_failure_is_not_an_empty_retrieval(self):
        actor = constructing_actor()
        with mock.patch.object(ProgramTools, 'find_methods_c3', side_effect=OSError('fixture failure')):
            result, events = self.attempt(self.solver(actor))
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(actor.calls, 1)
        self.assertEqual(self.guidance(events), [])
        self.assertFalse(any(event['kind'] == 'observation' for event in events))

    def test_guidance_never_adds_calls_beyond_shared_or_attempt_ceiling(self):
        for shared, attempt in ((1, 8), (8, 1), (2, 8), (8, 2), (0, 8)):
            with self.subTest(shared=shared, attempt=attempt):
                actor = constructing_actor()
                solver = self.solver(actor, calls=shared, attempt_calls=attempt)
                result, _ = self.attempt(solver)
                # The second call stores correct source. Mechanical recovery can
                # then run the normal checker without another model call.
                recovered = min(shared, attempt) == 2
                self.assertEqual(result['status'], 'checked' if recovered else 'incomplete')
                self.assertEqual(actor.calls, min(shared, attempt))
                self.assertEqual(solver.actor.counter['used'], actor.calls)
                if recovered:
                    self.assertIsNotNone(result['artifact'])
                    self.assertEqual(result['tool_calls'], 3)  # lookup, store, controller checker
                    self.assertEqual(result['qualification_tool_calls'], 1)
                else:
                    self.assertIsNone(result['artifact'])

    def test_storing_after_guidance_does_not_bypass_exhausted_checker_budget(self):
        actor = constructing_actor()
        result, events = self.attempt(self.solver(actor, tool_calls=2))
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['reason'], 'tool budget exhausted')
        self.assertIsNone(result['artifact'])
        self.assertEqual(sum(event['kind'] == 'tool_attempt' for event in events), 2)
        self.assertFalse(any(event['kind'] == 'observation' and event['body']['observation'] is True
                             for event in events))

    def test_constructed_wrong_method_still_reaches_original_checker_and_fails(self):
        actor = constructing_actor('def run(x):\n    return x\n')
        solver = self.solver(actor)
        checked_requests = []
        real_checker = ProgramTools.checker

        def record_check(tools, request, answer):
            checked_requests.append(request)
            return real_checker(tools, request, answer)

        with mock.patch.object(ProgramTools, 'checker', new=record_check):
            result, events = self.attempt(solver)
        self.assertEqual(result['status'], 'unresolved')
        self.assertIsNone(result['artifact'])
        self.assertEqual(checked_requests, [result['task'].request])
        self.assertEqual(len(self.guidance(events)), 1)
        self.assertTrue(any(event['kind'] == 'observation' and event['body']['observation'] is False
                            for event in events))


if __name__ == '__main__':
    unittest.main()
