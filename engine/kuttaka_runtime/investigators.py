"""Bounded executable investigators over the ordinary observation broker.

Investigator source is untrusted. Each policy decision runs in a fresh isolated
executor, and its actions are validated and committed before observations.
Artifacts carry source and minimal provenance hashes only; qualification is
always a property of a new host run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from .execution import PythonExecutor
from .runtime import TransportFailure, _action_schema_error, _failure_usage
from .store import Journal, canonical


POLICY_CONTRACT = '''An investigator is Python source defining run(input). It receives one JSON object with exactly: goal (string), tools (mapping tool names to docstrings), history (host observations and protocol rejections), remaining (remaining model_calls, tool_calls, policy_executions, and steps), state (JSON policy state), representation (JSON current representation), and model_reply (null except immediately after a model action). Return one JSON object with exactly state and action. state is JSON. Actions are: {"kind":"model","instruction":"..."}; {"kind":"experiment","tool":"...","input":...,"predictions":[{"hypothesis":"...","outcome":...}]}; {"kind":"revise","representation":...,"reason":"..."}; {"kind":"answer","answer":...}; or {"kind":"unresolved","reason":"..."}. Experiment predictions are committed before the tool runs. Only revise changes representation. The host independently checks answers. Do not assume model predictions or candidate output are observations.'''
DIRECT_CONTRACT = '''Investigate the goal using only the listed tools and observations. Return one JSON action: experiment with exactly kind, tool, input, predictions (a nonempty list of hypothesis and outcome objects); revise with kind, representation, reason; answer with kind and answer; or unresolved with kind and reason. Commit predictions before each experiment. Only host observations are evidence. The host independently checks answers, and a rejected final answer ends the run without revealing the target answer.'''

MAX_POLICY_INPUT = 24000
MAX_POLICY_OUTPUT = 24000
MAX_OBSERVATION = 8192
MAX_EXECUTOR_JOB = 32768
MAX_POLICY_SOURCE = 8192


class Model(Protocol):
    def propose(self, context: dict) -> dict: ...


@dataclass(frozen=True)
class InvestigatorBudget:
    model_calls: int = 10
    tool_calls: int = 8
    policy_executions: int = 48
    max_steps: int = 48
    input_bytes_per_call: int = MAX_POLICY_INPUT
    total_model_tokens: int | None = None
    max_response_tokens: int = 4096

    def __post_init__(self):
        for name in ('model_calls', 'tool_calls', 'policy_executions', 'max_steps', 'input_bytes_per_call'):
            value = getattr(self, name)
            if type(value) is not int or value < (1 if name == 'input_bytes_per_call' else 0):
                raise ValueError('investigator budgets must be non-negative integers')
        if type(self.max_response_tokens) is not int or self.max_response_tokens < 0:
            raise ValueError('response token budget must be a non-negative integer')
        if self.total_model_tokens is not None and (type(self.total_model_tokens) is not int or self.total_model_tokens < 0):
            raise ValueError('reported token budget must be a non-negative integer')


@dataclass(frozen=True)
class InvestigatorResult:
    status: str
    answer: object
    model_calls: int
    tool_calls: int
    policy_executions: int
    steps: int
    model_input_tokens: int | None
    model_output_tokens: int | None
    model_tokens_known: bool
    reason: str


def _copy(value):
    return json.loads(canonical(value))


def _source_and_metadata(policy, memory=None):
    if isinstance(policy, str) and memory is not None and len(policy) == 64:
        policy = memory.get(policy)
    if isinstance(policy, dict):
        policy = import_investigator(policy)
        body = policy['body']
        source = body['source']
        return source, {**{k: body[k] for k in ('name', 'description', 'interface_version') if k in body},
                        'provenance': policy['provenance']}
    if not isinstance(policy, str) or not policy.strip():
        raise ValueError('policy source or investigator artifact required')
    return policy, {}


def investigator_payload(source: str, *, name: str, description: str,
                         provenance: dict | None = None) -> dict:
    """Build the v1 portable artifact; provenance may contain hashes only."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError('nonempty investigator source required')
    if len(source.encode('utf-8')) > MAX_POLICY_SOURCE:
        raise ValueError('investigator source exceeds 8 KiB')
    if not isinstance(name, str) or not name.strip() or not isinstance(description, str):
        raise ValueError('investigator name and description required')
    source_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
    provenance = {} if provenance is None else _copy(provenance)
    if 'source_sha256' in provenance:
        raise ValueError('source_sha256 is assigned by the host')
    if any(not isinstance(k, str) or not isinstance(v, str) or not _is_sha256(v)
           for k, v in provenance.items()):
        raise ValueError('provenance values must be SHA-256 hashes')
    return {'kind': 'investigator',
            'body': {'language': 'python', 'source': source, 'name': name,
                     'description': description, 'interface_version': 1},
            'dependencies': [],
            'provenance': {'source_sha256': source_hash, **provenance},
            'applicability': {'interface_version': 1}}


