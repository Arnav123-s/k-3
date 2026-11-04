"""Regressions for evidence and resource accounting across solve composition."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kuttaka_runtime.solve import Metered, Solver, Workspace, settings, solve_task
from tests.test_kuttaka_campaign import local_executor


class OracleDifferentialPlanner:
    identity = {'model': 'oracle-differential-planner'}
    last_usage = {}

    def propose(self, context):
        if 'inputs' in context:
            return {'expected': [abs(value) for value in context['inputs']]}
        return {
            'contract': 'run(x) returns the absolute value of x',
            'checks': [{'input': 1, 'expected': 1}, {'input': 2, 'expected': 2}],
            'input': None,
            'verify': 'def verify(x, y):\n    return y == 99\n',
            'reference': 'def reference(x):\n    return abs(x)\n',
            'small_inputs': [-1, 3],
        }


class IdentityActor:
    identity = {'model': 'identity-actor'}
    last_usage = {}

    def propose(self, context):
        raise AssertionError('the evidence regression should not need an actor call')


class ReferenceAuditor:
    identity = {'model': 'reference-auditor'}
    last_usage = {}

    def propose(self, context):
        return {'expected': [abs(value) for value in context['inputs']]}


class CompositionEvidenceTests(unittest.TestCase):
    def test_oracle_support_survives_differential_scope_change(self):
        counter = {'used': 0, 'limit': 20}
        workspace = Workspace(Path(tempfile.mkdtemp()))
        oracle = lambda _task, value: {'expected': value}
        solver = Solver(
            workspace,
            Metered(OracleDifferentialPlanner(), counter),
            Metered(IdentityActor(), counter),
            local_executor,
            auditor=Metered(ReferenceAuditor(), counter),
            max_executions=20,
            check_oracle=oracle,
            oracle_id='test-absolute-value-oracle-v1',
            say=lambda _: None,
        )

        spec = solver.plan('absolute value', [], examples_origin='user')
        solver._resolve_checks('absolute value', spec)
        prior_book = solver._checkbooks[spec['check_scope']]
        supported_check = next(row for row in prior_book.records if row['input'] == 1)
        self.assertIn((prior_book.scope_id, supported_check['id']), solver._oracle_supported)

        admitted = solver.admit(
            'absolute value', spec['checks'], 'def run(x):\n    return x\n',
            'identity candidate', check_scope=spec['check_scope'])
        self.assertEqual(admitted['status'], 'checked')

        added = solver.differential('absolute value', spec, admitted['artifact'])

        self.assertEqual(added, 1)
        self.assertNotEqual(spec['check_scope'], prior_book.scope_id)
        current_book = solver._checkbooks[spec['check_scope']]
        current_supported_check = next(row for row in current_book.records if row['input'] == 1)
        self.assertIn((current_book.scope_id, current_supported_check['id']), solver._oracle_supported)

        partial = {
            'artifact': admitted['artifact'],
            'failed_checks': [{'input': 1, 'expected': 1, 'got': 99}],
        }
        self.assertEqual(solver.audit_by_verifier(spec, partial, []), 0)
        self.assertEqual(current_book.checks[0], {'input': 1, 'expected': 1})
        self.assertEqual(current_book.revisions, [])

    def test_rounds_share_execution_limit_and_report_aggregate(self):
        budgets = []

        class IncompleteSolver:
            def __init__(self, _workspace, _planner, _actor, _executor, *, max_executions,
                         splitter=None, auditor=None, sketcher=None, deploy_executor=None,
                         confirm=None, say=None, resume_candidates=None, resume_repairs=None,
                         resume_contract=None):
                self.budget = max_executions
                self.usage = {'input_tokens': 0, 'output_tokens': 0, 'unknown_usage_calls': 0}

            def resume_candidates(self):
                return {}  # This fixture checks aggregate budgets without producing partial methods.

            def resume_repairs(self):
                return {}

            def resume_contract(self):
                return None  # No model contract is produced by this aggregate-budget fixture.

            def solve(self, task, examples):
                used = min(2, self.budget)
                budgets.append((self.budget, used))
                return {'status': 'incomplete', 'task': task, 'execution_attempts': used,
                        'reason': 'scripted incomplete round', 'reused': [], 'subtasks': []}

        workspace = Workspace(Path(tempfile.mkdtemp()))
        with patch('kuttaka_runtime.solve.Solver', IncompleteSolver):
            result = solve_task(
                'a scripted task', settings=settings(rounds=3, max_calls=5, max_executions=3),
                workspace=workspace,
                models=(None, None, None, None, None),
                executor=local_executor,
                say=lambda _: None,
            )

        self.assertEqual(budgets, [(3, 2), (1, 1)])
        self.assertEqual([row['execution_attempts'] for row in result['rounds']], [2, 1])
        self.assertEqual(result['cost']['execution_attempts'], 3)
        self.assertEqual(result['execution_attempts'], 1)


if __name__ == '__main__':
    unittest.main()
