"""Compile bounded JSON composition recipes into ordinary stored methods.

Recipes are data, never Python source. The compiler emits a small fixed Python
wrapper that invokes stored methods by artifact ID; the resulting method still
needs the normal, current-task ProgramTools qualification before use.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable


COMPILER_VERSION = 'kuttaka-composition-recipe-v1'
MAX_RECIPE_BYTES = 64 * 1024
MAX_NODES = 128
MAX_DEPTH = 16
MAX_METHODS = 16
MAX_CALLS = 4096
MAX_MAP_ITEMS = 4096
MAX_JSON_NODES = 8192
MAX_JSON_DEPTH = 48
MAX_JSON_STRING_CHARS = 64 * 1024
MAX_JSON_INT_BITS = 8192
_ARTIFACT_ID = re.compile(r'^[0-9a-f]{64}$')
_SYMBOL = re.compile(r'^[a-z][a-z0-9_]{0,31}$')


@dataclass(frozen=True)
class CompiledComposition:
    """Deterministic executable wrapper plus the exact recipe that produced it."""

    source: str
    uses: tuple[str, ...]
    _manifest_json: str
    identity: str

    @property
    def manifest(self) -> dict:
        """Return a detached JSON manifest; caller mutation cannot alter this result."""
        return json.loads(self._manifest_json)


def _strict_json_copy(value):
    """Bound and copy finite JSON, rejecting cycles and Python-only values."""
    state = {'nodes': 0, 'active': set(), 'string_chars': 0}

    def copy(item, depth):
        state['nodes'] += 1
        if state['nodes'] > MAX_JSON_NODES:
            raise ValueError('recipe JSON exceeds 8192 values')
        if depth > MAX_JSON_DEPTH:
            raise ValueError('recipe JSON exceeds nesting depth 48')
        if item is None or type(item) is bool:
            return item
        if type(item) is int:
            if item.bit_length() > MAX_JSON_INT_BITS:
                raise ValueError('recipe integer exceeds 8192 bits')
            return item
        if type(item) is str:
            state['string_chars'] += len(item)
            if state['string_chars'] > MAX_JSON_STRING_CHARS:
                raise ValueError('recipe JSON strings exceed 64 KiB')
            return item
        if type(item) is float:
            if item != item or item in (float('inf'), float('-inf')):
                raise ValueError('recipe values must be finite JSON data')
            return item
        if type(item) in (list, dict):
            object_id = id(item)
            if object_id in state['active']:
                raise ValueError('recipe JSON must not contain cycles')
            state['active'].add(object_id)
            try:
                if type(item) is list:
                    return [copy(child, depth + 1) for child in item]
                copied = {}
                for key, child in item.items():
                    if type(key) is not str:
                        raise ValueError('recipe object keys must be strings')
                    copied[copy(key, depth + 1)] = copy(child, depth + 1)
                return copied
            finally:
                state['active'].remove(object_id)
        raise ValueError('recipe values must be finite JSON data')

    return copy(value, 0)


def _encoded_size(value) -> int:
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False,
                         separators=(',', ':')).encode('ascii')
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError('recipe must be finite JSON data') from error
    if len(raw) > MAX_RECIPE_BYTES:
        raise ValueError('recipe exceeds 64 KiB')
    return len(raw)


def _parse(recipe, *, bindings=None):
    recipe = _strict_json_copy(recipe)
    _encoded_size(recipe)
    if type(recipe) is not dict or set(recipe) != {'version', 'output'}:
        raise ValueError('recipe must contain exactly version and output')
    if type(recipe['version']) is not int or recipe['version'] != 1:
        raise ValueError('unsupported recipe version')
    if bindings is None:
        bindings = {}
    if type(bindings) is not dict:
        raise ValueError('bindings must be an object')
    binding_copy = _strict_json_copy(bindings)
    for symbol, artifact in binding_copy.items():
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise ValueError('binding names must be lowercase symbolic identifiers')
        if not isinstance(artifact, str) or not _ARTIFACT_ID.fullmatch(artifact):
            raise ValueError('bindings must map symbols to artifact IDs')

    methods = []
    node_count = 0

    def bind(reference):
        if not isinstance(reference, str):
            raise ValueError('method reference must be a symbol or artifact ID')
        if _ARTIFACT_ID.fullmatch(reference):
            artifact = reference
        elif _SYMBOL.fullmatch(reference):
            if reference not in binding_copy:
                raise ValueError(f'unbound method symbol: {reference}')
            artifact = binding_copy[reference]
        else:
            raise ValueError('method reference must be a lowercase symbol or artifact ID')
        if artifact not in methods:
            methods.append(artifact)
        if len(methods) > MAX_METHODS:
            raise ValueError('recipe references more than 16 methods')
        return artifact

    def expr(node, depth):
        nonlocal node_count
        node_count += 1
        if node_count > MAX_NODES:
            raise ValueError('recipe exceeds 128 expression nodes')
        if depth > MAX_DEPTH:
            raise ValueError('recipe exceeds expression depth 16')
        if type(node) is not dict or not isinstance(node.get('op'), str):
            raise ValueError('expression must be an operation object')
        op = node['op']
        if op == 'input':
            if set(node) != {'op'}:
                raise ValueError('input expression accepts no fields')
            return {'op': op}
        if op == 'literal':
            if set(node) != {'op', 'value'}:
                raise ValueError('literal expression requires only value')
            return {'op': op, 'value': _strict_json_copy(node['value'])}
        if op == 'call' or op == 'map':
            if set(node) != {'op', 'method', 'arg'}:
                raise ValueError(f'{op} expression requires method and arg')
            artifact = bind(node['method'])
            return {'op': op, 'method': artifact, 'arg': expr(node['arg'], depth + 1)}
        if op == 'array':
            if set(node) != {'op', 'items'} or type(node['items']) is not list:
                raise ValueError('array expression requires an items list')
            return {'op': op, 'items': [expr(item, depth + 1) for item in node['items']]}
        if op == 'object':
            if set(node) != {'op', 'fields'} or type(node['fields']) is not dict:
                raise ValueError('object expression requires a fields object')
            fields = {}
            for key, value in node['fields'].items():
                if type(key) is not str:
                    raise ValueError('object field names must be strings')
                fields[key] = expr(value, depth + 1)
            return {'op': op, 'fields': fields}
        if op == 'get':
            if set(node) != {'op', 'value', 'key'}:
                raise ValueError('get expression requires value and key')
            key = node['key']
            if not (type(key) is str or (type(key) is int and key >= 0)):
                raise ValueError('get key must be a string or nonnegative integer')
            return {'op': op, 'value': expr(node['value'], depth + 1), 'key': key}
        raise ValueError(f'unsupported recipe operation: {op}')

    output = expr(recipe['output'], 1)
    bound_recipe = {'version': 1, 'output': output}
    _encoded_size(bound_recipe)
    return bound_recipe, methods, node_count


def inspect_recipe(recipe) -> dict:
    """Validate a recipe and return its ordered, distinct references and node count.

    Symbolic references are reported as symbols here; compilation resolves them
    through explicit bindings and returns the corresponding artifact IDs.
    """
    copied = _strict_json_copy(recipe)
    _encoded_size(copied)
    if type(copied) is not dict or set(copied) != {'version', 'output'}:
        raise ValueError('recipe must contain exactly version and output')
    if type(copied['version']) is not int or copied['version'] != 1:
        raise ValueError('unsupported recipe version')
    refs = []

    def collect(node):
        if type(node) is not dict:
            return
        op = node.get('op')
        if op in ('call', 'map') and isinstance(node.get('method'), str):
            if node['method'] not in refs:
                refs.append(node['method'])
            collect(node.get('arg'))
        elif op == 'array' and type(node.get('items')) is list:
            for item in node['items']:
                collect(item)
        elif op == 'object' and type(node.get('fields')) is dict:
            for item in node['fields'].values():
                collect(item)
        elif op == 'get':
            collect(node.get('value'))

    collect(copied['output'])
    if len(refs) > MAX_METHODS:
        raise ValueError('recipe references more than 16 methods')
    bindings = {ref: '0' * 64 for ref in refs if _SYMBOL.fullmatch(ref)}
    _, _, node_count = _parse(copied, bindings=bindings)
    return {'methods': refs, 'node_count': node_count}


def compile_recipe(recipe, *, bindings=None, resolve_method: Callable[[str], object]):
    """Compile a validated recipe into deterministic Python source.

    `resolve_method(id)` must return a stored method payload with a Python body.
    The method bodies are checked as data only and are never executed here.
    """
    if not callable(resolve_method):
        raise ValueError('resolve_method must be callable')
    bound_recipe, methods, node_count = _parse(recipe, bindings=bindings)
    # ArtifactMemory/ProgramTools store declarations as a sorted set. Execution
    # order is carried by the recipe/source, not by this dependency declaration.
    methods.sort()
    for artifact in methods:
        payload = resolve_method(artifact)
        if (type(payload) is not dict or payload.get('kind') != 'method'
                or type(payload.get('body')) is not dict
                or payload['body'].get('language') != 'python'
                or not isinstance(payload['body'].get('source'), str)
                or not payload['body']['source'].strip()):
            raise ValueError(f'{artifact} is not a stored Python method')

    def py_literal(value):
        # `value` was recursively restricted to JSON data. Python repr keeps
        # strings, dict insertion order, booleans, and null as safe literals.
        if type(value) is list:
            return '[' + ', '.join(py_literal(item) for item in value) + ']'
        if type(value) is dict:
            return '{' + ', '.join(py_literal(k) + ': ' + py_literal(v)
                                   for k, v in value.items()) + '}'
        return repr(value)

    def emit(node):
        op = node['op']
        if op == 'input':
            return 'value'
        if op == 'literal':
            return py_literal(node['value'])
        if op == 'call':
            return f"_invoke({node['method']!r}, {emit(node['arg'])})"
        if op == 'map':
            return f"_map({node['method']!r}, {emit(node['arg'])})"
        if op == 'array':
            return '[' + ', '.join(emit(item) for item in node['items']) + ']'
        if op == 'object':
            return '{' + ', '.join(py_literal(key) + ': ' + emit(item)
                                   for key, item in node['fields'].items()) + '}'
        if op == 'get':
            key = node['key']
            if type(key) is int:
                return f'_get_index({emit(node["value"])}, {key})'
            return f'_get_key({emit(node["value"])}, {key!r})'
        raise AssertionError('validated operation missing emitter')

    source = (
        f'_CALL_LIMIT = {MAX_CALLS}\n'
        f'_MAP_LIMIT = {MAX_MAP_ITEMS}\n'
        'def run(value):\n'
        '    calls = [0]\n'
        '    def _invoke(artifact, argument):\n'
        '        calls[0] += 1\n'
        '        if calls[0] > _CALL_LIMIT:\n'
        '            raise ValueError("composition call limit exceeded")\n'
        '        return use(artifact)(argument)\n'
        '    def _map(artifact, items):\n'
        '        if type(items) is not list:\n'
        '            raise TypeError("composition map requires a list")\n'
        '        if len(items) > _MAP_LIMIT:\n'
        '            raise ValueError("composition map limit exceeded")\n'
        '        return [_invoke(artifact, item) for item in items]\n'
        '    def _get_key(container, key):\n'
        '        if type(container) is not dict:\n'
        '            raise TypeError("composition key lookup requires an object")\n'
        '        return container[key]\n'
        '    def _get_index(container, index):\n'
        '        if type(container) is not list:\n'
        '            raise TypeError("composition index lookup requires an array")\n'
        '        return container[index]\n'
        f'    return {emit(bound_recipe["output"])}\n'
    )
    # Keep recipe JSON as an ordered string. Artifact payload canonicalization
    # sorts object keys recursively; that would otherwise change observable field
    # evaluation order if the recipe were later reconstructed from a payload.
    bound_recipe_json = json.dumps(bound_recipe, ensure_ascii=True, allow_nan=False,
                                   separators=(',', ':'))
    manifest = {'compiler': COMPILER_VERSION, 'call_limit': MAX_CALLS,
                'map_limit': MAX_MAP_ITEMS, 'recipe_json': bound_recipe_json}
    identity_input = {'source': source, 'manifest': manifest, 'uses': methods}
    identity_bytes = json.dumps(identity_input, ensure_ascii=True, allow_nan=False,
                                separators=(',', ':')).encode('ascii')
    identity = hashlib.sha256(identity_bytes).hexdigest()
    manifest_json = json.dumps(manifest, ensure_ascii=True, allow_nan=False,
                               separators=(',', ':'))
    return CompiledComposition(source=source, uses=tuple(methods),
                               _manifest_json=manifest_json, identity=identity)
