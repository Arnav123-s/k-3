import tempfile
import unittest

from kuttaka_runtime.runtime import TransportFailure
from kuttaka_runtime.solve import CallsExhausted, Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_kuttaka_solve import Planner


class RecordingActor:
    identity = {'model': 'scripted-actor'}

    def __init__(self, *, candidate='def run(x):\n    return 2 * x\n'):
        self.contexts = []
        self.candidate = candidate

    def propose(self, context):
        self.contexts.append(context)
        if not context['history']:
            return {'kind': 'experiment', 'tool': 'find_methods',
                    'input': {'query': 'double', 'limit': 5},
                    'predictions': [{'hypothesis': 'lookup', 'outcome': 'empty'}]}
        observation = context['history'][-1]['observation']
        if 'methods' in observation:
            return {'kind': 'experiment', 'tool': 'remember_method',
                    'input': {'name': 'double', 'description': 'double', 'source': self.candidate},
                    'predictions': [{'hypothesis': 'stored', 'outcome': 'method id'}]}
        if 'artifact' in observation:
            return {'kind': 'answer', 'answer': {'artifact': observation['artifact']}}
        return {'kind': 'unresolved', 'reason': 'no scripted continuation'}


class RecordingAuditor:
    identity = {'model': 'scripted-auditor'}
    last_usage = {}

    def __init__(self, values):
        self.values = values
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        return {'expected': [self.values for _ in context['inputs']]}


