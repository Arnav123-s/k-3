"""Failures found by the independent review of the composition merge (391278e); each reproduced first.

Scripted models and the local executor only; no model service or evaluated task is used.
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace, same_value, settings, solve_task
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor


def check(i, e):
    return {'input': i, 'expected': e}


def store(source, uses=None):
    body = {'name': 'm', 'description': 'm', 'source': source}
    if uses:
        body['uses'] = uses
    return {'kind': 'experiment', 'tool': 'remember_method', 'input': body,
            'predictions': [{'hypothesis': 'stored', 'outcome': 'artifact id'}]}


class Model:
    """One scripted model for every role, routed by the context keys each role receives."""
    identity = {'model': 'scripted-review'}
    last_usage = {'input_tokens': 1, 'output_tokens': 1}

    def __init__(self, plan, act, split=None, audit=None):
        self.plan, self.act, self.split, self.audit = plan, act, split, audit
        self.log = []

    def propose(self, context):
        if 'request' in context and 'history' in context:
            self.log.append(('actor', context['request'][:60]))
            history = context['history']
            if history and 'artifact' in history[-1].get('observation', {}):
                return {'kind': 'answer', 'answer': {'artifact': history[-1]['observation']['artifact']}}
            if history:
                return {'kind': 'unresolved', 'reason': 'scripted stop'}
            return self.act(context['request'])
        if 'failure' in context:
            self.log.append(('splitter', context['task'][:60]))
            return self.split(context) if self.split else {'subtasks': []}
        if 'inputs' in context:
            return self.audit(context) if self.audit else {}
        self.log.append(('planner', context['task'][:60]))
        return self.plan(context)


def make_solver(model, ws=None, max_calls=40, max_executions=256):
    ws = ws or Workspace(Path(tempfile.mkdtemp()))
    m = Metered(model, {'used': 0, 'limit': max_calls})
    return Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                  max_executions=max_executions, say=lambda _: None), ws


def events(result, kind):
    return [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == kind]


class ReviewTwoTests(unittest.TestCase):
    def test_a_refuted_method_is_never_delivered_even_when_the_new_check_is_rejected(self):
        def plan(context):
            return {'contract': 'run(n) returns "a" repeated n times', 'input': None,
                    'checks': [check(0, ''), check(1, 'a'), check(3, 'aaa')],
                    'reference': 'def reference(n):\n    return "a" * n\n',
                    'small_inputs': [6, 70000]}   # 70000 letters exceed the check contract's limit
        solver, ws = make_solver(Model(plan, lambda r: store('def run(n):\n    return "a" * min(n, 5)\n'),
                                       audit=lambda c: {'expected': ['a' * n for n in c['inputs']]}))
        result = solver.solve('Letters: run(n) returns a string of n letters a.')
        self.assertNotEqual(result['status'], 'checked')
        self.assertTrue(ws.refuted())
        self.assertFalse(ws._read(ws.solved_path))
        # Since checks are admitted one by one (third review, N1), the counterexample that fits (6 letters) is
        # added although the 70000-letter one is rejected, so the refuted method fails it before the gate.
        self.assertIn(check(6, 'aaaaaa'), result['checks'])

    def test_the_gate_refuses_a_refuted_method_whose_only_counterexample_cannot_be_held(self):
        def plan(context):   # the fourth review's scenario: the only counterexample exceeds the check contract
            return {'contract': 'run(n) returns "a" repeated n times', 'input': None,
                    'checks': [check(0, ''), check(1, 'a'), check(3, 'aaa')],
                    'reference': 'def reference(n):\n    return "a" * n\n', 'small_inputs': [70000]}
        solver, ws = make_solver(Model(plan, lambda r: store('def run(n):\n    return "a" * min(n, 1000)\n'),
                                       audit=lambda c: {'expected': ['a' * n for n in c['inputs']]}))
        result = solver.solve('Letters: run(n) returns a string of n letters a.')
        self.assertNotEqual(result['status'], 'checked')
        self.assertTrue(ws.refuted())
        self.assertTrue(events(result, 'solve_delivery_refused'))

    def test_a_memorizing_piece_is_refuted_and_never_reused(self):
        parent, piece = 'Triple a number.', 'Triple a number exactly.'

        def plan(context):
            if context['task'] == parent:
                return {'contract': 'run(x) returns 3*x', 'input': None, 'checks': [check(5, 15), check(7, 22)],
                        'decomposition': {'subtasks': [{'id': 'triple', 'request': piece,
                                                        'checks': [check(2, 6), check(3, 9)]}],
                                          'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'triple',
                                                                               'arg': {'op': 'input'}}}}}
            if context['task'] == piece:
                return {'contract': 'run(x) returns 3*x', 'input': None, 'checks': [check(2, 6), check(3, 9)]}
            return {'contract': 'run(x) returns 3*x', 'input': 7, 'checks': [check(4, 12), check(10, 30)]}

        def act(request):
            if request.startswith(piece):
                return store('def run(x):\n    if x == 7:\n        return 22\n    return 3 * x\n')
            return {'kind': 'unresolved', 'reason': 'not attempted directly'}
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(parent)
        pieces = [e['artifact'] for e in events(first, 'solve_decomposition_piece') if e['status'] == 'checked']
        self.assertTrue(pieces and set(pieces) <= ws.refuted())
        later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve('Triple a number exactly, for any integer.')
        self.assertNotIn(later.get('artifact'), pieces)
        self.assertNotEqual(later.get('answer'), 22)

    def test_a_recipe_that_answers_the_checks_from_its_literals_is_rejected(self):
        from kuttaka_runtime.check_memorization import memorized_in_recipe
        checks = [check(5, 15), check(7, 21), check(11, 33)]
        recipe = {'version': 1, 'output': {'op': 'call', 'method': 'lookup', 'arg': {'op': 'array', 'items': [
            {'op': 'input'}, {'op': 'literal', 'value': [[c['input'], c['expected']] for c in checks]}]}}}
        self.assertEqual(memorized_in_recipe(recipe, checks), checks)
        honest = {'version': 1, 'output': {'op': 'call', 'method': 'scale', 'arg': {'op': 'array', 'items': [
            {'op': 'input'}, {'op': 'literal', 'value': 3}]}}}
        self.assertEqual(memorized_in_recipe(honest, checks), [])

    def test_very_large_integers_compare_without_overflow(self):
        self.assertTrue(same_value(10**400, 10**400))
        self.assertFalse(same_value(10**400 + 1, 10**400))
        self.assertFalse(same_value(1e300, 10**400))
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments' / 'solve_suite'))
        import run
        self.assertTrue(run.same(10**400 + 1, 10**400))   # the pre-registered 1e-9 relative tolerance
        self.assertFalse(run.same(10**12 + 2000, 10**12))

    def test_a_splitter_recipe_with_a_bad_reference_still_solves_its_pieces(self):
        parent = 'Sum the squares of a list.'
        subtasks = [{'id': 'square', 'request': 'Square a number.', 'checks': [check(3, 9)]},
                    {'id': 'total', 'request': 'Add up a list.', 'checks': [check([1, 2], 3)]}]
        bad = {'version': 1, 'output': {'op': 'call', 'method': 'e' * 64, 'arg': {
            'op': 'array', 'items': [{'op': 'call', 'method': 'total', 'arg': {
                'op': 'map', 'method': 'square', 'arg': {'op': 'input'}}}]}}}

        def plan(context):
            task = context['task']
            if task == parent:
                return {'contract': 'run(xs) returns the sum of squares', 'input': None,
                        'checks': [check([1, 2], 5), check([3], 9)]}
            if task.startswith('Square'):
                return {'contract': 'run(x) returns x*x', 'input': None, 'checks': [check(3, 9)]}
            return {'contract': 'run(xs) returns sum(xs)', 'input': None, 'checks': [check([1, 2], 3)]}

        def act(request):
            if request.startswith('Square'):
                return store('def run(x):\n    return x * x\n')
            if request.startswith('Add'):
                return store('def run(xs):\n    return sum(xs)\n')
            ids = re.findall(r'^([0-9a-f]{64}): (Square|Add)', request, re.M)
            if len(ids) >= 2:
                sq = next(i for i, t in ids if t == 'Square')
                tot = next(i for i, t in ids if t == 'Add')
                return store('def run(xs):\n    return use(%r)([use(%r)(x) for x in xs])\n' % (tot, sq), [sq, tot])
            return {'kind': 'unresolved', 'reason': 'too hard in one step'}
        solver, _ = make_solver(Model(plan, act, split=lambda c: {'subtasks': subtasks, 'recipe': bad}), max_calls=14)
        result = solver.solve(parent)
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(len(result['subtasks']), 2)

    def test_an_exhausted_budget_is_not_reported_or_learned_as_a_wrong_method(self):
        class Planner:
            identity, last_usage, n = {'model': 'p'}, {}, 0

            def propose(self, context):
                Planner.n += 1
                checks = [check(2, 5), check(3, 9)] if Planner.n == 1 else [check(2, 4), check(3, 9)]
                return {'contract': 'run(x) returns x*x', 'input': 12, 'checks': checks}
        actor = Model(None, lambda request: store('def run(x):\n    return x * x\n'))
        ws = Workspace(Path(tempfile.mkdtemp()))
        result = solve_task('Square a number.', settings=settings(rounds=2, max_calls=3, max_executions=6),
                            workspace=ws, models=(Planner(), actor, actor, actor, actor), executor=local_executor)
        self.assertNotEqual(result['reason'], 'the checked method failed on the requested input')
        self.assertFalse(any('failed on the requested input' in l['mistake'] for l in ws._read(ws.lessons_path)))

    def test_labels_are_cleared_when_nothing_is_delivered(self):
        def plan(context):
            return {'contract': 'run(x) returns x*x', 'input': 4, 'checks': [check(2, 4), check(3, 9)],
                    'verify': 'def verify(x, y):\n    return y == x * x + 1\n'}   # disagrees with every output
        solver, _ = make_solver(Model(plan, lambda r: store('def run(x):\n    return x * x\n'),
                                      audit=lambda c: {'expected': [0 for _ in c['inputs']]}))
        result = solver.solve('Square a number.')
        self.assertNotEqual(result['status'], 'checked')
        self.assertIsNone(result['delivered_by'])
        self.assertNotIn('composition', result)


if __name__ == '__main__':
    unittest.main()
