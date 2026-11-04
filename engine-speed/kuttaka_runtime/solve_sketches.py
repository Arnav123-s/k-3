"""Bounded AST-guided substitution for finite solve sketches.

Generated source remains untrusted and must be admitted and checked by the
existing sandbox path. This module parses and edits source text; it never runs it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

MAX_HOLES = 3
MAX_VALUES_PER_HOLE = 40
MAX_CANDIDATES_PER_PAGE = 256
MAX_SOURCE_BYTES = 64 * 1024
MAX_CANDIDATE_BYTES = 1024 * 1024
MAX_DATA_BYTES = 64 * 1024
MAX_AST_NODES = 10000
MAX_AST_DEPTH = 128
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10000
MAX_INTEGER_BITS = 8192
MAX_STRING_CHARS = 65536
_HOLE_NAME = re.compile(r'HOLE[0-9]+\Z')
_ALLOWED_HOLES = frozenset({'HOLE0', 'HOLE1', 'HOLE2'})


def _validate_json(value: Any, *, depth=0, budget=None) -> None:
    if budget is None:
        budget = [MAX_JSON_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_JSON_DEPTH:
        raise ValueError('hole value exceeds JSON structure limits')
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if value.bit_length() > MAX_INTEGER_BITS:
            raise ValueError('hole integer exceeds the bit limit')
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('hole values must be finite JSON data')
        return
    if type(value) is str:
        if len(value) > MAX_STRING_CHARS:
            raise ValueError('hole string exceeds the character limit')
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item, depth=depth + 1, budget=budget)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError('hole object keys must be strings')
            if len(key) > MAX_STRING_CHARS:
                raise ValueError('hole object key exceeds the character limit')
            _validate_json(item, depth=depth + 1, budget=budget)
        return
    raise ValueError('hole values must be finite JSON data')


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _copy_json(value: Any) -> Any:
    _validate_json(value)
    try:
        encoded = _canonical(value)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError('hole values must be finite JSON data') from error
    if len(encoded.encode('utf-8')) > MAX_DATA_BYTES:
        raise ValueError('hole value data exceeds the byte limit')
    return json.loads(encoded)


def _ast_depth(root: ast.AST) -> int:
    maximum = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        maximum = max(maximum, depth)
        if maximum > MAX_AST_DEPTH:
            raise ValueError('source AST exceeds the depth limit')
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return maximum


def _target_names(tree: ast.AST) -> set[str]:
    """Names occupying identifier/binding positions that are not ast.Name loads."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.update(part for part in node.name.split('.') if part)
            if node.asname:
                names.add(node.asname)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif hasattr(ast, 'TypeAlias') and isinstance(node, ast.TypeAlias):
            if isinstance(node.name, ast.Name):
                names.add(node.name.id)
    return names


def _literal_expression(value: Any) -> str:
    """Make a Python literal expression and verify its AST contains no executable nodes."""
    try:
        expression = ast.parse(repr(value), mode='eval')
    except (SyntaxError, ValueError, RecursionError) as error:
        raise ValueError('hole value cannot be represented as a Python literal') from error
    permitted = (ast.Expression, ast.Constant, ast.List, ast.Tuple, ast.Dict,
                 ast.UnaryOp, ast.USub, ast.UAdd, ast.Load)
    if any(not isinstance(node, permitted) for node in ast.walk(expression)):
        raise ValueError('hole value did not produce a literal expression')
    return ast.unparse(expression.body)


