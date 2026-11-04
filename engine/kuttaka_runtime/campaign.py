"""Task-stream campaigns: the same frozen model with and without retained method memory.

Arms (identical budgets, prompts, tools and executor):
  MODEL_ALONE  every task starts from an empty method memory;
  KUTTAKA      one memory is carried along the stream, so methods discovered,
               validated and stored on earlier tasks can be found, called by
               artifact id (``use``), and composed.

``run`` writes one row per task. ``replay`` re-executes a finished campaign from
its journals with no model transport; identical journal hashes mean identical
runs. ``score`` runs each delivered method on evaluator-held holdout inputs that
neither the model nor the run ever saw. ``compare`` reports paired measures and
claim eligibility. Holdout data is read only by ``score``.
"""

import argparse
import hashlib
import inspect
import json
import shutil
import sys
import time
from math import comb
from pathlib import Path

from .artifacts import ArtifactMemory
from .execution import PythonExecutor, preflight
from .models import INSTRUCTIONS_C3, JsonModel, ReplayModel, tcp_reachable
from .programs import ProgramTask, ProgramTools
from .runtime import Budget, TransportFailure, investigate
from .store import Journal, canonical

ARMS = ('MODEL_ALONE', 'KUTTAKA')
CAMPAIGN_FORMAT = 'kuttaka-task-stream-campaign-v2'
ARM_OUTAGE_LIMIT = 5
CAMPAIGN_PROFILE = 'c1-task-stream-v2-low-stream-provider-defaults'
# Opt-in KUTTAKA-C3 profile (docs/KUTTAKA_C3_PROTOCOL.md): the c3 program-tool profile and the
# C3 instruction note, with every other C1/C2 option unchanged. The default stays C1.
C3_PROFILE = 'c3-reuse-tools-v1-low-stream-provider-defaults'
# Amendment C2-b: C1 tools and instructions, with replay-stable execution receipts.
C2_PROFILE = 'c2-task-stream-v2-stable-receipts-low-stream-provider-defaults'
PROFILES = {CAMPAIGN_PROFILE: None, C2_PROFILE: 'c2', C3_PROFILE: 'c3'}
CLI_PROFILES = {'c1': CAMPAIGN_PROFILE, 'c2': C2_PROFILE, 'c3': C3_PROFILE}


class CeilingReached(RuntimeError):
    """The owner-typed campaign call ceiling is spent."""


class TransportOutage(BaseException):
    """Several consecutive model calls failed in transport: stop the arm, keep it resumable.

    A BaseException, so the run loop does not record it as a finished task: the task that was
    running stays unfinished and is rerun live by ``--resume-from`` once the endpoint is back.
    """


class CampaignIneligible(ValueError):
    """Campaign integrity or C1 reply-validity requirements were not met."""


def _json_loads(text):
    """Load bounded campaign JSON without duplicate-key or non-finite ambiguity."""
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON object key')
            result[key] = value
        return result

    def bad_constant(_):
        raise ValueError('non-finite JSON number is not allowed')

    return json.loads(text, object_pairs_hook=object_pairs, parse_constant=bad_constant)


def _task_manifest_digest(stream):
    rows = [{'id': task['id'], 'chain': task.get('chain'), 'manifest': _manifest(task)}
            for task in stream]
    return hashlib.sha256(canonical(rows).encode('ascii')).hexdigest()


def _validate_task_ids(tasks):
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('a stream is a nonempty JSON list of tasks')
    seen = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError('every task must be an object')
        task_id = task.get('id')
        if (not isinstance(task_id, str) or not task_id or task_id in ('.', '..')
                or '/' in task_id or '\\' in task_id or ':' in task_id
                or Path(task_id).is_absolute() or Path(task_id).name != task_id
                or any(ord(char) < 32 or ord(char) == 127 for char in task_id)
                or task_id in seen):
            raise ValueError('every task needs a unique safe single-component string id')
        seen.add(task_id)


def _manifest_digest(manifest):
    return hashlib.sha256(canonical(manifest).encode('ascii')).hexdigest()


def _executor_contract(executor):
    if isinstance(executor, PythonExecutor):
        contract = {'kind': 'python_executor', 'image': executor.image,
                    'timeout': executor.timeout, 'output_limit': executor.output_limit}
        if getattr(executor, 'trace', 'all') != 'all':
            contract['trace'] = executor.trace  # opt-in C3 runner; C1/C2 contracts unchanged
        return contract
    identity = getattr(executor, 'campaign_identity', None)
    if not isinstance(identity, str) or not identity:
        module = getattr(executor, '__module__', type(executor).__module__)
        name = getattr(executor, '__qualname__', type(executor).__qualname__)
        try:
            source = inspect.getsource(executor)
        except (OSError, TypeError):
            source = ''
        code = getattr(executor, '__code__', None)
        fingerprint = source.encode('utf-8') if source else (code.co_code if code else b'')
        if not fingerprint:
            raise ValueError('callable executor needs a stable campaign_identity')
        identity = f'{module}.{name}:{hashlib.sha256(fingerprint).hexdigest()}'
    return {'kind': 'scripted_callable', 'identity': identity}


def _campaign_contract_digest(model_identity, budget, max_executions, options,
                              call_ceiling, executor_contract):
    return _manifest_digest({'model_identity': model_identity,
                             'budget': {'model_calls': budget.model_calls,
                                        'tool_calls': budget.tool_calls},
                             'max_executions': max_executions,
                             'options': options,
                             'campaign_call_ceiling': call_ceiling,
                             'executor': executor_contract})


class Ceiling:
    """Hard count of model calls across a whole campaign, including failed calls.

    ``outage_after`` consecutive transport failures stop the arm (C1-d), so an endpoint outage
    cannot spend the budget on calls that never reach the model.
    """

    def __init__(self, model, limit, used=0, outage_after=5, reach=None, reach_wait=0,
                 sleep=time.sleep):
        if type(limit) is not int or limit < 1 or type(used) is not int or used < 0:
            raise ValueError('campaign call ceiling and used count must be non-negative integers')
        if type(outage_after) is not int or outage_after < 1:
            raise ValueError('outage_after must be a positive integer')
        if type(reach_wait) not in (int, float) or reach_wait < 0 or (reach is None) != (reach_wait == 0):
            raise ValueError('reach and a positive reach_wait go together')
        self.reach, self.reach_wait, self._sleep = reach, reach_wait, sleep
        self.unreachable_checks = 0
        self.model, self.limit, self.used = model, limit, used
        self.identity = model.identity
        self.reached = False
        self.outage_after, self.consecutive_failures = outage_after, 0
        self._last_response, self._last_usage = None, {}

    @property
    def last_response(self):
        return self._last_response

    @property
    def last_usage(self):
        return self._last_usage

    def wait_until_reachable(self):
        """C2 transport amendment: before an attempt is logged, wait while the first network hop
        refuses TCP connections (no request is sent, so no model call is made). After
        ``reach_wait`` seconds stop the arm, resumable, with no attempt spent."""
        if self.reach is None:
            return
        waited, delay = 0, 2
        while not self.reach():
            self.unreachable_checks += 1
            if waited >= self.reach_wait:
                raise TransportOutage(f'network unreachable for {waited} s before sending; no call spent')
            self._sleep(delay)
            waited += delay
            delay = min(delay * 2, 60)

    def propose(self, context):
        # Clear before admission so a refused attempt cannot inherit the prior
        # provider response or usage in the runtime journal.
        self._last_response, self._last_usage = None, {}
        if self.used >= self.limit:
            self.reached = True
            raise CeilingReached('campaign model-call ceiling reached')
        if self.outage_after and self.consecutive_failures >= self.outage_after:
            raise TransportOutage(f'{self.consecutive_failures} consecutive transport failures')
        self.used += 1
        try:
            action = self.model.propose(context)
        except TransportFailure:
            self._last_response = getattr(self.model, 'last_response', None)
            self._last_usage = getattr(self.model, 'last_usage', {}) or {}
            self.consecutive_failures += 1
            raise
        except BaseException:
            self._last_response = getattr(self.model, 'last_response', None)
            self._last_usage = getattr(self.model, 'last_usage', {}) or {}
            raise
        self._last_response = getattr(self.model, 'last_response', None)
        self._last_usage = getattr(self.model, 'last_usage', {}) or {}
        self.consecutive_failures = 0
        return action