def export_investigator(policy, *, memory=None) -> dict:
    """Export an investigator without run traces or qualification records."""
    source, meta = _source_and_metadata(policy, memory)
    hashes = {key: value for key, value in meta.get('provenance', {}).items()
              if key != 'source_sha256'}
    return investigator_payload(source, name=meta.get('name', 'investigator'),
                                description=meta.get('description', ''), provenance=hashes)


def import_investigator(payload) -> dict:
    """Validate and return a detached minimal v1 investigator artifact."""
    if (not isinstance(payload, dict) or set(payload) != {
            'kind', 'body', 'dependencies', 'provenance', 'applicability'}
            or payload.get('kind') != 'investigator'):
        raise ValueError('invalid investigator payload')
    body = payload['body']
    if (not isinstance(body, dict) or set(body) != {
            'language', 'source', 'name', 'description', 'interface_version'}
            or body.get('language') != 'python' or body.get('interface_version') != 1
            or not isinstance(body.get('source'), str) or not body['source'].strip()
            or len(body['source'].encode('utf-8')) > MAX_POLICY_SOURCE
            or not isinstance(body.get('name'), str) or not isinstance(body.get('description'), str)):
        raise ValueError('invalid investigator body')
    if payload['dependencies'] != []:
        raise ValueError('v1 investigators may not declare dependencies')
    provenance = payload['provenance']
    if not isinstance(provenance, dict) or any(not isinstance(k, str) or not _is_sha256(v)
                                               for k, v in provenance.items()):
        raise ValueError('invalid investigator provenance')
    expected = hashlib.sha256(body['source'].encode('utf-8')).hexdigest()
    if provenance.get('source_sha256') != expected:
        raise ValueError('investigator source hash mismatch')
    if payload['applicability'] != {'interface_version': 1}:
        raise ValueError('invalid investigator applicability')
    return _copy(payload)


