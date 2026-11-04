"""Budget and round-progress regressions using only scripted, test-written programs."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.check_contracts import CheckBook
from kuttaka_runtime.solve import Metered, Solver, Workspace, settings, solve_task
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_evidence_integration import ScriptedModel, find_action, remember_action


PARTIAL = 'def run(x):\n    return 2*x\n'
CORRECT = 'def run(x):\n    return x+1\n'
CHECKS = [{'input': 1, 'expected': 2}, {'input': 2, 'expected': 3}]


class ProgressRepairs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(Path(self.temp.name))

    def solver(self, calls=24, executions=24):
        self.counter = {'used': 0, 'limit': calls}
        model = Metered(ScriptedModel({}), self.counter)
        return Solver(self.ws, model, model, local_executor, max_executions=executions, say=lambda _: None)

    def test_difficult_first_piece_leaves_resources_for_sibling_and_parent(self):
        solver = self.solver()
        visited = []
        spec = {'checks': CHECKS, 'check_scope': 'test-scope'}
        pieces = [{'id': name, 'request': name, 'checks': CHECKS} for name in ('first', 'second')]
        recipe = {'version': 1, 'output': {'op': 'array', 'items': [
            {'op': 'call', 'method': name, 'arg': {'op': 'input'}} for name in ('first', 'second')]}}

        def consume(request, checks, depth, **kwargs):
            visited.append((request, solver.actor.counter['limit'] - solver.actor.counter['used'],
                            solver._executions_left()))
            while solver._calls_left():
                solver._ask(solver.actor, {})
            while solver._executions_left():
                solver._execute({'source': CORRECT, 'input': 1}, executor=lambda job: {'status': 'completed'})
            return {'status': 'checked', 'artifact': request, 'task': request}

        def compose(*args):
            self.assertTrue(solver._calls_left(), 'parent must retain a glue or audit call')
            self.assertGreaterEqual(solver._executions_left(), len(CHECKS) + 1)
            solver._ask(solver.actor, {})
            solver._execute({'source': CORRECT, 'input': 1}, executor=lambda job: {'status': 'completed'})
            return {'status': 'checked'}

        with mock.patch.object(solver, 'solve', side_effect=consume), \
                mock.patch.object(solver, 'compose', side_effect=compose):
            parts, outcome = solver._assemble('parent', spec, 'parent',
                                              {'subtasks': pieces, 'recipe': recipe}, 0, '')
        self.assertEqual([row[0] for row in visited], ['first', 'second'])
        self.assertTrue(all(calls > 0 and executions > 0 for _, calls, executions in visited))
        self.assertEqual(outcome['status'], 'checked')
        self.assertEqual(self.counter['limit'], 24)
        self.assertEqual(solver.max_executions, 24)
        self.assertLessEqual(self.counter['used'], 24)
        self.assertLessEqual(solver._execution_used, 24)

    def test_later_round_repairs_a_freshly_rechecked_partial(self):
        planner = ScriptedModel({'contract': 'run(x) returns x+1', 'checks': CHECKS, 'input': None})

        def reply(context):
            history = context['history']
            repairing = 'Source:\n' + PARTIAL in context['request']
            if not history:
                return find_action()
            observation = history[-1]['observation']
            if 'methods' in observation:
                return remember_action(CORRECT if repairing else PARTIAL)
            if repairing and observation.get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': observation['artifact']}}
            return {'kind': 'unresolved', 'reason': 'partial retained for another round'}

        actor = ScriptedModel(reply)
        with mock.patch.object(Solver, 'derive_blind', False):
            result = solve_task('increment', CHECKS, settings=settings(max_calls=4, rounds=2), workspace=self.ws,
                                models=(planner, actor, None, None, None), executor=local_executor)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(len(result['rounds']), 2)
        self.assertLessEqual(result['cost']['model_calls'], 8)
        self.assertTrue(any('Source:\n' + PARTIAL in c['request'] for c in actor.contexts))

    def test_fallback_split_also_allocates_to_siblings_and_restores_parent(self):
        solver = self.solver()
        pieces = [{'request': name, 'checks': CHECKS} for name in ('first', 'second')]
        solver.splitter = Metered(ScriptedModel({'subtasks': pieces}), self.counter)
        visited = []

        def consume(request, *args, **kwargs):
            visited.append((request, solver.actor.counter['limit'] - solver.actor.counter['used']))
            while solver._calls_left():
                solver._ask(solver.actor, {})
            return {'status': 'checked', 'artifact': request, 'task': request}

        spec = {'checks': CHECKS, 'check_scope': 'scope', 'contract': 'increment'}
        with mock.patch.object(solver, 'solve', side_effect=consume), \
                mock.patch.object(solver, 'assemble', return_value={'status': 'checked'}) as assembly:
            parts, _, outcome = solver.split('parent', spec, [], {'reason': 'failed'}, None,
                                              'parent', [], 0, '')
        self.assertEqual([name for name, _ in visited], ['first', 'second'])
        self.assertTrue(all(calls > 0 for _, calls in visited))
        self.assertTrue(solver._calls_left())
        self.assertEqual(self.counter['limit'], 24)
        assembly.assert_called_once()

    def test_nested_piece_exception_restores_both_absolute_limits(self):
        solver = self.solver()
        spec = {'checks': CHECKS}
        first = {'request': 'first', 'checks': CHECKS}
        nested = {'request': 'nested', 'checks': CHECKS}
        observed = []

        def fail(request, *args, **kwargs):
            observed.append((request, solver.actor.counter['limit'], solver.max_executions))
            if request == 'first':
                return solver._solve_piece(nested, spec, 1, 1)
            raise RuntimeError('fixture interruption')

        with mock.patch.object(solver, 'solve', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'fixture interruption'):
                solver._solve_piece(first, spec, 0, 2)
        self.assertEqual(self.counter['limit'], 24)
        self.assertEqual(solver.max_executions, 24)
        self.assertLessEqual(observed[1][1], observed[0][1])
        self.assertLessEqual(observed[1][2], observed[0][2])

    def partial_fixture(self):
        seed = self.solver()
        book = CheckBook('increment', 'run(x) returns x+1', CHECKS, [])
        spec = {'checks': book.checks, 'check_scope': book.scope_id, 'contract': book.contract}
        outcome = seed.admit('increment', book.checks, PARTIAL, 'partial', check_scope=book.scope_id)
        solver = self.solver()
        solver._checkbooks[book.scope_id] = book
        solver._resume_candidates = {book.scope_id: [outcome['candidate']]}
        return solver, spec, outcome['candidate']

    def test_resume_requires_new_execution_and_uses_only_new_pass_counts(self):
        solver, spec, artifact = self.partial_fixture()
        jobs = []

        def wrong_now(job):
            jobs.append(job)
            return {'status': 'completed', 'exit_code': 0, 'output': json.dumps({'result': 0}),
                    'diagnostics': ''}

        solver.executor = wrong_now
        outcome = solver._resume_partial('increment', 'increment', spec)
        self.assertEqual(len(jobs), len(CHECKS))
        self.assertEqual(outcome['partial']['passed_checks'], [])
        self.assertEqual(solver._frontiers[spec['check_scope']].best_count, 0)
        self.assertNotEqual(outcome['status'], 'checked')
        self.assertEqual(solver.resume_candidates(), {spec['check_scope']: [artifact]})

    def test_changed_scope_does_not_resume_old_partial(self):
        solver, spec, _ = self.partial_fixture()
        book = CheckBook('increment', 'different contract', CHECKS, [])
        solver._checkbooks[book.scope_id] = book
        changed = dict(spec, check_scope=book.scope_id)
        self.assertIsNone(solver._resume_partial('increment', 'increment', changed))
        self.assertEqual(solver._execution_used, 0)

    def test_refuted_or_corrupt_partial_is_never_executed(self):
        solver, spec, artifact = self.partial_fixture()
        solver.ws.refute(artifact, 'fixture refutation')
        self.assertIsNone(solver._resume_partial('increment', 'increment', spec))
        self.assertEqual(solver._execution_used, 0)
        solver._resume_candidates = {spec['check_scope']: [artifact]}
        with mock.patch.object(ArtifactMemory, 'get', side_effect=ValueError('corrupt fixture')):
            self.assertIsNone(solver._resume_partial('increment', 'increment', spec))
        self.assertEqual(solver._execution_used, 0)

    def test_incomplete_revalidation_never_reuses_prior_pass_counts(self):
        solver, spec, artifact = self.partial_fixture()
        solver.executor = lambda job: {'status': 'timed_out', 'exit_code': None,
                                       'output': '', 'diagnostics': ''}
        self.assertIsNone(solver._resume_partial('increment', 'increment', spec))
        self.assertEqual(solver._partials, {})
        self.assertEqual(solver._frontiers, {})
        self.assertEqual(solver.resume_candidates(), {spec['check_scope']: [artifact]})

    def test_untried_partial_survives_a_local_execution_boundary(self):
        solver, spec, artifact = self.partial_fixture()
        solver.max_executions = 1
        self.assertIsNone(solver._resume_partial('increment', 'increment', spec))
        self.assertEqual(solver._execution_used, 0)
        self.assertEqual(solver.resume_candidates(), {spec['check_scope']: [artifact]})

    def test_zero_piece_budget_does_not_start_a_child(self):
        for calls, executions in ((0, 20), (20, 0)):
            with self.subTest(calls=calls, executions=executions):
                solver = self.solver(calls=calls, executions=executions)
                with mock.patch.object(solver, 'solve') as child:
                    result = solver._solve_piece({'request': 'piece', 'checks': CHECKS}, {'checks': CHECKS}, 0, 2)
                child.assert_not_called()
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(self.counter, {'used': 0, 'limit': calls})

    def test_revalidation_keeps_later_passes_after_an_early_failure(self):
        solver, spec, artifact = self.partial_fixture()
        solver.executor = lambda job: {'status': 'completed', 'exit_code': 0,
                                       'output': json.dumps({'result': 3}), 'diagnostics': ''}
        result = solver._resume_partial('increment', 'increment', spec)
        self.assertEqual(result['partial']['passed_checks'], [CHECKS[1]])
        self.assertEqual(result['partial']['failed_checks'][0]['input'], CHECKS[0]['input'])
        self.assertEqual(solver._execution_used, 2)

    def test_piece_requested_input_cannot_skip_gate_at_execution_boundary(self):
        seed = self.solver()
        admitted = seed.admit('increment', CHECKS, CORRECT, 'correct')
        for executions, expected in ((0, 'incomplete'), (1, 'checked')):
            with self.subTest(executions=executions):
                solver = self.solver(executions=executions)
                solver.planner = Metered(ScriptedModel({'contract': 'increment', 'checks': CHECKS, 'input': 3}),
                                        self.counter)
                with mock.patch.object(solver, 'adopt', return_value=dict(admitted)), \
                        mock.patch.object(solver, '_memorized', return_value=([], [])):
                    result = solver.solve('increment', CHECKS, depth=1)
                self.assertEqual(result['status'], expected)
                if not executions:
                    self.assertIn('execution limit', result['reason'])

    def test_suite_runner_carries_partial_candidates_between_rounds(self):
        # These are invented fixture cases in a temporary directory, never repository suite data.
        from tests.test_solve_suite_runner import run

        suite = Path(self.temp.name) / 'fixture-suite'
        suite.mkdir()
        (suite / 'tasks.json').write_text(json.dumps([
            {'id': 'fixture', 'domain': 'fixture', 'level': 1, 'prompt': 'increment'}]))
        (suite / 'hidden.json').write_text(json.dumps({'fixture': [{'input': 3, 'expected': 4}]}))
        planner = ScriptedModel({'contract': 'run(x) returns x+1', 'checks': CHECKS, 'input': None})

        def reply(context):
            history = context['history']
            repairing = 'Source:\n' + PARTIAL in context['request']
            if not history:
                return find_action()
            observation = history[-1]['observation']
            if 'methods' in observation:
                return remember_action(CORRECT if repairing else PARTIAL)
            if repairing and observation.get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': observation['artifact']}}
            return {'kind': 'unresolved', 'reason': 'partial retained'}

        actor = ScriptedModel(reply)

        def models(args, counter):
            return tuple(Metered(model, counter) for model in (planner, actor, planner, planner, planner))

        out = Path(self.temp.name) / 'fixture-out'
        with mock.patch.multiple(run, HERE=suite, preflight=lambda: {'ready': True},
                                 infrastructure_ready=lambda: True,
                                 PythonExecutor=lambda **kwargs: local_executor, build_models=models), \
                mock.patch.object(Solver, 'derive_blind', False):
            run.main(['--arm', 'fresh', '--out', str(out), '--rounds', '2', '--max-calls', '5'])
        row = json.loads((out / 'rows.jsonl').read_text().splitlines()[0])
        self.assertEqual(row['status'], 'checked', row)
        self.assertEqual(row['source'], CORRECT)
        self.assertEqual(row['rounds'], 2)
        self.assertLessEqual(row['model_calls'], 10)


if __name__ == '__main__':
    unittest.main()
