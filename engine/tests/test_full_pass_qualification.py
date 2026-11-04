"""Authored fixtures: diagnostic success must enter normal qualification, never replace it."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.check_contracts import CheckBook
from kuttaka_runtime.solve import Metered, Solver, Workspace, digest
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor


INCREMENT = 'def run(n):\n    return n + 1\n'
DOUBLE = 'def run(n):\n    return n * 2\n'
CHECKS = [{'input': n, 'expected': n + 1} for n in range(5)]
PARENT_CHECKS = [{'input': -1, 'expected': 0}, {'input': 2, 'expected': 6},
                 {'input': 4, 'expected': 10}]


def store(source, uses=()):
    return {'kind': 'experiment', 'tool': 'remember_method',
            'input': {'name': 'authored fixture', 'description': 'authored fixture',
                      'source': source, 'uses': list(uses)},
            'predictions': [{'hypothesis': 'source stored', 'outcome': 'artifact ID'}]}


class StoreOnly:
    identity = {'model': 'authored-store-only-fixture'}
    last_usage = {}

    def __init__(self, sources=(INCREMENT,), uses=(), submit=False):
        self.sources, self.uses, self.submit = sources, uses, submit

    def propose(self, context):
        history = context['history']
        if len(history) < len(self.sources):
            return store(self.sources[len(history)], self.uses)
        if self.submit:
            return {'kind': 'answer', 'answer': {'artifact': history[-1]['observation']['artifact']}}
        return {'kind': 'unresolved', 'reason': 'cannot finish the answer protocol'}


class ParentModel(StoreOnly):
    """Can write two children but cannot write parent glue; supplies a bounded recipe."""
    def __init__(self, *, submit=False, wrong_recipe=False):
        super().__init__(submit=submit)
        self.wrong_recipe = wrong_recipe

    def propose(self, context):
        if 'request' in context:
            if context['request'].startswith('Parent'):
                return {'kind': 'unresolved', 'reason': 'cannot write parent glue'}
            self.sources = (INCREMENT if context['request'].startswith('Increment') else DOUBLE,)
            return super().propose(context)
        if 'failure' in context:
            return {'subtasks': []}
        if 'source' in context:
            return {}
        task = context['task']
        operation = ((lambda n: 2 * (n + 1)) if task.startswith('Parent') else
                     (lambda n: n + 1) if task.startswith('Increment') else (lambda n: 2 * n))
        if 'inputs' in context:
            return {'expected': [operation(n) for n in context['inputs']]}
        if task.startswith('Parent'):
            outer, inner = ('inc', 'double') if self.wrong_recipe else ('double', 'inc')
            return {'contract': 'run(n) returns 2*(n+1)', 'checks': PARENT_CHECKS, 'input': 5,
                    'decomposition': {'subtasks': [
                        {'id': 'inc', 'request': 'Increment input.', 'checks': CHECKS},
                        {'id': 'double', 'request': 'Double input.',
                         'checks': [{'input': n, 'expected': 2 * n} for n in range(5)]}],
                        'recipe': {'version': 1, 'output': {'op': 'call', 'method': outer,
                            'arg': {'op': 'call', 'method': inner, 'arg': {'op': 'input'}}}}}}
        increment = task.startswith('Increment')
        expression = 'n+1' if increment else 'n*2'
        return {'contract': 'run(n) returns ' + expression, 'input': None,
                'checks': [{'input': n, 'expected': operation(n)} for n in range(5)],
                'reference': 'def reference(n):\n    return ' + expression + '\n'}


class FullPassQualificationTests(unittest.TestCase):
    def make(self, model=None, *, calls=2, executions=64):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.counter = {'used': 0, 'limit': calls}
        metered = Metered(model or StoreOnly(), self.counter)
        return Solver(Workspace(Path(directory.name)), metered, metered, local_executor,
                      max_executions=executions, say=lambda _: None)

    def test_full_pass_qualifies_after_last_model_call_with_fresh_check_receipts(self):
        solver = self.make()
        with mock.patch.object(solver, 'admit', wraps=solver.admit) as admit:
            outcome = solver.attempt('Increment input.', CHECKS)
        self.assertEqual(outcome['status'], 'checked')
        self.assertEqual(self.counter['used'], 2)
        self.assertEqual(solver._execution_used, 10)  # five diagnostics, five normal validation executions
        self.assertEqual(admit.call_count, 1)
        self.assertEqual(admit.call_args.kwargs['stored'], outcome['artifact'])
        self.assertEqual(outcome['tool_calls'], 2)  # store plus controller checker
        self.assertEqual(outcome['qualification_tool_calls'], 1)
        self.assertEqual(outcome['reused'], [])  # a newly written method is not prior memory reuse
        events = Journal.read(Path(outcome['run']) / 'events.jsonl')
        receipts = [e['body'] for e in events if e['kind'] == 'method_check_receipt']
        self.assertEqual([r['status'] for r in receipts], ['passed'] * 5)
        with ArtifactMemory(solver.ws.memory) as memory:
            self.assertEqual(memory.status(outcome['artifact'], outcome['task'].scope_id), 'qualified')

    def test_wrong_source_keeps_counterexamples_and_is_not_admitted(self):
        solver = self.make(StoreOnly(('def run(n):\n    return n + 2\n',)))
        with mock.patch.object(solver, 'admit', wraps=solver.admit) as admit:
            outcome = solver.attempt('Increment input.', CHECKS)
        self.assertNotEqual(outcome['status'], 'checked')
        self.assertEqual(len(outcome['partial']['failed_checks']), 5)
        self.assertEqual(solver._execution_used, 5)
        admit.assert_not_called()

    def test_normal_admission_can_exhaust_execution_budget_without_qualifying(self):
        solver = self.make(executions=9)
        with mock.patch.object(solver, 'admit', wraps=solver.admit) as admit:
            outcome = solver.attempt('Increment input.', CHECKS)
        self.assertNotEqual(outcome['status'], 'checked')
        self.assertEqual(len(outcome['partial']['passed_checks']), 5)
        self.assertEqual(self.counter['used'], 2)
        self.assertEqual(solver._execution_used, 9)
        self.assertEqual(admit.call_count, 1)
        self.assertEqual(outcome['tool_calls'], 2)  # unsuccessful checker calls are still spent
        self.assertEqual(outcome['qualification_tool_calls'], 1)
        with ArtifactMemory(solver.ws.memory) as memory:
            self.assertEqual(memory.status(outcome['partial']['artifact'], outcome['task'].scope_id), 'proposed')

    def test_full_pass_recovery_requires_a_remaining_tool_slot(self):
        for limit in (1, 2):
            with self.subTest(tool_calls=limit):
                solver = self.make()
                solver.tool_calls = limit
                with mock.patch.object(solver, 'admit', wraps=solver.admit) as admit:
                    outcome = solver.attempt('Increment input.', CHECKS)
                self.assertEqual(outcome['status'] == 'checked', limit == 2)
                self.assertEqual(outcome['tool_calls'], limit)
                self.assertEqual(outcome['qualification_tool_calls'], limit - 1)
                self.assertEqual(admit.call_count, limit - 1)
                self.assertEqual(self.counter['used'], 2)
                self.assertEqual(solver._execution_used, 5 if limit == 1 else 10)
                if limit == 1:
                    self.assertEqual(len(outcome['partial']['passed_checks']), 5)

    def test_stale_scope_changed_checks_and_mismatched_identity_are_not_recovered(self):
        mutations = {
            'scope': lambda p: p.update(check_scope='stale'),
            'checks': lambda p: p.update(passed_checks=[{'input': n, 'expected': n + 2} for n in range(5)]),
            'source': lambda p: p.update(source=DOUBLE),
            'dependencies': lambda p: p.update(uses=['0' * 64]),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                solver = self.make()
                original = solver._partial
                def corrupt(*args, **kwargs):
                    partial = dict(original(*args, **kwargs))
                    mutate(partial)
                    return partial
                with mock.patch.object(solver, '_partial', side_effect=corrupt), \
                        mock.patch.object(solver, 'admit', wraps=solver.admit) as admit:
                    outcome = solver.attempt('Increment input.', CHECKS)
                self.assertNotEqual(outcome['status'], 'checked')
                admit.assert_not_called()

    def test_pass_evidence_does_not_replace_normal_checker(self):
        solver = self.make(StoreOnly(('def run(n):\n    return n + 2\n',)))
        original = solver._partial
        def false_diagnostic(*args, **kwargs):
            partial = dict(original(*args, **kwargs))
            partial.update(passed_checks=CHECKS, failed_checks=[])
            return partial
        with mock.patch.object(solver, '_partial', side_effect=false_diagnostic):
            outcome = solver.attempt('Increment input.', CHECKS)
        self.assertNotEqual(outcome['status'], 'checked')
        self.assertEqual(solver._execution_used, 10)

    def test_refuted_artifact_and_task_source_are_not_resurrected(self):
        for kind in ('artifact', 'source', 'other task source'):
            with self.subTest(kind=kind):
                solver = self.make()
                prompt = 'Increment input.'
                book = CheckBook(prompt, 'run(n) returns n+1', CHECKS, [])
                solver._checkbooks[book.scope_id] = book
                original = solver._partial
                def refute(*args, **kwargs):
                    partial = original(*args, **kwargs)
                    solver.ws.refute(partial['artifact'] if kind == 'artifact' else '0' * 64,
                                     'authored prior counterexample', source_digest=digest(INCREMENT),
                                     task='unrelated task' if kind == 'other task source' else prompt)
                    return partial
                with mock.patch.object(solver, '_partial', side_effect=refute):
                    outcome = solver.attempt(prompt + '\n\nContract: run(n) returns n+1', CHECKS,
                                             check_scope=book.scope_id)
                self.assertEqual(outcome['status'] == 'checked', kind == 'other task source')

    def test_dependency_refutation_and_invalidation_block_full_pass_recovery(self):
        for blocked in (None, 'refuted', 'invalidated'):
            with self.subTest(blocked=blocked):
                model = StoreOnly()
                solver = self.make(model)
                dependency = solver.admit('Increment dependency.', CHECKS, INCREMENT, 'dependency')['artifact']
                model.sources = ('def run(n):\n    return use("' + dependency + '")(n)\n',)
                model.uses = (dependency,)
                if blocked == 'refuted':
                    solver.ws.refute(dependency, 'authored counterexample')
                elif blocked == 'invalidated':
                    with ArtifactMemory(solver.ws.memory) as memory:
                        memory.invalidate(dependency, 'authored counterexample', ['fixture-evidence'])
                outcome = solver.attempt('Increment input.', CHECKS)
                self.assertEqual(outcome['status'] == 'checked', blocked is None)
                if blocked:
                    self.assertEqual(len(outcome['partial']['passed_checks']), 5)
                else:
                    self.assertEqual(outcome['reused'], [dependency])

    def test_recovery_keeps_all_other_written_candidates_as_rivals(self):
        alternative = 'def run(n):\n    return n + 1 if n < 5 else n + 2\n'
        solver = self.make(StoreOnly((INCREMENT, alternative)), calls=3)
        with mock.patch.object(solver, '_read_written', wraps=solver._read_written) as read:
            outcome = solver.attempt('Increment input.', CHECKS)
        self.assertEqual(outcome['status'], 'checked')
        with ArtifactMemory(solver.ws.memory) as memory:
            written = set(memory.ids('method'))
        self.assertEqual(set(outcome['rivals']), written - {outcome['artifact']})
        self.assertEqual(len(outcome['rivals']), 1)
        self.assertEqual(read.call_args.args[0], outcome['rivals'])

    def test_default_scheduler_composes_store_only_children_like_submitted_children(self):
        for submit in (False, True):
            with self.subTest(submit=submit):
                solver = self.make(ParentModel(submit=submit), calls=40, executions=256)
                outcome = solver.solve('Parent: increment and then double.', PARENT_CHECKS)
                self.assertEqual(outcome['status'], 'checked', outcome)
                self.assertEqual(outcome['answer'], 12)
                self.assertEqual([p['status'] for p in outcome['subtasks']], ['checked', 'checked'])
                self.assertEqual(set(outcome['reused']), {p['artifact'] for p in outcome['subtasks']})
                self.assertEqual(self.counter['used'], 7)
                events = Journal.read(outcome['solve_journal'])
                recoveries = [e for e in events if e['kind'] == 'solve_full_pass_qualification']
                self.assertEqual(len(recoveries), 0 if submit else 2)
                if not submit:
                    attempts = [e['body'] for e in events if e['kind'] == 'solve_attempt_finished']
                    self.assertTrue(all(a['status'] != 'checked' and a['artifact'] is None for a in attempts))
                    by_run = {a['run']: a for a in attempts}
                    for recovery in recoveries:
                        body = recovery['body']
                        self.assertEqual(body['tool_calls_before'], by_run[body['attempt_run']]['tool_calls'])
                        self.assertEqual(body['tool_calls'], body['tool_calls_before'] + 1)
                        self.assertEqual(body['qualification_tool_calls'], 1)
                        self.assertLessEqual(body['tool_calls'], body['tool_call_limit'])
                        self.assertNotEqual(body['run'], body['attempt_run'])

    def test_qualified_children_do_not_certify_wrong_parent_recipe(self):
        solver = self.make(ParentModel(wrong_recipe=True), calls=40, executions=256)
        outcome = solver.solve('Parent: increment and then double.', PARENT_CHECKS)
        self.assertNotEqual(outcome['status'], 'checked')
        self.assertNotIn('answer', outcome)
        self.assertTrue(any(p['status'] == 'checked' for p in outcome['subtasks']))

    def test_recovered_parent_still_faces_final_answer_gate(self):
        solver = self.make(ParentModel(), calls=40, executions=256)
        with mock.patch.object(solver, '_answer_gate', return_value={'reason': 'authored unresolved disagreement'}):
            outcome = solver.solve('Parent: increment and then double.', PARENT_CHECKS)
        self.assertNotEqual(outcome['status'], 'checked')
        self.assertNotIn('answer', outcome)
        self.assertEqual(outcome['reason'], 'authored unresolved disagreement')


if __name__ == '__main__':
    unittest.main()
