"""Offline toy canary plus a separately frozen, explicit-opt-in live runner.

Importing the module never contacts a provider. ``run_live`` refuses execution
unless the caller opts in and an externally reviewed freeze manifest matches.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from kuttaka_runtime.models import JsonModel
from kuttaka_runtime.response_feedback import classify_invalid_action
from kuttaka_runtime.runtime import Budget, ToolInputError, investigate
from kuttaka_runtime.store import Journal, canonical


TOOL_BUDGET = 4  # three tool actions plus the independent checker call
MODEL_REQUESTS_PER_ARM = 5
MAX_MODEL_REQUESTS_TOTAL = 10
MAX_CONTEXT_BYTES = 24000
MAX_RESPONSE_TOKENS = 8192
REQUEST_TIMEOUT_SECONDS = 300
MODEL_NAME = "meta/muse-glimmer-30b"
MODEL_ENDPOINT = "https://integrate.api.nvidia.com/v1"
ARM_ORDER = ("high", "low")
CANARY_SOURCE_PATH = Path(__file__).resolve()
FREEZE_PATH = CANARY_SOURCE_PATH.with_name("freeze.json")
API_KEY_ENV = "NVIDIA_API_KEY"
TOY_RECORDS = {"sample": "cobalt"}
CANARY_REQUEST = (
    'Use lookup_item with key "sample". Copy only the value returned by that '
    'observation into store_item under name "copy", read "copy" back with '
    'read_item, then answer with exactly {"value": "<observed value>"}. '
    'Do not guess values or claim an observation unless a listed tool returned it.'
)


class ToyToolError(ValueError):
    """Safe toy-tool schema or sequence failure."""


class ToyToolBudgetExceeded(RuntimeError):
    """The fixed action-plus-checker tool budget is exhausted."""


class OfflineToyCanary:
    """Strict deterministic lookup/storage tools and sequence-checking oracle."""

    def __init__(self):
        self._calls = 0
        self._events = []
        self._lookup_value = None
        self._stored_value = None
        self._read_value = None
        self.tools = {
            "lookup_item": self.lookup_item,
            "store_item": self.store_item,
            "read_item": self.read_item,
        }

    @property
    def calls_used(self):
        return self._calls

    @property
    def events(self):
        """Return a defensive copy of the public toy interaction log."""
        return deepcopy(self._events)

    def _spend(self, name):
        if self._calls >= TOOL_BUDGET:
            raise ToyToolBudgetExceeded("toy canary tool budget exhausted")
        self._calls += 1
        event = {"kind": name, "status": "started"}
        self._events.append(event)
        return event

    @staticmethod
    def _object_with_exact_keys(value, keys):
        return type(value) is dict and set(value) == set(keys)

    def lookup_item(self, argument):
        """Look up a public toy record. Input exactly {"key": string}; only key "sample" is available."""
        event = self._spend("lookup_item")
        if (not self._object_with_exact_keys(argument, ("key",))
                or type(argument.get("key")) is not str
                or argument["key"] not in TOY_RECORDS):
            event["status"] = "schema_error"
            raise ToyToolError('lookup_item requires exactly {"key": "sample"}')
        observation = {"found": True, "value": TOY_RECORDS[argument["key"]]}
        self._lookup_value = observation["value"]
        event.update({"status": "ok", "input": {"key": argument["key"]},
                      "observation": deepcopy(observation)})
        return observation

    def store_item(self, argument):
        """Store the value actually returned by lookup_item. Input exactly {"name": string, "value": string}; name must be "copy"."""
        event = self._spend("store_item")
        valid_shape = (self._object_with_exact_keys(argument, ("name", "value"))
                       and type(argument.get("name")) is str
                       and type(argument.get("value")) is str
                       and len(argument.get("value", "")) <= 128)
        if not valid_shape or argument["name"] != "copy":
            event["status"] = "schema_error"
            raise ToyToolError('store_item requires exactly {"name": "copy", "value": string}')
        if self._lookup_value is None or argument["value"] != self._lookup_value:
            event["status"] = "sequence_error"
            raise ToyToolError("store_item requires the value returned by lookup_item")
        self._stored_value = argument["value"]
        observation = {"stored": True}
        event.update({"status": "ok", "input": deepcopy(argument),
                      "observation": deepcopy(observation)})
        return observation

    def read_item(self, argument):
        """Read the stored toy value. Input exactly {"name": string}; name must be "copy" and store_item must have succeeded first."""
        event = self._spend("read_item")
        if (not self._object_with_exact_keys(argument, ("name",))
                or type(argument.get("name")) is not str
                or argument["name"] != "copy"):
            event["status"] = "schema_error"
            raise ToyToolError('read_item requires exactly {"name": "copy"}')
        if self._stored_value is None:
            event["status"] = "sequence_error"
            raise ToyToolError("read_item requires a successful store_item call")
        self._read_value = self._stored_value
        observation = {"found": True, "value": self._read_value}
        event.update({"status": "ok", "input": {"name": argument["name"]},
                      "observation": deepcopy(observation)})
        return observation

    def check_answer(self, request, submitted):
        """Check both the answer and the complete successful host tool sequence."""
        event = self._spend("checker")
        success = False
        tool_events = [row for row in self._events if row.get("kind") in self.tools]
        expected_names = ["lookup_item", "store_item", "read_item"]
        if (request == CANARY_REQUEST
                and type(submitted) is dict and set(submitted) == {"value"}
                and type(submitted.get("value")) is str
                and [row.get("kind") for row in tool_events] == expected_names
                and all(row.get("status") == "ok" for row in tool_events)):
            lookup, store, read = tool_events
            expected_value = TOY_RECORDS["sample"]
            success = (
                lookup.get("input") == {"key": "sample"}
                and lookup.get("observation") == {"found": True, "value": expected_value}
                and store.get("input") == {"name": "copy", "value": expected_value}
                and store.get("observation") == {"stored": True}
                and read.get("input") == {"name": "copy"}
                and read.get("observation") == {"found": True, "value": expected_value}
                and self._lookup_value == self._stored_value == self._read_value == expected_value
                and submitted == {"value": expected_value}
            )
        event["status"] = "accepted" if success else "rejected"
        return success


def build_offline_canary():
    """Return task, broker, tool map, and the host checker for offline review."""
    broker = OfflineToyCanary()
    return CANARY_REQUEST, broker, broker.tools, broker.check_answer


class ContextBudgetRefusal(ValueError):
    """Host-side refusal before any provider request is opened."""


class ActualRequestBudgetExceeded(RuntimeError):
    """A defensive cap preventing more than the reviewed HTTP request budget."""


class RequestCountingOpener:
    """Count actual HTTP opener attempts separately from logical model calls."""

    def __init__(self, delegate, maximum):
        self.delegate = delegate
        self.maximum = maximum
        self.actual_requests = 0
        self.per_logical_attempt = []
        self.elapsed_per_logical_attempt = []
        self._active = None
        self._attempt_started = None

    def begin_attempt(self):
        if self._active is not None:
            raise RuntimeError("nested model attempts are not supported")
        self.per_logical_attempt.append(0)
        self.elapsed_per_logical_attempt.append(None)
        self._active = len(self.per_logical_attempt) - 1
        self._attempt_started = time.monotonic()

    def end_attempt(self):
        if self._active is not None and self._attempt_started is not None:
            self.elapsed_per_logical_attempt[self._active] = max(0.0, time.monotonic() - self._attempt_started)
        self._active = None
        self._attempt_started = None

    def open(self, *args, **kwargs):
        if self._active is None:
            raise RuntimeError("HTTP request occurred outside a logical model attempt")
        if self.actual_requests >= self.maximum:
            raise ActualRequestBudgetExceeded("actual HTTP request budget exhausted")
        self.actual_requests += 1
        self.per_logical_attempt[self._active] += 1
        if self.delegate is None:
            # Used by offline tests; never used by the live JsonModel.
            return None
        return self.delegate.open(*args, **kwargs)


class ContextBoundModel:
    """Count logical calls and refuse oversize contexts before provider access."""

    def __init__(self, wrapped, request_counter, max_context_bytes):
        self.wrapped = wrapped
        self.request_counter = request_counter
        self.max_context_bytes = max_context_bytes
        self.identity = wrapped.identity
        self.max_tokens = wrapped.max_tokens
        self.last_usage = {}
        self.last_response = None

    def propose(self, context):
        self.request_counter.begin_attempt()
        self.last_usage, self.last_response = {}, None
        wrapped_called = False
        try:
            if len(canonical(context).encode("utf-8")) > self.max_context_bytes:
                raise ContextBudgetRefusal("model context exceeds the canary byte ceiling")
            wrapped_called = True
            return self.wrapped.propose(context)
        finally:
            if wrapped_called:
                self.last_usage = getattr(self.wrapped, "last_usage", {}) or {}
                self.last_response = getattr(self.wrapped, "last_response", None)
            self.request_counter.end_attempt()


def _repo_root():
    return CANARY_SOURCE_PATH.parents[2]


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fixture_digest():
    fixture = {"request": CANARY_REQUEST, "records": TOY_RECORDS,
               "tool_names": sorted(("lookup_item", "store_item", "read_item")),
               "tool_budget": TOOL_BUDGET}
    return hashlib.sha256(canonical(fixture).encode("utf-8")).hexdigest()


def _assert_runtime_source_binding(root):
    expected_package = (root / "kuttaka_runtime").resolve()
    package = sys.modules.get("kuttaka_runtime")
    if package is None:
        raise RuntimeError("kuttaka_runtime package was not imported from this checkout")
    package_file = getattr(package, "__file__", None)
    if not isinstance(package_file, str) or Path(package_file).resolve().parent != expected_package:
        raise RuntimeError("loaded kuttaka_runtime package is outside the frozen checkout")
    package_paths = getattr(package, "__path__", ())
    if len(package_paths) != 1 or Path(next(iter(package_paths))).resolve() != expected_package:
        raise RuntimeError("loaded kuttaka_runtime package path differs from the frozen checkout")
    for name, module in tuple(sys.modules.items()):
        if name != "kuttaka_runtime" and not name.startswith("kuttaka_runtime."):
            continue
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str) or not Path(module_file).resolve().is_relative_to(expected_package):
            raise RuntimeError("loaded runtime module is outside the frozen checkout")


def _profile(effort):
    if effort not in ("high", "low"):
        raise ValueError("only the frozen high and low profiles are available")
    return {
        "model": MODEL_NAME,
        "protocol": "openai",
        "endpoint": MODEL_ENDPOINT,
        "temperature": 0.95,
        "top_p": 1.0,
        "reasoning_effort": effort,
        "chat_template_kwargs": None,
        "max_tokens": MAX_RESPONSE_TOKENS,
        "stream": True,
        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "parse_mode": "strict",
        "reasoning_hint": None,
        "sampling_fields": True,
        "context_bytes": MAX_CONTEXT_BYTES,
        "credential_env": API_KEY_ENV,
        "detailed_response_feedback": False,
        "normalize": False,
        "continue_transport_failures": False,
        "automatic_submission": False,
    }


def freeze_manifest():
    """Return the separate review manifest; this function never writes it."""
    root = _repo_root()
    _assert_runtime_source_binding(root)
    files = (
        "experiments/astra_interface_canary/run.py",
        "experiments/astra_interface_canary/PROTOCOL.md",
        "tests/test_astra_interface_canary.py",
    )
    runtime_files = tuple(sorted(
        path.relative_to(root).as_posix()
        for path in (root / "kuttaka_runtime").glob("*.py")
        if path.is_file()))
    files = (*files, *runtime_files)
    hashes = {}
    for relative in files:
        target = root / relative
        if not target.is_file():
            raise RuntimeError(f"freeze input is missing: {relative}")
        hashes[relative] = _sha256_file(target)
    return {
        "format": "task-neutral-interface-canary-freeze-v1",
        "repository_root": ".",
        "canary_source_path": CANARY_SOURCE_PATH.relative_to(root).as_posix(),
        "freeze_manifest_path": FREEZE_PATH.resolve().relative_to(root).as_posix(),
        "source_sha256": dict(sorted(hashes.items())),
        "toy_fixture_sha256": _fixture_digest(),
        "profiles": {arm: _profile(arm) for arm in ARM_ORDER},
        "arm_order": list(ARM_ORDER),
        "budgets": {
            "model_attempts_per_arm": MODEL_REQUESTS_PER_ARM,
            "actual_http_requests_per_arm": MODEL_REQUESTS_PER_ARM,
            "actual_http_requests_total": MAX_MODEL_REQUESTS_TOTAL,
            "runtime_tool_calls_per_arm_including_checker": TOOL_BUDGET,
        },
        "interpretation": "single public toy interface diagnostic; no evaluated-task or capability claim",
    }


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def validate_freeze(freeze_path=None):
    """Fail closed unless the externally reviewed manifest matches this tree."""
    freeze_path = FREEZE_PATH if freeze_path is None else Path(freeze_path)
    try:
        if Path(freeze_path).resolve() != FREEZE_PATH.resolve():
            raise RuntimeError("canary freeze manifest must be adjacent to the canary source")
    except OSError as error:
        raise RuntimeError("canary freeze manifest path is invalid") from error
    try:
        recorded = json.loads(freeze_path.read_text(encoding="utf-8"),
                              object_pairs_hook=_unique_pairs,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, ValueError) as error:
        raise RuntimeError("reviewed canary freeze manifest is missing or malformed") from error
    expected = freeze_manifest()
    if recorded != expected:
        raise RuntimeError("canary freeze manifest differs from code, tests, protocol, profile, order, or budget")
    return hashlib.sha256(freeze_path.read_bytes()).hexdigest()


def _private_output_path(output):
    root = (_repo_root() / ".astra").resolve()
    target = Path(output).expanduser().resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError("canary output must be a new child of repository .astra")
    if target.exists():
        raise FileExistsError("canary output must be fresh")
    if not target.parent.is_dir():
        raise ValueError("canary output parent must already exist below .astra")
    return target


def _adapt_tools(tools):
    adapted = {}
    for name, tool in tools.items():
        def invoke(argument, _tool=tool):
            try:
                return _tool(argument)
            except ToyToolError:
                raise ToolInputError("toy canary tool rejected its schema or call order") from None
        invoke.__name__ = name
        invoke.__doc__ = tool.__doc__ or ""
        adapted[name] = invoke
    return adapted


def _safe_finish_reason(usage):
    value = usage.get("finish_reason") if type(usage) is dict else None
    allowed = {"stop", "length", "tool_calls", "content_filter"}
    return value if type(value) is str and value in allowed else (
        "unknown" if value is None else "other")


def _schema_rejection_category(action):
    """Map rejected actions to fixed categories without returning model data."""
    if type(action) is not dict:
        return "non_object_action"
    kind = action.get("kind")
    if kind == "experiment":
        if set(action) != {"kind", "tool", "input", "predictions"}:
            return "experiment_fields"
        if type(action.get("tool")) is not str or action["tool"] not in {
                "lookup_item", "store_item", "read_item"}:
            return "experiment_tool"
        predictions = action.get("predictions")
        if type(predictions) is not list or not predictions:
            return "experiment_predictions_required"
        if any(type(item) is not dict or set(item) != {"hypothesis", "outcome"}
               or type(item.get("hypothesis")) is not str or not item["hypothesis"].strip()
               for item in predictions):
            return "experiment_prediction_fields"
        return "experiment_schema_other"
    if kind == "answer":
        return "answer_fields" if set(action) != {"kind", "answer"} else "answer_schema_other"
    if kind == "unresolved":
        return "unresolved_fields"
    return "unsupported_action_kind"


def _classify_attempt_action(action, usage, segment):
    """Return fixed action/schema/tool categories for one logical attempt."""
    rejected = any(event["kind"] == "action_rejected" for event in segment)
    if action is None:
        parse_status, action_category = "non_object_action", "non_object_action"
        action_kind = "non_object"
        schema_status = "rejected" if rejected else "unavailable"
        rejection_category = _schema_rejection_category(action) if rejected else None
    elif (type(action) is dict and set(action) == {"kind", "raw_reply"}
          and action.get("kind") == "invalid"):
        parse_status = "invalid_sentinel"
        action_kind = "invalid"
        classified = classify_invalid_action(action, usage)
        if classified is not None:
            action_category = {
                "empty_final_content": "empty_final_stop",
                "empty_final_content_truncated": "empty_final_length",
                "invalid_json_after_truncation": "nonempty_invalid_json_length",
                "invalid_json": "nonempty_invalid_json_other",
            }[classified["category"]]
        else:
            raw = action.get("raw_reply")
            finish = usage.get("finish_reason") if type(usage) is dict else None
            if type(raw) is not str:
                action_category = "invalid_parser_reply"
            elif not raw.strip():
                action_category = "empty_final_other_finish"
            else:
                try:
                    parsed = json.loads(raw)
                except (ValueError, TypeError):
                    action_category = ("nonempty_invalid_json_other" if finish in ("stop", "length")
                                       else "nonempty_invalid_json_unknown_finish")
                else:
                    action_category = "non_object_json" if type(parsed) is not dict else "invalid_parser_object"
        schema_status = "rejected" if rejected else "unavailable"
        rejection_category = "parser_invalid_reply" if rejected else None
    elif type(action) is not dict:
        parse_status, action_category = "non_object_action", "non_object_action"
        action_kind = "non_object"
        schema_status = "rejected" if rejected else "unavailable"
        rejection_category = _schema_rejection_category(action) if rejected else None
    else:
        parse_status = "object"
        kind = action.get("kind")
        action_kind = kind if type(kind) is str and kind in {"experiment", "answer", "unresolved"} else "other"
        allowed_kinds = {"experiment": "valid_experiment", "answer": "valid_answer",
                         "unresolved": "valid_unresolved"}
        if rejected:
            action_category = "schema_rejected_action"
            schema_status = "rejected"
            rejection_category = _schema_rejection_category(action)
        elif type(kind) is str and kind in allowed_kinds:
            action_category = allowed_kinds[kind]
            schema_status = "accepted"
            rejection_category = None
        else:
            action_category = "unsupported_action_kind"
            schema_status = "unavailable"
            rejection_category = None

    tool_attempt = next((event for event in segment if event["kind"] == "tool_attempt"), None)
    if tool_attempt is None:
        tool_status = "not_attempted"
    else:
        observation_event = next((event["body"] for event in segment
                                  if event["kind"] == "observation"), None)
        tool_error = next((event["body"] for event in segment if event["kind"] == "tool_error"), None)
        observation = observation_event.get("observation") if type(observation_event) is dict else None
        if (type(observation) is dict and observation.get("status") == "tool_rejection") or observation is False:
            tool_status = "rejected"
        elif type(tool_error) is dict:
            tool_status = "failed"
        elif type(observation_event) is dict:
            tool_status = "accepted"
        else:
            tool_status = "failed"
    return {"action_parse_status": parse_status,
            "action_kind": action_kind,
            "action_category": action_category,
            "action_schema_status": schema_status,
            "schema_rejection_category": rejection_category,
            "tool_call_status": tool_status}


def _account_attempts(events, request_counts, elapsed_per_attempt, *, actual_http_requests=None):
    attempts = [(index, event) for index, event in enumerate(events)
                if event["kind"] == "model_attempt"]
    per_attempt = []
    known_input = known_output = 0
    unknown_input = unknown_output = 0
    actual_total = 0
    unknown_request_count_attempts = 0
    action_categories = []
    schema_rejection_categories = []
    for index, (event_index, attempt) in enumerate(attempts):
        end = attempts[index + 1][0] if index + 1 < len(attempts) else len(events)
        segment = events[event_index + 1:end]
        response = next((event for event in segment if event["kind"] == "model_response"), None)
        err = next((event["body"] for event in segment if event["kind"] == "model_error"), None)
        count = request_counts[index] if index < len(request_counts) else None
        usage = response["body"].get("usage") if response else None
        receipt = response["body"].get("receipt") if response else None
        if type(count) is int and count >= 0:
            actual_total += count
        else:
            unknown_request_count_attempts += 1
        input_tokens = output_tokens = None
        reasoning_chars = None
        finish = "unknown"
        if type(count) is int and count > 0:
            if type(usage) is dict:
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                input_known = type(input_tokens) is int and input_tokens >= 0
                output_known = type(output_tokens) is int and output_tokens >= 0
                if input_known:
                    known_input += input_tokens
                else:
                    input_tokens = None
                if output_known:
                    known_output += output_tokens
                else:
                    output_tokens = None
                # JsonModel makes one request per call. If that changes, one
                # reported usage row cannot account for any additional opens.
                unknown_input += count - (1 if input_known else 0)
                unknown_output += count - (1 if output_known else 0)
                raw_reasoning_chars = usage.get("reasoning_chars")
                if type(raw_reasoning_chars) is int and raw_reasoning_chars >= 0:
                    reasoning_chars = raw_reasoning_chars
                finish = _safe_finish_reason(usage)
            else:
                unknown_input += count
                unknown_output += count
        receipt_body = receipt.get("body") if type(receipt) is dict else None
        body_bytes = receipt_body.encode("utf-8") if type(receipt_body) is str else None
        reply_event = next((event for event in segment if event["kind"] == "model_reply"), None)
        reply = reply_event["body"] if reply_event is not None else None
        action = reply.get("action") if type(reply) is dict else None
        reply_usage = reply.get("usage") if type(reply) is dict else usage
        categories = (_classify_attempt_action(action, reply_usage, segment) if reply_event is not None
                      else {"action_parse_status": "no_model_reply",
                            "action_category": "no_model_reply",
                            "action_schema_status": "unavailable",
                            "schema_rejection_category": None,
                            "tool_call_status": "not_attempted"})
        action_categories.append(categories["action_category"])
        if categories["schema_rejection_category"] is not None:
            schema_rejection_categories.append(categories["schema_rejection_category"])
        per_attempt.append({
            "attempt_number": attempt["body"].get("number"),
            "actual_http_requests": count,
            "elapsed_seconds": (elapsed_per_attempt[index]
                                if index < len(elapsed_per_attempt) else None),
            "http_status": receipt.get("status") if type(receipt) is dict else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_chars": reasoning_chars,
            "finish_reason": finish,
            "receipt_body_bytes": len(body_bytes) if body_bytes is not None else None,
            "receipt_body_sha256": hashlib.sha256(body_bytes).hexdigest() if body_bytes is not None else None,
            "error_type": err.get("type") if type(err) is dict else None,
            "error_status": err.get("status") if type(err) is dict else None,
            "error_kind": err.get("kind") if type(err) is dict and type(err.get("kind")) is str and err.get("kind") in {
                "provider_http_error", "request_timeout", "request_transport_error",
                "response_read_error", "empty_stream", "incomplete_stream", "stream_error"
            } else None,
            **categories,
        })
    if len(request_counts) > len(attempts):
        extra = request_counts[len(attempts):]
        actual_total += sum(value for value in extra if type(value) is int and value >= 0)
        unknown_input += sum(value for value in extra if type(value) is int and value > 0)
        unknown_output += sum(value for value in extra if type(value) is int and value > 0)
    errors = [event["body"] for event in events if event["kind"] == "model_error"]
    context_refusals = sum(event.get("type") == "ContextBudgetRefusal" for event in errors)
    hard_4xx = sum(type(event.get("status")) is int
                   and 400 <= event["status"] < 500 for event in errors)
    request_rows_complete = (len(request_counts) == len(attempts)
                             and all(type(value) is int and value >= 0 for value in request_counts))
    direct_count_valid = type(actual_http_requests) is int and actual_http_requests >= 0
    request_accounting_complete = request_rows_complete and (
        not direct_count_valid or actual_total == actual_http_requests)
    counted_http_requests = actual_http_requests if direct_count_valid else (
        actual_total if request_accounting_complete else None)
    category_counts = {}
    for category in action_categories:
        category_counts[category] = category_counts.get(category, 0) + 1
    rejection_counts = {}
    for category in schema_rejection_categories:
        rejection_counts[category] = rejection_counts.get(category, 0) + 1
    return {
        "logical_model_attempts": len(attempts),
        "actual_http_requests": counted_http_requests,
        "known_http_request_subtotal": actual_total,
        "request_accounting_complete": request_accounting_complete,
        "unknown_http_request_count_attempts": unknown_request_count_attempts,
        "unmatched_http_request_count_rows": abs(len(request_counts) - len(attempts)),
        "http_request_counts_by_attempt": request_counts,
        "known_input_token_subtotal": known_input,
        "known_output_token_subtotal": known_output,
        "input_token_total": known_input if unknown_input == 0 and request_accounting_complete else None,
        "output_token_total": known_output if unknown_output == 0 and request_accounting_complete else None,
        "unknown_input_usage_requests": unknown_input if request_accounting_complete else None,
        "unknown_output_usage_requests": unknown_output if request_accounting_complete else None,
        "host_context_refusals": context_refusals,
        "provider_http_or_transport_failures": sum(
            (type(event.get("type")) is str and event.get("type") in {"TransportFailure", "HTTPError", "URLError",
                                           "TimeoutError", "ConnectionError", "ConnectionResetError", "OSError"})
            or (type(event.get("status")) is int and 400 <= event["status"] <= 599)
            for event in errors),
        "adapter_failures": sum((type(event.get("type")) is not str or event.get("type") not in {
            "ContextBudgetRefusal", "TransportFailure", "HTTPError", "URLError",
            "TimeoutError", "ConnectionError", "ConnectionResetError", "OSError"
        }) and not (type(event.get("status")) is int
                   and 400 <= event["status"] <= 599) for event in errors),
        "hard_4xx_terminal_errors": hard_4xx,
        "action_category_counts": dict(sorted(category_counts.items())),
        "schema_accepted_actions": sum(row["action_schema_status"] == "accepted" for row in per_attempt),
        "schema_rejected_actions": sum(row["action_schema_status"] == "rejected" for row in per_attempt),
        "schema_rejection_categories": dict(sorted(rejection_counts.items())),
        "accepted_tool_calls": sum(row["tool_call_status"] == "accepted" for row in per_attempt),
        "rejected_tool_calls": sum(row["tool_call_status"] == "rejected" for row in per_attempt),
        "failed_tool_calls": sum(row["tool_call_status"] == "failed" for row in per_attempt),
        "attempts": per_attempt,
    }


def _run_arm(arm_dir, effort, *, model_factory,
             max_actual_requests=MODEL_REQUESTS_PER_ARM, investigator_fn=investigate):
    """Run one arm; the explicit factory is injected by run_live or offline tests."""
    arm_dir = Path(arm_dir)
    arm_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    profile = _profile(effort)
    request, broker, raw_tools, checker = build_offline_canary()
    tools = _adapt_tools(raw_tools)
    started = time.monotonic()
    request_counter = RequestCountingOpener(None, max_actual_requests)
    events_path = arm_dir / "events.jsonl"
    result_data = None
    try:
        model = model_factory(
            profile["model"], profile["endpoint"], protocol=profile["protocol"],
            key_env=profile["credential_env"], timeout=profile["timeout_seconds"],
            max_tokens=profile["max_tokens"], temperature=profile["temperature"],
            top_p=profile["top_p"], reasoning_effort=profile["reasoning_effort"],
            stream=profile["stream"], reasoning_hint=None,
            sampling_fields=True, parse_mode="strict")
        if not hasattr(model, "_opener"):
            model._opener = None
        request_counter.delegate = model._opener
        model._opener = request_counter
        bounded_model = ContextBoundModel(model, request_counter, MAX_CONTEXT_BYTES)
        with Journal(events_path) as journal:
            result = investigator_fn(
                request, bounded_model, tools, checker, journal,
                Budget(model_calls=MODEL_REQUESTS_PER_ARM, tool_calls=TOOL_BUDGET),
                normalize=False, automatic_submission=False,
                continue_transport_failures=False, outage_limit=None,
                detailed_response_feedback=False)
        result_data = asdict(result)
    except Exception as error:
        # Record only the exception class; provider text is retained only in the
        # bounded, credential-scrubbed response receipt inside the private journal.
        result_data = {"status": "host_failure", "error_type": type(error).__name__}
        if events_path.exists():
            try:
                with Journal.resume(events_path, expected_head=Journal.read(events_path)[-1]["hash"]) as journal:
                    journal.append("canary_host_failure", {"type": type(error).__name__})
            except Exception:
                pass
    elapsed = time.monotonic() - started
    journal_integrity = "missing"
    journal_error_type = None
    if events_path.exists():
        try:
            events = Journal.read(events_path)
            journal_integrity = "valid"
        except Exception as error:
            events = []
            journal_integrity = "invalid"
            journal_error_type = type(error).__name__
    else:
        events = []
    accounting = _account_attempts(events, request_counter.per_logical_attempt,
                                   request_counter.elapsed_per_logical_attempt,
                                   actual_http_requests=request_counter.actual_requests)
    if journal_integrity != "valid":
        accounting["logical_model_attempts"] = None
        accounting["input_token_total"] = None
        accounting["output_token_total"] = None
    if type(accounting["actual_http_requests"]) is not int:
        raise AssertionError("actual HTTP request total is unavailable")
    if accounting["actual_http_requests"] > max_actual_requests:
        raise AssertionError("actual HTTP request ceiling exceeded")
    outcome = {
        "arm": effort,
        "profile": profile,
        "result": result_data,
        "elapsed_seconds": elapsed,
        "accounting": accounting,
        "journal_integrity": journal_integrity,
        "journal_error_type": journal_error_type,
        "toy_host_events": broker.events,
        "journal_sha256": _sha256_file(events_path) if events_path.exists() else None,
        "journal_event_count": len(events),
    }
    _write_private_json(arm_dir / "arm-result.json", outcome)
    return outcome


def _write_private_json(path, value):
    Path(path).write_bytes(canonical(value).encode("utf-8") + b"\n")
    os.chmod(path, 0o600)


def run_live(output, *, opt_in=False):
    """Opt-in live entry point; requires a separately reviewed freeze.json."""
    if opt_in is not True:
        raise RuntimeError("live canary requires explicit opt_in=True / --live")
    freeze_sha256 = validate_freeze()
    output = _private_output_path(output)
    if not (os.environ.get(API_KEY_ENV) or "").strip():
        raise RuntimeError(f"required credential environment variable {API_KEY_ENV} is absent")
    if MODEL_ENDPOINT != "https://integrate.api.nvidia.com/v1":
        raise RuntimeError("fixed canary endpoint does not match frozen endpoint")

    previous_umask = os.umask(0o077)
    try:
        output.mkdir(mode=0o700, parents=False, exist_ok=False)
        protocol = {
            "format": "task-neutral-interface-canary-run-v1",
            "freeze_sha256": freeze_sha256,
            "arm_order": list(ARM_ORDER),
            "profiles": {arm: _profile(arm) for arm in ARM_ORDER},
            "toy_fixture_sha256": _fixture_digest(),
            "preflight": {"key_present": True, "fixed_endpoint": MODEL_ENDPOINT,
                          "network_preflight_requests": 0},
        }
        _write_private_json(output / "protocol.json", protocol)
        arm_outcomes = []
        total_requests = 0
        for effort in ARM_ORDER:
            remaining = MAX_MODEL_REQUESTS_TOTAL - total_requests
            arm_budget = min(MODEL_REQUESTS_PER_ARM, remaining)
            outcome = _run_arm(output / effort, effort, max_actual_requests=arm_budget,
                               model_factory=JsonModel)
            arm_outcomes.append(outcome)
            total_requests += outcome["accounting"]["actual_http_requests"]
        if total_requests > MAX_MODEL_REQUESTS_TOTAL:
            raise AssertionError("campaign actual request ceiling exceeded")
        campaign = {
            "format": "task-neutral-interface-canary-result-v1",
            "arm_order": list(ARM_ORDER),
            "actual_http_requests_total": total_requests,
            "max_actual_http_requests_total": MAX_MODEL_REQUESTS_TOTAL,
            "outcomes": arm_outcomes,
        }
        _write_private_json(output / "campaign-result.json", campaign)
        return campaign
    finally:
        os.umask(previous_umask)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="explicitly opt in to two live development-endpoint toy sessions")
    parser.add_argument("--output", required=True, type=Path,
                        help="fresh private child directory of repository .astra")
    args = parser.parse_args(argv)
    result = run_live(args.output, opt_in=args.live)
    print(json.dumps({"status": "complete", "arms": len(result["outcomes"]),
                      "actual_http_requests_total": result["actual_http_requests_total"]},
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