def load_stream(path):
    tasks = _json_loads(Path(path).read_text())
    _validate_task_ids(tasks)
    return tasks


def _manifest(task):
    return {k: task[k] for k in ('request', 'checks', 'context', 'input') if k in task}


def _closure(memory, artifact):
    order, seen = [], set()

    def visit(current):
        if current in seen:
            return
        for dependency in memory.get(current)['dependencies']:
            visit(dependency)
        seen.add(current)
        order.append(current)
    visit(artifact)
    return order


def _row(task, journal_path, result, memory, created_before, tool_profile=None):
    events = Journal.read(journal_path)
    accounting = _account_attempts(events)
    row = {'task': task['id'], 'chain': task.get('chain'), 'status': result.status,
           'model_calls': accounting['attempts'], 'model_replies': accounting['replies'],
           'tool_calls': result.tool_calls,
           'input_tokens': accounting['input_tokens'], 'output_tokens': accounting['output_tokens'],
           'known_input_tokens': accounting['known_input_tokens'],
           'known_output_tokens': accounting['known_output_tokens'],
           'unknown_input_calls': accounting['unknown_input_calls'],
           'unknown_output_calls': accounting['unknown_output_calls'],
           'unknown_usage_calls': accounting['unknown_usage_calls'],
           'usage_complete': accounting['usage_complete'],
           'transport_errors': accounting['transport_errors'],
           'journal_sha256': events[-1]['hash'] if events else None,
           'artifact': None, 'reused_artifacts': [], 'executed_reused_artifacts': []}
    if result.status == 'checked':
        artifact = result.answer['artifact']
        row['artifact'] = artifact
        # Declared dependencies and sandbox-reported calls are separate evidence.
        reused = [a for a in _closure(memory, artifact) if a in created_before]
        invoked = set()
        for event in events:
            if (event.get('kind') == 'program_execution_result'
                    and event.get('body', {}).get('status') == 'completed'
                    and event.get('body', {}).get('artifact') == artifact):
                calls = event.get('body', {}).get('dependency_calls', [])
                if isinstance(calls, list):
                    invoked.update(item for item in calls if item in created_before)
        if tool_profile == 'c3':
            # C3: delivering a method stored on an earlier task is reuse by id when this task's
            # journal shows that exact artifact executing to completion (C1 counts only calls
            # through use()).
            row['delivered_stored_method'] = artifact in created_before
            if artifact in created_before and any(
                    event.get('kind') == 'program_execution_result'
                    and event.get('body', {}).get('status') == 'completed'
                    and event.get('body', {}).get('artifact') == artifact for event in events):
                invoked.add(artifact)
        row['reused_artifacts'] = reused
        row['declared_reused_artifacts'] = reused
        row['executed_reused_artifacts'] = [a for a in reused if a in invoked]
        row['reuse_evidence'] = 'sandbox-reported cooperative invocation; not causal proof'
    return row


def _account_attempts(events):
    """Count schema-accepted action replies; missing provider usage remains unknown, never zero."""
    attempts = []
    current = None
    transport_errors = {}
    for event in events:
        kind, body = event.get('kind'), event.get('body', {})
        if kind == 'model_attempt':
            current = {'usage': None, 'valid_reply': False, 'returned': False}
            attempts.append(current)
        elif kind == 'model_response' and current is not None:
            current['usage'] = body.get('usage')
        elif kind == 'model_reply' and current is not None:
            current['valid_reply'] = current['returned'] = True
        elif kind == 'action_rejected' and current is not None:
            current['valid_reply'] = False
        elif kind == 'model_error' and body.get('soft') is True:
            label = body.get('kind')
            if not isinstance(label, str):
                label = 'transport_failure'
            transport_errors[label] = transport_errors.get(label, 0) + 1

    known_input = known_output = unknown_input = unknown_output = unknown = 0
    for attempt in attempts:
        usage = attempt['usage']
        input_tokens = usage.get('input_tokens') if isinstance(usage, dict) else None
        output_tokens = usage.get('output_tokens') if isinstance(usage, dict) else None
        input_known = type(input_tokens) is int and input_tokens >= 0
        output_known = type(output_tokens) is int and output_tokens >= 0
        if input_known:
            known_input += input_tokens
        else:
            unknown_input += 1
        if output_known:
            known_output += output_tokens
        else:
            unknown_output += 1
        if not input_known or not output_known:
            unknown += 1
    complete = unknown == 0
    replies = sum(attempt['valid_reply'] for attempt in attempts)
    tool_calls = sum(event.get('kind') == 'tool_attempt' for event in events)
    execution_attempts = sum(event.get('kind') == 'program_execution_precommit' for event in events)
    has_model_reply_event = any(event.get('kind') == 'model_reply' for event in events)
    return {'attempts': len(attempts), 'replies': replies,
            'returned': sum(attempt['returned'] for attempt in attempts),
            'tool_calls': tool_calls, 'execution_attempts': execution_attempts,
            'input_tokens': known_input if unknown_input == 0 else None,
            'output_tokens': known_output if unknown_output == 0 else None,
            'known_input_tokens': known_input, 'known_output_tokens': known_output,
            'unknown_input_calls': unknown_input, 'unknown_output_calls': unknown_output,
            'unknown_usage_calls': unknown, 'usage_complete': complete,
            'trailing_transport_failures': _trailing_transport_failures(events),
            'has_model_reply_event': has_model_reply_event,
            'transport_errors': transport_errors}


def _trailing_transport_failures(events, streak=0):
    for event in events:
        if event.get('kind') == 'model_reply':
            streak = 0
        elif event.get('kind') == 'model_error' and event.get('body', {}).get('soft') is True:
            streak += 1
    return streak


def finished_tasks(source):
    """Task ids whose journal in ``source`` ends with a finish event (complete and intact)."""
    done = set()
    for path in sorted(Path(source).glob('runs/*/events.jsonl')):
        try:
            events = Journal.read(path)
        except ValueError as error:
            raise CampaignIneligible(f'cannot resume from corrupt journal {path.name}') from error
        if events and events[-1]['kind'] == 'finish':
            body = events[-1].get('body', {})
            # An arm-level outage is a cleanly journaled but unfinished task. Replay
            # earlier complete tasks, then rerun this task live after the endpoint recovers.
            if body.get('reason') == 'transport outage threshold reached':
                continue
            done.add(path.parent.name)
    return done


def spent_calls(source):
    """Count current journal attempts plus attempts inherited from earlier interrupted runs."""
    total = 0
    for path in Path(source).glob('runs/*/events.jsonl'):
        try:
            total += sum(event.get('kind') == 'model_attempt' for event in Journal.read(path))
        except ValueError as error:
            raise CampaignIneligible(f'cannot count calls in corrupt journal {path.name}') from error
    manifest = _read_manifest(source)
    if type(manifest.get('claim_context_reconstructed')) is not bool:
        raise CampaignIneligible('resume source has no task-context recovery status')
    inherited = manifest.get('resumed_task_accounting', {})
    if not isinstance(inherited, dict):
        raise CampaignIneligible('inherited resume call accounting is malformed')
    for account in inherited.values():
        if (not isinstance(account, dict) or type(account.get('attempts')) is not int
                or account['attempts'] < 0):
            raise CampaignIneligible('inherited resume call accounting is malformed')
        total += account['attempts']
    return total


