import itertools
import unittest
from kuttaka_runtime.factored_memory import compile_factored, evaluate
from kuttaka_runtime.planning_memory import compile_model, plan


def fixture(noise=0):
    names = ['key', 'door'] + [f'n{i}' for i in range(noise)]
    return {'variables': names, 'initial': {v: False for v in names}, 'goal': 'door',
        'actions': {
            'collect': {'guard': True, 'cost': 1, 'assign': {'key': True}},
            'open': {'guard': 'key', 'cost': 2, 'assign': {'door': 'key'}},
            'noise': {'guard': True, 'cost': 0, 'assign': {v: ['not', v] for v in names if v.startswith('n')}}}}


class FactoredMemoryTests(unittest.TestCase):
    def test_thousand_dimensions_without_enumerating_them(self):
        record, start, audit = compile_factored(fixture(998), context='door', complete=True)
        self.assertEqual(audit['source_variables'], 1000)
        self.assertEqual(audit['enumerated_projected_states'], 4)
        self.assertEqual(audit['retained_variables'], ['door', 'key'])
        self.assertEqual(plan(record, start, context='door')['actions'], ['collect', 'open'])
        self.assertEqual(plan(record, start, context='door')['cost'], 3)

    def test_against_fully_enumerated_oracle_for_all_initial_states(self):
        spec = fixture(3)
        concrete = {}
        names = spec['variables']
        for bits in itertools.product((False, True), repeat=len(names)):
            state = dict(zip(names, bits))
            edges = {}
            for a, row in spec['actions'].items():
                if evaluate(row['guard'], state):
                    after = {v: evaluate(row['assign'].get(v, v), state) for v in names}
                    edges[a] = {'to': str(tuple(after[v] for v in names)), 'cost': row['cost']}
            concrete[str(bits)] = {'observation': {'goal': state['door']}, 'transitions': edges}
        raw, encoder, _ = compile_model(concrete, context=None, complete=True)
        for bits in itertools.product((False, True), repeat=len(names)):
            spec['initial'] = dict(zip(names, bits))
            projected, start, _ = compile_factored(spec, context=None, complete=True)
            self.assertEqual(plan(projected, start, context=None)['cost'],
                             plan(raw, encoder[str(bits)], context=None)['cost'])

    def test_guard_dependency_must_not_be_discarded(self):
        spec = fixture(1)
        spec['actions']['open']['guard'] = ['and', 'key', 'n0']
        _, _, audit = compile_factored(spec, context=None, complete=True)
        self.assertIn('n0', audit['retained_variables'])

    def test_transitive_assignment_dependency_is_retained(self):
        spec = fixture(2)
        spec['actions']['collect']['assign']['key'] = 'n0'
        spec['actions']['noise']['assign']['n0'] = 'n1'
        _, _, audit = compile_factored(spec, context=None, complete=True)
        self.assertEqual(len(audit['retained_variables']), 4)
        with self.assertRaisesRegex(ValueError, 'ceiling'):
            compile_factored(spec, context=None, complete=True, max_relevant=2)

    def test_false_initial_and_synchronous_updates(self):
        spec = fixture()
        spec['actions'] = {'swap': {'guard': True, 'cost': 1, 'assign': {'key': 'door', 'door': 'key'}}}
        spec['initial']['key'] = True
        record, start, _ = compile_factored(spec, context=None, complete=True)
        self.assertEqual(plan(record, start, context=None)['cost'], 1)
