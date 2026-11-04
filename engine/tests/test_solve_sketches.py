import ast
import unittest
from unittest.mock import patch

from kuttaka_runtime.solve_sketches import SketchCatalogue


class SketchCatalogueTests(unittest.TestCase):
    def test_literal_strings_comments_and_other_identifiers_are_unchanged(self):
        source = '# HOLE0 comment\ntext = "HOLE0"\nx_HOLE10 = HOLE0\n'
        candidate = next(SketchCatalogue(source, {'HOLE0': ['value']}).candidates())
        self.assertIn('# HOLE0 comment', candidate['source'])
        self.assertIn('text = "HOLE0"', candidate['source'])
        self.assertIn('x_HOLE10 =', candidate['source'])
        self.assertTrue(candidate['source'].endswith("x_HOLE10 = ('value')\n"))

    def test_exact_multi_digit_hole_name_is_not_treated_as_shorter_hole(self):
        with self.assertRaisesRegex(ValueError, 'undeclared hole identifier HOLE10'):
            SketchCatalogue('result = HOLE10 + HOLE1', {'HOLE1': [3]})
        source = 'result = myHOLE10 + HOLE1\n'
        candidate = next(SketchCatalogue(source, {'HOLE1': [3]}).candidates())
        self.assertEqual(candidate['source'], 'result = myHOLE10 + (3)\n')

    def test_scalar_list_and_dict_values_become_literal_expressions(self):
        source = 'result = [HOLE0, HOLE1, HOLE2]\n'
        candidate = next(SketchCatalogue(source, {
            'HOLE0': [False], 'HOLE1': [[1, 'a']], 'HOLE2': [{'k': 2}],
        }).candidates())
        assignment = ast.parse(candidate['source']).body[0]
        values = [ast.literal_eval(node) for node in assignment.value.elts]
        self.assertEqual(values, [False, [1, 'a'], {'k': 2}])
        self.assertIs(values[0], False)
        self.assertEqual(candidate['values'], {
            'HOLE0': False, 'HOLE1': [1, 'a'], 'HOLE2': {'k': 2}})

    def test_cursor_pages_repeat_deterministically_in_mixed_radix_order(self):
        book = SketchCatalogue('x = (HOLE0, HOLE1)\n', {
            'HOLE1': ['a', 'b', 'c'], 'HOLE0': [10, 20],
        })
        all_rows = list(book.candidates(limit=20))
        self.assertEqual(book.total, 6)
        self.assertEqual([r['index'] for r in all_rows], list(range(6)))
        self.assertEqual(list(book.candidates(start=2, limit=3)), all_rows[2:5])
        self.assertEqual(list(book.candidates(start=2, limit=3)), all_rows[2:5])
        self.assertEqual(all_rows[0]['values'], {'HOLE0': 10, 'HOLE1': 'a'})
        self.assertEqual(all_rows[1]['values'], {'HOLE0': 10, 'HOLE1': 'b'})

    def test_candidate_page_limit_and_generated_source_are_bounded(self):
        book = SketchCatalogue('x = HOLE0\n', {'HOLE0': list(range(40))})
        self.assertEqual(len(list(book.candidates(limit=256))), 40)
        cartesian = SketchCatalogue('x = (HOLE0, HOLE1, HOLE2)\n', {
            'HOLE0': list(range(20)), 'HOLE1': list(range(20)), 'HOLE2': list(range(20)),
        })
        self.assertGreater(cartesian.total, 256)
        self.assertEqual(len(list(cartesian.candidates(limit=256))), 256)
        with self.assertRaisesRegex(ValueError, 'limit must be between'):
            list(book.candidates(limit=257))
        many_holes = 'x = (' + ' + '.join(['HOLE0'] * 22) + ')\n'
        large = SketchCatalogue(many_holes, {'HOLE0': ['x' * 50000, 'ok']})
        bounded = list(large.candidates())
        self.assertEqual([row['index'] for row in bounded], [0, 1])
        self.assertEqual([row['status'] for row in bounded], ['incomplete', 'ready'])
        self.assertIn('exceeds the 1 MiB limit', bounded[0]['reason'])
        self.assertNotIn('source', bounded[0])
        self.assertIn("('ok')", bounded[1]['source'])

    def test_invalid_fstring_filling_is_reported_and_next_cursor_continues(self):
        source = 'def run():\n    return f"{HOLE0}"\n'
        book = SketchCatalogue(source, {'HOLE0': ['first', 'second']})
        # Inject a malformed first literal expression to exercise final candidate
        # parsing on every Python version, including versions with PEP 701 f-strings.
        with patch('kuttaka_runtime.solve_sketches._literal_expression',
                   side_effect=[')', "'second'"]):
            rows = list(book.candidates())
        self.assertEqual([row['index'] for row in rows], [0, 1])
        self.assertEqual([row['status'] for row in rows], ['invalid_syntax', 'ready'])
        self.assertIn('not valid Python syntax', rows[0]['reason'])
        self.assertIn('f"{', rows[0]['source'])
        self.assertIn("('second')", rows[1]['source'])

    def test_literal_representation_failure_is_incomplete_per_index(self):
        book = SketchCatalogue('x = HOLE0\n', {'HOLE0': [1, 2]})
        with patch('kuttaka_runtime.solve_sketches._literal_expression',
                   side_effect=[ValueError('test representation failure'), '2']):
            rows = list(book.candidates())
        self.assertEqual([row['index'] for row in rows], [0, 1])
        self.assertEqual([row['status'] for row in rows], ['incomplete', 'ready'])
        self.assertEqual(rows[0]['reason'], 'test representation failure')
        self.assertNotIn('source', rows[0])
        self.assertEqual(rows[1]['source'], 'x = (2)\n')

    def test_candidate_parser_resource_errors_are_incomplete_and_continue(self):
        for failure in (RecursionError('parser recursion limit'), MemoryError('parser memory limit')):
            with self.subTest(failure=type(failure).__name__):
                book = SketchCatalogue('x = HOLE0\n', {'HOLE0': [1, 2]})
                original_parse = ast.parse

                def parse(source, mode='exec', **kwargs):
                    if mode == 'exec' and source == 'x = (1)\n':
                        raise failure
                    return original_parse(source, mode=mode, **kwargs)

                with patch('kuttaka_runtime.solve_sketches.ast.parse', side_effect=parse):
                    rows = list(book.candidates())
                self.assertEqual([row['index'] for row in rows], [0, 1])
                self.assertEqual([row['status'] for row in rows], ['incomplete', 'ready'])
                self.assertEqual(rows[0]['reason'], str(failure))
                self.assertEqual(rows[0]['source'], 'x = (1)\n')
                self.assertEqual(rows[1]['source'], 'x = (2)\n')

    def test_undeclared_and_write_context_holes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'undeclared hole identifier'):
            SketchCatalogue('x = HOLE1\n', {'HOLE0': [1]})
        for source in (
            'HOLE0 = 1\n',
            'def run(HOLE0):\n    return HOLE0\n',
            'def HOLE0():\n    return 1\n',
            'import math as HOLE0\n',
        ):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'binding target|load'):
                SketchCatalogue(source, {'HOLE0': [1]})

    def test_values_dedupe_by_typed_canonical_form(self):
        book = SketchCatalogue('x = HOLE0\n', {'HOLE0': [False, False, 0]})
        rows = list(book.candidates())
        self.assertEqual(book.total, 2)
        self.assertIs(rows[0]['values']['HOLE0'], False)
        self.assertEqual(rows[1]['values']['HOLE0'], 0)
        self.assertIs(type(rows[1]['values']['HOLE0']), int)

    def test_source_is_only_parsed_and_never_executed(self):
        source = "def run():\n    raise RuntimeError(HOLE0)\n"
        book = SketchCatalogue(source, {'HOLE0': ['candidate']})
        candidate = next(book.candidates())
        self.assertIn("raise RuntimeError(('candidate'))", candidate['source'])

    def test_negative_literals_are_parenthesized_to_preserve_precedence(self):
        candidate = next(SketchCatalogue(
            'def run():\n    return HOLE0 ** 2\n', {'HOLE0': [-2]}).candidates())
        expression = ast.parse(candidate['source']).body[0].body[0].value
        self.assertIsInstance(expression, ast.BinOp)
        self.assertIsInstance(expression.left, ast.UnaryOp)
        self.assertIsInstance(expression.op, ast.Pow)
        self.assertIn('(-2) ** 2', candidate['source'])

    def test_parenthesized_literals_work_in_attribute_and_subscript_positions(self):
        source = 'first = HOLE0.value\nsecond = HOLE1[0]\n'
        candidate = next(SketchCatalogue(source, {
            'HOLE0': ['obj'], 'HOLE1': [[5]],
        }).candidates())
        parsed = ast.parse(candidate['source'])
        self.assertIsInstance(parsed.body[0].value, ast.Attribute)
        self.assertIsInstance(parsed.body[0].value.value, ast.Constant)
        self.assertIsInstance(parsed.body[1].value, ast.Subscript)
        self.assertEqual(parsed.body[1].value.value.elts[0].value, 5)

    def test_unicode_before_hole_uses_ast_byte_offsets_correctly(self):
        source = 'label = "λ"\nresult = HOLE0\n'
        candidate = next(SketchCatalogue(source, {'HOLE0': [7]}).candidates())
        self.assertEqual(candidate['source'], 'label = "λ"\nresult = (7)\n')

    def test_fstring_interaction_is_parsed_before_candidate_is_returned(self):
        source = 'def run():\n    return f"{HOLE0}"\n'
        book = SketchCatalogue(source, {'HOLE0': ["a'b"]})
        try:
            candidate = next(book.candidates())
        except ValueError as error:
            self.assertIn('generated candidate', str(error))
        else:
            ast.parse(candidate['source'])

    def test_catalogue_requires_at_least_one_hole(self):
        with self.assertRaisesRegex(ValueError, 'between 1 and 3 holes'):
            SketchCatalogue('x = 1\n', {})

    def test_inputs_and_candidate_values_are_detached(self):
        values = [{'x': [1]}]
        book = SketchCatalogue('x = HOLE0\n', {'HOLE0': values})
        values[0]['x'][0] = 99
        candidate = next(book.candidates())
        candidate['values']['HOLE0']['x'][0] = 88
        self.assertEqual(next(book.candidates())['values']['HOLE0'], {'x': [1]})

    def test_declared_holes_must_be_exact_allowed_names_and_used(self):
        with self.assertRaisesRegex(ValueError, 'exactly HOLE0'):
            SketchCatalogue('x = HOLE10\n', {'HOLE10': [1]})
        with self.assertRaisesRegex(ValueError, 'does not occur'):
            SketchCatalogue('x = 1\n', {'HOLE0': [1]})

    def test_cursor_validation_and_source_data_limits(self):
        book = SketchCatalogue('x = HOLE0\n', {'HOLE0': [1]})
        for start, limit in ((-1, 1), (2, 1), (True, 1), (0, True)):
            with self.subTest(start=start, limit=limit), self.assertRaises(ValueError):
                list(book.candidates(start=start, limit=limit))
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            SketchCatalogue('x = 1\n' + '# padding\n' * 10000, {})


if __name__ == '__main__':
    unittest.main()
