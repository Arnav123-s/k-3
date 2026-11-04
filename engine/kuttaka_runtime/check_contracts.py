"""Bounded, provenance-aware finite check contracts for ``kuttaka solve``.

This module manages visible check data only. It does not establish that an
expected value is correct; revisions are recorded as heuristics with evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

MAX_CHECKS = 64
MAX_MODEL_CHECKS = 12
MAX_VALUE_BYTES = 256 * 1024
MAX_VALUE_DEPTH = 32
MAX_VALUE_NODES = 10000
MAX_INT_BITS = 8192
MAX_STRING_CHARS = 65536
MAX_PROPOSAL_ITEMS = 4096
MAX_TEXT_CHARS = 16384
MAX_REVISIONS = 128
REVISION_SUPPORT = frozenset({'heuristic_not_verified', 'host_oracle_supported', 'two_derivations_agree'})


class CheckConflict(ValueError):
    """The requested finite check contract contains incompatible expectations."""


def _validate_json(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [MAX_VALUE_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_VALUE_DEPTH:
        raise ValueError('JSON value exceeds the check contract limits')
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if value.bit_length() > MAX_INT_BITS:
            raise ValueError('integer exceeds the check contract bit limit')
        return
    if type(value) is str:
        if len(value) > MAX_STRING_CHARS:
            raise ValueError('string exceeds the check contract character limit')
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('check values must be finite JSON data')
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item, depth=depth + 1, budget=budget)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError('JSON object keys must be strings')
            if len(key) > MAX_STRING_CHARS:
                raise ValueError('JSON object key exceeds the character limit')
            _validate_json(item, depth=depth + 1, budget=budget)
        return
    raise ValueError('check values must be finite JSON data')


def _copy(value: Any) -> Any:
    _validate_json(value)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError('check values must be finite JSON data') from error
    if len(encoded.encode('utf-8')) > MAX_VALUE_BYTES:
        raise ValueError('JSON value exceeds the check contract byte limit')
    return json.loads(encoded)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    if len(value) > MAX_TEXT_CHARS:
        raise ValueError(f'{name} exceeds the character limit')
    return value


def _check_list(value: Any, name: str, *, limit: int) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(f'{name} must be a list')
    if len(value) > limit:
        raise ValueError(f'{name} must contain at most {limit} checks')
    copied = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {'input', 'expected'}:
            raise ValueError(f'{name}[{index}] must contain exactly input and expected')
        copied.append({'input': _copy(item['input']), 'expected': _copy(item['expected'])})
    if len(_canonical(copied).encode('utf-8')) > MAX_VALUE_BYTES:
        raise ValueError(f'{name} exceeds the check contract byte limit')
    return copied


class CheckBook:
    """Immutable-input check contract with explicit provenance and revision history.

    Public properties return fresh JSON copies. ``revise`` mutates only a model
    check and records the old/new check IDs, scopes and supplied evidence.
    """

    def __init__(self, request, contract, supplied, proposed, *, supplied_origin='user', model_limit=12):
        self._request = _text(request, 'request')
        self._contract = _text(contract, 'contract')
        if supplied_origin not in ('user', 'model'):
            raise ValueError("supplied_origin must be 'user' or 'model'")
        if type(model_limit) is not int or not 0 <= model_limit <= MAX_MODEL_CHECKS:
            raise ValueError(f'model_limit must be between 0 and {MAX_MODEL_CHECKS}')
        self._model_limit = model_limit
        anchors = _check_list(supplied, 'supplied', limit=MAX_CHECKS)
        proposals = _check_list(proposed, 'proposed', limit=MAX_PROPOSAL_ITEMS)
        self._supplied_count = len(anchors)

        self._conflicts = []
        self._revisions = []
        self._records = []
        active_by_input = {}

        def add_anchor(check, origin):
            input_key = _canonical(check['input'])
            expected_key = _canonical(check['expected'])
            prior = active_by_input.get(input_key)
            if prior is not None:
                if prior['expected_key'] != expected_key:
                    raise CheckConflict(f'conflicting {origin} expectations for the same input')
                return
            record = self._record(check, origin, 'supplied')
            self._records.append(record)
            active_by_input[input_key] = {'record': record, 'expected_key': expected_key}

        for anchor in anchors:
            add_anchor(anchor, supplied_origin)

        # Validate all model proposals against one another before applying the
        # admission limit. A late contradictory proposal must not be hidden by it.
        proposal_by_input = {}
        unique_proposals = []
        for proposal in proposals:
            input_key = _canonical(proposal['input'])
            expected_key = _canonical(proposal['expected'])
            prior_expected = proposal_by_input.get(input_key)
            if prior_expected is not None and prior_expected != expected_key:
                raise CheckConflict('conflicting model expectations for the same input')
            if prior_expected is None:
                proposal_by_input[input_key] = expected_key
                unique_proposals.append(proposal)

        self.__proposal_checks = _copy(unique_proposals)

        admitted = 0
        omitted = 0
        for proposal in unique_proposals:
            input_key = _canonical(proposal['input'])
            expected_key = _canonical(proposal['expected'])
            existing = active_by_input.get(input_key)
            if existing is not None:
                if existing['expected_key'] == expected_key:
                    continue
                if existing['record']['origin'] == 'user':
                    self._conflicts.append({
                        'kind': 'user_anchor_rejected_model_proposal',
                        'input': _copy(proposal['input']),
                        'anchor_check_id': existing['record']['id'],
                        'rejected_expected': _copy(proposal['expected']),
                        'source': 'model_proposal',
                    })
                    continue
                raise CheckConflict('conflicting model expectations for the same input')
            if admitted >= model_limit:
                omitted += 1
                continue
            if len(self._records) >= MAX_CHECKS:
                omitted += 1
                continue
            record = self._record(proposal, 'model', 'model_proposal')
            self._records.append(record)
            active_by_input[input_key] = {'record': record, 'expected_key': expected_key}
            admitted += 1

        self._omitted_model_checks = omitted
        if not self._records:
            raise ValueError('check contract must contain at least one active check')
        # Validate aggregate bundles now so construction cannot succeed only for
        # detached public properties to fail later.
        _copy(self._records)
        _copy(self._conflicts)
        _copy(self._revisions)

    @staticmethod
    def _record(check, origin, source):
        item = {'input': _copy(check['input']), 'expected': _copy(check['expected']),
                'origin': origin, 'source': source}
        item['id'] = _sha(item)
        return item

    @property
    def contract(self):
        return self._contract

    @property
    def checks(self):
        return [{'input': _copy(row['input']), 'expected': _copy(row['expected'])}
                for row in self._records]

    @property
    def checks_by(self):
        origins = {row['origin'] for row in self._records}
        if origins == {'user'}:
            return 'user'
        if origins == {'model'}:
            return 'model'
        return 'user and model'

    @property
    def scope_id(self):
        return self._scope_id_for(self._records)

    @property
    def records(self):
        return _copy(self._records)

    @property
    def conflicts(self):
        return _copy(self._conflicts)

    @property
    def revisions(self):
        return _copy(self._revisions)

    @property
    def metadata(self):
        """Admission accounting for caller diagnostics and journaling."""
        return _copy({'supplied_count': self._supplied_count,
                      'model_limit': self._model_limit,
                      'admitted_model_checks': sum(row['origin'] == 'model' for row in self._records),
                      'omitted_model_checks': self._omitted_model_checks,
                      'unique_model_proposal_inputs': len(self.__proposal_checks),
                      'conflict_count': len(self._conflicts)})

    def revise(self, input, new_expected, *, evidence, support='heuristic_not_verified'):
        """Revise one model check; support labels never claim verification."""
        return self._revise(input, new_expected, evidence=evidence,
                            support=support)

    def _revise(self, input, new_expected, *, evidence, support):
        if (not isinstance(support, str) or len(support) > 64
                or support not in REVISION_SUPPORT):
            raise ValueError('unsupported revision support label')
        copied_input = _copy(input)
        copied_expected = _copy(new_expected)
        copied_evidence = _copy(evidence)
        input_key = _canonical(copied_input)
        match = next((row for row in self._records if _canonical(row['input']) == input_key), None)
        if match is None:
            return False
        if match['origin'] == 'user':
            raise CheckConflict('user-origin checks cannot be revised')
        if _canonical(match['expected']) == _canonical(copied_expected):
            return False
        before_scope = self.scope_id
        before = _copy(match)
        after = self._record({'input': match['input'], 'expected': copied_expected}, 'model', match['source'])
        index = self._records.index(match)
        projected_records = _copy(self._records)
        projected_records[index] = after
        projected_records = _copy(projected_records)
        after_scope = self._scope_id_for(projected_records)
        revision = {
            'before_check_id': before['id'], 'after_check_id': after['id'],
            'before': before, 'after': _copy(after),
            'scope_before': before_scope, 'scope_after': after_scope,
            'evidence': copied_evidence, 'support': support,
        }
        if len(self._revisions) >= MAX_REVISIONS:
            raise ValueError(f'revision history is limited to {MAX_REVISIONS} entries')
        projected_revisions = _copy(self._revisions + [revision])
        # Commit only after both projected public bundles pass all JSON limits.
        self._records = projected_records
        self._revisions = projected_revisions
        return True

    def carry_revisions(self, revisions):
        """Keep the revision history of the book this one is rebuilt from (the same checks admitted again, with one
        returned): a rebuilt book must not forget a revision made before it (the third review's F11). The oldest
        entries go first when the history limit is reached."""
        projected = _copy(list(revisions) + self._revisions)
        self._revisions = projected[-MAX_REVISIONS:] if len(projected) > MAX_REVISIONS else projected

    def _scope_id_for(self, records):
        body = {'request': self._request, 'contract': self._contract,
                'checks': [{'input': row['input'], 'expected': row['expected'],
                            'origin': row['origin'], 'source': row['source']} for row in records]}
        return _sha(body)