def _campaign_manifest(stream, arm, budget, max_executions, model_identity, *,
                       call_ceiling=None, calls_spent_before=0, resume_source_digest=None,
                       resumed_task_accounting=None, reply_repair=False,
                       executor_contract=None, claim_context_reconstructed=True,
                       profile=CAMPAIGN_PROFILE):
    if type(reply_repair) is not bool:
        raise ValueError('reply_repair must be bool')
    if profile not in PROFILES:
        raise ValueError('unknown campaign profile')
    options = {'profile': profile,
               'parse_mode': 'strict', 'normalize': reply_repair,
               'automatic_submission': reply_repair,
               'continue_transport_failures': True,
               'arm_outage_limit': ARM_OUTAGE_LIMIT}
    if not isinstance(executor_contract, dict):
        raise ValueError('executor campaign contract is required')
    contract_sha256 = _campaign_contract_digest(model_identity, budget, max_executions,
                                                options, call_ceiling, executor_contract)
    return {'format': CAMPAIGN_FORMAT, 'arm': arm,
            'tasks': [task['id'] for task in stream],
            'task_manifest_sha256': _task_manifest_digest(stream),
            'budget': {'model_calls': budget.model_calls, 'tool_calls': budget.tool_calls},
            'max_executions': max_executions, 'model_identity': model_identity,
            'options': options, 'campaign_call_ceiling': call_ceiling,
            'executor_contract': executor_contract,
            'campaign_contract_sha256': contract_sha256,
            'calls_spent_before_start': calls_spent_before,
            'resume_source_manifest_sha256': resume_source_digest,
            'resumed_task_accounting': resumed_task_accounting or {},
            'claim_context_reconstructed': claim_context_reconstructed}


def _read_manifest(root):
    path = Path(root) / 'campaign.json'
    try:
        value = _json_loads(path.read_text())
    except (OSError, ValueError) as error:
        raise CampaignIneligible('campaign manifest is missing or malformed') from error
    if not isinstance(value, dict):
        raise CampaignIneligible('campaign manifest must be an object')
    return value


def _journal_index(root, task_ids):
    root = Path(root)
    expected = set(task_ids)
    paths = sorted(root.glob('runs/*/events.jsonl'))
    indexed = {}
    for path in paths:
        task_id = path.parent.name
        if task_id not in expected or task_id in indexed:
            raise CampaignIneligible('campaign contains an unexpected or duplicate task journal')
        try:
            events = Journal.read(path)
        except ValueError as error:
            raise CampaignIneligible(f'campaign journal for {task_id} is corrupt') from error
        if not events:
            raise CampaignIneligible(f'campaign journal for {task_id} is empty')
        indexed[task_id] = events
    return indexed


def _validate_resume_source(source, stream, arm, budget, max_executions, model_identity,
                            reply_repair, executor_contract, profile=CAMPAIGN_PROFILE):
    source = Path(source)
    manifest = _read_manifest(source)
    if type(manifest.get('claim_context_reconstructed')) is not bool:
        raise CampaignIneligible('resume source has no task-context recovery status')
    expected = _campaign_manifest(stream, arm, budget, max_executions, model_identity,
                                  call_ceiling=manifest.get('campaign_call_ceiling'),
                                  reply_repair=reply_repair,
                                  executor_contract=executor_contract, profile=profile)
    for field in ('format', 'arm', 'tasks', 'task_manifest_sha256', 'budget',
                  'max_executions', 'model_identity', 'options', 'executor_contract',
                  'campaign_contract_sha256'):
        if manifest.get(field) != expected[field]:
            raise CampaignIneligible(f'resume source campaign binding differs: {field}')
    task_ids = expected['tasks']
    inherited_accounting = manifest.get('resumed_task_accounting', {})
    if (not isinstance(inherited_accounting, dict)
            or set(inherited_accounting) - set(task_ids)):
        raise CampaignIneligible('inherited resume accounting is malformed')
    journals = _journal_index(source, task_ids)
    done = finished_tasks(source)
    ordered_done = [task_id for task_id in task_ids if task_id in done]
    if ordered_done != task_ids[:len(ordered_done)]:
        raise CampaignIneligible('finished resume tasks are not a contiguous stream prefix')
    # Journals after the finished prefix may include at most one partial task.
    if len(journals) > len(ordered_done) + 1:
        raise CampaignIneligible('resume source contains journals after the first unfinished task')
    expected_journal_ids = task_ids[:len(journals)]
    if sorted(journals, key=task_ids.index) != expected_journal_ids:
        raise CampaignIneligible('resume journals are not a contiguous task prefix')
    consecutive_transport_failures = 0
    for task_id in task_ids:
        if task_id not in journals:
            break
        events = journals[task_id]
        prior = inherited_accounting.get(task_id, {})
        if not isinstance(prior, dict):
            raise CampaignIneligible('inherited task accounting is malformed')
        prior_attempts = prior.get('attempts', 0)
        prior_tools = prior.get('tool_calls', 0)
        prior_executions = prior.get('execution_attempts', 0)
        prior_trailing_failures = prior.get('trailing_transport_failures', 0)
        if any(type(value) is not int or value < 0
               for value in (prior_attempts, prior_tools, prior_executions, prior_trailing_failures)):
            raise CampaignIneligible('inherited task limits are malformed')
        expected_runtime_options = {'normalize': reply_repair,
                                    'automatic_submission': reply_repair,
                                    'continue_transport_failures': True,
                                    'outage_limit': max(1, ARM_OUTAGE_LIMIT - consecutive_transport_failures
                                                        - prior_trailing_failures)}
        task_events = [event for event in events if event.get('kind') == 'program_task']
        expected_task_executions = max(0, max_executions - prior_executions)
        if (len(task_events) != 1 or task_events[0].get('body', {}).get('task') != task_id
                or task_events[0].get('body', {}).get('arm') != arm
                or task_events[0].get('body', {}).get('execution_limit') != expected_task_executions):
            raise CampaignIneligible(f'resume journal identity mismatch for {task_id}')
        starts = [event.get('body', {}) for event in events if event.get('kind') == 'start']
        start = starts[0] if len(starts) == 1 else {}
        start_budget = start.get('budget')
        if (len(starts) != 1 or start.get('model') != model_identity
                or not isinstance(start_budget, dict)
                or set(start_budget) != {'model_calls', 'tool_calls'}
                or type(start_budget.get('model_calls')) is not int
                or type(start_budget.get('tool_calls')) is not int
                or not 0 <= start_budget['model_calls'] <= max(0, budget.model_calls - prior_attempts)
                or start_budget.get('tool_calls') != max(0, budget.tool_calls - prior_tools)
                or not isinstance(start.get('runtime_options'), dict)
                or set(start['runtime_options']) != set(expected_runtime_options)
                or any(type(start['runtime_options'].get(key)) is not type(value)
                       for key, value in expected_runtime_options.items())
                or start.get('runtime_options') != expected_runtime_options):
            raise CampaignIneligible(f'resume journal model/budget/options mismatch for {task_id}')
        consecutive_transport_failures = _trailing_transport_failures(
            events, consecutive_transport_failures + prior_trailing_failures)
    rows_path = source / 'rows.jsonl'
    try:
        source_rows = [_json_loads(line) for line in rows_path.read_text().splitlines()]
    except (OSError, ValueError) as error:
        raise CampaignIneligible('resume rows are missing or malformed') from error
    if any(not isinstance(row, dict) for row in source_rows):
        raise CampaignIneligible('resume rows must be JSON objects')
    row_ids = [row.get('task') for row in source_rows]
    if row_ids[:len(ordered_done)] != ordered_done:
        raise CampaignIneligible('resume rows do not match the finished journal prefix')
    marker_task = None
    if len(row_ids) == len(ordered_done) + 1:
        marker = source_rows[-1]
        expected_task = task_ids[len(ordered_done)] if len(ordered_done) < len(task_ids) else None
        if (marker.get('task') != expected_task
                or marker.get('status') not in ('transport_outage', 'ceiling_reached')):
            raise CampaignIneligible('resume has an unexpected unfinished-task row')
        marker_task = expected_task
    elif len(row_ids) != len(ordered_done):
        raise CampaignIneligible('resume rows do not match the finished task prefix')
    prior_accounting = dict(manifest.get('resumed_task_accounting', {}))
    if any(not isinstance(account, dict) for account in prior_accounting.values()):
        raise CampaignIneligible('inherited resume accounting is malformed')
    partial_ids = [task_id for task_id in journals if task_id not in done]
    if marker_task and marker_task in journals and marker_task not in partial_ids:
        partial_ids.append(marker_task)
    if len(partial_ids) > 1:
        raise CampaignIneligible('resume source has multiple unfinished task journals')
    if partial_ids:
        task_id = partial_ids[0]
        prior_accounting[task_id] = _combine_accounting(
            _account_attempts(journals[task_id]), prior_accounting.get(task_id))
    return (_manifest_digest(manifest), spent_calls(source), ordered_done, prior_accounting,
            bool(partial_ids))


