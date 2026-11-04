"""Finite Bayesian belief controls; probability tables are caller-supplied."""

import math
import unittest

from kuttaka_runtime.beliefs import FiniteBelief, outcome_key


def row(**probabilities):
    return {outcome_key(outcome): probability for outcome, probability in probabilities.items()}


class FiniteBeliefTests(unittest.TestCase):
    def test_noisy_observations_can_reverse_a_description_length_prior(self):
        belief = FiniteBelief(
            {'short': 0, 'long': 2},
            {'measure-1': {
                'short': row(a=0.2, b=0.8),
                'long': row(a=0.9, b=0.1),
            }, 'measure-2': {
                'short': row(a=0.2, b=0.8),
                'long': row(a=0.9, b=0.1),
            }})
        self.assertGreater(belief.posterior()['short'], belief.posterior()['long'])
        first = belief.observe('measure-1', 'a')
        self.assertEqual(first.status, 'updated')
        self.assertGreater(first.posterior['long'], first.posterior['short'])
        second = belief.observe('measure-2', 'a')
        self.assertEqual(second.status, 'updated')
        self.assertGreater(second.posterior['long'], second.posterior['short'])
        self.assertTrue(all(math.isfinite(x) for x in second.log_posterior.values()))

    def test_deterministic_point_mass_eliminates_incompatible_candidate(self):
        belief = FiniteBelief({'left': 0, 'right': 0}, {'probe': {
            'left': row(yes=1.0), 'right': row(no=1.0)}})
        update = belief.observe('probe', 'yes')
        self.assertEqual(update.status, 'updated')
        self.assertEqual(update.posterior, {'left': 1.0, 'right': 0.0})
        self.assertEqual(update.log_posterior['right'], -math.inf)

    def test_unsupported_observation_is_a_conflict_without_fabricated_posterior(self):
        belief = FiniteBelief({'a': 0, 'b': 1}, {'probe': {
            'a': row(x=1.0), 'b': row(y=1.0)}})
        update = belief.observe('probe', 'z')
        self.assertEqual(update.status, 'mismatch')
        self.assertIsNone(update.posterior)
        self.assertIsNone(belief.posterior())
        compatible, _ = belief.last_compatible_posterior()
        self.assertAlmostEqual(compatible['a'], 2 / 3)
        self.assertAlmostEqual(compatible['b'], 1 / 3)
        self.assertEqual(belief.evidence[0].status, 'conflict')
        self.assertIsNone(belief.choose_experiment({'probe': 1}))
        self.assertEqual(dict(belief.last_scores), {})

    def test_experiment_id_is_single_use(self):
        belief = FiniteBelief({'a': 0}, {'draw-1': {'a': row(x=0.5, y=0.5)}})
        belief.observe('draw-1', 'x')
        with self.assertRaises(ValueError):
            belief.observe('draw-1', 'x')

    def test_observationally_equivalent_hypotheses_decline_experiment(self):
        belief = FiniteBelief({'a': 0, 'b': 1}, {'same': {
            'a': row(x=0.25, y=0.75), 'b': row(x=0.25, y=0.75)}})
        self.assertIsNone(belief.choose_experiment({'same': 1.0}))

    def test_invalid_probability_tables_are_rejected(self):
        invalid = [
            {'a': row(x=float('nan'))},
            {'a': row(x=float('inf'))},
            {'a': row(x=-0.1, y=1.1)},
            {'a': row(x=0.4)},
            {'a': {'x': 1.0}},
        ]
        for matrix in invalid:
            with self.subTest(matrix=matrix):
                with self.assertRaises(ValueError):
                    FiniteBelief({'a': 0}, {'probe': matrix})

    def test_matrices_are_copied_and_frozen(self):
        descriptions = {'a': 0, 'b': 1}
        matrix = {'probe': {'a': row(x=1.0), 'b': row(y=1.0)}}
        belief = FiniteBelief(descriptions, matrix)
        matrix['probe']['a'].clear()
        descriptions['a'] = 100
        self.assertEqual(belief.predict('probe')['a'], row(x=1.0))
        self.assertEqual(belief.description_lengths['a'], 0.0)
        with self.assertRaises(TypeError):
            belief.likelihoods['probe']['a'][outcome_key('x')] = 0.0

    def test_log_posterior_survives_float_probability_underflow(self):
        belief = FiniteBelief({'common': 0, 'rare': 2000}, {'noisy': {
            'common': row(x=0.5, y=0.5), 'rare': row(x=0.5000000000001, y=0.4999999999999)}})
        update = belief.observe('noisy', 'x')
        self.assertEqual(update.status, 'updated')
        self.assertTrue(math.isfinite(update.log_posterior['rare']))
        self.assertGreater(update.log_posterior['rare'], -math.inf)
        # The ordinary float view may underflow; log probabilities are authoritative.
        self.assertEqual(update.posterior['rare'], 0.0)

    def test_extreme_equal_description_lengths_keep_equal_prior(self):
        belief = FiniteBelief({'a': 1e308, 'b': 1e308}, {'probe': {
            'a': row(x=1.0), 'b': row(x=1.0)}})
        self.assertEqual(belief.posterior(), {'a': 0.5, 'b': 0.5})
        self.assertAlmostEqual(belief.log_posterior()['a'], -math.log(2))

    def test_positive_likelihood_can_recover_float_underflowed_candidate(self):
        table = {}
        for index in range(4):
            table[f'draw-{index}'] = {
                'common': row(x=1e-100, y=1.0 - 1e-100),
                'rare': row(x=1.0, y=0.0),
            }
        belief = FiniteBelief({'common': 0, 'rare': 1100}, table)
        self.assertEqual(belief.posterior()['rare'], 0.0)
        for index in range(4):
            update = belief.observe(f'draw-{index}', 'x')
            self.assertEqual(update.status, 'updated')
            self.assertGreater(update.log_posterior['rare'], -math.inf)
        self.assertGreater(belief.posterior()['rare'], belief.posterior()['common'])

    def test_revision_replays_prior_evidence_and_can_recover_conflict(self):
        belief = FiniteBelief({'a': 0}, {
            'probe-x': {'a': row(x=1.0)}, 'probe-y': {'a': row(y=1.0)}})
        belief.observe('probe-x', 'x')
        belief.observe('probe-y', 'y')
        revised, status = belief.revise(
            {'a': 0, 'b': 1},
            {'probe-x': {'a': row(x=0.0, z=1.0), 'b': row(x=0.0, z=1.0)},
             'probe-y': {'a': row(y=1.0), 'b': row(y=0.0, z=1.0)}})
        self.assertEqual(status.status, 'mismatch')
        self.assertTrue(revised.inconsistent)
        self.assertEqual(len(revised.evidence), 2)
        revised, status = belief.revise(
            {'a': 0, 'b': 1},
            {'probe-x': {'a': row(x=0.5, y=0.5), 'b': row(x=0.5, y=0.5)},
             'probe-y': {'a': row(x=0.5, y=0.5), 'b': row(x=0.5, y=0.5)}})
        self.assertEqual(status.status, 'updated')
        self.assertEqual(len(revised.evidence), 2)
        self.assertFalse(revised.inconsistent)

    def test_expected_information_gain_per_caller_cost(self):
        belief = FiniteBelief({'a': 0, 'b': 0}, {
            'useful': {'a': row(x=1.0), 'b': row(y=1.0)},
            'weak': {'a': row(x=0.6, y=0.4), 'b': row(x=0.4, y=0.6)},
        })
        picked = belief.choose_experiment({'useful': 2.0, 'weak': 1.0})
        self.assertEqual(picked[0], 'useful')
        self.assertAlmostEqual(picked[1], 0.5)
        with self.assertRaises(ValueError):
            belief.choose_experiment({'useful': float('nan')})
        with self.assertRaises(ValueError):
            belief.choose_experiment({'useful': 1e-320})


if __name__ == '__main__':
    unittest.main()
