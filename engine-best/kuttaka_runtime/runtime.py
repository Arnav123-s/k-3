"""Engineered investigation loop; supplied tools/checkers remain attributed inputs.

Tools are trusted host-provided callables. ProgramTools delegates generated
program execution to the bounded Docker executor; this loop never uses exec.
The journal is private run evidence: inputs/results may contain task-sensitive data.
"""

import json

from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from .response_feedback import INVALID_JSON, TRUNCATED, classify_invalid_action
from .store import canonical


class TransportFailure(RuntimeError):
    """One spent provider/network failure, with only safe kind and status metadata."""

    KINDS = {'provider_http_error', 'request_timeout', 'request_transport_error',
             'response_read_error', 'empty_stream', 'incomplete_stream', 'stream_error'}

    def __init__(self, kind, *, status=None):
        if not isinstance(kind, str) or kind not in self.KINDS:
            kind = 'request_transport_error'
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            status = None
        super().__init__(kind)
        self.kind, self.status = kind, status


class ToolInputError(ValueError):
    """Malformed tool input with bounded, host-authored feedback for correction."""


class Model(Protocol):
    def propose(self, context: dict) -> dict:
        """Return experiment, answer, or unresolved; weights remain fixed."""


@dataclass(frozen=True)
class Budget:
    model_calls: int = 16
    tool_calls: int = 64

    def __post_init__(self):
        if any(type(x) is not int or x < 0 for x in (self.model_calls, self.tool_calls)):
            raise ValueError('budgets must be non-negative integers')


@dataclass(frozen=True)
class RunResult:
    status: str
    answer: object
    model_calls: int
    tool_calls: int
    reason: str


def _failure_usage(model):
    """Record known token counts and explicit nulls without copying provider prose."""
    usage = getattr(model, 'last_usage', {})
    usage = usage if isinstance(usage, dict) else {}
    result = {key: value if type(value) is int and value >= 0 else None
              for key, value in ((name, usage.get(name))
                                 for name in ('input_tokens', 'output_tokens'))}
    reasoning_chars = usage.get('reasoning_chars')
    if type(reasoning_chars) is int and reasoning_chars >= 0:
        result['reasoning_chars'] = reasoning_chars
    finish_reason = usage.get('finish_reason')
    if finish_reason in ('stop', 'length', 'tool_calls', 'content_filter'):
        result['finish_reason'] = finish_reason
    return result


def normalize_action(action):
    """Repair only documented C1 reply shapes; callers must opt in."""
    if not isinstance(action, dict):
        return action
    if 'kind' not in action and set(action) == {'artifact'}:
        return {'kind': 'answer', 'answer': {'artifact': action['artifact']}}
    if action.get('kind') == 'answer' and set(action) == {'kind', 'artifact'}:
        return {'kind': 'answer', 'answer': {'artifact': action['artifact']}}
    if action.get('kind') == 'experiment' and set(action) == {'kind', 'tool', 'input'}:
        return dict(action, predictions=[])
    return action


def _action_schema_error(action, tools, allow_empty_predictions=False):
    """Return fixed, host-authored protocol feedback or None for a valid action."""
    if not isinstance(action, dict):
        return 'Return one JSON object with kind experiment, answer, or unresolved.'
    kind = action.get('kind')
    if kind == 'experiment':
        if set(action) != {'kind', 'tool', 'input', 'predictions'}:
            return 'Experiment fields must be exactly kind, tool, input, predictions.'
        if not isinstance(action['tool'], str) or action['tool'] not in tools:
            names = ', '.join(sorted(tools))
            return 'Choose a tool from the available tool names: ' + (names or '(none)') + '.'
        predictions = action['predictions']
        if not isinstance(predictions, list) or (not predictions and not allow_empty_predictions):
            return 'Before an experiment, provide a nonempty predictions list with hypothesis and outcome fields.'
        if any(not isinstance(p, dict) or set(p) != {'hypothesis', 'outcome'}
               or not isinstance(p['hypothesis'], str) or not p['hypothesis'].strip()
               for p in predictions):
            return 'Each prediction needs a nonempty hypothesis string and a JSON outcome.'
        return None
    if kind == 'answer':
        return None if set(action) == {'kind', 'answer'} else 'Answer fields must be exactly kind and answer.'
    if kind == 'unresolved':
        if set(action) == {'kind', 'reason'} and isinstance(action['reason'], str) and action['reason'].strip():
            return None
        return 'Unresolved fields must be kind and a nonempty reason.'
    return 'Use kind experiment, answer, or unresolved and follow that action schema.'