def _combine_accounting(current, prior=None):
    prior = prior or {}
    attempts = current['attempts'] + prior.get('attempts', 0)
    replies = current['replies'] + prior.get('replies', 0)
    # A resume record written before C3-b has no 'returned': use its attempts less its transport
    # failures. That can overcount by one call when a non-transport model error ended the segment.
    prior_returned = prior.get('returned', prior.get('attempts', 0)
                               - sum(prior.get('transport_errors', {}).values()))
    returned = current.get('returned', current['replies']) + prior_returned
    tool_calls = current.get('tool_calls', 0) + prior.get('tool_calls', 0)
    execution_attempts = current.get('execution_attempts', 0) + prior.get('execution_attempts', 0)
    has_reply = current.get('has_model_reply_event', False)
    trailing_failures = (current.get('trailing_transport_failures', 0) if has_reply
                         else prior.get('trailing_transport_failures', 0)
                         + current.get('trailing_transport_failures', 0))
    known_input = current['known_input_tokens'] + prior.get('known_input_tokens', 0)
    known_output = current['known_output_tokens'] + prior.get('known_output_tokens', 0)
    unknown = current['unknown_usage_calls'] + prior.get('unknown_usage_calls', 0)
    unknown_input = (current.get('unknown_input_calls', current['unknown_usage_calls'])
                     + prior.get('unknown_input_calls', prior.get('unknown_usage_calls', 0)))
    unknown_output = (current.get('unknown_output_calls', current['unknown_usage_calls'])
                      + prior.get('unknown_output_calls', prior.get('unknown_usage_calls', 0)))
    errors = dict(current['transport_errors'])
    for key, value in prior.get('transport_errors', {}).items():
        errors[key] = errors.get(key, 0) + value
    return {'attempts': attempts, 'replies': replies, 'returned': returned,
            'tool_calls': tool_calls, 'execution_attempts': execution_attempts,
            'input_tokens': known_input if unknown_input == 0 else None,
            'output_tokens': known_output if unknown_output == 0 else None,
            'known_input_tokens': known_input, 'known_output_tokens': known_output,
            'unknown_input_calls': unknown_input, 'unknown_output_calls': unknown_output,
            'unknown_usage_calls': unknown, 'usage_complete': unknown == 0,
            'trailing_transport_failures': trailing_failures,
            'has_model_reply_event': has_reply or prior.get('has_model_reply_event', False),
            'transport_errors': errors}


def _reply_gate_valid(replies, attempts):
    return (type(replies) is int and type(attempts) is int and attempts > 0
            and 0 <= replies <= attempts and replies * 10 >= attempts * 9)


def _read_rows(path):
    try:
        rows = [_json_loads(line) for line in path.read_text().splitlines()]
    except (OSError, ValueError) as error:
        raise CampaignIneligible('campaign rows are missing or malformed') from error
    if any(not isinstance(row, dict) for row in rows):
        raise CampaignIneligible('each campaign row must be an object')
    return rows


def _validate_scoring_campaign(out, stream, rows):
    manifest = _read_manifest(out)
    if manifest.get('format') != CAMPAIGN_FORMAT:
        return {'eligible': False,
                'diagnostics': ['legacy campaign manifest has no verified C1-d reply gate'],
                'manifest': manifest, 'digest': _manifest_digest(manifest),
                'accounts': {}}
    task_ids = [task['id'] for task in stream]
    expected_budget = manifest.get('budget')
    options = manifest.get('options')
    if (manifest.get('arm') not in ARMS or manifest.get('tasks') != task_ids
            or manifest.get('task_manifest_sha256') != _task_manifest_digest(stream)
            or not isinstance(expected_budget, dict)
            or set(expected_budget) != {'model_calls', 'tool_calls'}
            or any(type(expected_budget.get(key)) is not int or expected_budget[key] < 0
                   for key in ('model_calls', 'tool_calls'))
            or type(manifest.get('max_executions')) is not int or manifest['max_executions'] < 0
            or not isinstance(manifest.get('model_identity'), dict)
            or not isinstance(options, dict)
            or not isinstance(manifest.get('executor_contract'), dict)
            or not isinstance(options.get('profile'), str)
            or options.get('profile') not in PROFILES
            or options != {
                'profile': options.get('profile'),
                'parse_mode': 'strict',
                'normalize': options.get('normalize'),
                'automatic_submission': options.get('automatic_submission'),
                'continue_transport_failures': True,
                'arm_outage_limit': ARM_OUTAGE_LIMIT}):
        raise CampaignIneligible('campaign manifest does not match the scored stream or runtime contract')
    if (type(options.get('normalize')) is not bool
            or type(options.get('automatic_submission')) is not bool
            or options['normalize'] != options['automatic_submission']):
        raise CampaignIneligible('reply-repair options are not a supported paired configuration')
    if type(manifest.get('claim_context_reconstructed')) is not bool:
        raise CampaignIneligible('campaign has no task-context recovery status')
    expected_contract = _campaign_contract_digest(
        manifest['model_identity'], Budget(**expected_budget), manifest['max_executions'], options,
        manifest.get('campaign_call_ceiling'), manifest['executor_contract'])
    if manifest.get('campaign_contract_sha256') != expected_contract:
        raise CampaignIneligible('campaign model, budget, options, or executor contract hash is invalid')
    if len(rows) != len(task_ids) or [row.get('task') for row in rows] != task_ids:
        raise CampaignIneligible('campaign row count/order does not match the full planned stream')
    indexed = _journal_index(out, task_ids)
    if set(indexed) != set(task_ids):
        raise CampaignIneligible('campaign is incomplete: every planned task needs one intact journal')
    prior_accounts = manifest.get('resumed_task_accounting', {})
    if not isinstance(prior_accounts, dict) or set(prior_accounts) - set(task_ids):
        raise CampaignIneligible('resume accounting is malformed')
    for account in prior_accounts.values():
        if (not isinstance(account, dict)
                or any(type(account.get(key)) is not int or account[key] < 0
                       for key in ('attempts', 'replies', 'tool_calls', 'execution_attempts',
                                   'known_input_tokens',
                                   'known_output_tokens', 'unknown_usage_calls'))
                or account['replies'] > account['attempts']
                or not isinstance(account.get('transport_errors'), dict)):
            raise CampaignIneligible('resumed task accounting is malformed')
        for dimension in ('unknown_input_calls', 'unknown_output_calls'):
            if dimension in account and (type(account[dimension]) is not int
                                         or not 0 <= account[dimension] <= account['attempts']):
                raise CampaignIneligible('resumed dimension-specific usage accounting is malformed')
        if ('trailing_transport_failures' in account
                and (type(account['trailing_transport_failures']) is not int
                     or not 0 <= account['trailing_transport_failures'] <= ARM_OUTAGE_LIMIT)):
            raise CampaignIneligible('resumed transport-streak accounting is malformed')
        if (max(account.get('unknown_input_calls', account['unknown_usage_calls']),
                account.get('unknown_output_calls', account['unknown_usage_calls']))
                > account['unknown_usage_calls']):
            raise CampaignIneligible('resumed unknown-usage dimensions exceed incomplete usage calls')
    accounts = {}
    total_attempts = total_replies = total_returned = 0
    consecutive_transport_failures = 0
    for task_id, row in zip(task_ids, rows):
        events = indexed[task_id]
        if events[-1].get('kind') != 'finish':
            raise CampaignIneligible(f'task {task_id} did not finish')
        finish = events[-1].get('body', {})
        if finish.get('reason') == 'transport outage threshold reached':
            raise CampaignIneligible('C1-d outage-stopped arm is incomplete; resume before scoring')
        if row.get('status') != finish.get('status'):
            raise CampaignIneligible(f'row status differs from task {task_id} journal')
        task_events = [event for event in events if event.get('kind') == 'program_task']
        if (len(task_events) != 1 or task_events[0].get('body', {}).get('task') != task_id
                or task_events[0].get('body', {}).get('arm') != manifest['arm']):
            raise CampaignIneligible(f'task {task_id} journal identity mismatch')
        starts = [event.get('body', {}) for event in events if event.get('kind') == 'start']
        start = starts[0] if len(starts) == 1 else {}
        start_budget = start.get('budget')
        runtime_options = start.get('runtime_options')
        prior = prior_accounts.get(task_id, {})
        prior_attempts = prior.get('attempts', 0)
        prior_tools = prior.get('tool_calls', 0)
        prior_executions = prior.get('execution_attempts', 0)
        prior_trailing_failures = prior.get('trailing_transport_failures', 0)
        if any(type(value) is not int or value < 0
               for value in (prior_attempts, prior_tools, prior_executions, prior_trailing_failures)):
            raise CampaignIneligible('resumed task limits are malformed')
        expected_execution_limit = max(0, manifest['max_executions'] - prior_executions)
        if (len(starts) != 1 or start.get('model') != manifest['model_identity']
                or not isinstance(start_budget, dict)
                or set(start_budget) != {'model_calls', 'tool_calls'}
                or type(start_budget.get('model_calls')) is not int
                or type(start_budget.get('tool_calls')) is not int
                or not 0 <= start_budget['model_calls'] <= max(0, expected_budget['model_calls'] - prior_attempts)
                or start_budget.get('tool_calls') != max(0, expected_budget['tool_calls'] - prior_tools)
                or not isinstance(runtime_options, dict)
                or set(runtime_options) != {'normalize', 'automatic_submission',
                                            'continue_transport_failures', 'outage_limit'}
                or type(runtime_options.get('normalize')) is not bool
                or type(runtime_options.get('automatic_submission')) is not bool
                or runtime_options.get('normalize') != options['normalize']
                or runtime_options.get('automatic_submission') != options['automatic_submission']
                or runtime_options.get('continue_transport_failures') is not True
                or type(runtime_options.get('outage_limit')) is not int
                or runtime_options['outage_limit'] != max(
                    1, ARM_OUTAGE_LIMIT - consecutive_transport_failures
                    - prior_trailing_failures)):
            raise CampaignIneligible(f'task {task_id} journal model/budget/options mismatch')
        if task_events[0].get('body', {}).get('execution_limit') != expected_execution_limit:
            raise CampaignIneligible(f'task {task_id} execution budget reset or changed')
        account = _combine_accounting(_account_attempts(events), prior_accounts.get(task_id))
        if (account['attempts'] > expected_budget['model_calls']
                or account['tool_calls'] > expected_budget['tool_calls']
                or account['execution_attempts'] > manifest['max_executions']):
            raise CampaignIneligible(f'task {task_id} exceeded its cumulative task budget')
        if (row.get('journal_sha256') != events[-1].get('hash')
                or row.get('campaign_arm') != manifest['arm']
                or row.get('task_manifest_sha256') != manifest['task_manifest_sha256']
                or row.get('campaign_manifest_sha256') != _manifest_digest(manifest)
                or row.get('campaign_contract_sha256') != manifest['campaign_contract_sha256']
                or row.get('model_calls') != account['attempts']
                or row.get('model_replies') != account['replies']
                or row.get('tool_calls') != account['tool_calls']
                or row.get('execution_attempts') != account['execution_attempts']):
            raise CampaignIneligible(f'task {task_id} row accounting differs from journal')
        accounts[task_id] = account
        total_attempts += account['attempts']
        total_replies += account['replies']
        total_returned += account['returned']
        consecutive_transport_failures = _trailing_transport_failures(
            events, consecutive_transport_failures + prior_trailing_failures)
    if total_attempts <= 0:
        raise CampaignIneligible('campaign has no model attempts; C1 reply gate is undefined')
    # Amendment C3-b: C3's G2 counts calls that returned a reply (C1-d's written definition), so a
    # malformed action the model sent is a spent call, not a transport loss. C1/C2 keep their count.
    c3 = options['profile'] == C3_PROFILE
    gate_replies = total_returned if c3 else total_replies
    if not _reply_gate_valid(gate_replies, total_attempts):
        raise CampaignIneligible(
            f'{"C3 G2" if c3 else "C1-d"} reply-validity gate failed: '
            f'{gate_replies}/{total_attempts} replies, need at least 90%')
    ceiling = manifest.get('campaign_call_ceiling')
    if type(ceiling) is not int or ceiling < 1 or total_attempts > ceiling:
        raise CampaignIneligible('campaign call ceiling is missing or exceeded')
    context_reconstructed = manifest['claim_context_reconstructed']
    diagnostics = ([] if context_reconstructed else
                   ['an interrupted task was restarted without replaying its prior actions/memory; descriptive only'])
    return {'eligible': context_reconstructed, 'diagnostics': diagnostics, 'format_v2': True,
            'manifest': manifest,
            'digest': _manifest_digest(manifest), 'accounts': accounts,
            'attempts': total_attempts, 'replies': total_replies,
            'reply_fraction': total_replies / total_attempts,
            'returned_replies': total_returned}


