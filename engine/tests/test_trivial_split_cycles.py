"""A split cannot evade active-goal detection by adding boundary whitespace.

All planners, actors, reductions and checks here are authored fixtures. No model
service or evaluation task is read. Scheduling keys never replace evidence identities.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.solve_progress import boundary_restatement_key, subproblem_key
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_kuttaka_solve import Actor, Planner
from tests.test_solve_review_regressions import Model, check, store


ROOT = 'Sum the squares of a list.'
CHECKS = [{'input': [1, 2], 'expected': 5}, {'input': [], 'expected': 0}]


class DiagnosticSplit:
    identity = {'model': 'authored-trivial-cycle-diagnostic'}
    last_usage = {'input_tokens': 1, 'output_tokens': 1}

    def __init__(self, mode):
        self.mode = mode

    def propose(self, context):
        if self.mode == 'useful':
            return {'subtasks': [
                {'id': 'square', 'request': 'Square a number.', 'checks': [check(3, 9)]},
                {'id': 'total', 'request': 'Add up a list.', 'checks': [check([1, 2], 3)]}],
                'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'total',
                    'arg': {'op': 'map', 'method': 'square', 'arg': {'op': 'input'}}}}}
        request = context['task'] + (' ' if self.mode == 'restatement' else '')
        return {'subtasks': [{'id': 'again', 'request': request, 'checks': context['checks']}],
                'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'again',
                                                   'arg': {'op': 'input'}}}}


class BoundaryRestatementKeyTests(unittest.TestCase):
    def test_scheduling_key_trims_boundaries_but_exact_identity_does_not(self):
        checks = [{'input': ' a  b ', 'expected': True}]
        original = json.dumps(checks)
        self.assertEqual(boundary_restatement_key(' goal \n', '\tcontract ', checks),
                         boundary_restatement_key('goal', 'contract', checks))
        self.assertNotEqual(subproblem_key(' goal \n', '\tcontract ', checks),
                            subproblem_key('goal', 'contract', checks))
        self.assertEqual(json.dumps(checks), original)

    def test_internal_prose_code_and_quoted_whitespace_stays_significant(self):
        for first, second in [('sum  values', 'sum values'),
                              ('return "a b"', 'return "a  b"'),
                              ('if x:\n  return x', 'if x:\n    return x')]:
            with self.subTest(first=first):
                self.assertNotEqual(boundary_restatement_key(first, 'contract', CHECKS),
                                    boundary_restatement_key(second, 'contract', CHECKS))
                self.assertNotEqual(boundary_restatement_key('request', first, CHECKS),
                                    boundary_restatement_key('request', second, CHECKS))

    def test_check_types_values_order_and_real_problem_changes_remain_distinct(self):
        for first, second in [([True], [1]), ([1], [1.0]), (['x '], ['x']),
                              ([1, 2], [2, 1]), ([check(2, 4)], [check(2, 8)])]:
            with self.subTest(first=first, second=second):
                self.assertNotEqual(boundary_restatement_key('r', 'c', first),
                                    boundary_restatement_key('r ', 'c', second))
        self.assertNotEqual(boundary_restatement_key('Square.', 'return x*x', CHECKS),
                            boundary_restatement_key('Cube.', 'return x*x', CHECKS))
        self.assertNotEqual(boundary_restatement_key('r', 'return x*x', CHECKS),
                            boundary_restatement_key('r ', 'return x*x*x', CHECKS))


class TrivialSplitCycleTests(unittest.TestCase):
    def workspace(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Workspace(Path(folder.name))

    def diagnostic(self, mode):
        counter = {'used': 0, 'limit': 40}
        solver = Solver(self.workspace(), Metered(Planner(), counter), Metered(Actor(), counter),
                        local_executor, splitter=Metered(DiagnosticSplit(mode), counter), say=lambda _: None)
        solver.derive_blind = False
        result = solver.solve(ROOT, CHECKS)
        return result, Journal.read(result['solve_journal']), counter, solver

    def test_diagnostic_whitespace_restatement_is_blocked_before_recursive_attempts(self):
        result, events, counter, solver = self.diagnostic('restatement')
        blocked = [e['body'] for e in events if e['kind'] == 'solve_cycle_blocked']
        self.assertNotEqual(result['status'], 'checked')
        self.assertTrue(blocked)
        self.assertTrue(all(e['reason'] == 'boundary_whitespace_restatement' for e in blocked))
        self.assertTrue(all(e['request'] == ROOT + ' ' for e in blocked), 'keep the original child request')
        self.assertTrue(all(e['body']['depth'] == 1 for e in events if e['kind'] == 'solve_piece_budget'))
        self.assertLess(counter['used'], 40)
        self.assertFalse(solver._active)
        self.assertFalse(solver._active_boundary_restatements)

    def test_control_exact_only_guard_allows_boundary_whitespace_recursion(self):
        with mock.patch('kuttaka_runtime.solve.boundary_restatement_key', subproblem_key):
            _, events, _, _ = self.diagnostic('restatement')
        self.assertFalse(any(e['kind'] == 'solve_cycle_blocked' for e in events))
        self.assertTrue(any(e['body']['depth'] > 1 for e in events if e['kind'] == 'solve_piece_budget'))

    def test_exact_cycle_keeps_its_distinct_reason(self):
        _, events, _, _ = self.diagnostic('exact')
        blocked = [e['body'] for e in events if e['kind'] == 'solve_cycle_blocked']
        self.assertTrue(blocked)
        self.assertTrue(all(e['reason'] == 'exact_active_subproblem' for e in blocked))

    def test_useful_nontrivial_split_still_solves_and_composes(self):
        result, events, _, _ = self.diagnostic('useful')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['answer'], 25)
        self.assertEqual([part['status'] for part in result['subtasks']], ['checked', 'checked'])
        self.assertFalse(any(e['kind'] == 'solve_cycle_blocked' for e in events))

    def test_changed_checks_or_contract_reach_the_child_under_their_own_scope(self):
        root_contract = 'run(x) returns x'
        for child_checks, child_contract in [([check(2, 2)], root_contract),
                                             ([check(1, 1)], 'run(x) returns abs(x)')]:
            with self.subTest(checks=child_checks, contract=child_contract):
                model = Model(lambda c: {'contract': root_contract if c['task'] == ROOT else child_contract,
                                        'checks': c.get('user_examples', c.get('model_examples')),
                                        'input': None}, lambda _: {'kind': 'unresolved'})
                meter = Metered(model, {'used': 0, 'limit': 8})
                solver = Solver(self.workspace(), meter, meter, local_executor, say=lambda _: None)
                solver.derive_blind = False
                visited = []

                def planned(prompt, examples, depth, spec):
                    visited.append((prompt, spec['check_scope']))
                    if depth == 0:
                        return solver.solve(prompt + ' ', child_checks, depth=1)
                    return {'status': 'incomplete', 'task': prompt, 'reason': 'child reached',
                            'artifact': None, 'reused': [], 'subtasks': []}

                with mock.patch.object(solver, '_solve_planned', side_effect=planned):
                    result = solver.solve(ROOT, [check(1, 1)])
                self.assertEqual(result['reason'], 'child reached')
                self.assertEqual([prompt for prompt, _ in visited], [ROOT, ROOT + ' '])
                self.assertNotEqual(visited[0][1], visited[1][1])
                self.assertFalse(any(e['kind'] == 'solve_cycle_blocked'
                                     for e in Journal.read(result['solve_journal'])))

    def test_valid_decreasing_valli_recursion_still_reaches_checked_delivery(self):
        reduction = ('def measure(n):\n    return n\n'
                     'def small(n):\n    return n <= 3\n'
                     'def reduce(n):\n    return [n - 1]\n'
                     'def combine(n, answers):\n    return answers[0] + n\n')
        checks = [check(0, 0), check(4, 10), check(10, 55), check(30, 465)]
        model = Model(lambda _: {'contract': 'run(n) returns 0 + 1 + ... + n', 'input': None,
                                 'checks': checks,
                                 'reference': 'def reference(n):\n    return sum(range(n + 1))\n'},
                      lambda _: store('def run(n):\n    return n\n'),
                      split=lambda _: {'reduction': reduction})
        meter = Metered(model, {'used': 0, 'limit': 30})
        solver = Solver(self.workspace(), meter, meter, local_executor, splitter=meter, auditor=meter,
                        sketcher=meter, max_executions=256, say=lambda _: None)
        result = solver.solve('Sum of the whole numbers up to n.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['delivered_by'], 'valli')
        self.assertFalse(any(e['kind'] == 'solve_cycle_blocked'
                             for e in Journal.read(result['solve_journal'])))


if __name__ == '__main__':
    unittest.main()
