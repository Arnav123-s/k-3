"""Local integration tests for mechanical composition in ``kuttaka solve``."""

import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.composition import compile_recipe
from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor


def check(value, expected):
    return {'input': value, 'expected': expected}


SQUARE = 'def run(x):\n    return x * x\n'
TOTAL = 'def run(xs):\n    return sum(xs)\n'
INCREMENT = 'def run(x):\n    return x + 1\n'


def square_total_recipe(square='square', total='total'):
    return {'version': 1, 'output': {
        'op': 'call', 'method': total,
        'arg': {'op': 'map', 'method': square, 'arg': {'op': 'input'}}}}


def decomposition(*, first_id='square', first_request='square each value', first_checks=None,
                  recipe=None, second_id='total', second_request='sum values', second_checks=None):
    return {
        'subtasks': [
            {'id': first_id, 'request': first_request,
             'checks': first_checks if first_checks is not None else [check(2, 4)]},
            {'id': second_id, 'request': second_request,
             'checks': second_checks if second_checks is not None else [check([1, 2], 3)]},
        ],
        'recipe': recipe if recipe is not None else square_total_recipe(first_id, second_id),
    }


class ScriptedPlanner:
    identity = {'model': 'local-composition-planner'}
    last_usage = {}

    def __init__(self, parent_decomposition=None, *, parent_input=None, parent_checks=None,
                 parent_request='sum squares list', children=None):
        self.parent_decomposition = parent_decomposition
        self.parent_input = parent_input
        self.parent_checks = parent_checks or [check([1, 2, 3], 14)]
        self.parent_request = parent_request
        self.children = children or {
            'square each value': ('square values', [check(2, 4)]),
            'sum values': ('sum a list', [check([1, 2], 3)]),
        }
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        task = context['task']
        if task == self.parent_request:
            reply = {'contract': 'run(xs) returns the sum of the squares of xs',
                     'checks': self.parent_checks, 'input': self.parent_input}
            if self.parent_decomposition is not None:
                reply['decomposition'] = self.parent_decomposition
            return reply
        for request, (contract, checks) in self.children.items():
            if task == request:
                return {'contract': f'run(input) returns {contract}', 'checks': checks, 'input': None}
        raise AssertionError(f'unexpected planner task: {task!r}')


class ComponentActor:
    identity = {'model': 'local-composition-actor'}
    last_usage = {}

    def __init__(self, sources, *, parent_request='sum squares list', forbid_parent=False,
                 unresolved_parent=False):
        self.sources = sources
        self.parent_request = parent_request
        self.forbid_parent = forbid_parent
        self.unresolved_parent = unresolved_parent
        self.parent_calls = 0
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        request = context['request']
        if request.startswith(self.parent_request):
            self.parent_calls += 1
            if self.forbid_parent:
                raise AssertionError('mechanical parent assembly must not call the parent actor')
            if self.unresolved_parent:
                return {'kind': 'unresolved', 'reason': 'parent has no direct implementation'}
        label = next((key for key in self.sources if request.startswith(key)), None)
        if label is None:
            return {'kind': 'unresolved', 'reason': 'no scripted candidate for this request'}
        history = context['history']
        if not history:
            return {'kind': 'experiment', 'tool': 'find_methods',
                    'input': {'query': label, 'limit': 4},
                    'predictions': [{'hypothesis': 'a prior component may apply', 'outcome': 'method list'}]}
        observation = history[-1].get('observation', {})
        if 'methods' in observation:
            return {'kind': 'experiment', 'tool': 'remember_method',
                    'input': {'name': label, 'description': label, 'source': self.sources[label]},
                    'predictions': [{'hypothesis': 'component is stored', 'outcome': 'artifact id'}]}
        if observation.get('artifact'):
            return {'kind': 'answer', 'answer': {'artifact': observation['artifact']}}
        return {'kind': 'unresolved', 'reason': 'scripted component attempt stopped'}


class EmptySplitter:
    identity = {'model': 'local-empty-splitter'}
    last_usage = {}

    def propose(self, context):
        return {'subtasks': []}