def run_campaign(stream, out, arm, model, *, budget, executor, max_executions=64, replay_from=None,
                 resume_from=None, reply_repair=False, profile=CAMPAIGN_PROFILE):
    """Run ``stream`` in order. ``model`` is a Ceiling-wrapped port, or None with ``replay_from``.

    ``resume_from`` (after a container restart): tasks whose journal there is finished are
    re-executed from that journal with no model call, the stream order and memory are rebuilt
    exactly, and the first unfinished task and everything after it run live.
    """
    if arm not in ARMS:
        raise ValueError('unknown arm')
    _validate_task_ids(stream)
    if type(reply_repair) is not bool:
        raise ValueError('reply_repair must be bool')
    if profile not in PROFILES:
        raise ValueError('unknown campaign profile')
    tool_profile = PROFILES[profile]
    if resume_from and replay_from:
        raise ValueError('resume_from and replay_from are mutually exclusive')
    model_identity = getattr(model, 'identity', None) if model is not None else None
    executor_contract = _executor_contract(executor)
    resume_source_digest = None
    calls_spent_before = 0
    resumed_task_accounting = {}
    claim_context_reconstructed = True
    resumed = set()
    if resume_from:
        source_manifest = _read_manifest(resume_from)
        if getattr(model, 'limit', None) != source_manifest.get('campaign_call_ceiling'):
            raise CampaignIneligible('resume source campaign call ceiling changed')
        if model_identity is None:
            model_identity = source_manifest.get('model_identity')
        (resume_source_digest, calls_spent_before, ordered_done, resumed_task_accounting,
         had_partial_task) = _validate_resume_source(
            resume_from, stream, arm, budget, max_executions, model_identity, reply_repair,
            executor_contract, profile)
        resumed = set(ordered_done)
        claim_context_reconstructed = (source_manifest['claim_context_reconstructed']
                                       and not had_partial_task)
    if replay_from:
        source_manifest = _read_manifest(replay_from)
        if model_identity is None:
            model_identity = source_manifest.get('model_identity')
        (replay_source_digest, replay_calls_spent, replay_done, resumed_task_accounting,
         had_partial_task) = _validate_resume_source(
            replay_from, stream, arm, budget, max_executions, model_identity, reply_repair,
            executor_contract, profile)
        if replay_done != [task['id'] for task in stream]:
            raise CampaignIneligible('replay source is not a complete campaign')
        if source_manifest.get('format') != CAMPAIGN_FORMAT:
            raise CampaignIneligible('legacy campaign cannot produce a C1-identical replay')
        resume_source_digest = replay_source_digest
        calls_spent_before = replay_calls_spent
        claim_context_reconstructed = (source_manifest['claim_context_reconstructed']
                                       and not had_partial_task)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    call_ceiling = (getattr(model, 'limit', None) if model is not None
                    else source_manifest.get('campaign_call_ceiling') if replay_from else None)
    manifest = _campaign_manifest(stream, arm, budget, max_executions, model_identity,
                                  call_ceiling=call_ceiling,
                                  calls_spent_before=calls_spent_before,
                                  resume_source_digest=resume_source_digest,
                                  resumed_task_accounting=resumed_task_accounting,
                                  reply_repair=reply_repair,
                                  executor_contract=executor_contract,
                                  claim_context_reconstructed=claim_context_reconstructed,
                                  profile=profile)
    (out / 'campaign.json').write_text(canonical(manifest) + '\n')
    rows = []
    consecutive_transport_failures = 0
    for index, task in enumerate(stream):
        memory_path = out / 'memory' if arm == 'KUTTAKA' else out / 'memory' / task['id']
        journal_path = out / 'runs' / task['id'] / 'events.jsonl'
        if replay_from:
            proposer = ReplayModel(Path(replay_from) / 'runs' / task['id'] / 'events.jsonl')
        elif task['id'] in resumed:
            proposer = ReplayModel(Path(resume_from) / 'runs' / task['id'] / 'events.jsonl')
        else:
            resumed = set()  # once live, every later task is live
            proposer = model
        prior_task_accounting = resumed_task_accounting.get(task['id'], {})
        task_outage_limit = max(1, ARM_OUTAGE_LIMIT - consecutive_transport_failures
                                - prior_task_accounting.get('trailing_transport_failures', 0))
        task_budget = budget
        if replay_from or task['id'] in (resumed if resume_from else set()):
            journal_source = replay_from if replay_from else resume_from
            source_events = Journal.read(Path(journal_source) / 'runs' / task['id'] / 'events.jsonl')
            source_starts = [event.get('body', {}) for event in source_events
                             if event.get('kind') == 'start']
            if len(source_starts) != 1 or not isinstance(source_starts[0].get('budget'), dict):
                raise CampaignIneligible(f'replay source has no unique task budget for {task["id"]}')
            task_budget = Budget(**source_starts[0]['budget'])
            source_runtime_options = source_starts[0].get('runtime_options')
            if (not isinstance(source_runtime_options, dict)
                    or type(source_runtime_options.get('outage_limit')) is not int):
                raise CampaignIneligible(f'replay source has no outage budget for {task["id"]}')
            task_outage_limit = source_runtime_options['outage_limit']
        elif isinstance(proposer, Ceiling):
            prior = resumed_task_accounting.get(task['id'], {})
            remaining_task_model = max(0, budget.model_calls - prior.get('attempts', 0))
            remaining_task_tools = max(0, budget.tool_calls - prior.get('tool_calls', 0))
            remaining = max(0, proposer.limit - proposer.used)
            if remaining == 0:
                rows.append({'task': task['id'], 'status': 'ceiling_reached', 'model_calls': 0,
                             'model_replies': 0, 'campaign_arm': arm})
                (out / 'rows.jsonl').write_text(''.join(canonical(r) + '\n' for r in rows))
                break
            task_budget = Budget(model_calls=min(remaining_task_model, remaining),
                                 tool_calls=remaining_task_tools)
        elif proposer is not None:
            prior = resumed_task_accounting.get(task['id'], {})
            task_budget = Budget(model_calls=max(0, budget.model_calls - prior.get('attempts', 0)),
                                 tool_calls=max(0, budget.tool_calls - prior.get('tool_calls', 0)))
        task_execution_limit = max(0, max_executions - prior_task_accounting.get('execution_attempts', 0))
        if replay_from or task['id'] in (resumed if resume_from else set()):
            source_events = Journal.read(Path(journal_source) / 'runs' / task['id'] / 'events.jsonl')
            source_task_events = [event.get('body', {}) for event in source_events
                                  if event.get('kind') == 'program_task']
            if len(source_task_events) != 1:
                raise CampaignIneligible(f'replay source has no unique task record for {task["id"]}')
            task_execution_limit = source_task_events[0].get('execution_limit')
            if type(task_execution_limit) is not int or task_execution_limit < 0:
                raise CampaignIneligible(f'replay source has no execution budget for {task["id"]}')
        program = ProgramTask.from_dict(_manifest(task))
        with Journal(journal_path) as journal, ArtifactMemory(memory_path) as memory:
            if tool_profile == 'c3':
                created_before = set(memory.ids('method')) if index else set()  # no 100-entry cap
            else:
                created_before = set(a['id'] for a in memory.catalog(kind='method', limit=100)) if index else set()
            provenance = {'producer': proposer.identity.get('model'), 'campaign_arm': arm, 'task': task['id']}
            tools = ProgramTools(program, memory, journal, executor,
                                 max_executions=task_execution_limit, provenance=provenance,
                                 profile=tool_profile)
            journal.append('program_task', {'request': program.request, 'scope': program.scope_id,
                                            'task': task['id'], 'arm': arm,
                                            'execution_limit': task_execution_limit})
            # Strict parsing is the default. ``reply_repair`` is explicit and version-bound.
            # Continue within a task only up to the remaining arm-wide outage threshold.
            result = investigate(program.request, proposer, tools.tools(), tools.checker, journal, task_budget,
                                 checker_feedback=tools.checker_feedback,
                                 normalize=reply_repair,
                                 automatic_submission=reply_repair,
                                 continue_transport_failures=True,
                                 outage_limit=task_outage_limit)
            if getattr(proposer, 'reached', False):
                # The run loop records the refused call as a model error; the task is not scored.
                rows.append({'task': task['id'], 'status': 'ceiling_reached'})
                (out / 'rows.jsonl').write_text(''.join(canonical(r) + '\n' for r in rows))
                break
            row = _row(task, journal_path, result, memory, created_before, tool_profile)
            account = _combine_accounting(_account_attempts(Journal.read(journal_path)),
                                          prior_task_accounting)
            row.update({'model_calls': account['attempts'], 'model_replies': account['replies'],
                        'tool_calls': account['tool_calls'],
                        'execution_attempts': account['execution_attempts'],
                        'input_tokens': account['input_tokens'], 'output_tokens': account['output_tokens'],
                        'known_input_tokens': account['known_input_tokens'],
                        'known_output_tokens': account['known_output_tokens'],
                        'unknown_input_calls': account['unknown_input_calls'],
                        'unknown_output_calls': account['unknown_output_calls'],
                        'unknown_usage_calls': account['unknown_usage_calls'],
                        'usage_complete': account['usage_complete'],
                        'transport_errors': account['transport_errors']})
            row.update({'campaign_arm': arm,
                        'task_manifest_sha256': manifest['task_manifest_sha256'],
                        'campaign_contract_sha256': manifest['campaign_contract_sha256'],
                        'campaign_manifest_sha256': _manifest_digest(manifest)})
            if result.reason == 'transport outage threshold reached':
                row['status'] = 'transport_outage'
                row['diagnostic'] = 'arm stopped after configured consecutive transport failures'
            rows.append(row)
        (out / 'rows.jsonl').write_text(''.join(canonical(r) + '\n' for r in rows))
        print(canonical(rows[-1]), flush=True)
        events = Journal.read(journal_path)
        consecutive_transport_failures = _trailing_transport_failures(
            events, consecutive_transport_failures
            + prior_task_accounting.get('trailing_transport_failures', 0))
        if rows[-1]['status'] == 'transport_outage':
            break
    return rows


