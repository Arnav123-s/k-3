import json
"""Local integration checks for delivery gating and property-verifier behavior."""

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor


def check(value, expected):
    return {'input': value, 'expected': expected}


class Planner:
    identity = {'model': 'local-planner'}
    last_usage = {'input_tokens': 1, 'output_tokens': 1}

    def __init__(self, *, checks, source_input=None, verify=None, contract='run(x) returns the requested value'):
        self.checks = checks
        self.source_input = source_input
        self.verify_source = verify
        self.contract = contract
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        if 'inputs' in context:
            return {}
        result = {'contract': self.contract, 'checks': self.checks, 'input': self.source_input}
        if self.verify_source is not None:
            result['verify'] = self.verify_source
        return result


class Actor:
    identity = {'model': 'local-actor'}
    last_usage = {'input_tokens': 2, 'output_tokens': 1}

    def __init__(self, source, *, name='candidate'):
        self.source, self.name = source, name
        self.calls = 0

    def propose(self, context):
        self.calls += 1
        history = context['history']
        if not history:
            return {'kind': 'experiment', 'tool': 'find_methods',
                    'input': {'query': 'task', 'limit': 4},
                    'predictions': [{'hypothesis': 'a prior method may apply', 'outcome': 'empty'}]}
        observation = history[-1].get('observation', {})
        if 'methods' in observation:
            return {'kind': 'experiment', 'tool': 'remember_method',
                    'input': {'name': self.name, 'description': self.name, 'source': self.source},
                    'predictions': [{'hypothesis': 'candidate is stored', 'outcome': 'artifact id'}]}
        if observation.get('artifact'):
            return {'kind': 'answer', 'answer': {'artifact': observation['artifact']}}
        return {'kind': 'unresolved', 'reason': 'unexpected fixture observation'}


class Auditor:
    identity = {'model': 'local-auditor'}
    last_usage = {'input_tokens': 1, 'output_tokens': 1}

    def __init__(self):
        self.calls = 0
        self.contexts = []

    def propose(self, context):
        self.calls += 1
        self.contexts.append(context)
        return {'expected': []}


