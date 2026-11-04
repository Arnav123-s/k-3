import tempfile
import unittest
from pathlib import Path

from experiments.astra_predictive_state.run import collision, replay_method, run
from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.store import Journal


class PredictiveStateResearchTests(unittest.TestCase):
    def test_reloaded_methods_commit_forecasts_and_preserve_counterevidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'experiment'
            result = run(output)
            events = Journal.read(output / 'events.jsonl')
            positions = {event['hash']: index for index, event in enumerate(events)}
            # Every measured value follows its own forecast commitment.
            for event in events:
                if event['kind'] == 'measurement':
                    prior = events[positions[event['body']['proposal']]]
                    self.assertEqual(prior['kind'], 'inquiry_proposal')
                    self.assertLess(prior['seq'], event['seq'])
            changed = next(row for row in result['cases'] if row['name'] == 'delayed_change')
            self.assertEqual(changed['matches'], 2)
            self.assertEqual(changed['inquiry_statuses'][-1], 'mismatch')
            self.assertEqual(changed['revision_status'], 'no_fit')
            self.assertTrue(changed['invalidated_reuse_blocked'])
            with ArtifactMemory(output / 'memory') as memory:
                question = memory.get(changed['question_ids'][0])
                self.assertEqual(question['kind'], 'question')
                self.assertEqual(question['body']['observed'], '65')
                self.assertEqual(memory.status(changed['artifact'],
                                 'conditional-forecast:delayed_change'), 'invalidated')
                # Counterexample affects the extrapolation scope, not the true
                # statement that the old finite prefix was fitted exactly.
                self.assertEqual(memory.status(changed['artifact'],
                                 'finite-fit:delayed_change'), 'qualified')
                with Journal(Path(tmp) / 'extra.jsonl') as journal:
                    with self.assertRaisesRegex(ValueError, 'context'):
                        replay_method(memory, journal, changed['artifact'], [0, 1, 4], 1,
                                      'different-units-or-system')
            calls = [e for e in events if e['kind'] == 'method_execution_result']
            self.assertGreaterEqual(len(calls), 6)
            self.assertTrue(all('artifact' in e['body'] for e in calls))
            self.assertEqual(result['endpoint_calls'], 0)
            expansion = result['rational_ratio_language_expansion']
            self.assertEqual(expansion['degree'], 4)
            self.assertEqual(expansion['matches'], 3)
            self.assertTrue(expansion['symbolic_identity_check']['cross_product_identity'])
            for row in result['historical_inquiries']:
                self.assertEqual(row['status'], 'mismatch')
                self.assertTrue(row['question_id'])

    def test_state_collision_is_a_counterexample_not_an_absence_proof(self):
        witness = collision(['0', '1', '0', '-1', '0', '1'], 1)
        self.assertEqual(witness['state'], ['0'])
        self.assertEqual(witness['next_outputs'], ['1', '-1'])
        self.assertIsNone(collision(['0', '1', '0', '-1', '0', '1'], 2))


if __name__ == '__main__':
    unittest.main()
