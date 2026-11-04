"""Finite query-sufficiency and compiled-memory controls."""

import json
import unittest

from kuttaka_runtime.query_memory import QueryMemory, QueryTable


class QueryMemoryTests(unittest.TestCase):
    def table(self):
        return QueryTable(
            ['s1', 's2', 's3'],
            {'parity': {'operation': 'mod', 'modulus': 2},
             'sign': {'operation': 'compare', 'value': 0}},
            {'parity': {'s1': 0, 's2': 0, 's3': 1},
             'sign': {'s1': -1, 's2': -1, 's3': 1}},
            context={'units': 'abstract'})

    def test_exact_view_and_minimal_signature_partition(self):
        table = self.table()
        view = {'s1': 'a', 's2': 'a', 's3': 'b'}
        assessment = table.assess_view(view)
        self.assertEqual(assessment['status'], 'exact')
        self.assertEqual(assessment['code_class_count'], 2)
        self.assertEqual(assessment['signature_class_count'], 2)
        self.assertEqual(assessment['minimum_fixed_bits'], 1)
        self.assertEqual(table.signature_view(), {'s1': 's0', 's2': 's0', 's3': 's1'})

    def test_collision_returns_concrete_counterexample(self):
        assessment = self.table().assess_view({'s1': 0, 's2': 1, 's3': 0})
        self.assertEqual(assessment['status'], 'lossy')
        self.assertEqual(assessment['collision_witnesses'], [
            {'code': 0, 'states': ['s1', 's3'], 'query': 'parity', 'answers': [0, 1]},
            {'code': 0, 'states': ['s1', 's3'], 'query': 'sign', 'answers': [-1, 1]},
        ])

    def test_incomplete_cells_are_unknown_and_never_certified(self):
        table = QueryTable(['a', 'b'], {'q': {'meaning': 'value'}},
                           {'q': {'a': 3}}, context={})
        assessment = table.assess_view({'a': 0, 'b': 0})
        self.assertEqual(assessment['status'], 'incomplete')
        self.assertIsNone(assessment['minimum_fixed_bits'])
        self.assertEqual(assessment['missing_cells'], [{'query': 'q', 'state': 'b'}])
        with self.assertRaises(ValueError):
            table.signature_view()
        memory = table.compile_view({'a': 0, 'b': 0})
        self.assertEqual(memory.answer(0, 'q', context={})['status'], 'unknown')

    def test_known_collision_is_reported_even_when_another_cell_is_missing(self):
        table = QueryTable(['a', 'b', 'c'], {'q': {'meaning': 'value'}},
                           {'q': {'a': 1, 'b': 2}}, context={})
        assessment = table.assess_view({'a': 'same', 'b': 'same', 'c': 'same'})
        self.assertEqual(assessment['status'], 'incomplete')
        self.assertEqual(assessment['collision_witnesses'][0]['states'], ['a', 'b'])
        answer = table.compile_view({'a': 0, 'b': 0, 'c': 0}).answer(0, 'q', context={})
        self.assertEqual(answer['status'], 'ambiguous')
        self.assertTrue(answer['additional_answers_possible'])

    def test_new_query_splits_old_partition_and_changes_scope(self):
        old = QueryTable(['a', 'b'], {'q1': {'meaning': 'first'}},
                         {'q1': {'a': 5, 'b': 5}}, context={'v': 1})
        view = {'a': 'same', 'b': 'same'}
        self.assertEqual(old.assess_view(view)['status'], 'exact')
        refined = old.refine_view(view)
        self.assertEqual(refined, {'a': 'r0', 'b': 'r0'})
        expanded = QueryTable(['a', 'b'],
                              {'q1': {'meaning': 'first'}, 'q2': {'meaning': 'new'}},
                              {'q1': {'a': 5, 'b': 5}, 'q2': {'a': 'x', 'b': 'y'}},
                              context={'v': 1})
        self.assertNotEqual(old.scope_id, expanded.scope_id)
        self.assertEqual(expanded.assess_view(view)['status'], 'lossy')
        self.assertEqual(expanded.refine_view(view), {'a': 'r0', 'b': 'r1'})

    def test_compiled_memory_refuses_wrong_context_unknown_and_ambiguous(self):
        table = self.table()
        memory = table.compile_view({'s1': 'same', 's2': 'same', 's3': 'other'})
        self.assertEqual(memory.answer('same', 'parity', context={'units': 'abstract'}),
                         {'status': 'answer', 'answer': 0})
        self.assertEqual(memory.answer('same', 'sign', context={'units': 'abstract'}),
                         {'status': 'answer', 'answer': -1})
        self.assertEqual(memory.answer('other', 'parity', context={'units': 'abstract'}),
                         {'status': 'answer', 'answer': 1})
        self.assertEqual(memory.answer('same', 'parity', context={'units': 'meters'})['status'],
                         'context_mismatch')
        self.assertEqual(memory.answer('same', 'missing', context={'units': 'abstract'})['status'],
                         'unknown')
        self.assertEqual(memory.answer('unseen', 'parity', context={'units': 'abstract'})['status'],
                         'unknown')
        colliding = table.compile_view({'s1': 'same', 's2': 'other', 's3': 'same'})
        self.assertEqual(colliding.answer('same', 'parity', context={'units': 'abstract'})['status'],
                         'ambiguous')

    def test_round_trip_is_defensive_and_contains_no_source_state_ids(self):
        states = ['secret-state-a', 'secret-state-b']
        query_def = {'purpose': ['read', 'x']}
        source_answers = {'q': {'secret-state-a': {'v': 1}, 'secret-state-b': {'v': 1}}}
        context = {'scale': 2}
        table = QueryTable(states, {'q': query_def}, source_answers, context=context)
        memory = table.compile_view({'secret-state-a': {'code': True},
                                     'secret-state-b': {'code': True}})
        query_def['purpose'].append('mutated')
        source_answers['q']['secret-state-a']['v'] = 900
        context['scale'] = -1
        states.append('mutated-state')
        payload = memory.serialized()
        text = payload.decode()
        self.assertNotIn('secret-state-a', text)
        self.assertNotIn('secret-state-b', text)
        restored = QueryMemory.from_serialized(payload)
        self.assertEqual(restored.scope_id, table.scope_id)
        self.assertEqual(restored.answer({'code': True}, 'q', context={'scale': 2}),
                         {'status': 'answer', 'answer': {'v': 1}})
        returned = restored.answer({'code': True}, 'q', context={'scale': 2})
        returned['answer']['v'] = 1234
        self.assertEqual(restored.answer({'code': True}, 'q', context={'scale': 2})['answer'],
                         {'v': 1})
        self.assertEqual(restored.serialized(), payload)
        with self.assertRaises(AttributeError):
            restored._serialized = b'changed'

    def test_boolean_and_integer_codes_are_distinct_json_values(self):
        table = QueryTable(['a', 'b'], {'q': {'meaning': 'identity'}},
                           {'q': {'a': 'yes', 'b': 'no'}}, context={})
        assessment = table.assess_view({'a': True, 'b': 1})
        self.assertEqual(assessment['status'], 'exact')
        self.assertEqual(assessment['code_class_count'], 2)
        memory = table.compile_view({'a': True, 'b': 1})
        self.assertEqual(memory.answer(True, 'q', context={})['answer'], 'yes')
        self.assertEqual(memory.answer(1, 'q', context={})['answer'], 'no')

    def test_assessment_is_explicitly_finite_scope_and_rejects_bad_ids(self):
        table = self.table()
        self.assertIn('finite declared states', table.assess_view(
            {'s1': 0, 's2': 0, 's3': 1})['scope_limit'])
        with self.assertRaises(ValueError):
            table.assess_view({'s1': 0, 's2': 0, 'not-a-state': 1})
        with self.assertRaises(ValueError):
            QueryTable(['a', 'a'], {'q': {}}, {'q': {}}, context={})
        with self.assertRaises(ValueError):
            QueryTable(['a'], {'q': {}}, {'other': {'a': 1}}, context={})
        with self.assertRaises(ValueError):
            QueryTable(['a'], {'q': {}}, {'q': {'a': {1: 'non-string key'}}}, context={})

    def test_serialized_payload_is_structurally_validated_not_authenticated(self):
        memory = self.table().compile_view({'s1': 0, 's2': 0, 's3': 1})
        data = json.loads(memory.serialized())
        data['context'] = {'units': 'tampered'}
        restored = QueryMemory.from_serialized(json.dumps(data).encode())
        # Scope ID is descriptive only. Changed content is accepted structurally,
        # but the original context no longer authorizes a read.
        self.assertEqual(restored.scope_id, memory.scope_id)
        self.assertEqual(restored.answer(0, 'parity', context={'units': 'abstract'})['status'],
                         'context_mismatch')

    def test_parser_rejects_duplicate_keys_empty_known_options_and_nonfinite_answers(self):
        memory = self.table().compile_view({'s1': 0, 's2': 0, 's3': 1})
        duplicated = memory.serialized().decode().replace('"context":', '"context":{},"context":', 1)
        with self.assertRaises(ValueError):
            QueryMemory.from_serialized(duplicated.encode())
        for answers in ([], [float('nan')]):
            data = json.loads(memory.serialized())
            data['code_options']['0']['parity'] = {'answers': answers, 'missing': False}
            with self.assertRaises(ValueError):
                QueryMemory.from_serialized(json.dumps(data).encode())


if __name__ == '__main__':
    unittest.main()