def investigate(request: str, model: Model, tools: dict[str, Callable], checker: Callable,
                journal, budget: Budget = Budget(), *, checker_feedback: Callable | None = None,
                normalize: bool = False, automatic_submission: bool = False,
                continue_transport_failures: bool = False, outage_limit: int | None = None,
                detailed_response_feedback: bool = False,
                truncation_limit: int | None = None,
                invalid_json_limit: int | None = None,
                host_tools: dict[str, Callable] | None = None,
                prelude: tuple = (),
                auto_validate: tuple | None = None) -> RunResult:
    """One request, multiple experiments, independent checks, no domain dispatch.

Experiment action: {kind, tool, input, predictions}. Predictions are a nonempty
list of {hypothesis, outcome} records; these are conjectures, never evidence.
Answer action: {kind, answer}. Checker receives (original_request, answer) and
must return a literal bool. It is a development checker, never the sealed final
benchmark scorer. Checker calls share the tool ceiling and are fully recorded.
False checker results may be supplemented with host-authored feedback; that
feedback never changes the checker's verdict. Invalid model actions consume a
model attempt, receive protocol feedback, and do not consume a tool call.
Unresolved action: {kind, reason}.

Tools must themselves bound execution and return JSON-compatible observations.
Transport failures are terminal by default. Continuing them requires an explicit flag and finite outage limit.
Detailed response feedback is opt-in. It explains recognized empty/truncated/malformed
invalid replies without repairing actions or adding model attempts.
truncation_limit (opt-in) ends the run as incomplete after that many consecutive
replies cut at the token limit with no valid action: a model that reasons past the
limit twice in a row seldom stops on the third try, and each such reply is the
longest and slowest a call can be (W2-b, h-r2-turning: five in a row, ~20 min each).
invalid_json_limit (opt-in) ends the run after that many consecutive nonempty
JSON syntax failures with a normal stop. Raw replies remain rejected and recorded;
the limit never repairs or executes them. Other replies reset this count.
Automatic deliverable submission is disabled unless requested. One writer owns a run.

Host steps (opt-in) are deterministic tool runs the host makes without a model call, so
the model's calls go to reasoning and code instead of protocol. In recorded hard runs,
retrieval and validation took most actor replies (W4-k Glimmer: 17 find_methods and
17 validate_method of 68; Llama 90B: 196 find_methods of 334, 8 methods written).
prelude: (tool, input) pairs run once before the first model call, from host_tools
(not offered to the model) or tools. auto_validate: (store_tool, validate_tool): after
the model stores a method, the host validates it at once; a deliverable is then
submitted as automatic submission does. Each host step is journaled as host_experiment,
spends a tool call, and its observation enters the history marked by "host". The
checker, the checks and the model-call budget are unchanged.
"""
    if not isinstance(request, str) or not request.strip():
        raise ValueError('a nonempty original request is required')
    for flag, name in ((normalize, 'normalize'), (automatic_submission, 'automatic_submission'),
                       (continue_transport_failures, 'continue_transport_failures'),
                       (detailed_response_feedback, 'detailed_response_feedback')):
        if type(flag) is not bool:
            raise ValueError(name + ' must be bool')
    if truncation_limit is not None and (type(truncation_limit) is not int or truncation_limit < 1):
        raise ValueError('truncation_limit must be a positive int or None')
    if invalid_json_limit is not None and (type(invalid_json_limit) is not int or invalid_json_limit < 1):
        raise ValueError('invalid_json_limit must be a positive int or None')
    if continue_transport_failures:
        if type(outage_limit) is not int or outage_limit < 1:
            raise ValueError('continuing transport failures requires a positive outage_limit')
    elif outage_limit is not None:
        raise ValueError('outage_limit requires continue_transport_failures=True')
    runtime_options = {'normalize': normalize,
                       'automatic_submission': automatic_submission,
                       'continue_transport_failures': continue_transport_failures,
                       'outage_limit': outage_limit}
    if detailed_response_feedback:
        runtime_options['detailed_response_feedback'] = True
    if truncation_limit is not None:
        runtime_options['truncation_limit'] = truncation_limit
    if invalid_json_limit is not None:
        runtime_options['invalid_json_limit'] = invalid_json_limit
    host_tools = dict(host_tools or {})
    prelude = tuple(prelude or ())
    for step in prelude:
        if (not isinstance(step, tuple) or len(step) != 2 or step[0] not in {**tools, **host_tools}
                or not isinstance(step[1], dict)):
            raise ValueError('prelude steps must be (known tool name, input object) pairs')
    if auto_validate is not None and (not isinstance(auto_validate, tuple) or len(auto_validate) != 2
                                      or auto_validate[0] not in tools
                                      or auto_validate[1] not in {**tools, **host_tools}):
        raise ValueError('auto_validate must be (store tool, validate tool), both known')
    if prelude:
        runtime_options['prelude'] = [name for name, _ in prelude]
    if auto_validate is not None:
        runtime_options['auto_validate'] = list(auto_validate)
    recorded_options = getattr(model, 'runtime_options', None)
    if recorded_options is not None and recorded_options != runtime_options:
        raise ValueError('replay runtime options do not match the recorded run')
    calls = tool_calls = 0
    consecutive_transport_failures = 0
    consecutive_truncations = 0
    consecutive_invalid_json = 0
    history = []
    start = {'request': request, 'budget': asdict(budget),
             'tools': sorted(tools), 'model': getattr(model, 'identity', None),
             'attribution': 'engineered runtime'}
    if (normalize or automatic_submission or continue_transport_failures or outage_limit is not None
            or detailed_response_feedback or truncation_limit is not None or invalid_json_limit is not None
            or prelude or auto_validate is not None):
        start['runtime_options'] = runtime_options
    journal.append('start', start)

    def finish(status, answer=None, reason=''):
        result = RunResult(status, answer, calls, tool_calls, reason)
        journal.append('finish', asdict(result))
        return result

    def host_step(name, tool_input, reason):
        """One deterministic tool run by the host. Returns its observation, or None after a tool error."""
        nonlocal tool_calls
        tool_calls += 1
        journal.append('host_experiment', {'tool': name, 'input': tool_input, 'reason': reason})
        journal.append('tool_attempt', {'number': tool_calls, 'kind': 'experiment', 'host': True})
        action = {'kind': 'experiment', 'tool': name, 'input': tool_input, 'by': 'host', 'reason': reason}
        try:
            observation = (host_tools.get(name) or tools[name])(json.loads(canonical(tool_input)))
            canonical(observation)
        except ToolInputError as error:
            observation = {'status': 'tool_rejection', 'feedback': str(error)[:500]}
            journal.append('tool_error', {'type': type(error).__name__, 'feedback': True, 'host': True})
        except Exception as error:
            journal.append('tool_error', {'type': type(error).__name__, 'host': True})
            return None
        record = {'action': action, 'observation': observation}
        journal.append('observation', record)
        history.append(record)
        return observation

    def submit(deliverable):
        """Automatic submission of a deliverable; a RunResult ends the run, None continues it."""
        nonlocal tool_calls
        tool_calls += 1
        journal.append('tool_attempt', {'number': tool_calls, 'kind': 'answer', 'automatic': True})
        try:
            submitted = json.loads(canonical(deliverable))
            accepted = checker(request, submitted)
            if type(accepted) is not bool:
                raise ValueError('checker must return bool')
        except Exception as error:
            journal.append('tool_error', {'type': type(error).__name__})
            return finish('incomplete', reason='tool or checker failure')
        automatic_record = {'action': {'kind': 'answer', 'answer': submitted, 'automatic': True},
                            'observation': accepted}
        journal.append('observation', automatic_record)
        history.append(automatic_record)
        if accepted:
            return finish('checked', submitted, 'accepted by supplied development checker')
        return None

    for name, tool_input in prelude:
        if tool_calls >= budget.tool_calls:
            break
        host_step(name, tool_input, 'prelude')

    while calls < budget.model_calls:
        # Optional pre-send check (a TCP connect, no request): an unreachable network is waited
        # out here, before the attempt is logged, instead of being spent as a call. It writes
        # nothing to the journal, so replays are unaffected.
        wait_until_reachable = getattr(model, 'wait_until_reachable', None)
        if wait_until_reachable is not None:
            wait_until_reachable()
        calls += 1
        journal.append('model_attempt', {'number': calls})
        try:
            # Copy context so a model adapter cannot alter the original history.
            context = json.loads(canonical(dict(
                request=request, tools={name: tool.__doc__ or '' for name, tool in tools.items()}, history=history,
                remaining={'model_calls': budget.model_calls - calls,
                           'tool_calls': budget.tool_calls - tool_calls})))
            journal.append('model_context', context)
            proposed = model.propose(json.loads(canonical(context)))
        except TransportFailure as error:
            receipt = getattr(model, 'last_response', None)
            usage = _failure_usage(model)
            journal.append('model_response', {'receipt': receipt, 'usage': usage})
            status = error.status if error.status is not None else (receipt or {}).get('status')
            if type(status) is not int or not 100 <= status <= 599:
                status = None
            journal.append('model_error', {'type': 'TransportFailure', 'kind': error.kind,
                                           'status': status, 'soft': True})
            consecutive_transport_failures += 1
            if not continue_transport_failures:
                return finish('incomplete', reason='model transport failure')
            if consecutive_transport_failures >= outage_limit:
                return finish('incomplete', reason='transport outage threshold reached')
            continue
        except Exception as error:
            # Exceptions can contain provider credentials; record only the class.
            receipt = getattr(model, 'last_response', None)
            usage = _failure_usage(model)
            journal.append('model_response', {'receipt': receipt, 'usage': usage})
            status = getattr(error, 'status', None)
            if status is None:
                status = (receipt or {}).get('status')
            if type(status) is not int or not 100 <= status <= 599:
                status = None
            error_type = getattr(error, 'recorded_type', type(error).__name__)
            journal.append('model_error', {'type': error_type, 'status': status})
            return finish('incomplete', reason='model or action failure')
        consecutive_transport_failures = 0
        journal.append('model_response', {'receipt': getattr(model, 'last_response', None),
                                          'usage': getattr(model, 'last_usage', {})})
        try:
            action = json.loads(canonical(proposed))
        except Exception:
            action = None
            schema_error = 'Return one JSON-compatible action object using the documented action schema.'
        else:
            normalized = normalize_action(action) if normalize else action
            if normalized != action:
                journal.append('action_normalized', {'from': action, 'to': normalized})
            schema_error = _action_schema_error(normalized, tools, allow_empty_predictions=normalize)
        classified = (classify_invalid_action(action, getattr(model, 'last_usage', {}))
                      if schema_error is not None
                      and (detailed_response_feedback or truncation_limit or invalid_json_limit) else None)
        if classified is not None and detailed_response_feedback:
            schema_error = classified['feedback']
        truncated = classified is not None and classified['category'] in TRUNCATED
        consecutive_truncations = consecutive_truncations + 1 if truncated else 0
        invalid_json = classified is not None and classified['category'] == INVALID_JSON
        consecutive_invalid_json = consecutive_invalid_json + 1 if invalid_json else 0
        journal.append('model_reply', {'action': action, 'usage': getattr(model, 'last_usage', {})})
        if schema_error is None:
            action = normalized
        if schema_error is not None:
            feedback = {'status': 'protocol_rejection', 'feedback': schema_error}
            journal.append('action_rejected', feedback)
            history.append({'action': action, 'observation': feedback})
            if truncation_limit is not None and consecutive_truncations >= truncation_limit:
                return finish('incomplete', reason='replies cut at the token limit')
            if invalid_json_limit is not None and consecutive_invalid_json >= invalid_json_limit:
                return finish('incomplete', reason='repeated invalid JSON replies')
            continue
        kind = action['kind']
        if kind == 'unresolved':
            return finish('unresolved', reason=action['reason'])
        if tool_calls >= budget.tool_calls:
            return finish('incomplete', reason='tool budget exhausted')
        journal.append('proposal', action)  # durable before any outside observation
        tool_calls += 1
        journal.append('tool_attempt', {'number': tool_calls, 'kind': kind})
        feedback_detail = None
        try:
            if kind == 'experiment':
                observation = tools[action['tool']](json.loads(canonical(action['input'])))
            else:
                submitted = json.loads(canonical(action['answer']))
                observation = checker(request, submitted)
                if type(observation) is not bool:
                    raise ValueError('checker must return bool')
                if observation is False and checker_feedback is not None:
                    try:
                        details = checker_feedback(request, submitted)
                        feedback_detail = json.loads(canonical(details))
                    except Exception as error:
                        journal.append('checker_feedback_error', {'type': type(error).__name__})
                    else:
                        journal.append('checker_feedback', feedback_detail)
            canonical(observation)
        except ToolInputError as error:
            journal.append('tool_error', {'type': type(error).__name__, 'feedback': True})
            record = {'action': action, 'observation': {
                'status': 'tool_rejection', 'feedback': str(error)[:500]}}
            journal.append('observation', record)
            history.append(record)
            continue
        except Exception as error:
            journal.append('tool_error', {'type': type(error).__name__})
            return finish('incomplete', reason='tool or checker failure')
        record = {'action': action, 'observation': observation}
        if feedback_detail is not None:
            record['checker_feedback'] = feedback_detail
        journal.append('observation', record)
        history.append(record)
        if kind == 'answer' and observation:
            return finish('checked', action['answer'], 'accepted by supplied development checker')
        deliverable = observation.get('deliverable') if isinstance(observation, dict) else None
        if (deliverable is None and auto_validate is not None and kind == 'experiment'
                and action['tool'] == auto_validate[0] and isinstance(observation, dict)
                and observation.get('status') == 'stored' and isinstance(observation.get('artifact'), str)
                and tool_calls < budget.tool_calls):
            validated = host_step(auto_validate[1], {'artifact': observation['artifact']}, 'auto_validate')
            if validated is None:
                return finish('incomplete', reason='tool or checker failure')
            deliverable = validated.get('deliverable') if isinstance(validated, dict) else None
        if (automatic_submission and kind == 'experiment' and deliverable is not None
                and tool_calls < budget.tool_calls):
            ended = submit(deliverable)
            if ended is not None:
                return ended
    return finish('incomplete', reason='model budget exhausted')
