import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kuttaka_runtime.active_navigation import FORMAT
from kuttaka_runtime.active_navigation import ActiveNavigation
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.goals import GoalLedger
from kuttaka_runtime.navigation_tasks import NavigationTools, execute_route, retain_route
from kuttaka_runtime.store import Journal


class NavigationTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.memory = ArtifactMemory(self.root/'memory')
        self.journal = Journal(self.root/'journal')
        self.goals = GoalLedger('Reach the observable G marker.', self.journal)
        self.authorize('G')
        self.scope, self.context = 'test-scope', 'test-world-v1'
        model = {'format': FORMAT, 'actions': ['a', 'b'], 'initial': 0,
                 'outputs': ['r', 'r', 'G'], 'access': [[], ['a'], ['a', 'b']],
                 'transitions': [{'a': 1, 'b': 0}, {'a': 1, 'b': 2}, {'a': 2, 'b': 2}],
                 'suffixes': [[], ['b']]}
        self.model = self.memory.put({'kind': 'representation', 'body': model,
            'dependencies': [], 'provenance': {'fixture': True},
            'applicability': {'scope': self.scope, 'context': self.context}})
        self.memory.qualify(self.model, self.scope, 'test-fixture', ['authored-evidence'])
        self.calls = []
        self.state = 0

    def tearDown(self):
        self.journal.close()
        self.memory.close()
        self.tmp.cleanup()

    def authorize(self, label):
        gid = self.goals.propose({'description': 'Reach '+label,
            'success_criteria': ['Observe '+label], 'unknowns': [], 'tradeoffs': [],
            'parent': None, 'based_on': [self.goals.request_evidence]})['goal']
        self.goals.authorize(gid, user_evidence=self.goals.request_evidence)

    def reset(self):
        self.assertEqual(Journal.read(self.root/'journal')[-1]['kind'], 'navigation_execution_attempt')
        self.calls.append('reset')
        self.state = 0
        return 'r'

    def step(self, action):
        self.assertEqual(Journal.read(self.root/'journal')[-1]['kind'], 'navigation_execution_attempt')
        self.calls.append(action)
        self.state = [{'a': 1, 'b': 0}, {'a': 1, 'b': 2}, {'a': 2, 'b': 2}][self.state][action]
        return ['r', 'r', 'G'][self.state]

    def route(self, output='G'):
        return retain_route(self.memory, self.journal, self.goals, self.model,
                            scope=self.scope, context=self.context, goal_output=output)

    def execute(self, route, **options):
        return execute_route(self.memory, self.journal, self.goals, route,
            scope=self.scope, context=self.context, reset=self.reset,
            goal_output='G', step=options.pop('step', self.step), **options)

    def test_reload_exact_route_and_goal_ledger_then_fresh_execution(self):
        route = self.route()['artifact']
        self.assertEqual(self.memory.status(route, self.scope), 'proposed')
        self.assertEqual(self.execute(route)['status'], 'goal_observed')
        self.memory.close()
        self.journal.close()
        events = Journal.read(self.root/'journal')
        self.memory = ArtifactMemory(self.root/'memory')
        self.journal = Journal.resume(self.root/'journal', expected_head=events[-1]['hash'])
        self.goals = GoalLedger.restore('Reach the observable G marker.', self.journal, events)
        self.assertEqual(self.execute(route)['status'], 'goal_observed')
        uses = [e['body'] for e in Journal.read(self.root/'journal') if e['kind'] == 'navigation_route_use']
        self.assertEqual([u['artifact'] for u in uses], [route, route])
        self.assertEqual(uses[-1]['previous_status'], 'qualified')
        self.assertEqual(self.calls, ['reset', 'a', 'b']*2)

    def test_contradiction_revokes_map_and_route_stops_without_retry(self):
        route = self.route()['artifact']
        self.execute(route)
        result = self.execute(route, step=lambda action: 'changed')
        self.assertEqual(result['status'], 'counterexample')
        self.assertEqual(result['action_attempts'], 1)
        self.assertEqual(self.memory.status(self.model, self.scope), 'invalidated')
        self.assertEqual(self.memory.status(route, self.scope), 'invalidated')
        before = list(self.calls)
        self.assertEqual(self.execute(route)['status'], 'invalidated')
        self.assertEqual(self.calls, before)
        self.assertEqual(self.memory.get(result['question'])['kind'], 'question')

    def test_goal_absence_and_unknown_reachability_remain_unresolved(self):
        self.assertEqual(self.route(None)['status'], 'goal_unresolved')
        self.assertEqual(self.route('unseen')['status'], 'no_route_in_hypothesis')
        self.goals = GoalLedger('Which of two markers is intended?', self.journal)
        self.assertEqual(self.route()['status'], 'goal_unresolved')
        self.assertEqual(self.calls, [])

    def test_changed_authorized_goal_blocks_old_route(self):
        route = self.route()['artifact']
        self.authorize('r')
        self.assertEqual(self.execute(route)['status'], 'goal_binding_changed')
        self.assertEqual(self.calls, [])

    def test_unqualified_context_and_scope_cannot_be_reused(self):
        with self.assertRaises(ValueError):
            retain_route(self.memory, self.journal, self.goals, self.model,
                scope=self.scope, context='other-world', goal_output='G')
        with self.assertRaises(PermissionError):
            retain_route(self.memory, self.journal, self.goals, self.model,
                scope='other-scope', context=self.context, goal_output='G')

    def test_insufficient_execution_budget_has_no_physical_effect(self):
        result = self.execute(self.route()['artifact'], max_actions=1)
        self.assertEqual(result['status'], 'execution_budget')
        self.assertEqual(self.calls, [])

    def test_failed_callback_is_charged_without_false_counterexample(self):
        route = self.route()['artifact']

        def failure(_):
            raise OSError('instrument unavailable')

        result = self.execute(route, step=failure)
        self.assertEqual(result['status'], 'observer_error')
        self.assertEqual(result['action_attempts'], 1)
        self.assertEqual(result['reset_attempts'], 1)
        self.assertEqual(self.memory.status(self.model, self.scope), 'qualified')
        self.assertEqual(self.memory.status(route, self.scope), 'proposed')

    def test_corrupt_route_cannot_invent_goal_success(self):
        route = self.route()['artifact']
        payload = self.memory.get(route)
        payload['body']['actions'] = ['a']
        payload['body']['expected'] = ['r', 'G']
        bad = self.memory.put(payload)
        with self.assertRaises(ValueError):
            self.execute(bad)
        self.assertEqual(self.calls, [])

    def test_artifact_cannot_redefine_host_target_as_easier_output(self):
        payload = self.memory.get(self.route()['artifact'])
        payload['body']['actions'] = []
        payload['body']['expected'] = ['r']
        payload['body']['goal']['output'] = 'r'
        forged = self.memory.put(payload)
        self.assertEqual(self.execute(forged)['status'], 'goal_binding_changed')
        self.assertEqual(self.calls, [])

    def test_tool_execution_ceiling_is_cumulative_and_charges_reset(self):
        learner = SimpleNamespace(memory=self.memory, journal=self.journal, scope=self.scope,
            context=self.context, reset_callback=self.reset, step_callback=self.step)
        api = NavigationTools(learner, self.goals, goal_output='G', execution_ceiling=3)
        route = api.route({'model': self.model})['artifact']
        self.assertEqual(api.execute({'route': route})['status'], 'goal_observed')
        self.assertEqual(api.execute({'route': route})['status'], 'execution_budget')
        self.assertEqual(self.calls, ['reset', 'a', 'b'])

    def test_host_configured_hybrid_suffix_compaction_learns_routes_and_executes_goal(self):
        from experiments.astra_active_navigation.run import _sequence_world
        world = _sequence_world('a', label='r')
        learner = ActiveNavigation(self.memory, self.journal, world.reset, world.step,
            actions=list(world.actions), context='host-configured-navigation-v1')
        api = NavigationTools(learner, self.goals, goal_output='G', challenge_depth=4,
            challenge_strategy='hybrid', sampling={"seed": 17, "max_length": 4,
                "persistence": True, "epsilon": [1, 2], "delta": [1, 2]},
            counterexample_strategy='suffix', hybrid_compact=True)
        learned = api.learn({})
        self.assertEqual(learned['status'], 'conditional_tested')
        route = api.route({'model': learned['artifact']})
        self.assertEqual(route['status'], 'route_proposed')
        executed = api.execute({'route': route['artifact']})
        self.assertEqual(executed['status'], 'goal_observed')
        self.assertEqual(executed['observed'][-1], 'G')
        events = Journal.read(self.root/'journal')
        self.assertTrue(any(event['kind'] == 'active_navigation_counterexample_strategy'
                            for event in events))
        component_constructions = [event['body'] for event in events
            if event['kind'] == 'active_navigation_hybrid_construction']
        self.assertTrue(component_constructions[0]['construction']['compact_prefixes'])

    def test_navigation_options_are_validated_before_any_physical_callback(self):
        calls = []
        learner = SimpleNamespace(actions=['a'], max_word_length=8,
            reset_callback=lambda: calls.append('reset'), step_callback=lambda _a: calls.append('step'))
        invalid = (
            {'challenge_strategy': 'distribution', 'sampling': {'seed': True}},
            {'challenge_strategy': 'distribution', 'sampling': {'max_length': 9}},
            {'challenge_strategy': 'shortlex', 'sampling': {}},
            {'challenge_strategy': 'distribution', 'hybrid_compact': True},
            {'challenge_strategy': 'hybrid', 'counterexample_strategy': 'unknown'},
        )
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                NavigationTools(learner, self.goals, goal_output='G', **options)
        self.assertEqual(calls, [])

    def test_host_sampling_is_copied_and_model_request_cannot_override_options(self):
        calls = []
        class FakeLearner:
            actions = ['a']
            max_word_length = 8
            def learn(self, **kwargs):
                calls.append(kwargs)
                return {'status': 'captured'}
        sampling = {'seed': 7, 'max_length': 4, 'persistence': False,
                    'epsilon': [1, 20], 'delta': [1, 20]}
        api = NavigationTools(FakeLearner(), self.goals, goal_output='G',
            challenge_strategy='distribution', sampling=sampling)
        sampling['seed'] = 99
        sampling['epsilon'][0] = 9
        with self.assertRaises(ValueError):
            api.learn({'sampling': {'seed': 99}})
        self.assertEqual(calls, [])
        self.assertEqual(api.learn({})['status'], 'captured')
        self.assertEqual(calls[-1]['sampling'], {'seed': 7, 'max_length': 4,
            'persistence': False, 'epsilon': [1, 20], 'delta': [1, 20]})
        calls[-1]['sampling']['seed'] = 100
        api.learn({})
        self.assertEqual(calls[-1]['sampling']['seed'], 7)

    def test_legacy_shortlex_host_call_keeps_original_kwargs(self):
        calls = []
        fake = SimpleNamespace(learn=lambda **kwargs: calls.append(kwargs) or {'status': 'captured'})
        api = NavigationTools(fake, self.goals, goal_output='G')
        self.assertEqual(api.learn({})['status'], 'captured')
        self.assertEqual(calls, [{'challenge_depth': 5, 'strategy': 'shortlex', 'extra_depth': 1}])


if __name__ == '__main__':
    unittest.main()