def score_campaign(out, stream, holdout, executor):
    """Execute each delivered method on evaluator-held inputs; never used during a run."""
    out = Path(out)
    rows = _read_rows(out / 'rows.jsonl')
    validation = _validate_scoring_campaign(out, stream, rows)
    if not isinstance(holdout, dict):
        raise CampaignIneligible('holdout must map task ids to case lists')
    if validation.get('format_v2'):
        task_ids = [task['id'] for task in stream]
        if set(holdout) != set(task_ids):
            raise CampaignIneligible('C1 holdout keys must match the complete task stream')
        for task_id in task_ids:
            cases = holdout[task_id]
            if (not isinstance(cases, list) or len(cases) < 5
                    or any(not isinstance(case, dict) or set(case) != {'input', 'expected'}
                           for case in cases)):
                raise CampaignIneligible(f'C1 holdout for {task_id} must contain at least five input/expected cases')
    holdout_sha = hashlib.sha256(canonical(holdout).encode('ascii')).hexdigest()
    score_executor_contract = _executor_contract(executor)
    by_id = {t['id']: t for t in stream}
    scored = []
    for row in rows:
        task_id = row.get('task')
        if task_id not in by_id:
            raise CampaignIneligible('score row references a task outside the supplied stream')
        account = validation['accounts'].get(task_id)
        entry = dict(row, holdout_passed=False, unsupported=False,
                     campaign_eligibility={'eligible': validation['eligible'],
                         'diagnostics': list(validation['diagnostics']),
                         'planned_tasks': [task['id'] for task in stream],
                         'reply_attempts': validation.get('attempts'),
                         'reply_count': validation.get('replies'),
                         'reply_fraction': validation.get('reply_fraction')})
        if validation.get('manifest', {}).get('options', {}).get('profile') == C3_PROFILE:
            entry['campaign_eligibility']['returned_replies'] = validation.get('returned_replies')
        if account is not None:
            entry.update({'model_calls': account['attempts'], 'model_replies': account['replies'],
                          'input_tokens': account['input_tokens'], 'output_tokens': account['output_tokens'],
                          'known_input_tokens': account['known_input_tokens'],
                          'known_output_tokens': account['known_output_tokens'],
                          'unknown_input_calls': account['unknown_input_calls'],
                          'unknown_output_calls': account['unknown_output_calls'],
                          'unknown_usage_calls': account['unknown_usage_calls'],
                          'usage_complete': account['usage_complete'],
                          'transport_errors': account['transport_errors']})
        campaign_contract_sha = validation['manifest'].get('campaign_contract_sha256')
        score_contract_sha = _manifest_digest({'campaign_contract_sha256': campaign_contract_sha,
                                               'holdout_sha256': holdout_sha,
                                               'score_executor_contract': score_executor_contract})
        entry.update({'campaign_contract_sha256': campaign_contract_sha,
                      'holdout_sha256': holdout_sha,
                      'score_executor_contract': score_executor_contract,
                      'score_contract_sha256': score_contract_sha})
        cases = holdout.get(row['task'], [])
        if row.get('status') == 'checked' and cases:
            arm = validation['manifest'].get('arm')
            memory_path = out / 'memory' if arm == 'KUTTAKA' else out / 'memory' / row['task']
            scratch = out / 'score-scratch' / row['task']
            shutil.rmtree(scratch, ignore_errors=True)
            shutil.copytree(memory_path, scratch, ignore=shutil.ignore_patterns('.writer.lock'))
            with ArtifactMemory(scratch) as memory:
                order = _closure(memory, row['artifact'])
                libraries = {a: memory.get(a)['body']['source'] for a in order[:-1]
                             if memory.get(a)['kind'] == 'method'}
                source = memory.get(row['artifact'])['body']['source']
            shutil.rmtree(scratch, ignore_errors=True)
            passed = 0
            for case in cases:
                job = {'source': source, 'input': case['input']}
                if libraries:
                    job['libraries'] = libraries
                try:
                    response = executor(job)
                except Exception as error:
                    response = {'status': 'executor_error', 'error_type': type(error).__name__}
                ok = False
                if (isinstance(response, dict) and response.get('status') == 'completed'
                        and type(response.get('exit_code')) is int and response['exit_code'] == 0
                        and isinstance(response.get('output'), str)):
                    try:
                        decoded = _json_loads(response['output'])
                        ok = (isinstance(decoded, dict) and 'result' in decoded
                              and canonical(decoded['result']) == canonical(case['expected']))
                    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
                        ok = False
                passed += ok
            entry['holdout_cases'] = len(cases)
            entry['holdout_correct'] = passed
            entry['holdout_passed'] = passed == len(cases)
            entry['unsupported'] = passed != len(cases)
        entry['in_stream'] = True
        scored.append(entry)
    (out / 'score.jsonl').write_text(''.join(canonical(r) + '\n' for r in scored))
    return scored


