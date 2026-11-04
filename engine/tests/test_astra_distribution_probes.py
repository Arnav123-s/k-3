"""Risk allocation, repeated attempts, and physical validation regressions."""

from fractions import Fraction
from pathlib import Path
import tempfile
import unittest

from kuttaka_runtime.active_navigation import ActiveNavigation
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.distribution_probes import ProbeStream, sample_count
from kuttaka_runtime.store import Journal, digest


def constant_model(actions=('a', 'b')):
    return {'format': 'active-moore-machine-v1', 'actions': list(actions),
        'initial': 0, 'outputs': ['x'], 'transitions': [{a: 0 for a in actions}],
        'access': [[]], 'suffixes': [[]]}


class DistributionProbeTests(unittest.TestCase):
    def test_count_is_exact_minimum_and_round_allocations_telescope(self):
        spent = Fraction(0)
        for r in (1, 2, 3, 100, 4096):
            n = sample_count((1, 20), (1, 20), r)
            allowance = Fraction(1, 20*r*(r+1))
            self.assertLessEqual(Fraction(19, 20)**n, allowance)
            self.assertGreater(Fraction(19, 20)**(n-1), allowance)
        for r in range(1, 101):
            spent += Fraction(1, 20*r*(r+1))
        self.assertEqual(spent, Fraction(1, 20) * Fraction(100, 101))
        self.assertIsNone(sample_count((1, 20), (1, 20), 1, max_tests=1))

    def test_reproducible_stream_without_deduplication_or_iid_claim(self):
        a = ProbeStream(['a', 'b'], seed=42, max_length=0)
        b = ProbeStream(['a', 'b'], seed=42, max_length=0)
        first, replay = a.construct(constant_model()), b.construct(constant_model())
        self.assertEqual(first, replay)
        self.assertEqual(len(first['words']), sample_count((1, 20), (1, 20), 1))
        self.assertTrue(all(word == [] for word in first['words']))
        self.assertFalse(first['statistical_certificate'])
        self.assertEqual(first['schedule_hash'], digest({**first, 'schedule_hash': None}))
        first['contract']['epsilon'][0] = 19
        self.assertEqual(a.contract['epsilon'], [1, 20])
        second = a.construct(constant_model())
        self.assertEqual(second['round'], 2)
        self.assertGreater(second['expected_words'], replay['expected_words'])

    def test_random_words_are_bounded_and_both_distributions_replay(self):
        for persistence in (False, True):
            a = ProbeStream(['a', 'b'], seed=72, persistence=persistence, max_length=12)
            b = ProbeStream(['a', 'b'], seed=72, persistence=persistence, max_length=12)
            for _ in range(3):
                result = a.construct(constant_model())
                self.assertEqual(result, b.construct(constant_model()))
                self.assertTrue(all(len(w) <= 12 and set(w) <= {'a', 'b'} for w in result['words']))
                self.assertTrue(any(len(w) > 4 for w in result['words']))

    def test_limits_discard_partial_schedules_and_spend_rounds(self):
        stream = ProbeStream(['a', 'b'], seed=1)
        for limits in ({'max_tests': 0}, {'max_work': 1}, {'max_work': 100}):
            result = stream.construct(constant_model(), **limits)
            self.assertEqual(result['status'], 'construction_limit')
            self.assertEqual(result['words'], [])
            self.assertIsNone(result['schedule_hash'])
            self.assertLessEqual(result['work'], limits.get('max_work', 1000000))
        self.assertEqual(stream.round, 3)

    def test_invalid_contracts_rejected(self):
        for kwargs in ({'seed': True}, {'seed': -1}, {'seed': 0, 'max_length': 33},
                       {'seed': 0, 'persistence': 1}, {'seed': 0, 'epsilon': [1, 1]},
                       {'seed': 0, 'delta': [True, 20]}):
            with self.assertRaises(ValueError):
                ProbeStream(['a'], **kwargs)

    def test_learner_commits_before_fresh_queries_and_retains_attempt_counter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with Journal(root/'events.jsonl') as journal, ArtifactMemory(root/'memory') as memory:
                learner = ActiveNavigation(memory, journal, lambda: 'x', lambda a: 'x',
                    actions=['a', 'b'], context='constant')
                first = learner.learn(strategy='distribution', sampling={'seed': 91})
                second = learner.learn(strategy='distribution', sampling={'seed': 91})
                self.assertEqual(first['status'], 'conditional_tested')
                self.assertEqual(second['challenge']['round'], 2)
                self.assertFalse(second['challenge']['statistical_certificate'])
                self.assertEqual(second['challenge']['words_completed'], second['challenge']['expected_words'])
                with self.assertRaises(ValueError):
                    learner.learn(strategy='distribution', sampling={'seed': 92})
                events = Journal.read(journal.path)
                construction = next(e for e in events if e['kind'] == 'active_navigation_distribution_construction')
                proposed = next(e for e in events if e['kind'] == 'active_navigation_model_proposed')
                challenge = next(e for e in events if e['kind'] == 'active_navigation_challenge')
                self.assertLess(proposed['seq'], construction['seq'])
                self.assertLess(construction['seq'], challenge['seq'])
                self.assertEqual(sum(e['kind']=='active_navigation_challenge' for e in events),
                    first['challenge']['words_completed']+second['challenge']['words_completed'])

    def test_incomplete_sampling_never_qualifies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with Journal(root/'events.jsonl') as journal, ArtifactMemory(root/'memory') as memory:
                learner = ActiveNavigation(memory, journal, lambda: 'x', lambda a: 'x',
                    actions=['a'], context='budget', max_queries=10)
                result = learner.learn(strategy='distribution')
                self.assertEqual(result['status'], 'schedule_incomplete')
                self.assertNotIn('artifact', result)
                self.assertEqual(memory.status(learner._models[-1], learner.scope), 'proposed')

    def test_action_budget_stops_a_partly_executed_schedule(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with Journal(root/'events.jsonl') as journal, ArtifactMemory(root/'memory') as memory:
                learner = ActiveNavigation(memory, journal, lambda: 'x', lambda a: 'x',
                    actions=['a'], context='budget', max_actions=10)
                result = learner.learn(strategy='distribution', sampling={'seed': 123})
                self.assertEqual(result['status'], 'budget_exhausted')
                self.assertNotIn('artifact', result)
                self.assertFalse(any(e['kind'] == 'active_navigation_schedule_completed'
                                     for e in Journal.read(journal.path)))

    def test_distribution_detects_deep_counterexample_and_refines(self):
        from experiments.astra_active_navigation.run import _make_long_trigger
        from experiments.astra_model_experiments.run import exact_equivalence
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            world = _make_long_trigger(goal_after=6)
            with Journal(root/'events.jsonl') as journal, ArtifactMemory(root/'memory') as memory:
                learner = ActiveNavigation(memory, journal, world.reset, world.step,
                    actions=world.actions, context='deep')
                result = learner.learn(strategy='distribution', sampling={'seed': 91})
                self.assertEqual(result['status'], 'conditional_tested')
                self.assertGreater(result['counterexamples'], 0)
                self.assertEqual(exact_equivalence(world, memory.get(result['artifact'])['body'])['status'], 'equivalent')

    def test_changed_reset_during_validation_never_earns_qualification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            resets = []
            def reset():
                resets.append(1)
                return 'x' if len(resets) <= 2 else 'changed'
            with Journal(root/'events.jsonl') as journal, ArtifactMemory(root/'memory') as memory:
                learner = ActiveNavigation(memory, journal, reset, lambda a: 'x',
                    actions=['a'], context='drift')
                result = learner.learn(strategy='distribution')
                self.assertEqual(result['status'], 'nonstationary')
                self.assertFalse(any(e['kind'] == 'active_navigation_schedule_completed'
                                     for e in Journal.read(journal.path)))


if __name__ == '__main__':
    unittest.main()
