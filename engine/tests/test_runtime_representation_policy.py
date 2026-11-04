import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.representation_search import RepresentationSearch
from kuttaka_runtime.store import Journal


class RepresentationPolicyTests(unittest.TestCase):
    def run_stream(self, values, **options):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            stream = iter(values)
            with ArtifactMemory(path/'memory') as memory, Journal(path/'events') as journal:
                controller = RepresentationSearch(memory, journal, lambda: next(stream),
                    context='identical-contract', **options)
                report = controller.run()
                states = {aid: memory.status(aid, controller.scope) for aid in controller.candidates}
            return report, Journal.read(path/'events'), states

    def test_measurement_failure_is_charged_and_never_retried(self):
        result, events, _ = self.run_stream([1.5, 1, 2], max_observations=10)
        self.assertEqual(result['status'], 'measurement_error')
        self.assertEqual(result['measurement_attempts'], 1)
        self.assertEqual(result['observations'], 0)

    def test_work_limit_is_not_a_mathematical_rejection(self):
        with patch('kuttaka_runtime.representation_search.fit_shift_relation',
                   side_effect=ValueError('exact work budget exceeded')) as fit:
            result, _, _ = self.run_stream([1, 2, 4, 8, 16], max_fits=1, max_observations=5)
        self.assertEqual(fit.call_count, 1)
        self.assertEqual(result['fit_calls'], 1)
        self.assertEqual(result['rejections'], 0)
        self.assertEqual(result['representation_status'], 'search_incomplete')

    def test_rank_data_requirement_does_not_claim_no_fit(self):
        result, _, _ = self.run_stream([0, 0, 0], allowed_pairs=[[4, 1]], max_observations=3)
        self.assertEqual(result['fit_calls'], 0)
        self.assertEqual(result['rejections'], 0)
        self.assertEqual(result['issued_predictions'], 0)
        self.assertEqual(result['families'][0]['status'], 'needs_data')

    def test_prior_agreement_cannot_hide_a_later_counterexample(self):
        values = [1, 2, 4, 8, 16, 33, 65, 129]
        result, events, states = self.run_stream(values, allowed_pairs=[[1, 0]],
                                                max_observations=8, min_fresh=2)
        self.assertEqual(result['wrong_issued'], 1)
        self.assertIn('invalidated', states.values())
        mismatch = next(e for e in events if e['kind'] == 'representation_counterexample')
        aid = mismatch['body']['artifact']
        self.assertFalse(any(e['kind'] == 'representation_forecast_attempt'
                             and e['body']['artifact'] == aid and e['seq'] > mismatch['seq']
                             for e in events))
        self.assertEqual(result['history'], list(map(str, values)))

    def test_automatic_revision_can_retain_an_exception_as_an_observed_seed(self):
        values = [2**n*(1 if n < 7 else 3) for n in range(16)]
        result, events, _ = self.run_stream(values, allowed_pairs=[[1, 0], [1, 1]],
                                            max_observations=16)
        self.assertEqual(result['wrong_issued'], 1)
        self.assertTrue(all(d['correct'] for d in result['decisions']
                            if d['index'] >= 10 and d['value'] is not None))
        self.assertTrue(any(d['index'] >= 10 and d['value'] is not None for d in result['decisions']))
        self.assertEqual(result['history'], list(map(str, values)))
        identified = [e['body'] for e in events if e['kind'] == 'representation_candidate_fit'
                      and e['body']['status'] == 'unique' and e['body']['predictive']]
        self.assertTrue(any(c['order'] == 1 and c['degree'] == 0 for c in identified))
        self.assertTrue(any(c['order'] == 1 and c['degree'] == 1 for c in identified))

    def test_declared_pair_order_cannot_act_as_a_task_hint(self):
        values = list(range(10))
        a, _, _ = self.run_stream(values, max_observations=10, allowed_pairs=[[1, 1], [2, 0]])
        b, _, _ = self.run_stream(values, max_observations=10, allowed_pairs=[[2, 0], [1, 1]])
        self.assertEqual(a['decisions'], b['decisions'])

    def test_prospective_validation_cannot_be_disabled_silently(self):
        with self.assertRaises(ValueError):
            self.run_stream([1, 2], min_fresh=0)

    def test_separate_runs_do_not_share_forecast_qualifications(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            with ArtifactMemory(path/'memory') as memory:
                with Journal(path/'a') as journal:
                    stream = iter([1, 2, 4, 8, 16, 33])
                    a = RepresentationSearch(memory, journal, lambda: next(stream), context='same',
                                             allowed_pairs=[[1, 0]], max_observations=6)
                    a.run()
                aid = next(iter(a.candidates))
                self.assertEqual(memory.status(aid, a.scope), 'invalidated')
                with Journal(path/'b') as journal:
                    stream = iter([1, 2, 4, 8, 16, 32])
                    b = RepresentationSearch(memory, journal, lambda: next(stream), context='same',
                                             allowed_pairs=[[1, 0]], max_observations=6)
                    report = b.run()
                self.assertNotEqual(a.scope, b.scope)
                self.assertEqual(memory.status(aid, b.scope), 'qualified')
                self.assertEqual(memory.status(aid, a.scope), 'invalidated')
                self.assertEqual(report['wrong_issued'], 0)


if __name__ == '__main__':
    unittest.main()
