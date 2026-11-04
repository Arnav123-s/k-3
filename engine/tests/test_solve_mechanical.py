"""Steps that need no model call: assembly from solved parts, reuse as is, and best-not-first Mati.

Scripted models and the local executor only; no model service or evaluated task is used.
"""

import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.solve_compose import compositions
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_kuttaka_solve import Actor, Planner, SQUARE
from tests.test_solve_mati_integration import Sketcher

LOWER = 'def run(s):\n    return s.lower()\n'
VOWELS = 'def run(s):\n    return sum(c in "aeiou" for c in s)\n'
DOUBLE = 'def run(x):\n    return 2 * x\n'


class PartsPlanner(Planner):
    """Plans two tasks the actor can only solve in parts, and splits them."""

    def propose(self, context):
        self.contexts.append(context)
        task = context['task']
        if 'failure' in context:
            if task.startswith('Count vowels'):
                return {'subtasks': [
                    {'request': 'Lowercase a string.', 'checks': [{'input': 'AB', 'expected': 'ab'}]},
                    {'request': 'Count the vowels of a lowercase string.',
                     'checks': [{'input': 'abe', 'expected': 2}]}]}
            return {'subtasks': [
                {'request': 'Double a number.', 'checks': [{'input': 2, 'expected': 4}]},
                {'request': 'Square a number.', 'checks': [{'input': 3, 'expected': 9}]}]}
        if task.startswith('Count vowels'):
            return {'contract': 'run(s) counts the vowels of s, ignoring case', 'input': None,
                    'checks': [{'input': 'AbE', 'expected': 2}, {'input': 'xyz', 'expected': 0}]}
        if task.startswith('Both'):
            return {'contract': 'run(x) returns [2*x, x*x]', 'input': None,
                    'checks': [{'input': 3, 'expected': [6, 9]}, {'input': 0, 'expected': [0, 0]}]}
        if task.startswith('Double'):
            return {'contract': 'run(x) returns 2*x', 'checks': [{'input': 2, 'expected': 4}], 'input': None}
        if task.startswith('Lowercase'):
            return {'contract': 'run(s) returns s lowercased', 'checks': [{'input': 'Q', 'expected': 'q'}],
                    'input': None}
        if task.startswith('Count the vowels'):
            return {'contract': 'run(s) counts vowels', 'checks': [{'input': 'oa', 'expected': 2}], 'input': None}
        return super().propose(dict(context))


class PartsActor(Actor):
    """Writes each part, but never the whole task, even when the parts are offered."""
    requests = []

    def propose(self, context):
        request = context['request']
        PartsActor.requests.append(request)
        if not context['history']:
            return super().propose(context)
        if 'artifact' in context['history'][-1]['observation']:
            return {'kind': 'answer', 'answer': {'artifact': context['history'][-1]['observation']['artifact']}}
        sources = {'Lowercase': LOWER, 'Count the vowels': VOWELS, 'Double': DOUBLE, 'Square': SQUARE}
        source = next((s for prefix, s in sources.items() if request.startswith(prefix)), None)
        if source is None:
            return {'kind': 'unresolved', 'reason': 'too hard in one step'}
        return {'kind': 'experiment', 'tool': 'remember_method',
                'input': {'name': 'm', 'description': 'm', 'source': source},
                'predictions': [{'hypothesis': 'stored', 'outcome': 'artifact id'}]}


def events(result, kind):
    return [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == kind]


