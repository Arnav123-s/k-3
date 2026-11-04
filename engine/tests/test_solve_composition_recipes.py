"""Bounded declarative composition compiler tests."""

import json
import subprocess
import sys
import unittest

from kuttaka_runtime.composition import (
    MAX_CALLS,
    MAX_DEPTH,
    MAX_METHODS,
    MAX_NODES,
    compile_recipe,
    inspect_recipe,
)
from kuttaka_runtime.execution import RUNNER


def run_recipe(compiled, value, libraries):
    job = {'source': compiled.source, 'input': value, 'libraries': libraries}
    return subprocess.run([sys.executable, '-I', '-c', RUNNER],
                          input=json.dumps(job).encode('utf-8'),
                          capture_output=True, timeout=5, check=False)


def method(source):
    return {'kind': 'method', 'body': {'language': 'python', 'source': source}}


class CompositionRecipeTests(unittest.TestCase):
    def test_map_then_call_runs_in_order_and_records_exact_ids(self):
        square, add_one = 'a' * 64, 'b' * 64
        recipe = {'version': 1, 'output': {
            'op': 'call', 'method': 'add_one',
            'arg': {'op': 'map', 'method': 'square', 'arg': {'op': 'input'}}}}
        bindings = {'square': square, 'add_one': add_one}
        compiled = compile_recipe(recipe, bindings=bindings, resolve_method=lambda _: method('def run(x): return x'))
        self.assertEqual(compiled.uses, (square, add_one))  # canonical declaration, not call order
        self.assertEqual(inspect_recipe(recipe), {'methods': ['add_one', 'square'], 'node_count': 3})
        proc = run_recipe(compiled, [2, 3], {
            square: 'def run(x): return x * x\n',
            add_one: 'def run(xs): return [x + 1 for x in xs]\n',
        })
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout), {
            'result': [5, 10], 'dependency_calls': [square, square, add_one]})

    def test_get_and_object_preserve_field_evaluation_order(self):
        first, second = '1' * 64, '2' * 64
        recipe = {'version': 1, 'output': {'op': 'object', 'fields': {
            'first': {'op': 'call', 'method': first, 'arg': {'op': 'input'}},
            'second': {'op': 'get', 'key': 'answer', 'value': {
                'op': 'call', 'method': second, 'arg': {'op': 'input'}}},
        }}}
        compiled = compile_recipe(recipe, resolve_method=lambda _: method('def run(x): return x'))
        proc = run_recipe(compiled, {'answer': 8}, {
            first: 'def run(x): return 1\n',
            second: 'def run(x): return x\n',
        })
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout), {
            'result': {'first': 1, 'second': 8}, 'dependency_calls': [first, second]})

    def test_recipe_round_trip_is_stable_and_order_is_identity_bound(self):
        artifact = 'c' * 64
        recipe = {'version': 1, 'output': {'op': 'object', 'fields': {
            'left': {'op': 'call', 'method': artifact, 'arg': {'op': 'input'}},
            'right': {'op': 'literal', 'value': 4},
        }}}
        resolver = lambda _: method('def run(x): return x')
        original = compile_recipe(recipe, resolve_method=resolver)
        persisted_manifest = json.loads(json.dumps(original.manifest, sort_keys=True))
        rebuilt = compile_recipe(json.loads(persisted_manifest['recipe_json']),
                                 resolve_method=resolver)
        self.assertEqual(original.source, rebuilt.source)
        self.assertEqual(original.uses, rebuilt.uses)
        self.assertEqual(original.identity, rebuilt.identity)
        exposed_manifest = original.manifest
        exposed_manifest['recipe_json'] = '{}'
        self.assertEqual(original.manifest['recipe_json'], persisted_manifest['recipe_json'])
        reversed_recipe = {'version': 1, 'output': {'op': 'object', 'fields': {
            'right': {'op': 'literal', 'value': 4},
            'left': {'op': 'call', 'method': artifact, 'arg': {'op': 'input'}},
        }}}
        self.assertNotEqual(original.identity,
                            compile_recipe(reversed_recipe, resolve_method=resolver).identity)

    def test_malicious_literal_is_data_and_unknown_operations_are_rejected(self):
        marker = "__import__('os').system('false')"
        compiled = compile_recipe({'version': 1, 'output': {
            'op': 'literal', 'value': marker}}, resolve_method=lambda _: None)
        proc = run_recipe(compiled, None, {})
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout)['result'], marker)
        with self.assertRaisesRegex(ValueError, 'unsupported recipe operation'):
            compile_recipe({'version': 1, 'output': {'op': 'python', 'source': 'pass'}},
                           resolve_method=lambda _: None)
        with self.assertRaisesRegex(ValueError, 'requires method and arg'):
            compile_recipe({'version': 1, 'output': {
                'op': 'call', 'method': 'a' * 64, 'arg': {'op': 'input'}, 'source': 'x'}},
                resolve_method=lambda _: method('def run(x): return x'))

    def test_binding_is_explicit_and_resolved_payload_must_be_python_method(self):
        recipe = {'version': 1, 'output': {
            'op': 'call', 'method': 'square', 'arg': {'op': 'input'}}}
        with self.assertRaisesRegex(ValueError, 'unbound method symbol'):
            compile_recipe(recipe, resolve_method=lambda _: method('def run(x): return x'))
        with self.assertRaisesRegex(ValueError, 'not a stored Python method'):
            compile_recipe(recipe, bindings={'square': 'd' * 64}, resolve_method=lambda _: {
                'kind': 'method', 'body': {'language': 'javascript', 'source': 'x'}})
        with self.assertRaisesRegex(ValueError, 'not a stored Python method'):
            compile_recipe({'version': 1, 'output': {'op': 'call', 'method': 'd' * 64,
                                                       'arg': {'op': 'input'}}},
                           resolve_method=lambda _: {'kind': 'question', 'body': {}})

    def test_incompatible_map_and_get_interfaces_fail_in_sandbox(self):
        artifact = 'e' * 64
        recipe = {'version': 1, 'output': {
            'op': 'map', 'method': artifact, 'arg': {'op': 'input'}}}
        compiled = compile_recipe(recipe, resolve_method=lambda _: method('def run(x): return x'))
        proc = run_recipe(compiled, 7, {artifact: 'def run(x): return x\n'})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b'map requires a list', proc.stderr)
        get_recipe = {'version': 1, 'output': {
            'op': 'get', 'key': 0, 'value': {'op': 'input'}}}
        get_compiled = compile_recipe(get_recipe, resolve_method=lambda _: None)
        proc = run_recipe(get_compiled, {'zero': 0}, {})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b'index lookup requires an array', proc.stderr)

    def test_total_call_budget_covers_multiple_map_nodes(self):
        artifact = 'f' * 64
        inner_map = {'op': 'map', 'method': artifact, 'arg': {'op': 'input'}}
        recipe = {'version': 1, 'output': {
            'op': 'map', 'method': artifact, 'arg': inner_map}}
        compiled = compile_recipe(recipe, resolve_method=lambda _: method('def run(x): return x'))
        values = list(range(MAX_CALLS // 2 + 1))
        proc = run_recipe(compiled, values, {artifact: 'def run(x): return x\n'})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b'call limit exceeded', proc.stderr)

    def test_node_depth_method_and_recipe_size_limits(self):
        artifact_ids = [f'{i:064x}' for i in range(MAX_METHODS + 1)]
        too_many_methods = {'version': 1, 'output': {'op': 'array', 'items': [
            {'op': 'call', 'method': artifact, 'arg': {'op': 'input'}}
            for artifact in artifact_ids]}}
        with self.assertRaisesRegex(ValueError, 'more than 16 methods'):
            compile_recipe(too_many_methods, resolve_method=lambda _: method('def run(x): return x'))

        deep = {'op': 'input'}
        for _ in range(MAX_DEPTH):
            deep = {'op': 'get', 'key': 0, 'value': deep}
        with self.assertRaisesRegex(ValueError, 'depth 16'):
            inspect_recipe({'version': 1, 'output': deep})

        many_nodes = {'op': 'array', 'items': [{'op': 'input'} for _ in range(MAX_NODES)]}
        with self.assertRaisesRegex(ValueError, '128 expression nodes'):
            inspect_recipe({'version': 1, 'output': many_nodes})

        many_refs = {'version': 1, 'output': {'op': 'array', 'items': [
            {'op': 'call', 'method': f'method_{i}', 'arg': {'op': 'input'}}
            for i in range(MAX_METHODS + 1)]}}
        with self.assertRaisesRegex(ValueError, 'more than 16 methods'):
            inspect_recipe(many_refs)

        large = {'version': 1, 'output': {'op': 'literal', 'value': 'x' * (64 * 1024)}}
        with self.assertRaisesRegex(ValueError, '64 KiB'):
            inspect_recipe(large)

    def test_strict_json_and_get_key_validation(self):
        with self.assertRaisesRegex(ValueError, 'finite JSON'):
            inspect_recipe({'version': 1, 'output': {'op': 'literal', 'value': float('nan')}})
        with self.assertRaisesRegex(ValueError, 'string or nonnegative integer'):
            inspect_recipe({'version': 1, 'output': {'op': 'get', 'key': True,
                                                      'value': {'op': 'input'}}})
        with self.assertRaisesRegex(ValueError, 'only value'):
            inspect_recipe({'version': 1, 'output': {'op': 'literal', 'value': 1,
                                                      'extra': 'ignored'}})

    def test_python_cycles_and_pathological_literal_nesting_are_bounded(self):
        cyclic = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(ValueError, 'must not contain cycles'):
            inspect_recipe({'version': 1, 'output': {'op': 'literal', 'value': cyclic}})

        deep_value = None
        for _ in range(64):
            deep_value = [deep_value]
        with self.assertRaisesRegex(ValueError, 'nesting depth 48'):
            inspect_recipe({'version': 1, 'output': {'op': 'literal', 'value': deep_value}})

        huge_integer = 1 << 8193
        with self.assertRaisesRegex(ValueError, '8192 bits'):
            inspect_recipe({'version': 1, 'output': {'op': 'literal', 'value': huge_integer}})


if __name__ == '__main__':
    unittest.main()