class SolveDeliveryTests(unittest.TestCase):
    def make_solver(self, *, checks, source, input_value=None, verify=None, executor=None,
                    max_executions=32, auditor=None):
        workspace = Workspace(Path(tempfile.mkdtemp()))
        counter = {'used': 0, 'limit': 30}
        planner = Planner(checks=checks, source_input=input_value, verify=verify)
        actor = Actor(source)
        solver = Solver(workspace, Metered(planner, counter), Metered(actor, counter),
                        executor or local_executor,
                        auditor=Metered(auditor, counter) if auditor else None,
                        max_executions=max_executions, say=lambda _: None)
        return solver, workspace, planner, actor, counter

    def test_failed_deployment_is_not_promoted_to_reusable_solved_memory(self):
        requests = []

        def failing_delivery(job):
            requests.append(job)
            if job.get('input') == 99:
                return {'status': 'timeout', 'exit_code': None, 'output': '',
                        'diagnostics': 'local deployment failure', 'dependency_calls': []}
            return local_executor(job)

        solver, workspace, _, _, _ = self.make_solver(
            checks=[check(1, 1), check(2, 2)], source='def run(x):\n    return x\n',
            input_value=99, executor=failing_delivery)

        result = solver.solve('identity transformation')

        self.assertEqual(result['status'], 'incomplete')
        self.assertIn('failed on the requested input', result['reason'])
        self.assertIn(99, [job['input'] for job in requests])
        self.assertEqual(workspace.helpful_methods('identity transformation'), [])
        self.assertEqual(workspace.history(), [])

    def test_valid_predicate_checks_finite_examples_without_a_requested_input(self):
        verifier_jobs = []

        def record_executor(job):
            if 'def verify(' in job.get('source', ''):
                verifier_jobs.append(job)
            return local_executor(job)

        solver, workspace, _, _, _ = self.make_solver(
            checks=[check(1, 2), check(2, 4)], source='def run(x):\n    return 2*x\n',
            verify='def verify(x, y):\n    return y == 2*x\n', executor=record_executor)

        result = solver.solve('double a number')

        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['property_evidence']['support'], 'model_authored_heuristic')
        self.assertEqual(result['property_evidence']['verdicts'], [True, True])
        # Once on the checks before any code; once on near misses of the checks' values, so that its acceptance
        # counts only where it tells them apart (2026-11-02); once on the method's outputs for inputs near the
        # checks. The verdicts on the checks are not run again at delivery.
        self.assertEqual(len(verifier_jobs), 3)
        self.assertEqual(verifier_jobs[0]['input'], [[1, 2], [2, 4]])
        self.assertEqual(verifier_jobs[1]['input'], [[1, 3], [1, 1], [2, 5], [2, 3]])
        self.assertTrue(verifier_jobs[2]['input'])
        self.assertTrue(all(y == 2 * x for x, y in verifier_jobs[2]['input']))
        self.assertEqual(len(workspace.helpful_methods('double a number')), 1)

    def test_a_verifier_without_a_usable_verdict_leaves_the_finite_checks_standing(self):
        # Owner policy (ledger 2026-10-29): a model verifier that cannot give a verdict is missing
        # evidence, not a wrong answer; the result stands on its checks and says so.
        sources = {
            'raising': 'def verify(x, y):\n    raise RuntimeError("unavailable")\n',
            'nonboolean': 'def verify(x, y):\n    return "yes"\n',
            'returns_none': 'def verify(x, y):\n    pass\n',
        }
        for label, verify_source in sources.items():
            with self.subTest(label=label):
                solver, workspace, _, _, _ = self.make_solver(
                    checks=[check(2, 4)], source='def run(x):\n    return 2*x\n',
                    verify=verify_source)

                result = solver.solve('double a number')

                self.assertEqual(result['status'], 'checked')
                self.assertEqual(result['property_evidence']['verdicts'], [None])
                self.assertEqual(result['property_evidence']['resolution'],
                                 'verifier gave no usable verdict; finite checks only')
                self.assertEqual(result['verification_scope'], 'supplied_finite_checks_only')

    def test_property_verifier_exhausting_shared_execution_budget_leaves_checks_standing(self):
        solver, workspace, _, _, _ = self.make_solver(
            checks=[check(2, 4)], source='def run(x):\n    return 2*x\n',
            verify='def verify(x, y):\n    return y == 2*x\n', max_executions=1)

        result = solver.solve('double a number')

        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['execution_attempts'], 1)
        self.assertEqual(result['property_evidence']['verdicts'], None)
        self.assertIn('finite checks only', result['property_evidence']['resolution'])

    def test_a_verifier_that_rejects_the_users_example_is_overruled_without_a_model_call(self):
        auditor = Auditor()
        solver, workspace, _, _, _ = self.make_solver(
            checks=[check(1, 2)],
            source='def run(x):\n    return x*x\n',
            verify='def verify(x, y):\n    return y == 2*x\n', auditor=auditor)
        # The wrapper supplies the user's anchor to the public solve method.
        result = solver.solve('a square function', [check(1, 1)])

        self.assertEqual(result['status'], 'checked')
        self.assertIn(check(1, 1), result['checks'])
        self.assertEqual(result['property_evidence']['verdicts'], [False])
        self.assertIn("user's own examples", result['property_evidence']['resolution'])
        self.assertEqual(result['check_evidence']['revisions'], [])
        self.assertEqual(auditor.calls, 0)

    def settle(self, auditor_values):
        class Fixed(Auditor):
            def propose(inner, context):
                inner.calls += 1
                inner.contexts.append(context)
                return {'expected': auditor_values}
        auditor = Fixed()
        solver, _, _, _, _ = self.make_solver(
            checks=[check(2, 4), check(3, 6)], source='def run(x):\n    return 2*x\n',
            verify='def verify(x, y):\n    return y == 3*x\n', auditor=auditor)
        return solver.solve('double a number'), auditor

    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_a_wrong_model_verifier_is_overruled_by_blind_rederivation(self):
        result, auditor = self.settle([4, 6])
        self.assertEqual(result['status'], 'checked')
        self.assertTrue(result['property_evidence']['resolution'].startswith('verifier overruled'))
        self.assertEqual(auditor.calls, 1)
        self.assertNotIn('verdict', json.dumps(auditor.contexts))   # blind: no outputs or verdicts shown

    def test_a_real_dispute_with_the_verifier_stays_incomplete(self):
        # The property and a blind re-derivation agree with each other against the hand-worked checks,
        # so the checks are revised before any code (two derivations against one); the method that
        # doubles then fails them, and nothing is delivered. Earlier the same dispute was found only
        # after delivery; either way no answer the dispute contradicts is delivered.
        result, _ = self.settle([6, 9])
        self.assertEqual(result['status'], 'incomplete')
        settled = [e['body'] for e in Journal.read(Path(result['solve_journal']))
                   if e['kind'] == 'solve_given_conditions']
        self.assertEqual([r['by'] for r in settled[0]['revised']], ['property and blind re-derivation'] * 2)
        self.assertIn(check(2, 6), result['checks'])


if __name__ == '__main__':
    unittest.main()
