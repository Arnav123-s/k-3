"""Bounded method storage, isolated execution, and task-scoped validation.

Stored Python source is data. This module never executes it in the host process;
all execution is delegated to the injected executor, which must provide isolation
and its own resource limits. A journal proposal is durable before each call.
Diagnostics are task-private untrusted output and are not claimed to be secret-free.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Callable, Mapping

from .artifacts import ArtifactMemory, canonical
from .runtime import ToolInputError
from .receipts import split_execution_receipt


TASK_VERSION = 'program-task-v1'
QUALIFIER_ID = 'kuttaka-program-check-json-v1'
COMPOSITE_QUALIFIER_ID = 'kuttaka-program-check-json-v1/as-dependency'
MAX_CHECKS = 64
MAX_DIAGNOSTIC_BYTES = 32768
_MISSING = object()

# Opt-in KUTTAKA-C3 tool profile (docs/KUTTAKA_C3_PROTOCOL.md). ENGINEERED. ``None`` is the
# C1/C2 behaviour, byte-identical; 'c3' changes only find_methods and remember_method.
# 'c2' (amendment C2-b) keeps C1/C2 tools and only makes execution receipts replay-stable.
TOOL_PROFILES = (None, 'c2', 'c3')
C3_FIND_LIMIT = 6          # entries returned at most
C3_INTERFACE_LIMIT = 3     # entries that carry an interface (task excerpt and examples)
C3_SCREEN_LIMIT = 3        # top standalone-checked entries tried on this task's visible checks
C3_SIMILAR = 50            # % of shared request words that makes a stored method relevant unasked
C3_TASK_EXCERPT = 120
C3_EXAMPLES = 2
C3_EXAMPLE_BYTES = 240
C3_STOPWORDS = frozenset(
    'a an and are as at be by each for from given if in is it its of on or p return returns '
    'run that the this to with write'.split())


def _c3_terms(text: str) -> set:
    return set(re.findall(r'[^\W_]+', text.casefold(), flags=re.UNICODE)) - C3_STOPWORDS


def _c3_source_key(source: str, uses: list) -> str:
    lines = [line.rstrip() for line in source.strip('\n').splitlines()]
    return hashlib.sha256(canonical({'source': '\n'.join(lines).strip(), 'uses': sorted(uses)})).hexdigest()


def _check_json(value: object) -> None:
    """Reject Python values that JSON would silently coerce or serialize oddly."""
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError('values must be finite JSON data')
        return
    if isinstance(value, list):
        for item in value:
            _check_json(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('JSON object keys must be strings')
        for item in value.values():
            _check_json(item)
        return
    raise ValueError('values must be finite JSON data')


def _json_copy(value: object):
    _check_json(value)
    return json.loads(canonical(value))


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} must be a nonempty string')
    return value


def _artifact_id(value: object) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(ch not in '0123456789abcdef' for ch in value)):
        raise ValueError('artifact must be a lowercase SHA-256 ID')
    return value


def _strict_json_output(output: str):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON object key')
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f'non-JSON numeric constant: {value}')

    try:
        decoded = json.loads(output, object_pairs_hook=reject_duplicates,
                             parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError('executor output must be exactly one JSON value') from error
    _check_json(decoded)
    if not isinstance(decoded, dict) or set(decoded) != {'result'}:
        raise ValueError('executor output must be one object with exactly a result field')
    return _json_copy(decoded['result'])


def _bounded_diagnostics(value: str) -> str:
    """Bound task-private candidate diagnostics before returning or journaling."""
    raw = value.encode('utf-8', errors='replace')
    if len(raw) <= MAX_DIAGNOSTIC_BYTES:
        return value
    marker = '…[truncated]'
    room = MAX_DIAGNOSTIC_BYTES - len(marker.encode('utf-8'))
    return raw[:room].decode('utf-8', errors='ignore') + marker


class ProgramTask:
    """Immutable finite contract for checking a stored method.

    Deployment input is deliberately excluded from ``scope_id``: it can be
    changed without changing the validation contract. This object declares only
    the supplied finite checks, request, context, and task version.
    """

    __slots__ = ('_request', '_checks_bytes', '_context_bytes', '_input_bytes',
                 '_has_input', '_scope_id')

    def __setattr__(self, name, value):
        if hasattr(self, name):
            raise AttributeError('ProgramTask is immutable')
        object.__setattr__(self, name, value)

    def __init__(self, request: str, checks: list, context: object = _MISSING,
                 deployment_input: object = _MISSING):
        self._request = _nonempty_string(request, 'request')
        if not isinstance(checks, list) or not checks or len(checks) > MAX_CHECKS:
            raise ValueError(f'checks must be a nonempty list of at most {MAX_CHECKS} items')
        copied_checks = []
        for index, check in enumerate(checks):
            if not isinstance(check, dict) or set(check) != {'input', 'expected'}:
                raise ValueError(f'check {index} must contain exactly input and expected')
            copied_checks.append({'input': _json_copy(check['input']),
                                  'expected': _json_copy(check['expected'])})
        context_copy = _json_copy({} if context is _MISSING else context)
        has_input = deployment_input is not _MISSING
        input_copy = _json_copy(deployment_input) if has_input else None
        self._checks_bytes = canonical(copied_checks)
        self._context_bytes = canonical(context_copy)
        self._has_input = has_input
        self._input_bytes = canonical(input_copy) if self._has_input else b''
        scope_body = {'version': TASK_VERSION, 'request': self._request,
                      'checks': copied_checks, 'context': context_copy}
        self._scope_id = hashlib.sha256(canonical(scope_body)).hexdigest()

    @classmethod
    def from_dict(cls, manifest: Mapping[str, object]) -> 'ProgramTask':
        if not isinstance(manifest, Mapping):
            raise ValueError('task manifest must be an object')
        allowed = {'request', 'checks', 'context', 'input'}
        if set(manifest) - allowed or not {'request', 'checks'} <= set(manifest):
            raise ValueError('task manifest has missing or unknown fields')
        return cls(manifest['request'], manifest['checks'],
                   manifest['context'] if 'context' in manifest else _MISSING,
                   manifest['input'] if 'input' in manifest else _MISSING)

    @property
    def request(self) -> str:
        return self._request

    @property
    def checks(self) -> list[dict]:
        return json.loads(self._checks_bytes)

    @property
    def context(self):
        return json.loads(self._context_bytes)

    @property
    def input(self):
        return json.loads(self._input_bytes) if self._has_input else None

    @property
    def has_input(self) -> bool:
        return self._has_input

    @property
    def scope_id(self) -> str:
        return self._scope_id


class ProgramTools:
    """Host tools for retaining and validating Python method artifacts."""

    def __init__(self, task: ProgramTask, memory: ArtifactMemory, journal,
                 executor: Callable, max_executions: int = 64,
                 provenance: object = _MISSING, profile: str | None = None):
        if profile not in TOOL_PROFILES:
            raise ValueError('unknown program tool profile')
        self.profile = profile
        self._screened: dict[str, bool | None] = {}
        if not isinstance(task, ProgramTask):
            raise ValueError('task must be a ProgramTask')
        if not isinstance(memory, ArtifactMemory):
            raise ValueError('memory must be an ArtifactMemory')
        if not callable(executor):
            raise ValueError('executor must be callable')
        if type(max_executions) is not int or max_executions < 0:
            raise ValueError('max_executions must be a nonnegative integer')
        if provenance is _MISSING:
            raise ValueError('provenance is required JSON data')
        provenance_copy = _json_copy(provenance)
        self.task = task
        self.memory = memory
        self.journal = journal
        self.executor = executor
        self.max_executions = max_executions
        self.provenance = provenance_copy
        self._execution_attempts = 0
        self._receipts: list[str] = []
        self._locally_qualified: set[str] = set()
        self._checker_feedback = None

    @property
    def execution_attempts(self) -> int:
        return self._execution_attempts

    @property
    def receipts(self) -> tuple[str, ...]:
        return tuple(self._receipts)

    def tools(self) -> dict[str, Callable]:
        def feedback(method):
            # Schema errors become model feedback; text is written by this host module.
            def tool(request):
                try:
                    return method(request)
                except ToolInputError:
                    raise
                except (ValueError, KeyError) as error:
                    raise ToolInputError(str(error)) from None
            tool.__doc__ = method.__doc__
            return tool
        names = ('remember_method', 'inspect_method', 'find_methods', 'probe_method', 'validate_method')
        methods = {name: getattr(self, name) for name in names}
        if self.profile == 'c3':
            # Same tool names and input schemas in both arms; only these two behave differently.
            methods['find_methods'] = self.find_methods_c3
            methods['remember_method'] = self.remember_method_c3
        return {name: feedback(methods[name]) for name in names}

    def remember_method(self, request: object, *, derivation=None) -> dict:
        """Store source as an unqualified method; never execute during admission.

        Input: {name: nonempty string, description: nonempty string,
        source: Python source defining run(input), uses: optional list of stored
        method IDs}. Inside source, use("ID") returns that stored method's run
        function; every ID called this way must be listed in uses.
        """
        return self._remember(request, derivation)

    def remember_method_c3(self, request: object) -> dict:
        """Store source as an unqualified method; never execute during admission.

        Input: {name: nonempty string, description: nonempty string,
        source: Python source defining run(input), uses: optional list of stored
        method IDs}. Inside source, use("ID") returns that stored method's run
        function; every ID called this way must be listed in uses. The method
        keeps this task's request and visible checks as its interface record;
        later tasks see them in find_methods once the method passes those checks.
        """
        return self._remember(request, None, examples=self.task.checks)

    def _remember(self, request: object, derivation=None, *, examples=None) -> dict:
        if (not isinstance(request, dict) or not {'name', 'description', 'source'} <= set(request)
                or set(request) - {'name', 'description', 'source', 'uses'}):
            raise ValueError('remember_method requires name, description, and source (uses optional)')
        name = _nonempty_string(request['name'], 'name')
        description = _nonempty_string(request['description'], 'description')
        source = _nonempty_string(request['source'], 'source')
        uses = request.get('uses', [])
        if not isinstance(uses, list) or len(uses) > 16:
            raise ValueError('uses must be a list of at most 16 method IDs')
        uses = sorted({_artifact_id(item) for item in uses})
        for dependency in uses:
            if self._method_payload(dependency) is None:
                raise ValueError('uses must name stored Python methods')
        provenance = self.provenance
        if derivation is not None:
            provenance = (dict(self.provenance) if isinstance(self.provenance, dict)
                          else {'producer': self.provenance})
            provenance['derivation'] = _json_copy(derivation)
        applicability = {'request': self.task.request,
                         'context': self.task.context,
                         'validation_scope': self.task.scope_id}
        if examples is not None:
            applicability['examples'] = _json_copy(examples)
        payload = {'kind': 'method',
                   'body': {'language': 'python', 'name': name,
                            'description': description, 'source': source},
                   'dependencies': uses, 'provenance': provenance,
                   'applicability': applicability}
        artifact = self.memory.put(payload)
        self.journal.append('method_remembered', {
            'artifact': artifact, 'scope': self.task.scope_id, 'uses': uses,
            'provenance': provenance, 'qualified': False})
        return {'status': 'stored', 'artifact': artifact,
                'qualification': self.memory.status(artifact, self.task.scope_id)}

    def inspect_method(self, request: object) -> dict:
        """Inspect one artifact and its status in this task's scope.

        Input: {artifact: SHA-256 artifact ID}.
        """
        if not isinstance(request, dict) or set(request) != {'artifact'}:
            raise ValueError('inspect_method requires artifact')
        artifact = _artifact_id(request['artifact'])
        payload = self.memory.get(artifact)
        return {'artifact': artifact, 'kind': payload['kind'], 'body': payload['body'],
                'status': self._qualification_role(artifact),
                'uses': payload['dependencies'],
                'qualifications': self.memory.qualifications(artifact),
                'scope': self.task.scope_id}

    def _qualification_role(self, artifact):
        status = self.memory.status(artifact, self.task.scope_id)
        for qualification in self.memory.qualifications(artifact):
            if (qualification['scope'] == self.task.scope_id and qualification['active']
                    and qualification['checker'] == COMPOSITE_QUALIFIER_ID):
                return 'component_only'
        return status

    def find_methods(self, request: object) -> dict:
        """Search all stored method names and descriptions, including methods
        qualified on earlier tasks (qualified_scopes > 0). status is relative to
        the current task. An empty query lists every method.

        Input: {query: string, limit: integer from 1 through 100}.
        Exact case-insensitive substring matches rank first, followed by
        candidates with shared query words; an empty query lists by ID.
        If no query terms match, return a bounded unfiltered catalog labeled
        unfiltered_catalog, so different vocabulary does not hide all memory.
        Results are candidates only, not applicability claims. Inspect and
        validate before reuse.
        """
        if not isinstance(request, dict) or set(request) != {'query', 'limit'}:
            raise ValueError('find_methods requires query and limit')
        if not isinstance(request['query'], str):
            raise ValueError('query must be a string')
        if type(request['limit']) is not int or not 1 <= request['limit'] <= 100:
            raise ValueError('limit must be an integer from 1 through 100')
        methods = self.memory.catalog(kind='method', query=request['query'],
                                      scope=self.task.scope_id, limit=request['limit'])
        retrieval = 'query_matches' if request['query'].strip() else 'catalog'
        if not methods and request['query'].strip():
            methods = self.memory.catalog(kind='method', query='', scope=self.task.scope_id,
                                          limit=request['limit'])
            retrieval = 'unfiltered_catalog'
        for entry in methods:
            body = self.memory.get(entry['id'])['body']
            entry['description'] = body.get('description', '')[:300]
            qualifications = self.memory.qualifications(entry['id'])
            entry['qualified_scopes'] = sum(q['active'] and q['checker'] == QUALIFIER_ID
                                            for q in qualifications)
            entry['component_scopes'] = sum(q['active'] and q['checker'] == COMPOSITE_QUALIFIER_ID
                                            for q in qualifications)
            entry['status'] = self._qualification_role(entry['id'])
        return {'methods': methods, 'retrieval': retrieval if methods else 'empty'}

    def find_methods_c3(self, request: object) -> dict:
        """Search checked methods; the top ones are also tried on this task.

        Input: {query: string, limit: integer from 1 through 100}. Only methods
        that passed a task's checks, alone or inside a checked composite, are
        listed, at most 6, most relevant first. A method is listed when a query
        word is in its name or description, or when the request it was written
        for and this task's request share at least half of the words of the
        shorter one; nothing else is listed, so an empty result means no
        relevant checked method. Each
        entry gives id, name, description, qualified_scopes and status in this
        task; the top three also give interface: the start of the original task
        request and up to two verified input/output examples, enough to call the
        method with use("ID") without inspecting it. The top three methods that
        passed alone are run on this task's visible checks: fits_this_task true
        means every visible check passed, so the method can be answered as is;
        false means it needs an adapter or new logic. A delivered method is still
        checked.
        """
        if not isinstance(request, dict) or set(request) != {'query', 'limit'}:
            raise ValueError('find_methods requires query and limit')
        if not isinstance(request['query'], str):
            raise ValueError('query must be a string')
        if type(request['limit']) is not int or not 1 <= request['limit'] <= 100:
            raise ValueError('limit must be an integer from 1 through 100')
        limit = min(request['limit'], C3_FIND_LIMIT)
        needle = request['query'].casefold().strip()
        query_terms = _c3_terms(request['query'])
        task_terms = _c3_terms(self.task.request)
        candidates = {}
        for position, artifact in enumerate(self.memory.ids('method')):
            payload = self._method_payload(artifact)
            if payload is None:
                continue
            qualifications = self.memory.qualifications(artifact)
            standalone = sum(q['active'] and q['checker'] == QUALIFIER_ID for q in qualifications)
            component = sum(q['active'] and q['checker'] == COMPOSITE_QUALIFIER_ID for q in qualifications)
            if not standalone and not component:
                continue  # only methods that passed checks are offered for reuse
            body = payload['body']
            applicability = payload.get('applicability')
            applicability = applicability if isinstance(applicability, dict) else {}
            origin = applicability.get('request')
            origin = origin if isinstance(origin, str) else ''
            name = body.get('name') if isinstance(body.get('name'), str) else ''
            description = body.get('description') if isinstance(body.get('description'), str) else ''
            label_terms, origin_terms = _c3_terms(name + ' ' + description), _c3_terms(origin)
            label_hits = len(query_terms & label_terms)
            origin_hits = len((query_terms - label_terms) & origin_terms)
            exact = bool(needle) and needle in (name + ' ' + description).casefold()
            # Integer similarity of the two requests, relative to the shorter one.
            similarity = (len(task_terms & origin_terms) * 100
                          // max(1, min(len(task_terms), len(origin_terms))))
            if not (label_hits or exact or similarity >= C3_SIMILAR):
                continue
            score = similarity + 30 * label_hits + 10 * origin_hits + (50 if exact else 0)
            record = {'artifact': artifact, 'origin': origin, 'name': name,
                      'description': description, 'standalone': standalone,
                      'score': score, 'position': position, 'applicability': applicability}
            # Identical source with identical dependencies is one method: keep the best-checked copy.
            key = _c3_source_key(body['source'], payload['dependencies'])
            kept = candidates.get(key)
            if kept is None or (standalone, -position) > (kept['standalone'], -kept['position']):
                candidates[key] = record
        records = sorted(candidates.values(),
                         key=lambda r: (-r['score'], -(r['standalone'] > 0), -r['position'], r['artifact']))
        entries = []
        for rank, record in enumerate(records[:limit]):
            artifact = record['artifact']
            entry = {'id': artifact, 'name': record['name'],
                     'description': record['description'][:160],
                     'qualified_scopes': record['standalone'],
                     'status': self._qualification_role(artifact)}
            if rank < C3_INTERFACE_LIMIT:
                entry['interface'] = self._c3_interface(artifact, record)
            entries.append(entry)
        screened = 0
        for entry in entries:
            if screened == C3_SCREEN_LIMIT:
                break
            if entry['qualified_scopes'] == 0:
                continue
            screened += 1
            entry['fits_this_task'] = self._screen(entry['id'], entry['status'])
        return {'methods': entries, 'retrieval': 'ranked' if entries else 'empty'}

    def _c3_interface(self, artifact: str, record: dict) -> dict:
        """Origin request excerpt, plus verified examples when the method passed its origin task."""
        origin = record['origin']
        interface = {'task': origin[:C3_TASK_EXCERPT] + ('…' if len(origin) > C3_TASK_EXCERPT else '')}
        examples = record['applicability'].get('examples')
        origin_scope = record['applicability'].get('validation_scope')
        if (isinstance(examples, list) and isinstance(origin_scope, str) and origin_scope
                and any(q['scope'] == origin_scope and q['active'] and q['checker'] == QUALIFIER_ID
                        for q in self.memory.qualifications(artifact))):
            shown = []
            for example in examples:
                if isinstance(example, dict) and set(example) == {'input', 'expected'}:
                    item = {'input': example['input'], 'output': example['expected']}
                    if len(canonical(item)) <= C3_EXAMPLE_BYTES:
                        shown.append(item)
                if len(shown) == C3_EXAMPLES:
                    break
            interface['examples'] = shown
        return interface

    def _screen(self, artifact: str, status: str) -> bool | None:
        """Run this task's visible checks on a stored method, stopping at the first miss.

        Not a qualification: nothing is recorded in memory. Results are cached per task, so a
        repeated search does not spend executions again. None means the execution budget ran out
        or the sandbox failed.
        """
        if status == 'qualified':
            return True
        if status in ('invalidated', 'component_only'):
            return False
        if artifact in self._screened:
            return self._screened[artifact]
        payload = self._method_payload(artifact)
        fits = payload is not None
        for index, check in enumerate(self.task.checks if fits else []):
            result = self._execute(artifact, payload['body']['source'], check['input'],
                                   purpose='screen', check_index=index)
            if result['status'] in ('budget_exhausted', 'infrastructure_error'):
                return None  # unknown, not evidence against the method; not cached
            if (result['status'] != 'completed'
                    or canonical(result['result']) != canonical(check['expected'])):
                fits = False
                break
        self._screened[artifact] = fits
        return fits

    def probe_method(self, request: object) -> dict:
        """Execute a stored method on one JSON input, even while unqualified.

        Input: {artifact: SHA-256 artifact ID, input: JSON value}.
        """
        if not isinstance(request, dict) or set(request) != {'artifact', 'input'}:
            raise ValueError('probe_method requires artifact and input')
        artifact = _artifact_id(request['artifact'])
        value = _json_copy(request['input'])
        payload = self._method_payload(artifact)
        if payload is None:
            return {'status': 'not_method', 'artifact': artifact}
        result = self._execute(artifact, payload['body']['source'], value,
                               purpose='probe', check_index=None)
        return self._public_execution_result(artifact, result)

    def validate_method(self, request: object) -> dict:
        """Run all task checks and qualify only exact JSON matches.

        Input: {artifact: SHA-256 artifact ID}. Expected values come only from
        the immutable host-supplied ProgramTask, never from the caller.
        """
        if not isinstance(request, dict) or set(request) != {'artifact'}:
            raise ValueError('validate_method requires artifact')
        artifact = _artifact_id(request['artifact'])
        payload = self._method_payload(artifact)
        if payload is None:
            return {'status': 'failed', 'passed': False, 'artifact': artifact,
                    'scope': self.task.scope_id, 'reason': 'artifact is not a Python method'}

        check_receipts = []
        failures = []
        failed_details = []
        infrastructure_errors = []
        budget_exhausted = False
        for index, check in enumerate(self.task.checks):
            result = self._execute(artifact, payload['body']['source'], check['input'],
                                  purpose='validation', check_index=index)
            if result['status'] == 'budget_exhausted':
                budget_exhausted = True
                break

            if result['status'] == 'completed':
                passed = canonical(result['result']) == canonical(check['expected'])
                check_status = 'passed' if passed else 'counterexample'
                receipt_body = {'artifact': artifact, 'scope': self.task.scope_id,
                                'check_index': index, 'input': check['input'],
                                'expected': check['expected'], 'actual': result['result'],
                                'status': check_status,
                                'execution_receipt': result['receipt']}
                if not passed:
                    failures.append(index)
                    failed_details.append({'check_index': index, 'input': check['input'],
                                           'expected': check['expected'],
                                           'actual': result['result']})
            elif result['status'] == 'candidate_error':
                check_status = 'counterexample'
                receipt_body = {'artifact': artifact, 'scope': self.task.scope_id,
                                'check_index': index, 'input': check['input'],
                                'expected': check['expected'], 'status': check_status,
                                'reason': result['reason'],
                                'execution_receipt': result['receipt']}
                failures.append(index)
                failed_detail = {'check_index': index, 'input': check['input'],
                                 'expected': check['expected'],
                                 'reason': result['reason']}
                if result.get('diagnostics'):
                    failed_detail['diagnostics'] = result['diagnostics']
                failed_details.append(failed_detail)
            else:
                check_status = 'infrastructure_error'
                receipt_body = {'artifact': artifact, 'scope': self.task.scope_id,
                                'check_index': index, 'input': check['input'],
                                'expected': check['expected'], 'status': check_status,
                                'reason': result['reason'],
                                'diagnostics': result.get('diagnostics', ''),
                                'execution_receipt': result['receipt']}
                infrastructure_errors.append(index)
            receipt = self.journal.append('method_check_receipt', receipt_body)
            self._receipts.append(receipt)
            check_receipts.append(receipt)

        if failures:
            if self.memory.status(artifact, self.task.scope_id) == 'qualified':
                self.memory.invalidate(artifact, 'task check counterexample',
                                       [check_receipts[index] for index in failures],
                                       scope=self.task.scope_id)
            self._locally_qualified.discard(artifact)
            self.journal.append('method_validation', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'status': 'failed', 'counterexample_checks': failures,
                'failed_checks': failed_details,
                'infrastructure_checks': infrastructure_errors,
                'check_receipts': check_receipts})
            return {'status': 'failed', 'passed': False, 'artifact': artifact,
                    'scope': self.task.scope_id, 'counterexample_checks': failures,
                    'failed_checks': failed_details,
                    'infrastructure_checks': infrastructure_errors,
                    'checks_completed': len(check_receipts),
                    'execution_attempts': self._execution_attempts}

        if budget_exhausted:
            self.journal.append('method_validation', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'status': 'incomplete', 'reason': 'execution budget exhausted',
                'checks_completed': len(check_receipts),
                'infrastructure_checks': infrastructure_errors,
                'check_receipts': check_receipts})
            return {'status': 'incomplete', 'passed': False, 'artifact': artifact,
                    'scope': self.task.scope_id, 'checks_completed': len(check_receipts),
                    'infrastructure_checks': infrastructure_errors,
                    'execution_attempts': self._execution_attempts}

        if infrastructure_errors:
            self.journal.append('method_validation', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'status': 'incomplete', 'infrastructure_checks': infrastructure_errors,
                'check_receipts': check_receipts})
            return {'status': 'incomplete', 'passed': False, 'artifact': artifact,
                    'scope': self.task.scope_id,
                    'infrastructure_checks': infrastructure_errors,
                    'execution_attempts': self._execution_attempts}

        try:
            for dependency in self._closure(artifact):
                # An invalidated dependency is never restored automatically.
                if self.memory.status(dependency, self.task.scope_id) == 'proposed':
                    # A dependency is qualified here only as part of a composite
                    # that passed every check of this task.
                    self.memory.qualify(dependency, self.task.scope_id, COMPOSITE_QUALIFIER_ID,
                                        check_receipts)
            self.memory.qualify(artifact, self.task.scope_id, QUALIFIER_ID, check_receipts)
        except Exception as error:
            self.journal.append('method_validation', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'status': 'qualification_error', 'error_type': type(error).__name__,
                'check_receipts': check_receipts})
            return {'status': 'incomplete', 'passed': False, 'artifact': artifact,
                    'scope': self.task.scope_id, 'execution_attempts': self._execution_attempts}

        self._locally_qualified.add(artifact)
        self.journal.append('method_validation', {
            'artifact': artifact, 'scope': self.task.scope_id,
            'status': 'qualified', 'checker': QUALIFIER_ID,
            'check_receipts': check_receipts})
        return {'status': 'passed', 'passed': True, 'artifact': artifact,
                'scope': self.task.scope_id, 'checks_passed': len(check_receipts),
                'execution_attempts': self._execution_attempts,
                'deliverable': {'artifact': artifact}}

    def checker(self, original_request: str, answer: object) -> bool:
        """Development checker: validate an artifact reference for this request."""
        self._checker_feedback = {'status': 'rejected', 'reason': 'invalid artifact answer for this task'}
        if original_request != self.task.request:
            return False
        if not isinstance(answer, dict) or set(answer) != {'artifact'}:
            return False
        try:
            artifact = _artifact_id(answer['artifact'])
            payload = self._method_payload(artifact)
            if payload is None:
                self._checker_feedback = {'status': 'rejected', 'reason': 'artifact is not a stored Python method'}
                return False
            closure_ready = False
            if (self.memory.status(artifact, self.task.scope_id) == 'qualified'
                    and artifact in self._locally_qualified):
                try:
                    qualified_payload = self.memory.get_qualified(artifact, self.task.scope_id)
                    closure_ready = (qualified_payload.get('kind') == 'method'
                                     and qualified_payload.get('body') == payload.get('body'))
                except (KeyError, ValueError, PermissionError, OSError):
                    closure_ready = False
            if not closure_ready:
                result = self.validate_method({'artifact': artifact})
                self._checker_feedback = result
                if result['status'] != 'passed':
                    return False
            final_payload = self.memory.get_qualified(artifact, self.task.scope_id)
            accepted = (self.memory.status(artifact, self.task.scope_id) == 'qualified'
                        and final_payload.get('kind') == 'method'
                        and final_payload.get('body') == payload.get('body'))
            self._checker_feedback = None if accepted else {
                'status': 'rejected', 'reason': 'artifact qualification changed'}
            return accepted
        except Exception:
            self._checker_feedback = {'status': 'incomplete', 'reason': 'artifact or qualification unavailable'}
            return False

    def checker_feedback(self, original_request: str, answer: object):
        """Return host check details after rejection without changing approval.

        Called immediately by the runtime after ``checker``. Details come from
        the task checks and execution receipts, never from a model attestation.
        """
        if original_request != self.task.request:
            return {'status': 'rejected', 'reason': 'request does not match this task'}
        return _json_copy(self._checker_feedback)

    def _closure(self, artifact: str) -> list[str]:
        """Dependencies of ``artifact`` in dependency-first order, excluding itself."""
        order, seen = [], set()

        def visit(current, path):
            if current in path:
                raise ValueError('method dependency cycle')
            if current in seen:
                return
            if len(path) > 16 or len(seen) >= 17:
                raise ValueError('method dependency closure exceeds 16 libraries')
            payload = self.memory.get(current)
            for dependency in payload['dependencies']:
                visit(dependency, path | {current})
            seen.add(current)
            order.append(current)
        visit(artifact, frozenset())
        if len(order) > 17:
            raise ValueError('method dependency closure exceeds 16 libraries')
        return order[:-1]

    def _libraries(self, artifact: str) -> dict:
        libraries = {}
        for dependency in self._closure(artifact):
            payload = self._method_payload(dependency)
            if payload is not None:  # non-method dependencies carry no code
                libraries[dependency] = payload['body']['source']
        return libraries

    def _method_payload(self, artifact: str) -> dict | None:
        try:
            payload = self.memory.get(artifact)
        except (KeyError, ValueError, OSError):
            return None
        body = payload.get('body')
        if (payload.get('kind') != 'method' or not isinstance(body, dict)
                or body.get('language') != 'python' or not isinstance(body.get('source'), str)
                or not body.get('source').strip()):
            return None
        return payload

    def _execute(self, artifact: str, source: str, input_value: object, *,
                 purpose: str, check_index: int | None) -> dict:
        """Journal exact identity/input before delegating one bounded attempt."""
        if self._execution_attempts >= self.max_executions:
            self.journal.append('program_execution_blocked', {
                'artifact': artifact, 'input': input_value, 'scope': self.task.scope_id,
                'purpose': purpose, 'check_index': check_index,
                'reason': 'execution budget exhausted',
                'attempts': self._execution_attempts})
            return {'status': 'budget_exhausted', 'reason': 'execution budget exhausted'}

        attempt_number = self._execution_attempts + 1
        libraries = self._libraries(artifact)
        proposal_hash = self.journal.append('program_execution_precommit', {
            'artifact': artifact, 'input': input_value, 'scope': self.task.scope_id,
            'purpose': purpose, 'check_index': check_index,
            'attempt': attempt_number, 'uses': sorted(libraries)})
        self._execution_attempts += 1
        job = {'source': source, 'input': _json_copy(input_value)}
        if libraries:
            job['libraries'] = libraries
        try:
            response = self.executor(job)
        except Exception as error:
            receipt = self.journal.append('program_execution_result', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'attempt': attempt_number, 'proposal': proposal_hash,
                'status': 'infrastructure_error', 'error_type': type(error).__name__})
            self._receipts.append(receipt)
            return {'status': 'infrastructure_error', 'reason': 'executor raised',
                    'receipt': receipt}

        try:
            response, metadata = split_execution_receipt(response)
            if any(aid not in libraries for aid in metadata.get('dependency_calls', [])):
                raise ValueError('undeclared dependency in receipt')
        except ValueError:
            receipt = self.journal.append('program_execution_result', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'attempt': attempt_number, 'proposal': proposal_hash,
                'status': 'infrastructure_error', 'error_type': 'InvalidExecutorResult'})
            self._receipts.append(receipt)
            return {'status': 'infrastructure_error', 'reason': 'invalid executor response',
                    'receipt': receipt}

        if self.profile in ('c2', 'c3'):
            # C2 and C3 journals keep only the deterministic dependency trace. Container names and
            # timings differ on every execution, so journaling them makes a model-free replay
            # differ from its run; they are operational diagnostics, not evidence.
            metadata = ({'dependency_calls': metadata['dependency_calls']}
                        if 'dependency_calls' in metadata else {})
        if metadata:
            self.journal.append('program_execution_metadata', {
                'artifact': artifact, 'proposal': proposal_hash, 'attempt': attempt_number,
                'metadata': metadata,
                'invocation_evidence': 'sandbox-reported cooperative trace; not causal or tamper-proof'})

        diagnostics = _bounded_diagnostics(response.get('diagnostics', ''))
        if response['status'] not in {'completed', 'timeout', 'output_limit', 'invalid_output'}:
            receipt = self.journal.append('program_execution_result', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'attempt': attempt_number, 'proposal': proposal_hash,
                'status': 'infrastructure_error', 'executor_status': response['status'],
                'exit_code': response['exit_code'], 'diagnostics': diagnostics})
            self._receipts.append(receipt)
            return {'status': 'infrastructure_error', 'reason': 'unknown executor status',
                    'diagnostics': diagnostics, 'receipt': receipt}

        if response['status'] != 'completed':
            if response['status'] == 'invalid_output':
                receipt = self.journal.append('program_execution_result', {
                    'artifact': artifact, 'scope': self.task.scope_id,
                    'attempt': attempt_number, 'proposal': proposal_hash,
                    'status': 'candidate_error', 'executor_status': response['status'],
                    'exit_code': response['exit_code'], 'diagnostics': diagnostics})
                self._receipts.append(receipt)
                return {'status': 'candidate_error', 'reason': 'executor reported invalid output',
                        'diagnostics': diagnostics, 'receipt': receipt}
            receipt = self.journal.append('program_execution_result', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'attempt': attempt_number, 'proposal': proposal_hash,
                'status': 'infrastructure_error', 'executor_status': response['status'],
                'exit_code': response['exit_code'], 'diagnostics': diagnostics})
            self._receipts.append(receipt)
            return {'status': 'infrastructure_error',
                    'reason': f"executor status {response['status']}", 'receipt': receipt}

        if response['exit_code'] in (125, 126, 127) or response['exit_code'] < 0:
            receipt = self.journal.append('program_execution_result', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'attempt': attempt_number, 'proposal': proposal_hash,
                'status': 'infrastructure_error', 'exit_code': response['exit_code'],
                'diagnostics': diagnostics})
            self._receipts.append(receipt)
            return {'status': 'infrastructure_error', 'reason': 'executor startup or signal failure',
                    'exit_code': response['exit_code'], 'diagnostics': diagnostics,
                    'receipt': receipt}

        if response['exit_code'] != 0:
            receipt = self.journal.append('program_execution_result', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'attempt': attempt_number, 'proposal': proposal_hash,
                'status': 'candidate_error', 'exit_code': response['exit_code'],
                'diagnostics': diagnostics})
            self._receipts.append(receipt)
            return {'status': 'candidate_error', 'reason': 'method exited unsuccessfully',
                    'exit_code': response['exit_code'], 'diagnostics': diagnostics,
                    'receipt': receipt}

        try:
            result = _strict_json_output(response['output'])
        except ValueError:
            receipt = self.journal.append('program_execution_result', {
                'artifact': artifact, 'scope': self.task.scope_id,
                'attempt': attempt_number, 'proposal': proposal_hash,
                'status': 'candidate_error', 'reason': 'invalid single-JSON output',
                'diagnostics': diagnostics})
            self._receipts.append(receipt)
            return {'status': 'candidate_error', 'reason': 'invalid single-JSON output',
                    'diagnostics': diagnostics, 'receipt': receipt}

        receipt = self.journal.append('program_execution_result', {
            'artifact': artifact, 'scope': self.task.scope_id,
            'attempt': attempt_number, 'proposal': proposal_hash,
            'status': 'completed', 'exit_code': response['exit_code'], 'result': result,
            'diagnostics': diagnostics,
            **({'dependency_calls': metadata['dependency_calls']} if 'dependency_calls' in metadata else {})})
        self._receipts.append(receipt)
        return {'status': 'completed', 'result': result, 'diagnostics': diagnostics,
                'receipt': receipt,
                **({'dependency_calls': metadata['dependency_calls']} if 'dependency_calls' in metadata else {})}

    @staticmethod
    def _public_execution_result(artifact: str, result: dict) -> dict:
        if result['status'] == 'completed':
            return {'status': 'completed', 'artifact': artifact, 'result': result['result'],
                    'diagnostics': result.get('diagnostics', ''),
                    'receipt': result['receipt'],
                    **({'dependency_calls': result['dependency_calls']} if 'dependency_calls' in result else {})}
        return {'status': result['status'], 'artifact': artifact,
                'reason': result.get('reason', ''),
                **({'diagnostics': result['diagnostics']} if result.get('diagnostics') else {}),
                **({'receipt': result['receipt']} if 'receipt' in result else {})}
