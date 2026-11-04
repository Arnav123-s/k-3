import itertools
import tempfile
import unittest
from pathlib import Path

from experiments.astra_model_experiments.run import (
    ARMS,
    ARMS_V2,
    CONTEXT,
    LIMITS,
    _hidden_alias_world,
    _generated_world,
    _run_arm,
    _v2_comparisons,
    cases,
    cases_v2,
    exact_equivalence,
)
from experiments.astra_active_navigation.run import _make_long_trigger
from kuttaka_runtime.active_navigation import ActiveNavigation, predict_trace
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.model_experiments import construct_experiments
from kuttaka_runtime.store import Journal


class ModelExperimentStudyTests(unittest.TestCase):
    def test_aggregate_charges_failed_and_discarded_construction_work(self):
        row = {'case': 'fallback', 'arm': 'adaptive-0', 'status': 'conditional_tested',
               'learn': {'schedule_work': 101}, 'schedule_work': [None],
               'schedule_records': [{'attempted_words': [[], ['a']]}],
               'actual_costs': {'challenge_word_attempts': 7}}
        aggregate = _v2_comparisons([row])['by_arm']['adaptive-0']
        self.assertEqual(aggregate['construction_work'], 101)
        self.assertEqual(aggregate['challenged_words'], 7)
        row['actual_costs'].pop('challenge_word_attempts')
        self.assertIsNone(_v2_comparisons([row])['by_arm']['adaptive-0']['challenged_words'])

    def test_generated_visible_and_color_cases_share_edges_and_reach_all_states(self):
        visible, truth_visible = _generated_world(8, 29, partially_observed=False)
        colors, truth_colors = _generated_world(8, 29, partially_observed=True)
        self.assertEqual(visible.transitions, colors.transitions)
        self.assertNotEqual(visible.outputs, colors.outputs)
        self.assertEqual(len(set(visible.outputs.values())), 8)
        self.assertEqual(len(set(colors.outputs.values())), 3)
        state = visible.initial
        reached = set()
        for _ in range(8):
            reached.add(state)
            state = visible.transitions[state]['a']
        self.assertEqual(len(reached), 8)
        self.assertEqual(truth_visible['state_count_evaluator_only'],
                         truth_colors['state_count_evaluator_only'])

    def test_v2_matrix_is_fixed_and_has_two_concealed_alias_controls(self):
        matrix = cases_v2()
        self.assertEqual(len(matrix), 28)
        self.assertEqual(len({item['case'] for item in matrix}), 28)
        self.assertEqual(len(ARMS_V2), 4)
        self.assertEqual({item['case'] for item in matrix if 'concealed_alias' in item['case']},
                         {'concealed_alias_delay_2', 'concealed_alias_delay_3'})
        for delay in (2, 3):
            world, truth = _hidden_alias_world(delay)
            self.assertEqual(truth['shortest_hidden_alias_counterexample_evaluator_only'],
                             ['b']*delay+['a'])
            frontier = [world.initial]
            seen = set()
            while frontier:
                state = frontier.pop()
                if state in seen:
                    continue
                seen.add(state)
                frontier.extend(world.transitions[state].values())
            self.assertEqual(seen, set(world.transitions))

    def test_exact_checker_finds_concealed_alias_after_visible_base_agrees(self):
        base_model = {"format": "active-moore-machine-v1", "actions": ["a", "b", "c"],
            "initial": 0, "outputs": ["x", "G"],
            "transitions": [{"a": 1, "b": 0, "c": 0},
                            {"a": 1, "b": 1, "c": 1}],
            "access": [[], ["a"]], "suffixes": [[], ["a"]]}
        for delay in (2, 3):
            world, _ = _hidden_alias_world(delay)
            result = exact_equivalence(world, base_model)
            self.assertEqual(result['status'], 'not_equivalent')
            self.assertEqual(result['shortest_counterexample'], ['b']*delay+['a'])

    def test_adaptive_selects_derived_on_visible_hypothesis_and_falls_back_on_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            visible_case = {"case": "generated-6-visible", "goal_output": "G",
                "factory": lambda: _generated_world(6, 101, partially_observed=False)[0]}
            selected = _run_arm(Path(td), visible_case, ('adaptive', 0))
            self.assertEqual(selected['selected_strategy'], 'model_derived')
            self.assertEqual(len(selected['selection_events']), 1)
            choice = selected['selection_events'][0]
            self.assertLess(choice['candidate_cost'], choice['shortlex_cost'])
            self.assertEqual(choice['selected_strategy'], 'model_derived')
            self.assertEqual(selected['schedule_records'][-1]['schedule_hash'], choice['schedule_hash'])
            self.assertEqual(selected['schedule_records'][-1]['attempted_words'], choice['words'])
            rank = {a: i for i, a in enumerate(['a', 'b', 'c'])}
            guard = {()} | {tuple(w) for depth in (1, 2)
                for w in itertools.product(['a', 'b', 'c'], repeat=depth)}
            selected_words = {tuple(w) for w in choice['words']}
            self.assertTrue(guard.issubset(selected_words))
            self.assertEqual(choice['candidate_cost'], sum(1+len(w) for w in selected_words))
            self.assertNotEqual(choice['schedule_hash'],
                                selected['schedule_records'][-1]['construction_schedule_hash'])
        with tempfile.TemporaryDirectory() as td:
            alias_case = next(item for item in cases() if item['case'] == 'aliased_rooms')
            fallback = _run_arm(Path(td), alias_case, ('adaptive', 0))
            self.assertEqual(fallback['selected_strategy'], 'shortlex')
            self.assertEqual(fallback['selection_events'][0]['words'], None)
            self.assertEqual(fallback['schedule_records'][-1]['selected_strategy'], 'shortlex')

    def test_adaptive_root_guard_passes_hidden_alias_but_exact_checker_and_goal_route_do_not_certify_map(self):
        with tempfile.TemporaryDirectory() as td:
            case = {"case": "concealed-alias-2", "goal_output": "G",
                "factory": lambda: _hidden_alias_world(2)[0]}
            adaptive = _run_arm(Path(td), case, ('adaptive', 0))
        self.assertEqual(adaptive['status'], 'conditional_tested')
        self.assertEqual(adaptive['selected_strategy'], 'model_derived')
        self.assertEqual(adaptive['exact_equivalence_evaluator_only']['status'], 'not_equivalent')
        self.assertEqual(adaptive['exact_equivalence_evaluator_only']['shortest_counterexample'], ['b', 'b', 'a'])
        self.assertEqual(adaptive['execution']['status'], 'goal_observed')
        self.assertGreaterEqual(adaptive['selection_events'][0]['root_guard_depth'], 2)

    def test_independent_product_checker_finds_delayed_hidden_goal(self):
        world = _make_long_trigger(goal_after=6)
        candidate = {"format": "active-moore-machine-v1", "actions": ["a", "b", "c"],
            "initial": 0, "outputs": ["0"],
            "transitions": [{"a": 0, "b": 0, "c": 0}],
            "access": [[]], "suffixes": [[]]}
        schedule = construct_experiments(candidate, extra_depth=1)
        self.assertEqual(schedule['status'], 'constructed')
        self.assertTrue(all(len(word) <= 2 for word in schedule['words']))
        for word in schedule['words']:
            self.assertEqual(predict_trace(candidate, word), _trace(world, word))
        result = exact_equivalence(world, candidate)
        self.assertEqual(result['status'], 'not_equivalent')
        self.assertEqual(result['shortest_counterexample'], ['a'] * 6)

    def test_both_complete_schedules_can_miss_the_same_reachable_goal(self):
        outcomes = []
        for arm in (ARMS[0], ARMS[2]):
            with tempfile.TemporaryDirectory() as td:
                case = {"case": "long_trigger_6", "goal_output": "G",
                    "factory": lambda: _make_long_trigger(goal_after=6)}
                result = _run_arm(Path(td), case, arm)
                outcomes.append(result)
        for result in outcomes:
            self.assertEqual(result['status'], 'conditional_tested')
            self.assertEqual(result['exact_equivalence_evaluator_only']['status'], 'not_equivalent')
            self.assertEqual(result['exact_equivalence_evaluator_only']['shortest_counterexample'], ['a'] * 6)
            self.assertEqual(result['route']['status'], 'no_route_in_hypothesis')
            self.assertEqual(result['execution']['status'], 'no_route_in_hypothesis')
            self.assertNotIn('reset_attempts', result['execution'])
            record = result['schedule_records'][-1]
            self.assertEqual(record['status'], 'completed')
            self.assertEqual(len(record['attempted_words']), result['learn']['challenge']['words_completed'])
        shortlex = outcomes[0]['schedule_records'][-1]
        derived = outcomes[1]['schedule_records'][-1]
        self.assertNotEqual(set(map(tuple, shortlex['attempted_words'])),
                            set(map(tuple, derived['attempted_words'])))
        self.assertLess(len(derived['attempted_words']), len(shortlex['attempted_words']))

    def test_visible_generated_world_is_checked_and_route_executes(self):
        with tempfile.TemporaryDirectory() as td:
            world, _ = _generated_world(4, 11, partially_observed=False)
            case = {"case": "generated-4-visible", "goal_output": "G",
                    "factory": lambda: _generated_world(4, 11, partially_observed=False)[0]}
            result = _run_arm(Path(td), case, ('model_derived', 1))
        self.assertEqual(result['status'], 'conditional_tested')
        self.assertEqual(result['exact_equivalence_evaluator_only']['status'], 'equivalent')
        self.assertEqual(result['route']['status'], 'route_proposed')
        self.assertEqual(result['execution']['status'], 'goal_observed')
        self.assertEqual(result['actual_costs']['route_reset_calls'], 1)

    def test_schedule_construction_limit_never_qualifies_a_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            with Journal(path/'events.jsonl') as journal, ArtifactMemory(path/'memory') as memory:
                learner = ActiveNavigation(memory, journal, lambda: 'idle', lambda _a: 'idle',
                    actions=['a'], context=CONTEXT, **LIMITS)
                result = learner.learn(challenge_depth=4, strategy='model_derived',
                                       extra_depth=1, max_schedule_work=1)
                self.assertEqual(result['status'], 'schedule_incomplete')
                self.assertNotIn('artifact', result)
                self.assertTrue(learner._models)
                self.assertEqual(memory.status(learner._models[-1], learner.scope), 'proposed')
                self.assertFalse(any(e['kind'] == 'active_navigation_schedule_completed'
                                     for e in Journal.read(path/'events.jsonl')))


def _trace(world, word):
    state = world.initial
    values = [world.outputs[state]]
    for action in word:
        state = world.transitions[state][action]
        values.append(world.outputs[state])
    return values


if __name__ == '__main__':
    unittest.main()
