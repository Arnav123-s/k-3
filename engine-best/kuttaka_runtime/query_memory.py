"""Finite task-relative query sufficiency and compiled memory views.

This module accepts declarative JSON tables only. It does not execute encoder,
decoder, query, or artifact source code. Results qualify only the finite domain,
query definitions, answers, and context included in a table's scope digest.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from types import MappingProxyType
from typing import Mapping


def _ensure_json(value: object) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('value must be finite JSON data')
        return
    if isinstance(value, list):
        for item in value:
            _ensure_json(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('JSON object keys must be strings')
        for item in value.values():
            _ensure_json(item)
        return
    raise ValueError('value must be finite JSON data')


def _canonical(value: object) -> str:
    _ensure_json(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('value must be finite JSON data') from error


def _copy(value: object):
    return json.loads(_canonical(value))


def _frozen_copy(value: object):
    """Private defensive JSON snapshot; returned public values are copied again."""
    return _copy(value)


def _fixed_bits(class_count: int) -> int:
    return (class_count - 1).bit_length()


def _scope_digest(states, queries, answers, context) -> str:
    body = {'states': list(states), 'queries': queries,
            'answers': answers, 'context': context}
    return hashlib.sha256(_canonical(body).encode('utf-8')).hexdigest()


def _canonical_key(value: object) -> str:
    return _canonical(value)


def _decode_canonical_key(key: object, what: str) -> str:
    if not isinstance(key, str):
        raise ValueError(f'{what} must be a canonical JSON key')
    try:
        if _canonical(json.loads(key)) != key:
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError(f'{what} must be a canonical JSON key') from None
    return key


class QueryTable:
    """Immutable snapshot of a finite state/query/answer table.

    ``states`` are unique string identifiers. ``queries`` maps identifiers to
    JSON descriptions of query semantics. ``answers`` maps each query ID to a
    partial state-ID→JSON-answer map; omitted cells remain explicitly unknown.
    """

    __slots__ = ('_states', '_queries', '_answers', '_context', '_scope_id')

    def __setattr__(self, name, value):
        if hasattr(self, name):
            raise AttributeError('QueryTable is immutable')
        object.__setattr__(self, name, value)

    def __init__(self, states: list[str], queries: Mapping[str, object],
                 answers: Mapping[str, Mapping[str, object]], *, context: object):
        if not isinstance(states, list) or not states:
            raise ValueError('states must be a nonempty list of string IDs')
        if any(not isinstance(state, str) or not state for state in states):
            raise ValueError('state IDs must be nonempty strings')
        if len(states) != len(set(states)):
            raise ValueError('duplicate state IDs')
        if not isinstance(queries, Mapping) or not queries:
            raise ValueError('queries must be a nonempty mapping')
        query_snapshot = {}
        for query_id, definition in queries.items():
            if not isinstance(query_id, str) or not query_id:
                raise ValueError('query IDs must be nonempty strings')
            query_snapshot[query_id] = _frozen_copy(definition)
        if not isinstance(answers, Mapping) or set(answers) - set(query_snapshot):
            raise ValueError('answers contain an unknown query ID')
        answer_snapshot = {}
        for query_id, cells in answers.items():
            if not isinstance(cells, Mapping):
                raise ValueError(f'answers for {query_id} must be a state mapping')
            if set(cells) - set(states):
                raise ValueError(f'answers for {query_id} contain an unknown state ID')
            answer_snapshot[query_id] = {state: _frozen_copy(value)
                                         for state, value in cells.items()}
        for query_id in query_snapshot:
            answer_snapshot.setdefault(query_id, {})

        self._states = tuple(states)
        self._queries = query_snapshot
        self._answers = answer_snapshot
        self._context = _frozen_copy(context)
        self._scope_id = _scope_digest(self._states, self._queries,
                                       self._answers, self._context)

    @property
    def scope_id(self) -> str:
        return self._scope_id

    @property
    def states(self) -> tuple[str, ...]:
        return self._states

    @property
    def queries(self) -> Mapping[str, object]:
        return MappingProxyType({key: _copy(value) for key, value in self._queries.items()})

    @property
    def answers(self) -> Mapping[str, Mapping[str, object]]:
        return MappingProxyType({q: MappingProxyType({s: _copy(v) for s, v in cells.items()})
                                 for q, cells in self._answers.items()})

    @property
    def context(self):
        return _copy(self._context)

    def _signature(self, state: str) -> tuple[str, ...]:
        return tuple(_canonical(self._answers[q][state]) for q in sorted(self._queries))

    def _check_view(self, state_to_code: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(state_to_code, Mapping):
            raise ValueError('state_to_code must map every state ID to a JSON code')
        if set(state_to_code) != set(self._states):
            missing = sorted(set(self._states) - set(state_to_code))
            unknown = sorted(set(state_to_code) - set(self._states))
            raise ValueError(f'encoding must be complete (missing={missing}, unknown={unknown})')
        return {state: _copy(state_to_code[state]) for state in self._states}

    def _missing(self) -> list[dict[str, str]]:
        return [{'query': query, 'state': state}
                for query in sorted(self._queries) for state in self._states
                if state not in self._answers[query]]

    def _signature_classes(self) -> dict[str, str] | None:
        if self._missing():
            return None
        signatures = {state: _canonical(list(self._signature(state))) for state in self._states}
        unique = sorted(set(signatures.values()))
        ids = {signature: f's{index}' for index, signature in enumerate(unique)}
        return {state: ids[signatures[state]] for state in self._states}

    def assess_view(self, state_to_code: Mapping[str, object]) -> dict:
        """Assess whether a candidate encoding preserves all known queries."""
        encodings = self._check_view(state_to_code)
        by_code = defaultdict(list)
        for state in self._states:
            by_code[_canonical_key(encodings[state])].append(state)

        collisions = []
        for members in by_code.values():
            for query in sorted(self._queries):
                first_by_answer = {}
                witness = None
                for state in members:
                    if state not in self._answers[query]:
                        continue
                    answer = self._answers[query][state]
                    answer_key = _canonical(answer)
                    if answer_key in first_by_answer:
                        continue
                    if first_by_answer:
                        other_key = next(iter(first_by_answer))
                        other_state, other_answer = first_by_answer[other_key]
                        witness = {'code': _copy(encodings[state]),
                                   'states': [other_state, state], 'query': query,
                                   'answers': [_copy(other_answer), _copy(answer)]}
                        break
                    first_by_answer[answer_key] = (state, answer)
                if witness is not None:
                    collisions.append(witness)
        missing = self._missing()
        signature_classes = self._signature_classes()
        status = 'incomplete' if missing else ('lossy' if collisions else 'exact')
        return {
            'scope_id': self.scope_id,
            'status': status,
            'code_class_count': len(by_code),
            'signature_class_count': (len(set(signature_classes.values()))
                                      if signature_classes is not None else None),
            'minimum_fixed_bits': (_fixed_bits(len(set(signature_classes.values())))
                                   if signature_classes is not None else None),
            'missing_cells': missing,
            'collision_witnesses': collisions,
            'domain_state_count': len(self._states),
            'query_count': len(self._queries),
            'scope_limit': 'finite declared states and query definitions only',
        }

    def signature_view(self) -> dict[str, str]:
        """Return minimum query-answer classes for a complete finite table."""
        classes = self._signature_classes()
        if classes is None:
            raise ValueError('signature view requires a complete answer table')
        return classes

    def refine_view(self, state_to_code: Mapping[str, object]) -> dict[str, str]:
        """Minimally refine each old code class by its complete answer signature."""
        encodings = self._check_view(state_to_code)
        if self._missing():
            raise ValueError('view refinement requires a complete answer table')
        pairs = {state: (_canonical_key(encodings[state]),
                         _canonical(list(self._signature(state)))) for state in self._states}
        pair_ids = {pair: f'r{index}' for index, pair in enumerate(sorted(set(pairs.values())))}
        return {state: pair_ids[pairs[state]] for state in self._states}

    def compile_view(self, encodings: Mapping[str, object]) -> 'QueryMemory':
        """Compile code→answer options, omitting source states and answer rows."""
        values = self._check_view(encodings)
        grouped = defaultdict(list)
        for state in self._states:
            grouped[_canonical_key(values[state])].append(state)
        code_options = {}
        for code_key, states in grouped.items():
            queries = {}
            for query in sorted(self._queries):
                found = {}
                missing = False
                for state in states:
                    if state not in self._answers[query]:
                        missing = True
                    else:
                        answer = self._answers[query][state]
                        found[_canonical(answer)] = _copy(answer)
                queries[query] = {'answers': [found[key] for key in sorted(found)],
                                  'missing': missing}
            code_options[code_key] = queries
        return QueryMemory(self.scope_id, self._queries, self._context, code_options)


class QueryMemory:
    """Compiled immutable finite decoder view without the source-state index.

    Supplied query definitions, codes, and answers are retained as given; these
    values may themselves include source identifiers or other task data.
    """

    _FORMAT = 'kuttaka-query-memory-v1'
    __slots__ = ('_serialized',)

    def __setattr__(self, name, value):
        if hasattr(self, name):
            raise AttributeError('QueryMemory is immutable')
        object.__setattr__(self, name, value)

    def __init__(self, scope_id: str, queries: Mapping[str, object], context: object,
                 code_options: Mapping[str, Mapping[str, Mapping[str, object]]]):
        data = self._validate_data({
            'format': self._FORMAT, 'scope_id': scope_id,
            'queries': queries, 'context': context, 'code_options': code_options})
        object.__setattr__(self, '_serialized', _canonical(data).encode('utf-8'))

    @classmethod
    def _validate_data(cls, data: object) -> dict:
        if not isinstance(data, dict) or set(data) != {
                'format', 'scope_id', 'queries', 'context', 'code_options'}:
            raise ValueError('invalid query-memory object shape')
        if data['format'] != cls._FORMAT:
            raise ValueError('unsupported query-memory format')
        scope_id = data['scope_id']
        if (not isinstance(scope_id, str) or len(scope_id) != 64
                or any(ch not in '0123456789abcdef' for ch in scope_id)):
            raise ValueError('invalid descriptive scope ID')
        queries = data['queries']
        if not isinstance(queries, dict) or not queries:
            raise ValueError('query-memory requires query definitions')
        if any(not isinstance(q, str) or not q for q in queries):
            raise ValueError('invalid query ID')
        queries = {q: _copy(definition) for q, definition in queries.items()}
        context = _copy(data['context'])
        options = data['code_options']
        if not isinstance(options, dict) or not options:
            raise ValueError('query-memory requires code options')
        checked_options = {}
        for code_key, by_query in options.items():
            _decode_canonical_key(code_key, 'code key')
            if not isinstance(by_query, dict) or set(by_query) != set(queries):
                raise ValueError('every code must include options for every query')
            checked_queries = {}
            for query, option in by_query.items():
                if not isinstance(option, dict) or set(option) != {'answers', 'missing'}:
                    raise ValueError('invalid answer option shape')
                answers, missing = option['answers'], option['missing']
                if not isinstance(answers, list) or type(missing) is not bool:
                    raise ValueError('answers must be a list and missing must be Boolean')
                keys = [_canonical(answer) for answer in answers]
                if len(set(keys)) != len(keys):
                    raise ValueError('duplicate answer option')
                if not answers and not missing:
                    raise ValueError('empty answers require an explicit missing flag')
                checked_queries[query] = {'answers': [_copy(x) for x in answers],
                                          'missing': missing}
            checked_options[code_key] = checked_queries
        return {'format': cls._FORMAT, 'scope_id': scope_id,
                'queries': queries, 'context': context, 'code_options': checked_options}

    @property
    def scope_id(self) -> str:
        return json.loads(self._serialized)['scope_id']

    def answer(self, code: object, query: str, *, context: object) -> dict:
        """Answer only when every source state represented by code agrees."""
        data = json.loads(self._serialized)
        if _canonical(context) != _canonical(data['context']):
            return {'status': 'context_mismatch', 'reason': 'context differs from compiled scope'}
        if not isinstance(query, str) or query not in data['queries']:
            return {'status': 'unknown', 'reason': 'query is outside compiled query family'}
        code_key = _canonical_key(code)
        by_query = data['code_options'].get(code_key)
        if by_query is None:
            return {'status': 'unknown', 'reason': 'code is outside compiled finite domain'}
        option = by_query[query]
        answers = option['answers']
        if len(answers) > 1:
            return {'status': 'ambiguous', 'alternatives': [_copy(value) for value in answers],
                    'additional_answers_possible': option['missing'],
                    'reason': 'states sharing this code have different known answers'}
        if option['missing'] or not answers:
            return {'status': 'unknown', 'reason': 'answer is missing for at least one represented state'}
        return {'status': 'answer', 'answer': _copy(answers[0])}

    def serialized(self) -> bytes:
        """Return canonical JSON bytes; scope_id is descriptive, not a MAC."""
        return self._serialized

    @classmethod
    def from_serialized(cls, payload: bytes) -> 'QueryMemory':
        if not isinstance(payload, bytes):
            raise ValueError('serialized query-memory must be bytes')
        try:
            def reject_duplicate_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('duplicate JSON object key')
                    result[key] = value
                return result
            data = json.loads(payload.decode('utf-8'), object_pairs_hook=reject_duplicate_pairs)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError('invalid serialized query-memory JSON') from error
        except ValueError as error:
            raise ValueError('invalid serialized query-memory JSON') from error
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: object) -> 'QueryMemory':
        """Internal validated construction used by the defensive parser."""
        checked = cls._validate_data(data)
        instance = cls.__new__(cls)
        object.__setattr__(instance, '_serialized', _canonical(checked).encode('utf-8'))
        return instance