class CompositionTests(unittest.TestCase):
    def test_fixed_rules_in_order_without_duplicates(self):
        rules = compositions(['a', 'b', 'a'])
        self.assertEqual([label for label, _, _ in rules],
                         ['chain', 'single', 'single', 'side by side', 'reverse chain'])
        self.assertEqual([uses for _, _, uses in rules][1:3], [['b'], ['a']])
        self.assertEqual([label for label, _, _ in compositions(['a'])], ['single'])

    def test_every_rule_runs_in_the_sandbox(self):
        libraries = {'a': DOUBLE, 'b': SQUARE}
        expected = {'chain': 36, 'single': None, 'side by side': [6, 9], 'reverse chain': 18}
        for label, source, uses in compositions(['a', 'b']):
            job = {'source': source, 'input': 3, 'libraries': {u: libraries[u] for u in uses}}
            result = json.loads(local_executor(job)['output'])['result']
            if expected[label] is not None:
                self.assertEqual(result, expected[label], label)


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(tempfile.mkdtemp())
        self.counter = {'used': 0, 'limit': 40}
        PartsActor.requests = []

    def solver(self):
        return Solver(self.ws, Metered(PartsPlanner(), self.counter), Metered(PartsActor(), self.counter),
                      local_executor, say=lambda text: None)

    def test_parts_are_chained_without_a_model_call(self):
        result = self.solver().solve('Count vowels in a string, ignoring case.')
        self.assertEqual(result['status'], 'checked')
        parts = [p['artifact'] for p in result['subtasks']]
        self.assertEqual(sorted(result['reused']), sorted(parts))
        self.assertIn('assembled', result['reason'])
        # The actor was asked for the parts only: never for glue over the stored parts.
        self.assertFalse(any(r.startswith('Count vowels in a string') and 'Lowercase a string' in r
                             for r in PartsActor.requests))
        assembly = events(result, 'solve_assembly')
        self.assertEqual(assembly[-1]['tried'][-1]['rule'], 'chain')
        self.assertEqual(assembly[-1]['artifact'], result['artifact'])
        mechanical = dict(result['mechanical'])
        mechanical.pop('prastara_screened', None)   # stored parts of the same input form are screened first
        self.assertEqual(mechanical, {'assembly_tried': 1, 'assembled': 1})
        # A breakdown that worked is kept for similar tasks.
        self.assertTrue(self.ws.similar_splits('Count vowels in a string, ignoring case.'))

    def test_parts_side_by_side(self):
        result = self.solver().solve('Both the double and the square of a number.')
        self.assertEqual(result['status'], 'checked')
        rules = [t['rule'] for t in events(result, 'solve_assembly')[-1]['tried']]
        self.assertEqual(rules, ['chain', 'single', 'single', 'side by side'])

    def test_no_fitting_rule_falls_back_to_the_model(self):
        counter = {'used': 0, 'limit': 40}
        solver = Solver(self.ws, Metered(Planner(), counter), Metered(Actor(), counter), local_executor,
                        say=lambda text: None)
        result = solver.solve('Sum the squares of a list.')
        self.assertEqual(result['status'], 'checked')
        assembly = events(result, 'solve_assembly')
        self.assertIsNone(assembly[-1]['artifact'])
        self.assertTrue(all(t['status'] == 'incomplete' for t in assembly[-1]['tried']))
        self.assertIn('use(', result['source'])   # the model's own glue
        mechanical = dict(result['mechanical'])
        mechanical.pop('prastara_screened', None)   # stored parts screened first; none fits these checks
        self.assertEqual(mechanical, {'assembly_tried': 1})


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(tempfile.mkdtemp())
        self.counter = {'used': 0, 'limit': 40}

    def solver(self):
        return Solver(self.ws, Metered(Planner(), self.counter), Metered(Actor(), self.counter),
                      local_executor, say=lambda text: None)

    def test_a_repeated_task_costs_only_the_plan(self):
        first = self.solver().solve('Square a number.')
        self.counter['used'] = 0
        again = self.solver().solve('Square a number.')
        self.assertEqual(again['status'], 'checked')
        self.assertEqual(again['artifact'], first['artifact'])
        self.assertIn(first['artifact'], again['reused'])
        self.assertEqual(self.counter['used'], 1)   # the planner only
        self.assertEqual(again['strategies'], ['reuse'])
        self.assertEqual(again['mechanical'], {'reused_as_is': 1})

    def test_a_stored_method_that_does_not_fit_is_not_used(self):
        square = self.solver().solve('Square a number.')
        self.ws.remember_solved('Fix the doubling of a number.', 'run(x) returns 2*x', square['artifact'])
        result = self.solver().solve('Fix the doubling of a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertNotEqual(result['artifact'], square['artifact'])
        tried = events(result, 'solve_adoption')[0]['tried']
        self.assertEqual([(t['artifact'], t['status']) for t in tried], [(square['artifact'], 'incomplete')])
        self.assertNotIn('reuse', result['strategies'])


class BestFillingTests(unittest.TestCase):
    TEMPLATE = 'def run(x):\n    return x * x if HOLE0 else x + x\n'

    @staticmethod
    def make(self, holes, spec_extra=(), checks=None, max_executions=64):
        counter = {'used': 0, 'limit': 20}
        self.ws = Workspace(Path(tempfile.mkdtemp()))
        sketcher = Sketcher(BestFillingTests.TEMPLATE, holes)
        solver = Solver(self.ws, Metered(sketcher, counter), Metered(sketcher, counter), local_executor,
                        sketcher=Metered(sketcher, counter), max_executions=max_executions, say=lambda _: None)
        solver._job = Journal(self.ws.root / 'job.jsonl')
        self.addCleanup(solver._job.close)
        spec = {'contract': 'run(x) returns 2*x', 'check_scope': 'scope-a',
                'checks': checks or [{'input': 0, 'expected': 0}, {'input': 2, 'expected': 4}]}
        spec.update(spec_extra)
        best = {'source': BestFillingTests.TEMPLATE, 'failed_checks': [], 'passed_checks': [], 'uses': []}
        return solver, spec, best

    def test_the_filling_the_reference_agrees_with_wins_over_the_first(self):
        # Both fillings pass both checks (0 -> 0, 2 -> 4); only x + x agrees with the reference.
        solver, spec, best = BestFillingTests.make(self, {'HOLE0': [True, False]},
                                       {'reference': 'def reference(x):\n    return 2 * x\n',
                                        'small_inputs': [3, 4, 5]})
        result = solver.mati('task', spec, best, 'task')
        self.assertEqual(result['status'], 'checked')
        with ArtifactMemory(self.ws.memory) as memory:
            self.assertIn('if (False)', memory.get(result['artifact'])['body']['source'])
            ranked = [e['body'] for e in Journal.read(self.ws.root / 'job.jsonl') if e['kind'] == 'solve_mati_ranked']
            self.assertEqual(ranked[0]['agreement'], [0, 3])
            loser = ranked[0]['artifacts'][0]
            self.assertTrue(all(not q['active'] for q in memory.qualifications(loser)))

    def test_without_a_reference_the_first_filling_that_passes_is_kept(self):
        solver, spec, best = BestFillingTests.make(self, {'HOLE0': [True, False]})
        result = solver.mati('task', spec, best, 'task')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(solver._execution_used, 2)   # one filling, two checks; the second is never run
        with ArtifactMemory(self.ws.memory) as memory:
            self.assertIn('if (True)', memory.get(result['artifact'])['body']['source'])

    def test_a_filling_that_passes_more_checks_is_kept_as_progress(self):
        # No filling passes all three checks, but both pass one more than best did.
        checks = [{'input': 0, 'expected': 0}, {'input': 2, 'expected': 4}, {'input': 3, 'expected': 7}]
        solver, spec, best = BestFillingTests.make(self, {'HOLE0': [True, False]}, checks=checks)
        best['passed_checks'] = [checks[0]]   # both fillings pass two checks; neither passes the third
        result = solver.mati('task', spec, best, 'task')
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(len(result['partial']['passed_checks']), 2)
        self.assertEqual(result['partial']['failed_checks'][0]['got'], 9)
        self.assertIn('if (True)', result['partial']['source'])   # first of the equally good fillings

    def test_a_filling_no_better_than_best_is_not_progress(self):
        checks = [{'input': 0, 'expected': 0}, {'input': 2, 'expected': 4}, {'input': 3, 'expected': 7}]
        solver, spec, best = BestFillingTests.make(self, {'HOLE0': [True, False]}, checks=checks)
        best['passed_checks'] = checks[:2]
        result = solver.mati('task', spec, best, 'task')
        self.assertIsNone(result.get('partial'))


if __name__ == '__main__':
    unittest.main()


class ReviewRegressionTests(unittest.TestCase):
    """Failures found by the independent review of PR 36; each reproduced on c7a3edc before the fix."""

    def test_a_part_rejected_for_memorizing_is_never_assembled_or_delivered(self):
        checks = [{'input': 5, 'expected': 15}, {'input': 7, 'expected': 22}]   # 7 -> 22 is a wrong check
        memo = 'def run(x):\n    if x == 5:\n        return 15\n    if x == 7:\n        return 22\n    return 3 * x\n'

        class Model:
            identity = {'model': 'scripted'}
            last_usage = {'input_tokens': 1, 'output_tokens': 1}

            def propose(self, context):
                if 'request' in context:
                    history, request = context['history'], context['request']
                    if history and 'artifact' in history[-1]['observation']:
                        return {'kind': 'answer', 'answer': {'artifact': history[-1]['observation']['artifact']}}
                    if not history and request.startswith('Triple a number exactly'):
                        return {'kind': 'experiment', 'tool': 'remember_method',
                                'input': {'name': 'm', 'description': 'm', 'source': memo},
                                'predictions': [{'hypothesis': 'stored', 'outcome': 'id'}]}
                    return {'kind': 'unresolved', 'reason': 'stuck'}
                if 'failure' in context:
                    return {'subtasks': [{'request': 'Triple a number exactly.', 'checks': checks}]}
                if 'inputs' in context:
                    return {'expected': [22 for _ in context['inputs']]}
                return {'contract': 'run(x) returns 3*x', 'checks': checks, 'input': None}

        ws = Workspace(Path(tempfile.mkdtemp()))
        counter, model = {'used': 0, 'limit': 40}, Model()
        result = Solver(ws, Metered(model, counter), Metered(model, counter), local_executor,
                        say=lambda _: None).solve('Triple a number.')
        self.assertNotEqual(result['status'], 'checked')
        self.assertTrue(all(p['status'] != 'checked' and not p.get('artifact') for p in result['subtasks']))
        self.assertTrue(ws.refuted())
        self.assertFalse(ws.helpful_methods('Triple a number exactly.'))

    def test_a_wrapper_around_a_memorizing_method_is_flagged(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        solver = Solver(ws, None, None, local_executor, say=lambda _: None)
        checks = [{'input': 7, 'expected': 22}]
        memo = 'def run(x):\n    if x == 7:\n        return 22\n    return 3 * x\n'
        inner = solver.admit('Triple.', checks, memo, 'inner')['artifact']
        outer = solver.admit('Triple.', checks, 'def run(x):\n    return use(%r)(x)\n' % inner, 'outer',
                             uses=[inner])['artifact']
        found, culprits = solver._memorized({'artifact': outer}, {'checks': checks})
        self.assertEqual(found, checks)
        self.assertEqual(culprits, [inner])   # the memorizing source itself, not just the wrapper

    def test_a_method_refuted_by_differential_testing_is_not_reused(self):
        from tests.test_kuttaka_solve import DiffActor, DiffPlanner

        def solver(ws):
            counter = {'used': 0, 'limit': 40}
            return Solver(ws, Metered(DiffPlanner(), counter), Metered(DiffActor(), counter), local_executor,
                          say=lambda _: None)

        ws = Workspace(tempfile.mkdtemp())
        first = solver(ws).solve('Identity abs of a number.')   # stores x, which is wrong for negatives
        second = solver(ws).solve('Abs of a number.')           # reuses it; differential testing refutes it
        self.assertIn('differential', second['strategies'])
        self.assertIn(first['artifact'], ws.refuted())
        third = solver(ws).solve('Identity abs of a number.')
        self.assertNotEqual(third['artifact'], first['artifact'])

    def test_checks_that_did_not_run_are_not_counted_as_passed(self):
        checks = [{'input': 5, 'expected': 11}, {'input': 0, 'expected': 0}, {'input': 3, 'expected': 6}]
        solver, spec, best = BestFillingTests.make(self, {'HOLE0': [True]}, checks=checks, max_executions=1)
        result = solver.mati('task', spec, best, 'task')
        self.assertEqual(result['validation']['checks_completed'], 1)
        self.assertIsNone(result.get('partial'))

    def test_a_filling_whose_batch_failed_is_unknown_not_refuted(self):
        solver, spec, best = BestFillingTests.make(
            self, {'HOLE0': [True, False]},
            {'reference': 'def reference(x):\n    return 2 * x\n', 'small_inputs': [3, 4, 5]})
        real = solver._sandbox_job
        solver._sandbox_job = lambda source, value, libraries: (None if '(False)' in source
                                                                else real(source, value, libraries))
        result = solver.mati('task', spec, best, 'task')
        ranked = [e['body'] for e in Journal.read(self.ws.root / 'job.jsonl') if e['kind'] == 'solve_mati_ranked']
        self.assertEqual(ranked[0]['agreement'], [0, None])
        with ArtifactMemory(self.ws.memory) as memory:
            for artifact in ranked[0]['artifacts']:
                self.assertTrue(any(q['active'] for q in memory.qualifications(artifact)))
        self.assertEqual(result['status'], 'checked')

    def test_no_extra_search_when_every_small_input_is_a_check(self):
        solver, spec, best = BestFillingTests.make(
            self, {'HOLE0': [True, False]},
            {'reference': 'def reference(x):\n    return 2 * x\n', 'small_inputs': [0, 2]})
        solver.mati('task', spec, best, 'task')
        self.assertEqual(solver._execution_used, 2)

    def test_delivered_by_names_the_step_that_wrote_the_method(self):
        ws = Workspace(tempfile.mkdtemp())
        counter = {'used': 0, 'limit': 40}

        def solver():
            return Solver(ws, Metered(Planner(), counter), Metered(Actor(), counter), local_executor,
                          say=lambda _: None)
        self.assertEqual(solver().solve('Square a number.')['delivered_by'], 'model')
        self.assertEqual(solver().solve('Square a number.')['delivered_by'], 'reuse')