def _is_sha256(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def _new_executor(executor):
    if executor is None:
        return PythonExecutor()
    if isinstance(executor, type):
        return executor()
    if isinstance(executor, PythonExecutor):
        return type(executor)(timeout=executor.timeout, output_limit=executor.output_limit,
                              image=executor.image)
    if callable(executor) and not hasattr(executor, '__call_policy__'):
        # The public API deliberately treats a callable as a factory so callers
        # can provide a fresh fake or configured executor for every decision.
        return executor()
    raise ValueError('executor must be a fresh-executor factory or PythonExecutor')


def _run_policy(source, context, executor):
    if len(source.encode('utf-8')) > MAX_POLICY_SOURCE:
        raise ValueError('investigator source exceeds 8 KiB')
    payload = canonical({'source': source, 'input': context}).encode('utf-8')
    if len(payload) > MAX_EXECUTOR_JOB:
        raise ValueError('policy executor job exceeds 32 KiB')
    result = _new_executor(executor)({'source': source, 'input': context})
    if not isinstance(result, dict):
        raise ValueError('policy executor returned invalid receipt')
    if (result.get('status') != 'completed' or type(result.get('exit_code')) is not int
            or result['exit_code'] != 0):
        raise _PolicyExecutionError(result)
    output = result.get('output')
    if not isinstance(output, str) or len(output.encode('utf-8')) > MAX_POLICY_OUTPUT:
        raise _PolicyExecutionError(result)

    def reject_duplicate_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON object key')
            value[key] = item
        return value

    try:
        decoded = json.loads(output, object_pairs_hook=reject_duplicate_keys)
    except (TypeError, ValueError):
        raise _PolicyExecutionError(result) from None
    if not isinstance(decoded, dict) or set(decoded) != {'result'}:
        raise _PolicyExecutionError(result)
    value = decoded['result']
    if not isinstance(value, dict) or set(value) != {'state', 'action'}:
        raise _PolicyExecutionError(result)
    try:
        _copy(value)
    except (TypeError, ValueError):
        raise _PolicyExecutionError(result) from None
    return value, result


class _PolicyExecutionError(RuntimeError):
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__('policy execution failed')


def _valid_action(action, tools):
    if not isinstance(action, dict):
        return 'Return one JSON object with a documented investigator action.'
    kind = action.get('kind')
    if kind == 'experiment':
        return _action_schema_error(action, tools)
    if kind == 'answer':
        return None if set(action) == {'kind', 'answer'} else 'Answer fields must be exactly kind and answer.'
    if kind == 'unresolved':
        return None if set(action) == {'kind', 'reason'} and isinstance(action['reason'], str) and action['reason'].strip() else 'Unresolved needs a nonempty reason.'
    if kind == 'revise':
        return None if set(action) == {'kind', 'representation', 'reason'} and isinstance(action['reason'], str) and action['reason'].strip() else 'Revision needs representation and a nonempty reason.'
    if kind == 'model':
        return None if set(action) == {'kind', 'instruction'} and isinstance(action['instruction'], str) and action['instruction'].strip() else 'Model action needs a nonempty instruction.'
    return 'Use kind model, experiment, revise, answer, or unresolved.'


def _usage(model):
    value = getattr(model, 'last_usage', {})
    if not isinstance(value, dict):
        return None, None
    vals = [value.get('input_tokens'), value.get('output_tokens')]
    return tuple(v if type(v) is int and v >= 0 else None for v in vals)


def run_policy(goal: str, policy, model: Model, tools: dict[str, Callable], checker: Callable,
               journal, executor=None, budget: InvestigatorBudget = InvestigatorBudget(), *,
               state=None, representation=None, memory=None,
               checker_feedback: Callable | None = None, pause_after_steps=None,
               resume=False, resume_context=None) -> InvestigatorResult:
    """Run an executable adaptive investigator through bounded host tools."""
    return _run(goal, policy, model, tools, checker, journal, executor, budget,
                state=state, representation=representation, memory=memory,
                checker_feedback=checker_feedback, policy_mode=True,
                pause_after_steps=pause_after_steps, resume=resume, resume_context=resume_context)


def run_investigation(goal: str, model: Model, tools: dict[str, Callable], checker: Callable,
                      journal, budget: InvestigatorBudget = InvestigatorBudget(), *,
                      state=None, representation=None,
                      checker_feedback: Callable | None = None, pause_after_steps=None,
                      resume=False, resume_context=None, context_memory=None) -> InvestigatorResult:
    """Direct model investigator with optional bounded history and exact recall.

    context_memory is opt-in and changes the model-visible context/tool contract.
    It bounds the history viewport, not retained evidence, host RAM, arbitrary
    policy state, or the whole request. Existing full-history behavior is default.
    """
    return _run(goal, None, model, tools, checker, journal, None, budget,
                state=state, representation=representation, memory=None,
                checker_feedback=checker_feedback, policy_mode=False,
                pause_after_steps=pause_after_steps, resume=resume, resume_context=resume_context,
                context_memory=context_memory)


def _resume_cursor(journal, contract):
    """Resume only an explicit clean pause; never retry an uncertain outside effect."""
    events = Journal.read(journal.path)
    if not events or events[-1]['kind'] != 'investigator_pause':
        raise ValueError('resume requires an intact journal ending at an explicit pause')
    checkpoint = events[-1]['body']
    if checkpoint.get('contract') != contract:
        raise ValueError('resume contract changed')
    starts = [i for i, event in enumerate(events) if event['kind'] == 'investigator_start']
    if len(starts) != 1:
        raise ValueError('resume requires one investigation per journal')
    run_events = events[starts[0]:]
    if any(e['kind'] == 'investigator_finish' for e in run_events):
        raise ValueError('cannot resume a terminal investigation')
    counts = checkpoint['cursor']['counts']
    for key, kind in [('model_calls', 'model_attempt'), ('tool_calls', 'investigator_tool_attempt'),
                      ('policy_executions', 'policy_attempt')]:
        if type(counts.get(key)) is not int or counts[key] != sum(e['kind'] == kind for e in run_events):
            raise ValueError('checkpoint count differs from charged journal attempts')
    expected_steps = (sum(e['kind'] == 'policy_result' for e in run_events) if contract['policy_mode']
                      else counts['model_calls'])
    if type(counts.get('steps')) is not int or counts['steps'] != expected_steps:
        raise ValueError('checkpoint step count differs from journal')
    for key, limit in [('model_calls', 'model_calls'), ('tool_calls', 'tool_calls'),
                       ('policy_executions', 'policy_executions'), ('steps', 'max_steps')]:
        if not 0 <= counts[key] <= contract['budget'][limit]:
            raise ValueError('checkpoint exceeds original budget')
    return _copy(checkpoint['cursor']), events[-1]['hash']


def _run(goal, policy, model, tools, checker, journal, executor, budget, *,
         state, representation, memory, checker_feedback, policy_mode,
         pause_after_steps, resume, resume_context, context_memory=None):
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError('a nonempty goal is required')
    if not isinstance(tools, dict) or any(not isinstance(k, str) for k in tools):
        raise ValueError('tools must map names to callables')
    if any(not callable(tool) for tool in tools.values()):
        raise ValueError('every tool must be callable')
    if context_memory is not None:
        from .context_memory import ContextMemory
        if policy_mode or not isinstance(context_memory, ContextMemory):
            raise ValueError('context_memory requires ContextMemory in direct investigation mode')
        memory_tools = context_memory.tools()
        if set(tools) & set(memory_tools):
            raise ValueError('history tool name collision')
        tools = {**tools, **memory_tools}
    if (pause_after_steps is not None and
            (type(pause_after_steps) is not int or pause_after_steps < 1)):
        raise ValueError('pause_after_steps must be a positive integer')
    if type(resume) is not bool:
        raise ValueError('resume must be a boolean')
    if resume and (state is not None or representation is not None):
        raise ValueError('resume restores state; do not replace it')
    source = _source_and_metadata(policy, memory)[0] if policy_mode else None
    policy_hash = hashlib.sha256(source.encode()).hexdigest() if source is not None else None
    contract = _copy({'version': 'investigator-continuation-v1', 'goal': goal,
        'budget': asdict(budget), 'tools': {k: v.__doc__ or '' for k, v in tools.items()},
        'policy_sha256': policy_hash,
        'policy_artifact': policy if isinstance(policy, str) and len(policy) == 64 and memory is not None else None,
        'model': getattr(model, 'identity', None), 'policy_mode': policy_mode,
        'host_context': resume_context})
    if context_memory is not None:
        contract['context_memory'] = context_memory.configuration
    state = None if state is None else _copy(state)
    representation = None if representation is None else _copy(representation)
    history = []
    counts = {'model_calls': 0, 'tool_calls': 0, 'policy_executions': 0, 'steps': 0}
    token_input = token_output = 0
    token_known = True
    previous_model_reply = None
    if resume:
        cursor, checkpoint_hash = _resume_cursor(journal, contract)
        state, representation, history = cursor['state'], cursor['representation'], cursor['history']
        counts = cursor['counts']
        token_input, token_output, token_known = cursor['token_input'], cursor['token_output'], cursor['token_known']
        previous_model_reply = cursor['previous_model_reply']
        journal.append('investigator_resume', {'checkpoint': checkpoint_hash, 'counts': counts})
    else:
        start = {'goal': goal, 'budget': asdict(budget),
                        'tools': sorted(tools), 'policy_sha256': policy_hash,
                        'model': getattr(model, 'identity', None), 'policy_mode': policy_mode,
                        'attribution': 'engineered runtime'}
        if context_memory is not None:
            start['context_memory'] = context_memory.configuration
        journal.append('investigator_start', start)
    if context_memory is not None:
        context_memory.bind(history)
    entered_steps = counts['steps']

    def finish(status, answer=None, reason=''):
        result = InvestigatorResult(status, answer, counts['model_calls'], counts['tool_calls'],
                    counts['policy_executions'], counts['steps'],
                    token_input if token_known else None, token_output if token_known else None,
                    token_known, reason)
        journal.append('investigator_finish', asdict(result))
        return result

    while counts['steps'] < budget.max_steps:
        if pause_after_steps is not None and counts['steps'] - entered_steps >= pause_after_steps:
            cursor = {'state': state, 'representation': representation, 'history': history,
                'counts': counts, 'token_input': token_input, 'token_output': token_output,
                'token_known': token_known, 'previous_model_reply': previous_model_reply}
            journal.append('investigator_pause', {'contract': contract, 'cursor': cursor})
            return InvestigatorResult('paused', None, counts['model_calls'], counts['tool_calls'],
                counts['policy_executions'], counts['steps'], token_input if token_known else None,
                token_output if token_known else None, token_known, 'explicit clean-boundary pause')
        if policy_mode:
            if counts['policy_executions'] >= budget.policy_executions:
                return finish('incomplete', reason='policy execution budget exhausted')
            context = {'goal': goal,
                       'tools': {name: (tool.__doc__ or '') for name, tool in tools.items()},
                       'history': history,
                       'remaining': {'model_calls': budget.model_calls - counts['model_calls'],
                                     'tool_calls': budget.tool_calls - counts['tool_calls'],
                                     'policy_executions': budget.policy_executions - counts['policy_executions'] - 1,
                                     'steps': budget.max_steps - counts['steps'] - 1},
                       'state': state, 'representation': representation,
                       'model_reply': previous_model_reply}
            if set(context) != {'goal', 'tools', 'history', 'remaining', 'state', 'representation', 'model_reply'}:
                raise AssertionError('internal policy contract mismatch')
            if len(canonical(context).encode('utf-8')) > min(budget.input_bytes_per_call, MAX_POLICY_INPUT):
                return finish('incomplete', reason='policy context exceeds input byte budget')
            counts['policy_executions'] += 1
            journal.append('policy_attempt', {'number': counts['policy_executions'], 'input': context})
            try:
                result, receipt = _run_policy(source, _copy(context), executor)
                journal.append('policy_execution_receipt', {'number': counts['policy_executions'],
                                                              'receipt': receipt})
                state = _copy(result['state'])
                action = _copy(result['action'])
            except _PolicyExecutionError as error:
                journal.append('policy_execution_failure', {'receipt': error.receipt})
                return finish('incomplete', reason='policy execution failure')
            except Exception as error:
                journal.append('policy_error', {'type': type(error).__name__})
                return finish('incomplete', reason='policy execution failure')
            journal.append('policy_result', {'state': state, 'action': action})
        else:
            action = None
        counts['steps'] += 1
        schema_error = _valid_action(action, tools) if policy_mode else None
        if policy_mode and schema_error:
            feedback = {'status': 'protocol_rejection', 'feedback': schema_error}
            journal.append('action_rejected', feedback)
            history.append({'action': action, 'observation': feedback})
            previous_model_reply = None
            continue
        if not policy_mode or action.get('kind') == 'model':
            if counts['model_calls'] >= budget.model_calls:
                return finish('incomplete', reason='model budget exhausted')
            if schema_error:
                feedback = {'status': 'protocol_rejection', 'feedback': schema_error}
                history.append({'action': action, 'observation': feedback})
                journal.append('action_rejected', feedback)
                previous_model_reply = None
                continue
            context = {'goal': goal, 'tools': {name: tool.__doc__ or '' for name, tool in tools.items()},
                       'history': history,
                       'remaining': {'model_calls': budget.model_calls - counts['model_calls'] - 1,
                                     'tool_calls': budget.tool_calls - counts['tool_calls'],
                                     'policy_executions': budget.policy_executions - counts['policy_executions'],
                                     'steps': budget.max_steps - counts['steps']},
                       'state': state, 'representation': representation,
                       'model_reply': previous_model_reply,
                       'instruction': action.get('instruction') if policy_mode else DIRECT_CONTRACT,
                       'policy_instruction': action.get('instruction') if policy_mode else DIRECT_CONTRACT}
            if context_memory is not None:
                context.update(context_memory.view())
                instruction = (DIRECT_CONTRACT + ' History is a bounded viewport, not the complete evidence. '
                    'The memory manifest declares omissions and record references. Use history_search '
                    'and history_read to inspect older evidence and contradictions before concluding. '
                    'These are ordinary charged tool calls. Unseen records have not been disproved or erased.')
                context['instruction'] = context['policy_instruction'] = instruction
            encoded_context = canonical(context)
            if len(encoded_context.encode('utf-8')) > budget.input_bytes_per_call:
                return finish('incomplete', reason='model context exceeds input byte budget')
            response_cap = budget.max_response_tokens
            model_cap = getattr(model, 'max_tokens', None)
            if type(model_cap) is int and model_cap > response_cap:
                return finish('incomplete', reason='model response ceiling exceeds investigator budget')
            if budget.total_model_tokens is not None:
                if token_known and token_input + token_output >= budget.total_model_tokens:
                    return finish('incomplete', reason='reported model token budget exhausted')
            counts['model_calls'] += 1
            journal.append('model_attempt', {'number': counts['model_calls'], 'context': context})
            try:
                proposed = model.propose(_copy(context))
            except Exception as error:
                receipt = getattr(model, 'last_response', None)
                input_used, output_used = _usage(model)
                if input_used is None or output_used is None:
                    token_known = False
                else:
                    token_input += input_used
                    token_output += output_used
                # An attempted call always gets a response-accounting row. A null
                # receipt means no response arrived; null token fields mean usage
                # is unknown, not zero.
                journal.append('model_response', {'receipt': receipt,
                                                  'usage': _failure_usage(model)})
                status = getattr(error, 'status', None)
                if status is None and isinstance(receipt, dict):
                    status = receipt.get('status')
                if type(status) is not int or not 100 <= status <= 599:
                    status = None
                if isinstance(error, TransportFailure):
                    journal.append('model_error', {'type': 'TransportFailure',
                                                   'kind': error.kind, 'status': status})
                else:
                    journal.append('model_error', {'type': type(error).__name__,
                                                   'status': status})
                return finish('incomplete', reason='model failure')
            receipt = getattr(model, 'last_response', None)
            usage = getattr(model, 'last_usage', {})
            journal.append('model_response', {'receipt': receipt, 'usage': usage})
            input_used, output_used = _usage(model)
            if input_used is None or output_used is None:
                token_known = False
            else:
                token_input += input_used
                token_output += output_used
            if budget.total_model_tokens is not None and not token_known:
                return finish('incomplete', reason='cannot verify reported model token budget')
            if (budget.total_model_tokens is not None and token_known
                    and token_input + token_output > budget.total_model_tokens):
                journal.append('model_token_budget_exceeded', {'reported': token_input + token_output,
                                                                 'limit': budget.total_model_tokens})
                return finish('incomplete', reason='reported model token budget exceeded')
            try:
                action = _copy(proposed)
            except Exception:
                action = None
            journal.append('model_reply', {'action': action})
            model_error = _valid_action(action, tools)
            if not policy_mode and isinstance(action, dict) and action.get('kind') == 'model':
                model_error = 'Direct model actions cannot recursively invoke the model.'
            if model_error:
                rejection = {'status': 'protocol_rejection', 'feedback': model_error}
                journal.append('action_rejected', rejection)
                history.append({'action': action, 'observation': rejection})
                previous_model_reply = action
                continue
            if policy_mode:
                previous_model_reply = action
                continue
        if action.get('kind') == 'unresolved':
            return finish('unresolved', reason=action['reason'])
        if action.get('kind') == 'model':
            previous_model_reply = None
            continue
        if action.get('kind') == 'revise':
            try:
                new_representation = _copy(action['representation'])
            except Exception:
                rejection = {'status': 'protocol_rejection', 'feedback': 'representation must be JSON'}
                history.append({'action': action, 'observation': rejection})
                journal.append('action_rejected', rejection)
                previous_model_reply = None
                continue
            journal.append('representation_revision', {'before': representation, 'after': new_representation,
                                                         'reason': action['reason']})
            representation = new_representation
            history.append({'action': action, 'observation': {'status': 'representation_revised'}})
            previous_model_reply = None
            continue
        if counts['tool_calls'] >= budget.tool_calls:
            return finish('incomplete', reason='tool budget exhausted')
        journal.append('investigator_proposal', action)
        counts['tool_calls'] += 1
        journal.append('investigator_tool_attempt', {'number': counts['tool_calls'], 'kind': action['kind']})
        try:
            if action['kind'] == 'experiment':
                observation = tools[action['tool']](_copy(action['input']))
            else:
                observation = checker(goal, _copy(action['answer']))
                if type(observation) is not bool:
                    raise ValueError('checker must return bool')
            observation = _copy(observation)
            if len(canonical(observation).encode('utf-8')) > MAX_OBSERVATION:
                raise ValueError('observation exceeds size limit')
        except Exception as error:
            journal.append('investigator_tool_error', {'type': type(error).__name__})
            return finish('incomplete', reason='tool or checker failure')
        record = {'action': action, 'observation': observation}
        journal.append('investigator_observation', record)
        history.append(record)
        previous_model_reply = None
        if action['kind'] == 'answer':
            if observation:
                return finish('checked', action['answer'], 'accepted by supplied development checker')
            # A false final answer terminates without target-answer feedback.
            return finish('failed', action['answer'], 'answer rejected by supplied development checker')
    return finish('incomplete', reason='step budget exhausted')

