"""Finite-check progress records for a single immutable evidence scope.

These helpers summarize observations over declared check IDs. They do not
establish real-world truth, semantic program equivalence, or generalization
beyond those checks.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Iterable


MAX_ID_CHARS = 256
MAX_CHECK_IDS = 4096
MAX_DEPENDENCIES = 4096
MAX_SOURCE_CHARS = 1_000_000
MAX_RECORDS = 10000


def _identifier(value: Any, name: str) -> str:
    if (type(value) is not str or not value or not value.strip()
            or len(value) > MAX_ID_CHARS):
        raise ValueError(f'{name} must be a nonempty string of at most {MAX_ID_CHARS} characters')
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def _bounded_ids(values: Iterable[str], name: str, limit: int) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f'{name} must be an iterable of IDs')
    try:
        iterator = iter(values)
    except TypeError as error:
        raise ValueError(f'{name} must be an iterable of IDs') from error
    result = []
    for value in iterator:
        if len(result) >= limit:
            raise ValueError(f'{name} may contain at most {limit} IDs')
        result.append(_identifier(value, f'{name} item'))
    return tuple(result)


def candidate_fingerprint(source: str, dependencies: Iterable[str] = ()) -> str:
    """Hash parsed Python AST plus sorted exact dependency IDs, without execution.

    AST locations and comments do not affect the digest. If parsing fails, the
    original source text is included verbatim so malformed candidates remain
    distinguishable. Dependency IDs are exact strings; duplicates are removed.
    """
    if type(source) is not str:
        raise ValueError('source must be text')
    if len(source) > MAX_SOURCE_CHARS:
        raise ValueError(f'source exceeds {MAX_SOURCE_CHARS} characters')
    if isinstance(dependencies, (str, bytes)):
        raise ValueError('dependencies must be an iterable of IDs')
    dependency_ids = sorted(set(_bounded_ids(dependencies, 'dependencies', MAX_DEPENDENCIES)))

    try:
        parsed = ast.parse(source, mode='exec')
        body = {'ast': ast.dump(parsed, annotate_fields=True, include_attributes=False)}
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        body = {'parse_error_source': source}
    material = {'candidate': body, 'dependencies': dependency_ids}
    return hashlib.sha256(_canonical(material).encode('utf-8')).hexdigest()


def _validate_json(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [10000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 32:
        raise ValueError('checks exceed the JSON structure limits')
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('checks must contain finite JSON numbers')
        return
    if type(value) is list:
        for item in value:
            _validate_json(item, depth=depth + 1, budget=budget)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError('check object keys must be strings')
            _validate_json(item, depth=depth + 1, budget=budget)
        return
    raise ValueError('checks must be JSON values')


def _exact_text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f'{name} must be nonempty text')
    return value


def subproblem_key(request: str, contract: str, checks: Any) -> tuple[str, str, str]:
    """Build an exact active-subproblem key, without semantic equivalence claims.

    Request and contract text is exact, including whitespace. Check JSON types,
    values, and list order remain exact; object-key order is canonicalized.
    """
    _validate_json(checks)
    return (_exact_text(request, 'request'),
            _exact_text(contract, 'contract'), _canonical(checks))


def boundary_restatement_key(request: str, contract: str, checks: Any) -> tuple[str, str, str]:
    """Scheduling-only key for trivial boundary-whitespace restatements.

    This is not a task, check-scope or evidence identity. Only surrounding whitespace
    in request/contract prose is ignored; internal spacing (including code and quoted
    literals) and check JSON types, values and order stay exact. Matching an active
    ancestor catches a trivial non-reduction, not semantic equivalence or difficulty.
    """
    request, contract, checks_json = subproblem_key(request, contract, checks)
    return request.strip(), contract.strip(), checks_json


def _snapshot(record: dict) -> dict:
    return deepcopy(record)


class EvidenceFrontier:
    """Track nondominated completed outcomes for one immutable finite check set.

    A candidate's coverage is the set of check IDs it passed. Strict set
    inclusion defines dominance, so equal-sized but different pass sets remain
    separate tradeoffs, and identical pass sets from distinct candidates remain
    as same-coverage alternatives. Conflicting completed outcomes for one
    candidate mark it inconsistent; its original evidence remains in ``records``
    and it is removed from the qualified frontier. Incomplete observations are
    archived but never alter candidate qualification or the best completed score.
    Since observations contain only passed IDs, conflicts that produce the same
    pass set cannot be detected here; callers must retain any richer outputs.
    """

    def __init__(self, check_ids: Iterable[str], *, max_records: int = MAX_RECORDS):
        ids = _bounded_ids(check_ids, 'check_ids', MAX_CHECK_IDS)
        if len(set(ids)) != len(ids):
            raise ValueError('check_ids must be unique')
        if type(max_records) is not int or max_records < 1:
            raise ValueError('max_records must be a positive integer')
        self._check_ids = frozenset(ids)
        self._ordered_check_ids = tuple(sorted(ids))
        self._scope_id = hashlib.sha256(_canonical(self._ordered_check_ids).encode('utf-8')).hexdigest()
        self._max_records = max_records
        self._records: list[dict] = []
        self._completed: dict[str, dict] = {}
        self._inconsistent: set[str] = set()
        self._frontier: dict[str, dict] = {}

    @property
    def scope_id(self) -> str:
        return self._scope_id

    @property
    def check_ids(self) -> list[str]:
        return list(self._ordered_check_ids)

    @property
    def records(self) -> list[dict]:
        return deepcopy(self._records)

    @property
    def frontier(self) -> list[dict]:
        return [_snapshot(self._frontier[key]) for key in sorted(self._frontier)]

    @property
    def best_count(self) -> int:
        return max((len(item['passed_ids']) for item in self._frontier.values()), default=0)

    def _view(self) -> dict:
        return {'scope_id': self._scope_id,
                'check_ids': list(self._ordered_check_ids),
                'frontier': self.frontier,
                'best_count': self.best_count,
                'check_count': len(self._check_ids)}

    def _recompute_frontier(self) -> None:
        eligible = {key: record for key, record in self._completed.items()
                    if key not in self._inconsistent}
        sets = {key: frozenset(record['passed_ids']) for key, record in eligible.items()}
        self._frontier = {
            key: record for key, record in eligible.items()
            if not any(sets[key] < other for other_key, other in sets.items() if other_key != key)
        }

    def observe(self, candidate_id: str, artifact_id: str, passed_ids: Iterable[str],
                *, completed: bool = True) -> dict:
        candidate_id = _identifier(candidate_id, 'candidate_id')
        artifact_id = _identifier(artifact_id, 'artifact_id')
        if type(completed) is not bool:
            raise ValueError('completed must be a bool')
        passed = set(_bounded_ids(passed_ids, 'passed_ids', MAX_CHECK_IDS))
        unknown = passed - self._check_ids
        if unknown:
            raise ValueError(f'passed_ids contain checks outside this scope: {sorted(unknown)!r}')
        if len(self._records) >= self._max_records:
            raise OverflowError('evidence record limit reached')

        passed_sorted = sorted(passed)
        classification = 'incomplete'
        inconsistent = False
        if completed:
            if candidate_id in self._inconsistent:
                classification = 'inconsistent'
                inconsistent = True
            elif candidate_id in self._completed:
                prior = self._completed[candidate_id]
                if frozenset(prior['passed_ids']) == frozenset(passed):
                    classification = 'repeated'
                else:
                    classification = 'inconsistent'
                    inconsistent = True
                    self._inconsistent.add(candidate_id)
                    self._recompute_frontier()
            else:
                old_sets = [frozenset(item['passed_ids']) for item in self._frontier.values()]
                new_set = frozenset(passed)
                record = {'candidate_id': candidate_id, 'artifact_id': artifact_id,
                          'passed_ids': passed_sorted, 'passed_count': len(passed),
                          'completed': True, 'consistent': True}
                # Keep every first completed result, including dominated ones,
                # so later repetitions and conflicts remain observable.
                self._completed[candidate_id] = record
                if not old_sets:
                    classification = 'new_evidence'
                elif any(new_set < old for old in old_sets):
                    classification = 'regression'
                else:
                    incomparable = [old for old in old_sets if not old <= new_set]
                    if any(new_set == old for old in old_sets):
                        classification = 'same_coverage'
                    else:
                        classification = 'tradeoff' if incomparable else 'improved'
                self._recompute_frontier()

        observation = {'candidate_id': candidate_id, 'artifact_id': artifact_id,
                       'passed_ids': passed_sorted, 'passed_count': len(passed),
                       'completed': completed, 'classification': classification,
                       'inconsistent': inconsistent, 'scope_id': self._scope_id}
        self._records.append(observation)
        return {**_snapshot(observation), **self._view()}
