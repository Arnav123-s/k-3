"""Local orchestration regressions for MATI catalogue progress.

No model service or evaluated task is used: the sketcher, checks, and executor
are deterministic local fixtures.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.solve_sketches import SketchCatalogue
from tests.test_kuttaka_campaign import local_executor


class Sketcher:
    identity = {'model': 'local-sketcher'}
    last_usage = {'input_tokens': 1, 'output_tokens': 1}

    def __init__(self, source, holes):
        self.reply = {'source': source, 'holes': holes}
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        return self.reply


class MATIIntegrationTests(unittest.TestCase):
    def make_solver(self, source, holes, *, max_executions=8, executor=None):
        counter = {'used': 0, 'limit': 20}
        workspace = Workspace(Path(tempfile.mkdtemp()))
        sketcher = Sketcher(source, holes)
        solver = Solver(workspace, Metered(sketcher, counter), Metered(sketcher, counter),
                        executor or local_executor, sketcher=Metered(sketcher, counter),
                        max_executions=max_executions, say=lambda _: None)
        spec = {'contract': 'run(x) returns the expected value',
                'checks': [{'input': 1, 'expected': 'second'}], 'check_scope': 'scope-a'}
        best = {'source': source, 'failed_checks': [], 'uses': []}
        return solver, sketcher, spec, best

    def test_invalid_filling_is_skipped_and_later_valid_filling_is_checked(self):
        source = 'def run(x):\n    return f"{HOLE0}"\n'
        solver, _, spec, best = self.make_solver(source, {'HOLE0': ['first', 'second']})
        # Force the first final candidate to be invalid even on interpreters
        # whose f-string grammar accepts nested expressions.
        with patch('kuttaka_runtime.solve_sketches._literal_expression',
                   side_effect=['!', "'second'"]):
            result = solver.mati('task', spec, best, 'task')

        self.assertEqual(result['status'], 'checked')
        self.assertIsNone(result['validation'])
        state = next(iter(solver._sketch_states.values()))
        self.assertEqual(state['cursor'], 2)
        self.assertEqual(state['incomplete'], [])
        self.assertEqual(solver._execution_used, 1)

    def test_execution_cap_leaves_catalogue_incomplete_not_exhausted(self):
        source = 'def run(x):\n    return HOLE0\n'
        solver, _, spec, best = self.make_solver(source, {'HOLE0': [0, 1]}, max_executions=1)
        spec['checks'] = [{'input': 1, 'expected': 1}]

        first = solver.mati('task', spec, best, 'task')
        second = solver.mati('task', spec, best, 'task')

        self.assertEqual(first['status'], 'incomplete')
        self.assertIsNone(second)
        state = next(iter(solver._sketch_states.values()))
        self.assertEqual(state['cursor'], 1)
        self.assertEqual(solver._execution_used, 1)
        self.assertLess(state['cursor'], SketchCatalogue(source, {'HOLE0': [0, 1]}).total)

    def test_repeated_catalogue_resumes_at_cursor(self):
        source = 'def run(x):\n    return HOLE0\n'
        calls = {'count': 0}

        def flaky_executor(job):
            calls['count'] += 1
            if calls['count'] == 1:
                return {'status': 'timeout', 'exit_code': None, 'output': '',
                        'diagnostics': 'fixture timeout', 'dependency_calls': []}
            return local_executor(job)

        solver, sketcher, spec, best = self.make_solver(
            source, {'HOLE0': [0, 1]}, executor=flaky_executor)
        spec['checks'] = [{'input': 1, 'expected': 1}]

        first = solver.mati('task', spec, best, 'task')
        second = solver.mati('task', spec, best, 'task')

        self.assertEqual(first['status'], 'incomplete')
        self.assertEqual(second['status'], 'checked')
        state = next(iter(solver._sketch_states.values()))
        self.assertEqual(state['cursor'], 2)
        self.assertEqual(solver._execution_used, 2)
        self.assertEqual(state['incomplete'], [0])
        self.assertEqual(sketcher.contexts[0]['dependencies'], [])
        self.assertEqual(sketcher.contexts[1]['dependencies'], [])

    def test_dependency_ids_are_offered_and_retained_by_admission(self):
        source = 'def run(x):\n    return HOLE0\n'
        solver, sketcher, spec, best = self.make_solver(source, {'HOLE0': [1]})
        best['uses'] = ['method-id']
        admitted = []

        def admit(request, checks, candidate_source, name, *, check_scope=None, uses=()):
            admitted.append((candidate_source, list(uses)))
            return {'status': 'checked', 'artifact': 'qualified-id', 'reason': 'local fixture',
                    'reused': list(uses), 'run': 'fixture', 'validation': {'status': 'passed'}}

        solver.admit = admit
        result = solver.mati('task', spec, best, 'task')

        self.assertEqual(result['status'], 'checked')
        self.assertEqual(sketcher.contexts[0]['dependencies'], ['method-id'])
        self.assertEqual(admitted[0][1], ['method-id'])
        self.assertEqual(result['reused'], ['method-id'])


if __name__ == '__main__':
    unittest.main()
