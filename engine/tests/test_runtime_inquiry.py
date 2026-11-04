"""Authored controls for recorded inquiry, not open-ended scientific discovery."""

from pathlib import Path
import tempfile
import unittest

from kuttaka_runtime import Budget, Journal, investigate
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.beliefs import outcome_key
from kuttaka_runtime.inquiry import InquirySession


def mass(value):
    return {outcome_key(value): 1.0}


class InquiryTests(unittest.TestCase):
    def test_session_runs_as_tool_with_separate_measurement_and_outer_budgets(self):
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            calls = []
            session = InquirySession({'a': 0, 'b': 0},
                                     {'probe': {'a': mass(1), 'b': mass(2)}},
                                     lambda exp: calls.append(exp) or 2, journal,
                                     provenance='authored integration control', max_observations=1)
            outer = self
            class ScriptedModel:
                def propose(self, context):
                    outer.assertEqual(context['request'], 'Identify the supplied candidate')
                    if not context['history']:
                        return {'kind': 'experiment', 'tool': 'inquire',
                                'input': {'costs': {'probe': 1}, 'challenge': False},
                                'predictions': [{'hypothesis': 'candidate b', 'outcome': 2}]}
                    observation = context['history'][-1]['observation']
                    outer.assertEqual(observation['posterior'], {'a': 0.0, 'b': 1.0})
                    return {'kind': 'answer', 'answer': 'b'}
            result = investigate('Identify the supplied candidate', ScriptedModel(),
                                 {'inquire': session.step}, lambda request, answer: answer == 'b',
                                 journal, Budget(model_calls=2, tool_calls=2))
            self.assertEqual((result.status, result.answer), ('checked', 'b'))
            self.assertEqual((result.model_calls, result.tool_calls, session.attempts), (2, 2, 1))
            self.assertEqual(calls, ['probe'])
            self.assertEqual(session.step({'costs': {'probe': 1}, 'challenge': True})['status'],
                             'budget_exhausted')

    def test_agreement_challenge_exposes_unasked_model_failure_and_preserves_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'evidence.jsonl'
            calls = []
            def sensor(experiment):
                events = Journal.read(path)
                self.assertEqual(events[-1]['kind'], 'measurement_attempt')
                self.assertEqual(events[-2]['kind'], 'inquiry_proposal')
                calls.append(experiment)
                return {'small': -2, 'large': -4, 'negative': 4}[experiment]
            with Journal(path) as journal, ArtifactMemory(Path(tmp) / 'memory') as memory:
                tables = {'small': {'linear': mass(-2)}, 'large': {'linear': mass(-6)}}
                session = InquirySession({'linear': 0}, tables, sensor, journal,
                                         memory=memory, provenance='authored-test')
                self.assertEqual(session.step({'costs': {'small': 1}, 'challenge': False})['status'],
                                 'no_discriminator')
                self.assertEqual(calls, [])
                session.step({'costs': {'small': 1}, 'challenge': True})
                failure = session.step({'costs': {'large': 1}, 'challenge': True})
                self.assertEqual(failure['status'], 'mismatch')
                self.assertEqual(memory.get(failure['question_id'])['body']['observed'], -4)
                self.assertEqual(session.step({'costs': {'small': 1}, 'challenge': True})['status'],
                                 'needs_revision')
                replacement = {
                    'small': {'linear': mass(-2), 'saturated': mass(-2)},
                    'large': {'linear': mass(-6), 'saturated': mass(-4)},
                    'negative': {'linear': mass(6), 'saturated': mass(4)},
                }
                result = session.revise({'linear': 0, 'saturated': 1}, replacement,
                                        provenance='explicit supplied expansion')
                self.assertEqual(result['posterior'], {'linear': 0.0, 'saturated': 1.0})
                fresh = session.step({'costs': {'negative': 1}, 'challenge': True})
                self.assertEqual(fresh['status'], 'updated')
                self.assertEqual(calls, ['small', 'large', 'negative'])
                question_id = failure['question_id']
            with ArtifactMemory(Path(tmp) / 'memory') as reopened:
                self.assertEqual(reopened.get(question_id)['kind'], 'question')
            events = Journal.read(path)
            self.assertTrue(any(e['kind'] == 'hypothesis_revision' for e in events))

    def test_measurement_failure_is_charged_and_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            def broken(_):
                raise RuntimeError('secret-not-for-journal')
            session = InquirySession({'a': 0}, {'x': {'a': mass(1)}}, broken, journal,
                                     provenance='test', max_observations=1)
            result = session.step({'costs': {'x': 1}, 'challenge': True})
            self.assertEqual(result['status'], 'measurement_error')
            self.assertEqual(session.attempts, 1)
            self.assertEqual(session.step({'costs': {'x': 1}, 'challenge': True})['status'],
                             'budget_exhausted')
            self.assertNotIn('secret-not-for-journal', (Path(tmp) / 'log').read_text())

    def test_invalid_revision_does_not_replace_evidence_or_state(self):
        with tempfile.TemporaryDirectory() as tmp, Journal(Path(tmp) / 'log') as journal:
            session = InquirySession({'a': 0}, {'x': {'a': mass(1)}}, lambda _: 1,
                                     journal, provenance='test')
            session.step({'costs': {'x': 1}, 'challenge': True})
            identity = session.definition_hash
            with self.assertRaises(ValueError):
                session.revise({'a': 0}, {'y': {'a': mass(2)}}, provenance='bad revision')
            self.assertEqual(session.definition_hash, identity)
            self.assertEqual(len(session.belief.evidence), 1)