def mcnemar(a, b):
    """Exact one-sided test that ``a`` succeeds where ``b`` fails more often than the reverse."""
    wins = sum(x and not y for x, y in zip(a, b))
    losses = sum(y and not x for x, y in zip(a, b))
    n = wins + losses
    p = sum(comb(n, i) for i in range(wins, n + 1)) / 2 ** n if n else 1.0
    return {'wins': wins, 'losses': losses, 'p_one_sided': p}


def _row_ids(rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise CampaignIneligible('comparison inputs must be lists of score row objects')
    ids = [row.get('task') for row in rows]
    if any(not isinstance(task_id, str) for task_id in ids) or len(set(ids)) != len(ids):
        raise CampaignIneligible('comparison rows need unique string task ids')
    return ids


def compare(kuttaka_rows, alone_rows):
    """Compare two scored arms; incomplete or legacy inputs never receive claim eligibility."""
    k_ids, a_ids = _row_ids(kuttaka_rows), _row_ids(alone_rows)
    # Preserve supplied order so a different arm ordering cannot look like a valid pair.
    kuttaka_rows, alone_rows = list(kuttaka_rows), list(alone_rows)
    all_rows = kuttaka_rows + alone_rows
    v2 = bool(all_rows) and all(
        isinstance(row.get('campaign_manifest_sha256'), str)
        and isinstance(row.get('task_manifest_sha256'), str)
        and isinstance(row.get('campaign_contract_sha256'), str)
        and isinstance(row.get('score_contract_sha256'), str)
        and isinstance(row.get('holdout_sha256'), str)
        and isinstance(row.get('score_executor_contract'), dict)
        and isinstance(row.get('campaign_arm'), str) for row in all_rows)
    diagnostics = []
    if v2:
        expected_task_lists = [gate.get('planned_tasks') for row in all_rows
                               if isinstance((gate := row.get('campaign_eligibility')), dict)]
        if (not k_ids or k_ids != a_ids or len(expected_task_lists) != len(all_rows)
                or not expected_task_lists
                or any(task_list != expected_task_lists[0] for task_list in expected_task_lists)
                or expected_task_lists[0] != k_ids):
            raise CampaignIneligible('v2 comparison arms must contain the same complete task order')
        if (any(row.get('campaign_arm') != 'KUTTAKA' for row in kuttaka_rows)
                or any(row.get('campaign_arm') != 'MODEL_ALONE' for row in alone_rows)):
            raise CampaignIneligible('v2 comparison arm labels do not match their inputs')
        if (len({row['task_manifest_sha256'] for row in all_rows}) != 1
                or len({row['campaign_contract_sha256'] for row in all_rows}) != 1
                or len({row['score_contract_sha256'] for row in all_rows}) != 1
                or len({row['holdout_sha256'] for row in all_rows}) != 1
                or len({canonical(row['score_executor_contract']) for row in all_rows}) != 1
                or len({row['campaign_manifest_sha256'] for row in kuttaka_rows}) != 1
                or len({row['campaign_manifest_sha256'] for row in alone_rows}) != 1):
            raise CampaignIneligible('v2 comparison arms do not bind to one stream and one manifest each')
        for row in all_rows:
            expected_score_contract = _manifest_digest({
                'campaign_contract_sha256': row['campaign_contract_sha256'],
                'holdout_sha256': row['holdout_sha256'],
                'score_executor_contract': row['score_executor_contract']})
            if row['score_contract_sha256'] != expected_score_contract:
                raise CampaignIneligible('scored rows do not bind to the same holdout and scoring executor')
        gates = [row.get('campaign_eligibility') for row in all_rows]
        diagnostics = sorted({message for gate in gates if isinstance(gate, dict)
                              for message in gate.get('diagnostics', []) if isinstance(message, str)})
        claim_eligible = all(isinstance(gate, dict) and gate.get('eligible') is True
                             for gate in gates)
        if not claim_eligible and not diagnostics:
            diagnostics = ['one or both arms failed campaign eligibility']
    else:
        claim_eligible = False
        diagnostics = ['legacy or unverified score rows: descriptive comparison only']
        if set(k_ids) != set(a_ids):
            diagnostics.append('task sets differ; only the intersection is summarized')
    alone = {r['task']: r for r in alone_rows}
    paired = [(k, alone[k['task']]) for k in kuttaka_rows if k['task'] in alone]
    solved_k = [bool(k.get('holdout_passed')) for k, _ in paired]
    solved_a = [bool(a.get('holdout_passed')) for _, a in paired]
    both = [(k, a) for k, a in paired if k.get('holdout_passed') is True and a.get('holdout_passed') is True]
    usage_pairs = [(k, a) for k, a in both
                   if all(type(row.get(field)) is int and row[field] >= 0
                          for row in (k, a) for field in ('input_tokens', 'output_tokens'))]
    cheaper = sum(k['input_tokens'] + k['output_tokens'] < a['input_tokens'] + a['output_tokens']
                  for k, a in usage_pairs)
    dearer = sum(k['input_tokens'] + k['output_tokens'] > a['input_tokens'] + a['output_tokens']
                 for k, a in usage_pairs)
    n = cheaper + dearer
    seen, later = set(), []
    for k, _ in paired:
        if k.get('chain') in seen:
            later.append(k)
        seen.add(k.get('chain'))
    return {
        'tasks': len(paired),
        'claim_eligibility': {'eligible': claim_eligible, 'diagnostics': diagnostics},
        'solved': {'KUTTAKA': sum(solved_k), 'MODEL_ALONE': sum(solved_a)},
        'A1_solves_more': mcnemar(solved_k, solved_a),
        'A2_fewer_tokens_when_both_solve': {
            'cheaper': cheaper, 'dearer': dearer,
            'p_one_sided': sum(comb(n, i) for i in range(cheaper, n + 1)) / 2 ** n if n else None,
            'available_pairs': len(usage_pairs), 'unavailable_pairs': len(both) - len(usage_pairs),
            'available': bool(both) and len(usage_pairs) == len(both)},
        'A3_unsupported': {'KUTTAKA': sum(bool(k.get('unsupported')) for k, _ in paired),
                           'MODEL_ALONE': sum(bool(a.get('unsupported')) for _, a in paired)},
        'A4_cooperative_invocation_of_earlier_artifacts': {
            'reusing': sum(bool(k.get('executed_reused_artifacts')) for k in later
                           if k.get('holdout_passed') is True),
            'solved_later_in_chain': sum(k.get('holdout_passed') is True for k in later),
            'evidence': 'sandbox-reported cooperative invocation; not causal proof'},
        'model_calls': {'KUTTAKA': sum(row.get('model_calls', 0) for row, _ in paired),
                        'MODEL_ALONE': sum(row.get('model_calls', 0) for _, row in paired)},
    }


def _model(args, *, profile=CAMPAIGN_PROFILE):
    if profile not in PROFILES:
        raise ValueError('unsupported campaign model profile')
    import os
    extra = {'instructions': INSTRUCTIONS_C3} if profile == C3_PROFILE else {}
    # Amendments C2-c and C3-a: as C1 ran (ef81a25), use the managed network credential when no
    # key is in the environment, and accept a JSON action wrapped in prose or a code fence.
    c2 = profile in (C2_PROFILE, C3_PROFILE)
    return JsonModel(args.model, args.base_url, protocol='openai', key_env='NVIDIA_API_KEY',
                     timeout=args.model_timeout, max_tokens=args.max_tokens, stream=True,
                     reasoning_hint='low', sampling_fields=False,
                     parse_mode='extract' if c2 else 'strict',
                     network_secret=c2 and not os.environ.get('NVIDIA_API_KEY'), **extra)


def main(argv=None):
    p = argparse.ArgumentParser(prog='kuttaka-campaign', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    for name in ('run', 'replay', 'score', 'compare'):
        c = sub.add_parser(name)
        if name in ('run', 'replay', 'score'):
            c.add_argument('--stream', required=True)
            c.add_argument('--out', required=True)
        if name in ('run', 'replay'):
            c.add_argument('--arm', choices=ARMS, required=True)
            c.add_argument('--model-calls', type=int, default=6, help='per task')
            c.add_argument('--tool-calls', type=int, default=24, help='per task')
            c.add_argument('--execution-timeout', type=float, default=10)
            c.add_argument('--reply-repair', action='store_true',
                           help='explicit legacy C1 normalization/automatic-submission compatibility')
            c.add_argument('--profile', choices=tuple(CLI_PROFILES), default='c1',
                           help='c1 (default, C1 behaviour), c2 (C1 tools, replay-stable receipts, KUTTAKA-C2) '
                                'or c3 (opt-in reuse tools, KUTTAKA-C3)')
        if name == 'run':
            c.add_argument('--ceiling', type=int, required=True,
                           help='the campaign call ceiling the owner typed in this session')
            c.add_argument('--base-url', default='https://integrate.api.nvidia.com/v1')
            c.add_argument('--model', default='meta/muse-glimmer-30b')
            c.add_argument('--max-tokens', type=int, default=4096)
            c.add_argument('--model-timeout', type=float, default=600)
            c.add_argument('--resume-from', help='an interrupted run of this arm: finished tasks replay, the rest run live')
            c.add_argument('--reach-wait', type=float, default=0,
                           help='seconds to wait, before logging a call, while the network refuses TCP connections (C2)')
        if name == 'replay':
            c.add_argument('--source', required=True, help='finished campaign directory')
        if name == 'score':
            c.add_argument('--holdout', required=True, help='evaluator-held file: {task_id: [{input, expected}]}')
        if name == 'compare':
            c.add_argument('--kuttaka', required=True)
            c.add_argument('--alone', required=True)
    args = p.parse_args(argv)
    if args.cmd == 'compare':
        read = lambda d: _read_rows(Path(d) / 'score.jsonl')
        print(json.dumps(compare(read(args.kuttaka), read(args.alone)), indent=2))
        return 0
    if not preflight()['ready']:
        print('Docker or the pinned Python image is unavailable', file=sys.stderr)
        return 2
    # C3 runs (and their replays) use the distinct-call trace; scoring and C1/C2 use the default.
    trace = 'distinct' if getattr(args, 'profile', 'c1') == 'c3' else 'all'
    executor = PythonExecutor(timeout=getattr(args, 'execution_timeout', 10), trace=trace)
    stream = load_stream(args.stream)
    if args.cmd == 'score':
        holdout = _json_loads(Path(args.holdout).read_text())
        rows = score_campaign(args.out, stream, holdout, executor)
        print(json.dumps({'solved_on_holdout': sum(r['holdout_passed'] for r in rows),
                          'unsupported': sum(r['unsupported'] for r in rows), 'tasks': len(rows)}))
        return 0
    budget = Budget(model_calls=args.model_calls, tool_calls=args.tool_calls)
    if args.cmd == 'run':
        # Calls spent by an interrupted run count against the same ceiling.
        profile = CLI_PROFILES[args.profile]
        live = _model(args, profile=profile)
        reach = (lambda: tcp_reachable(args.base_url)) if args.reach_wait else None
        model = Ceiling(live, args.ceiling, used=spent_calls(args.resume_from) if args.resume_from else 0,
                        reach=reach, reach_wait=args.reach_wait)
        try:
            run_campaign(stream, args.out, args.arm, model, budget=budget, executor=executor,
                         resume_from=args.resume_from, reply_repair=args.reply_repair,
                         profile=profile)
        except TransportOutage as outage:
            print(json.dumps({'status': 'transport_outage', 'detail': str(outage), 'model_calls_used': model.used,
                              'unreachable_checks': model.unreachable_checks,
                              'resume': 'move this folder aside and rerun with --resume-from it'}))
            return 3
        print(json.dumps({'model_calls_used': model.used, 'ceiling': args.ceiling,
                          'unreachable_checks': model.unreachable_checks}))
        return 0
    rows = run_campaign(stream, args.out, args.arm, None, budget=budget, executor=executor,
                        replay_from=args.source, reply_repair=args.reply_repair,
                        profile=CLI_PROFILES[args.profile])
    source = _read_rows(Path(args.source) / 'rows.jsonl')
    same = [a.get('journal_sha256') == b.get('journal_sha256') for a, b in zip(rows, source)]
    verdict = 'REPLAY IDENTICAL' if len(rows) == len(source) and all(same) else 'REPLAY DIFFERS'
    print(json.dumps({'verdict': verdict, 'tasks': len(rows),
                      'first_difference': next((r['task'] for r, s in zip(rows, same) if not s), None)}))
    return 0 if verdict == 'REPLAY IDENTICAL' else 1


if __name__ == '__main__':
    sys.exit(main())
