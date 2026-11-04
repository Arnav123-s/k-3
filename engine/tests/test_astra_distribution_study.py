import itertools
import unittest

from experiments.astra_active_navigation.run import ACTIONS
from experiments.astra_distribution_navigation.run import (
    _exact_prefix_word_for_truth,
    _fixed_cases,
    _kmp_world,
    _sample_patterns,
    _arm_specs,
    _summary,
)


def _first_goal(world, actions):
    world.reset()
    for index, action in enumerate(actions, start=1):
        if world.step(action) == "G":
            return index
    return None


class DistributionStudyTests(unittest.TestCase):
    def test_suffix_followup_has_fixed_pairs_and_no_initial_goal_cases(self):
        from experiments.astra_distribution_navigation.run_suffix import cases, arms, hybrid_arms, compact_arms
        matrix, policies = cases(), arms()
        self.assertEqual(len(matrix), 13)
        self.assertEqual(len(policies), 6)
        self.assertEqual(len(hybrid_arms()), 9)
        self.assertEqual(len(compact_arms()), 6)
        self.assertEqual({opts['hybrid_compact'] for _,_,opts in compact_arms()}, {False,True})
        self.assertEqual({(mode,opts['strategy']) for _,mode,opts in hybrid_arms()},
                         {('prefixes','distribution'), ('suffix','distribution'), ('suffix','hybrid')})
        self.assertEqual({mode for _,mode,_ in policies}, {'prefixes','suffix'})
        self.assertEqual(len({item['case'] for item in matrix}), 13)
        for item in matrix:
            world = item['factory']()
            self.assertNotEqual(world.outputs[world.initial], 'G')

    def test_rejected_candidates_do_not_count_as_accepted_high_error_maps(self):
        low = {'numerator': 1, 'denominator': 100}
        high = {'numerator': 1, 'denominator': 2}
        row = {'arm': 'probe', 'status': 'conditional_tested',
            'exact_equivalence_evaluator_only': {'status': 'not_equivalent'},
            'disagreement_mass_evaluator_only': {'uniform': low, 'persistent': low},
            'issued_model_evaluations_evaluator_only': [
                {'disagreement_mass_evaluator_only': {'uniform': high, 'persistent': high}},
                {'disagreement_mass_evaluator_only': {'uniform': low, 'persistent': low}}],
            'actual_costs': {'learning_actions': 10, 'learning_resets': 2,
                'challenge_attempts_all_candidates': None, 'construction_work_all_candidates': 7}}
        summary = _summary([row], [('probe', 'distribution', {})])['by_arm']['probe']
        self.assertEqual(summary['issued_model_mass_at_least_1_20']['uniform'], 1)
        self.assertEqual(summary['qualified_final_model_mass_at_least_1_20']['uniform'], 0)
        self.assertIsNone(summary['challenge_attempts_all_candidates'])
        self.assertEqual(summary['conditional_but_wrong_maps'], 1)

    def test_kmp_trigger_matches_substring_oracle_for_generated_patterns(self):
        for pattern in _sample_patterns().values():
            # Exhaustive words through two symbols beyond each pattern exercise
            # repeated prefixes, fallback transitions, and overlapping matches.
            max_length = len(pattern) + 2
            for size in range(max_length + 1):
                for word in itertools.product(ACTIONS, repeat=size):
                    expected = next((start + len(pattern) for start in range(
                        max(0, len(word) - len(pattern) + 1))
                        if list(word[start:start+len(pattern)]) == pattern), None)
                    self.assertEqual(_first_goal(_kmp_world(pattern), word), expected)

    def test_fixed_matrix_has_seventeen_cases_and_seven_arms_worth_of_cases(self):
        cases = _fixed_cases()
        names = {case["case"] for case in cases}
        self.assertEqual(len(cases), 17)
        self.assertEqual(len(names), 17)
        self.assertEqual(len(_arm_specs()), 7)
        self.assertEqual({name for name in names if name.startswith("generated_")}, {
            f"generated_{count}_{seed}_{variant}"
            for count in (6, 10) for seed in (211, 223) for variant in ("visible", "colors")})
        self.assertEqual({name for name in names if name.startswith("concealed_alias_")}, {
            "concealed_alias_2", "concealed_alias_3"})
        self.assertEqual({name for name in names if name.startswith("long_trigger_")}, {
            "long_trigger_6", "long_trigger_8", "long_trigger_17_beyond_support"})
        self.assertEqual({name for name in names if name.startswith("kmp_substring_")}, {
            "kmp_substring_5", "kmp_substring_7"})
        self.assertEqual({name for name in names if name.startswith("exact_prefix_sink_")}, {
            "exact_prefix_sink_14", "exact_prefix_sink_16"})
        # Fixed evaluator words are generated once from the declared sequential
        # PRNG streams and remain independent of learner callbacks.
        truth = {case["case"]: case["truth_factory"]() for case in cases}
        self.assertEqual(truth["kmp_substring_5"]["pattern_evaluator_only"], _sample_patterns()[5])
        self.assertEqual(truth["kmp_substring_7"]["pattern_evaluator_only"], _sample_patterns()[7])
        self.assertEqual(truth["exact_prefix_sink_14"]["word_evaluator_only"], _exact_prefix_word_for_truth(14))
        self.assertEqual(truth["exact_prefix_sink_16"]["word_evaluator_only"], _exact_prefix_word_for_truth(16))
