import copy
import itertools
import random
import unittest
from kuttaka_runtime.factored_memory import compile_factored, evaluate
from kuttaka_runtime.planning_memory import plan
from tests.test_runtime_factored_memory import fixture


class GoalMemoryTests(unittest.TestCase):
    def test_irrelevant_guard_explodes_behavior_but_not_goal(self):
        spec = fixture(20)
        expr = True
        for i in range(20):
            expr = ['and', expr, f'n{i}']
        spec['actions']['noise']['guard'] = expr
        with self.assertRaisesRegex(ValueError, 'ceiling'):
            compile_factored(spec, context=None, complete=True)
        memory, start, audit = compile_factored(spec, context=None, complete=True, preserve='goal_cost')
        self.assertEqual(audit['retained_variables'], ['door', 'key'])
        self.assertEqual(audit['removed_actions'], ['noise'])
        self.assertEqual(plan(memory, start, context=None)['cost'], 3)
        self.assertEqual(memory['preservation'], 'minimum_goal_cost')

    def test_guard_enabler_is_pulled_back_in(self):
        spec = fixture(2)
        spec['actions']['open']['guard'] = ['and', 'key', 'n0']
        _, _, audit = compile_factored(spec, context=None, complete=True, preserve='goal_cost')
        self.assertIn('noise', audit['retained_actions'])
        self.assertIn('n0', audit['retained_variables'])

    def test_goal_change_reinstates_dropped_dimensions(self):
        spec = fixture(1)
        _, _, old = compile_factored(spec, context='door', complete=True, preserve='goal_cost')
        spec['goal'] = 'n0'
        memory, start, new = compile_factored(spec, context='noise', complete=True, preserve='goal_cost')
        self.assertIn('noise', old['removed_actions'])
        self.assertIn('noise', new['retained_actions'])
        self.assertEqual(plan(memory, start, context='noise')['actions'], ['noise'])

    def test_negative_even_discardable_action_is_rejected(self):
        spec = fixture(1)
        spec['actions']['noise']['cost'] = -1
        with self.assertRaisesRegex(ValueError, 'cost'):
            compile_factored(spec, context=None, complete=True, preserve='goal_cost')

    def test_random_models_against_independent_concrete_value_iteration(self):
        rng = random.Random(29131)
        names = ['a', 'b', 'c', 'd']
        states = list(itertools.product((False, True), repeat=4))
        def expr():
            a, b = rng.choice(names), rng.choice(names)
            return rng.choice([True, False, a, ['not', a], ['and', a, b], ['xor', a, b]])
        for _ in range(80):
            spec = {'variables': names, 'initial': dict.fromkeys(names, False), 'goal': expr(), 'actions': {}}
            for i in range(4):
                spec['actions'][str(i)] = {'guard': expr(), 'cost': rng.randrange(4),
                    'assign': {v: expr() for v in rng.sample(names, rng.randrange(5))}}
            edges, goals = {}, {}
            for bits in states:
                state = dict(zip(names, bits))
                goals[bits] = evaluate(spec['goal'], state)
                edges[bits] = []
                for action, row in spec['actions'].items():
                    if evaluate(row['guard'], state):
                        after = tuple(evaluate(row['assign'].get(v, v), state) for v in names)
                        edges[bits].append((after, row['cost']))
            distance = {s: 0 if goals[s] else float('inf') for s in states}
            for _ in states:
                distance = {s: min([distance[s]] + [cost + distance[t] for t, cost in edges[s]]) for s in states}
            for bits in states:
                spec['initial'] = dict(zip(names, bits))
                memory, start, _ = compile_factored(spec, context=None, complete=True, preserve='goal_cost')
                result = plan(memory, start, context=None)
                if distance[bits] == float('inf'):
                    self.assertEqual(result['status'], 'no_path')
                else:
                    self.assertEqual(result['cost'], distance[bits])
                    state, total = copy.deepcopy(spec['initial']), 0
                    for action in result['actions']:
                        row = spec['actions'][action]
                        self.assertTrue(evaluate(row['guard'], state))
                        state = {v: evaluate(row['assign'].get(v, v), state) for v in names}
                        total += row['cost']
                    self.assertTrue(evaluate(spec['goal'], state))
                    self.assertEqual(total, result['cost'])