class SolveOracleTests(unittest.TestCase):
    def make_solver(self, *, oracle=None, oracle_id=None, actor=None, auditor=None, limit=40):
        self.counter = {'used': 0, 'limit': limit}
        self.planner = Planner()
        self.actor = actor or RecordingActor()
        self.solver = Solver(
            Workspace(tempfile.mkdtemp()), Metered(self.planner, self.counter),
            Metered(self.actor, self.counter), local_executor,
            auditor=Metered(auditor, self.counter) if auditor else None,
            check_oracle=oracle, oracle_id=oracle_id, say=lambda _: None)
        return self.solver

    def test_oracle_corrects_model_check_before_actor_sees_candidate(self):
        oracle_calls = []
        actor = RecordingActor()

        def oracle(prompt, value):
            oracle_calls.append((prompt, value))
            return {'expected': value * 2}

        solver = self.make_solver(oracle=oracle, oracle_id='test-oracle-v1', actor=actor)

        class CheckOracleActor(RecordingActor):
            def propose(self, context):
                self.contexts.append(context)
                self.assert_oracle_complete = len(oracle_calls) == 2
                if not context['history']:
                    return {'kind': 'experiment', 'tool': 'find_methods',
                            'input': {'query': 'double', 'limit': 5},
                            'predictions': [{'hypothesis': 'lookup', 'outcome': 'empty'}]}
                observation = context['history'][-1]['observation']
                if 'methods' in observation:
                    return {'kind': 'experiment', 'tool': 'remember_method',
                            'input': {'name': 'double', 'description': 'double',
                                      'source': 'def run(x):\n    return 2 * x\n'},
                            'predictions': [{'hypothesis': 'stored', 'outcome': 'method id'}]}
                if 'artifact' in observation:
                    return {'kind': 'answer', 'answer': {'artifact': observation['artifact']}}
                return {'kind': 'unresolved', 'reason': 'unexpected'}

        actor = CheckOracleActor()
        solver.actor = Metered(actor, self.counter)
        result = solver.solve('Double a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertTrue(actor.assert_oracle_complete)
        self.assertEqual(len(oracle_calls), 2)
        self.assertIn({'input': 3, 'expected': 6}, result['checks'])
        self.assertNotIn({'input': 3, 'expected': 7}, result['checks'])
        revision = result['check_evidence']['revisions'][0]
        self.assertEqual(revision['support'], 'host_oracle_supported')
        self.assertEqual(revision['before']['origin'], 'model')
        self.assertEqual(revision['after']['origin'], 'model')

    def test_oracle_expectation_is_retained_even_when_candidate_disagrees(self):
        actor = RecordingActor()
        solver = self.make_solver(
            oracle=lambda _prompt, value: {'expected': 999 if value == 3 else 2},
            oracle_id='test-oracle-v1', actor=actor, limit=4)
        result = solver.solve('Double a number.')
        self.assertNotEqual(result['status'], 'checked')
        self.assertIn({'input': 3, 'expected': 999}, result['checks'])
        self.assertEqual(result['check_evidence']['revisions'][0]['after']['expected'], 999)
        self.assertEqual(result['check_evidence']['revisions'][0]['support'], 'host_oracle_supported')

    def test_user_anchor_oracle_disagreement_stops_before_actor(self):
        actor = RecordingActor()
        solver = self.make_solver(
            oracle=lambda _prompt, value: {'expected': 6 if value == 3 else 2},
            oracle_id='test-oracle-v1', actor=actor)
        result = solver.solve('Double a number.', [{'input': 3, 'expected': 7}])
        self.assertEqual(result['status'], 'needs_verification')
        self.assertEqual(actor.contexts, [])

    def test_unsupported_oracle_result_retains_provisional_checks(self):
        # A general rule through both provisional checks (1 -> 2, 3 -> 7), not a special case of 3 (seventh review:
        # the earlier `2 * x + (x == 3)` special-cased 3 by arithmetic, out of the detector's sight).
        actor = RecordingActor(candidate='def run(x):\n    return (5 * x - 1) // 2\n')
        solver = self.make_solver(oracle=lambda _prompt, _value: None,
                                  oracle_id='test-oracle-v1', actor=actor)
        result = solver.solve('Double a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertIn({'input': 3, 'expected': 7}, result['checks'])
        self.assertEqual(result['check_evidence']['revisions'], [])
        self.assertEqual(result['check_evidence']['oracle_supported_check_ids'], [])

    def test_oracle_raise_or_malformed_output_stops_and_journals_error(self):
        cases = [
            ('raises', lambda _prompt, _value: (_ for _ in ()).throw(RuntimeError('offline'))),
            ('malformed', lambda _prompt, _value: {'wrong': 1}),
        ]
        for label, oracle in cases:
            with self.subTest(label=label):
                actor = RecordingActor()
                solver = self.make_solver(oracle=oracle, oracle_id='test-oracle-v1', actor=actor)
                result = solver.solve('Double a number.')
                self.assertEqual(result['status'], 'needs_verification')
                self.assertEqual(actor.contexts, [])
                events = Journal.read(result['solve_journal'])
                unavailable = [event for event in events if event['kind'] == 'solve_oracle_unavailable']
                self.assertEqual(len(unavailable), 1)
                self.assertIn('error_type', unavailable[0]['body'])

    def test_blind_auditor_cannot_rewrite_oracle_covered_model_check(self):
        auditor = RecordingAuditor(99)
        solver = self.make_solver(
            oracle=lambda _prompt, value: {'expected': value * 2},
            oracle_id='test-oracle-v1', auditor=auditor)
        spec = solver.plan('Double a number.', [])
        solver._resolve_checks('Double a number.', spec)
        count = solver.audit('Double a number.', spec,
                             {'artifact': 'candidate-id', 'failed_checks': [
                                 {'input': 3, 'expected': 6, 'got': 99}]}, [])
        self.assertEqual(count, 0)
        self.assertEqual(auditor.contexts, [])
        self.assertIn({'input': 3, 'expected': 6}, spec['checks'])
        record = next(row for row in solver._checkbooks[spec['check_scope']].records
                      if row['input'] == 3)
        self.assertEqual(record['origin'], 'model')


class MeteredUsageTests(unittest.TestCase):
    def make_accountant(self, counter):
        class CounterActor:
            identity = {'model': 'counter'}

            def __init__(self):
                self.counter = counter

        return Solver(Workspace(tempfile.mkdtemp()), object(), CounterActor(), local_executor,
                      say=lambda _: None)

    def test_transport_then_success_accounts_two_calls_once_each(self):
        class Flaky:
            identity = {'model': 'scripted'}
            last_usage = {'input_tokens': 900, 'output_tokens': 900}

            def __init__(self):
                self.calls = 0

            def propose(self, _context):
                self.calls += 1
                if self.calls == 1:
                    raise TransportFailure('cut off')
                self.last_usage = {'input_tokens': 11, 'output_tokens': 7}
                return {'ok': True}

        model = Flaky()
        counter = {'used': 0, 'limit': 4}
        metered = Metered(model, counter)
        solver = self.make_accountant(counter)
        self.assertEqual(solver._ask(metered, {}), {'ok': True})
        self.assertEqual(counter['used'], 2)
        self.assertEqual(model.calls, 2)
        self.assertEqual(solver.usage, {'input_tokens': 11, 'output_tokens': 7,
                                        'unknown_usage_calls': 1})

    def test_calls_exhausted_does_not_add_fabricated_usage(self):
        class NeverCalled:
            identity = {'model': 'scripted'}
            last_usage = {'input_tokens': 100, 'output_tokens': 100}

            def propose(self, _context):
                raise AssertionError('budget should stop before model call')

        counter = {'used': 1, 'limit': 1}
        solver = self.make_accountant(counter)
        with self.assertRaises(CallsExhausted):
            solver._ask(Metered(NeverCalled(), counter), {})
        self.assertEqual(counter['used'], 1)
        self.assertEqual(solver.usage, {'input_tokens': 0, 'output_tokens': 0,
                                        'unknown_usage_calls': 0})


if __name__ == '__main__':
    unittest.main()