class MechanicalCompositionIntegrationTests(unittest.TestCase):
    def make_solver(self, planner, actor, *, splitter=None, max_calls=40, max_executions=64):
        self.workspace = Workspace(Path(tempfile.mkdtemp()))
        self.counter = {'used': 0, 'limit': max_calls}
        return Solver(
            self.workspace,
            Metered(planner, self.counter),
            Metered(actor, self.counter),
            local_executor,
            splitter=Metered(splitter, self.counter) if splitter else None,
            max_executions=max_executions,
            say=lambda _: None,
        )

    def test_proactive_sum_of_squares_composes_without_parent_actor_and_delivers_input(self):
        planner = ScriptedPlanner(parent_decomposition=decomposition(), parent_input=[1, 2, 3])
        actor = ComponentActor({'square each value': SQUARE, 'sum values': TOTAL}, forbid_parent=True)
        solver = self.make_solver(planner, actor, max_calls=9)

        result = solver.solve('sum squares list')

        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['answer'], 14)
        self.assertEqual(actor.parent_calls, 0)
        self.assertEqual([piece['status'] for piece in result['subtasks']], ['checked', 'checked'])
        self.assertEqual(result['composition']['model_glue_calls'], 0)
        self.assertEqual(result['verification_scope'], 'supplied_finite_checks_only')

    def test_wrong_recipe_cannot_inherit_checked_piece_status(self):
        wrong = decomposition(
            first_id='increment', first_request='increment each value',
            first_checks=[check(2, 3)],
            second_id='total', second_request='sum values',
            recipe=square_total_recipe('increment', 'total'),
        )
        planner = ScriptedPlanner(
            parent_decomposition=wrong, parent_checks=[check([1, 3], 10)],
            children={
                'increment each value': ('increment values', [check(2, 3)]),
                'sum values': ('sum a list', [check([1, 2], 3)]),
            })
        actor = ComponentActor({'increment each value': INCREMENT, 'sum values': TOTAL})
        solver = self.make_solver(planner, actor, max_calls=24)

        result = solver.solve('sum squares list')

        self.assertNotEqual(result['status'], 'checked')
        self.assertNotIn('composition', result)
        events = Journal.read(result['solve_journal'])
        pieces = [event['body'] for event in events if event['kind'] == 'solve_decomposition_piece']
        self.assertEqual([piece['status'] for piece in pieces[:2]], ['checked', 'checked'])
        self.assertFalse(any(event['kind'] == 'solve_composition_result'
                             and event['body']['status'] == 'checked' for event in events))

    def test_empty_map_reports_only_dependencies_called_in_actual_trace(self):
        planner = ScriptedPlanner(parent_decomposition=decomposition(), parent_input=[],
                                  parent_checks=[check([], 0)])
        actor = ComponentActor({'square each value': SQUARE, 'sum values': TOTAL}, forbid_parent=True)
        solver = self.make_solver(planner, actor, max_calls=9)

        result = solver.solve('sum squares list')

        self.assertEqual(result['status'], 'checked', result)
        square_id, total_id = (piece['artifact'] for piece in result['subtasks'])
        self.assertEqual(set(result['composition']['dependencies']), {square_id, total_id})
        self.assertEqual(result['reused'], [total_id])
        self.assertNotIn(square_id, result['reused'])

    def test_compiled_manifest_reloads_with_exact_source_ids_and_parent_scope_qualification(self):
        planner = ScriptedPlanner(parent_decomposition=decomposition(), parent_checks=[check([2, 4], 20)])
        actor = ComponentActor({'square each value': SQUARE, 'sum values': TOTAL}, forbid_parent=True)
        solver = self.make_solver(planner, actor, max_calls=9)

        result = solver.solve('sum squares list')

        self.assertEqual(result['status'], 'checked', result)
        with ArtifactMemory(self.workspace.memory) as memory:
            parent = memory.get(result['artifact'])
            parent_scope = parent['applicability']['validation_scope']
            self.assertEqual(memory.status(result['artifact'], parent_scope), 'qualified')
            self.assertEqual(parent['provenance']['producer'], 'mechanical composition')
            manifest = result['composition']['manifest']
            rebuilt = compile_recipe(json.loads(manifest['recipe_json']), resolve_method=memory.get)
            self.assertEqual(rebuilt.source, parent['body']['source'])
            self.assertEqual(list(rebuilt.uses), parent['dependencies'])
            self.assertEqual(rebuilt.identity, result['composition']['identity'])
            for piece in result['subtasks']:
                payload = memory.get(piece['artifact'])
                self.assertNotEqual(payload['applicability']['validation_scope'], parent_scope)
                self.assertEqual(memory.status(piece['artifact'], parent_scope), 'qualified')
                self.assertTrue(any(q['scope'] == parent_scope and q['checker'].endswith('/as-dependency')
                                    and q['active'] for q in memory.qualifications(piece['artifact'])))

    def test_checked_pieces_compose_with_no_model_calls_left(self):
        planner = ScriptedPlanner(parent_decomposition=decomposition(), parent_checks=[check([1, 2], 5)])
        actor = ComponentActor({'square each value': SQUARE, 'sum values': TOTAL}, forbid_parent=True)
        solver = self.make_solver(planner, actor, max_calls=9)

        result = solver.solve('sum squares list')

        self.assertEqual(self.counter['used'], 9)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['composition']['model_glue_calls'], 0)
        self.assertEqual(actor.parent_calls, 0)

    def test_last_model_call_after_first_piece_leaves_parent_unchecked(self):
        planner = ScriptedPlanner(parent_decomposition=decomposition())
        actor = ComponentActor({'square each value': SQUARE, 'sum values': TOTAL}, forbid_parent=True)
        solver = self.make_solver(planner, actor, max_calls=5)

        result = solver.solve('sum squares list')

        self.assertEqual(self.counter['used'], 5)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(len(result['subtasks']), 1)
        self.assertEqual(result['subtasks'][0]['status'], 'checked')
        self.assertNotIn('composition', result)

    def test_malformed_recipe_and_unknown_artifact_are_rejected_before_execution(self):
        cases = [
            ('malformed recipe', {'version': 1, 'output': {'op': 'python', 'source': 'pass'}},
             'malformed'),
            ('unknown artifact', {'version': 1, 'output': {
                'op': 'call', 'method': 'f' * 64, 'arg': {'op': 'input'}}}, 'unknown'),
        ]
        for label, recipe, _kind in cases:
            with self.subTest(label=label):
                planner = ScriptedPlanner(
                    parent_decomposition={'subtasks': [], 'recipe': recipe})
                actor = ComponentActor({}, unresolved_parent=True)
                solver = self.make_solver(planner, actor, max_calls=12)

                result = solver.solve('sum squares list')

                self.assertNotEqual(result['status'], 'checked')
                events = Journal.read(result['solve_journal'])
                self.assertFalse(any(event['kind'] == 'solve_execution_intent' for event in events))
                self.assertFalse(any(event['kind'] == 'solve_composition_recipe' for event in events))

    def test_reactive_split_uses_a_stored_recipe_after_empty_model_split(self):
        parent_request = 'sum squares list'
        planner = ScriptedPlanner(parent_request=parent_request)
        actor = ComponentActor({'square each value': SQUARE, 'sum values': TOTAL},
                               parent_request=parent_request, unresolved_parent=True)
        splitter = EmptySplitter()
        solver = self.make_solver(planner, actor, splitter=splitter, max_calls=20)
        prior = decomposition()
        self.workspace.remember_split(parent_request, prior['subtasks'], prior['recipe'])

        result = solver.solve(parent_request)

        self.assertEqual(result['status'], 'checked', result)
        self.assertIn('system (a breakdown that worked on a similar task) recipe', result['split_by'])
        self.assertEqual([piece['status'] for piece in result['subtasks']], ['checked', 'checked'])
        self.assertEqual(result['composition']['model_glue_calls'], 0)
        self.assertGreater(actor.parent_calls, 0)


if __name__ == '__main__':
    unittest.main()