class SketchCatalogue:
    """A deterministic, lazy Cartesian catalogue of AST-declared hole values."""

    def __init__(self, source, holes):
        if not isinstance(source, str):
            raise ValueError('source must be a string')
        try:
            source_bytes = source.encode('utf-8')
        except UnicodeEncodeError as error:
            raise ValueError('source must be valid UTF-8 text') from error
        if len(source_bytes) > MAX_SOURCE_BYTES:
            raise ValueError('source exceeds the 64 KiB limit')
        if not isinstance(holes, Mapping):
            raise ValueError('holes must be a mapping')
        if not 1 <= len(holes) <= MAX_HOLES:
            raise ValueError(f'between 1 and {MAX_HOLES} holes are required')

        copied_holes = {}
        for name, values in holes.items():
            if name not in _ALLOWED_HOLES:
                raise ValueError('hole names must be exactly HOLE0, HOLE1, or HOLE2')
            if not isinstance(values, list) or not 1 <= len(values) <= MAX_VALUES_PER_HOLE:
                raise ValueError(f'{name} must have between 1 and {MAX_VALUES_PER_HOLE} values')
            unique, seen = [], set()
            for value in values:
                copied = _copy_json(value)
                key = _canonical(copied)
                if key not in seen:
                    seen.add(key)
                    unique.append(copied)
            copied_holes[name] = unique

        ordered_names = sorted(copied_holes)
        data = [value for name in ordered_names for value in copied_holes[name]]
        if len(_canonical(data).encode('utf-8')) > MAX_DATA_BYTES:
            raise ValueError('hole data exceeds the 64 KiB limit')

        try:
            tree = ast.parse(source, mode='exec')
        except (SyntaxError, ValueError, RecursionError) as error:
            raise ValueError('source must be valid Python syntax') from error
        nodes = list(ast.walk(tree))
        if len(nodes) > MAX_AST_NODES:
            raise ValueError('source AST exceeds the node limit')
        _ast_depth(tree)

        declared = set(ordered_names)
        targets = _target_names(tree)
        for name in targets:
            if _HOLE_NAME.fullmatch(name):
                raise ValueError(f'hole identifier {name} cannot be a binding target')

        uses = {name: 0 for name in ordered_names}
        for node in nodes:
            if not isinstance(node, ast.Name):
                continue
            if not _HOLE_NAME.fullmatch(node.id):
                continue
            if node.id not in declared:
                raise ValueError(f'undeclared hole identifier {node.id}')
            if not isinstance(node.ctx, ast.Load):
                raise ValueError(f'hole identifier {node.id} must be used only as a load')
            uses[node.id] += 1
        for name, count in uses.items():
            if count == 0:
                raise ValueError(f'declared hole {name} does not occur as an ast.Name load')

        self._source = source
        self._source_bytes = source_bytes
        self._tree = tree
        self._names = ordered_names
        self._holes = copied_holes
        self._total = 1
        for name in ordered_names:
            self._total *= len(copied_holes[name])
        self._identity = hashlib.sha256(_canonical({
            'source': source,
            'holes': copied_holes,
        }).encode('utf-8')).hexdigest()

        # Validate aggregate detached-output capacity at construction time.
        self._copy_candidate_values()

    def _copy_candidate_values(self):
        cloned = {name: json.loads(_canonical(values)) for name, values in self._holes.items()}
        if len(_canonical(cloned).encode('utf-8')) > MAX_DATA_BYTES:
            raise ValueError('hole data exceeds the 64 KiB limit')
        return cloned

    @property
    def total(self):
        return self._total

    @property
    def identity(self):
        return self._identity

    def candidates(self, start=0, limit=64):
        if type(start) is not int or not 0 <= start <= self._total:
            raise ValueError('start must be an integer cursor within the catalogue')
        if type(limit) is not int or not 0 <= limit <= MAX_CANDIDATES_PER_PAGE:
            raise ValueError(f'limit must be between 0 and {MAX_CANDIDATES_PER_PAGE}')
        stop = min(self._total, start + limit)
        line_starts = [0]
        line_starts.extend(match.end() for match in re.finditer(b'\r\n|\r|\n', self._source_bytes))
        holes_by_span = []
        for node in ast.walk(self._tree):
            if (isinstance(node, ast.Name) and node.id in self._holes
                    and isinstance(node.ctx, ast.Load)):
                line_offset = line_starts[node.lineno - 1]
                holes_by_span.append((line_offset + node.col_offset,
                                      line_offset + node.end_col_offset, node.id))
        holes_by_span.sort()

        for index in range(start, stop):
            values = {}
            for position, name in enumerate(self._names):
                stride = 1
                for later_name in self._names[position + 1:]:
                    stride *= len(self._holes[later_name])
                choice = (index // stride) % len(self._holes[name])
                values[name] = json.loads(_canonical(self._holes[name][choice]))
            try:
                expressions = {name: _literal_expression(values[name]).encode('utf-8')
                               for name in self._names}
            except (ValueError, SyntaxError, RecursionError, MemoryError) as error:
                yield {'index': index, 'status': 'incomplete',
                       'reason': str(error)[:256] or 'candidate literal representation failed',
                       'values': values}
                continue
            expressions = {name: b'(' + expression + b')'
                           for name, expression in expressions.items()}
            estimate = len(self._source_bytes) + sum(
                len(expressions[name]) - (right - left)
                for left, right, name in holes_by_span)
            if estimate > MAX_CANDIDATE_BYTES:
                yield {'index': index, 'status': 'incomplete',
                       'reason': 'generated candidate exceeds the 1 MiB limit',
                       'values': values}
                continue
            pieces, cursor = [], 0
            for left, right, name in holes_by_span:
                pieces.extend((self._source_bytes[cursor:left], expressions[name]))
                cursor = right
            pieces.append(self._source_bytes[cursor:])
            candidate = b''.join(pieces)
            try:
                candidate_source = candidate.decode('utf-8')
            except UnicodeDecodeError:
                yield {'index': index, 'status': 'incomplete',
                       'reason': 'generated candidate could not be decoded as UTF-8',
                       'values': values}
                continue
            try:
                ast.parse(candidate_source, mode='exec')
            except SyntaxError:
                yield {'index': index, 'status': 'invalid_syntax',
                       'reason': 'generated candidate is not valid Python syntax',
                       'source': candidate_source, 'values': values}
                continue
            except (ValueError, RecursionError, MemoryError) as error:
                yield {'index': index, 'status': 'incomplete',
                       'reason': str(error)[:256] or 'candidate parser resource limit',
                       'source': candidate_source, 'values': values}
                continue
            yield {'index': index, 'status': 'ready', 'source': candidate_source, 'values': values}
