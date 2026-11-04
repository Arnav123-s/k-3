"""Produce a privacy-filtered, integrity-checked projection of a completed run.

This module is reporting-only. It does not execute candidates, contact a model,
or invoke Docker. It refuses to overwrite its output directory. Raw model
contexts, provider bodies/reasoning, task values, expected outputs and candidate
source are omitted unless the explicit private artifact-export option is used.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import zlib


SEEDS = (1171, 1173, 1177)
ARMS = ("WITH_CONSTRUCTOR", "MODEL_ONLY")
EXPECTED_RUNS = tuple((seed, arm) for seed in SEEDS for arm in ARMS)
REUSE_SEED = 1181
ID_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_DEPENDENCIES = 128
REPORT_TOOLS = {"remember_method", "inspect_method", "find_methods",
                "probe_method", "validate_method", "observe_next", "fit_relation"}


class ReportError(ValueError):
    pass


def _canonical(value, *, artifact=False):
    options = {"sort_keys": True, "separators": (",", ":"), "allow_nan": False}
    if artifact:
        options["ensure_ascii"] = False
    return json.dumps(value, **options).encode("utf-8")


def _reject_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def load_json(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except FileNotFoundError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ReportError(f"malformed JSON: {path.name}") from error


def sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _journal_prefix_hashes(path: Path):
    """Return digests at valid line boundaries, including the empty prefix."""
    digest = hashlib.sha256()
    prefixes = {digest.hexdigest()}
    try:
        with path.open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    raise ReportError(f"incomplete journal line: {path.name}")
                digest.update(line)
                prefixes.add(digest.hexdigest())
    except OSError as error:
        raise ReportError(f"journal unreadable: {path.name}") from error
    return prefixes


def _journal_prefix_length(path: Path, expected: str):
    """Return the row count at an exact recorded byte-prefix digest."""
    digest = hashlib.sha256()
    if digest.hexdigest() == expected:
        return 0
    try:
        with path.open("rb") as stream:
            count = 0
            for line in stream:
                digest.update(line)
                count += 1
                if digest.hexdigest() == expected:
                    return count
    except OSError:
        return None
    return None


def read_journal(path: Path, *, artifact=False):
    """Strictly validate the evidence chain before exposing event summaries."""
    events = []
    try:
        with path.open("rb") as stream:
            for raw_line in stream:
                if not raw_line.endswith(b"\n"):
                    raise ReportError(f"incomplete journal line: {path.name}")
                try:
                    event = json.loads(raw_line, object_pairs_hook=_unique_object,
                                       parse_constant=_reject_constant)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                    raise ReportError(f"malformed journal line: {path.name}") from error
                if (not isinstance(event, dict)
                        or set(event) != {"seq", "kind", "body", "prev", "hash"}
                        or type(event.get("seq")) is not int
                        or event["seq"] != len(events)
                        or not isinstance(event.get("kind"), str)
                        or not isinstance(event.get("body"), dict)):
                    raise ReportError(f"invalid journal record: {path.name}")
                expected_prev = events[-1]["hash"] if events else ""
                if event["prev"] != expected_prev:
                    raise ReportError(f"journal chain break: {path.name}")
                payload = {key: value for key, value in event.items() if key != "hash"}
                actual_hash = hashlib.sha256(_canonical(payload, artifact=artifact)).hexdigest()
                if event["hash"] != actual_hash:
                    raise ReportError(f"journal hash mismatch: {path.name}")
                events.append(event)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise ReportError(f"journal unreadable: {path.name}") from error
    return events


def _counter(values):
    return dict(sorted(Counter(values).items()))


def _safe_int(value):
    return value if type(value) is int and value >= 0 else None


def _status_for_acquisition(record):
    if not isinstance(record, dict):
        return "missing"
    result = record.get("result")
    if isinstance(result, dict) and isinstance(result.get("status"), str):
        return result["status"]
    if record.get("status") == "study_run_failure":
        return "study_run_failure"
    return "incomplete_record"


def _forecast_summary(events):
    releases = [event["body"] for event in events if event["kind"] == "live_observation_release"]
    correct = sum(body.get("matches") is True for body in releases)
    incorrect = sum(body.get("matches") is False for body in releases)
    known = correct + incorrect
    result = {"observation_releases": len(releases), "correct": correct,
              "incorrect": incorrect,
              "unknown_outcome": len(releases) - known,
              "canonical_format_matches": sum(body.get("canonical_format_match") is True
                                               for body in releases),
              "canonical_format_mismatches": sum(body.get("canonical_format_match") is False
                                                 for body in releases),
              "canonical_format_unknown": sum(body.get("canonical_format_match") is not True
                                               and body.get("canonical_format_match") is not False
                                               for body in releases)}
    proposals = [event["body"] for event in events if event["kind"] == "proposal"]
    predictions = []
    for action in proposals:
        if action.get("kind") == "experiment":
            rows = action.get("predictions")
            if isinstance(rows, list):
                predictions.extend(rows)
    result["committed_prediction_records"] = len(predictions)
    result["experiment_proposals"] = sum(action.get("kind") == "experiment" for action in proposals)
    result["explicit_revisions"] = sum(event["kind"] == "representation_revision"
                                        or event["kind"] == "retraction"
                                        for event in events)
    return result


def _schema_valid_tool_action(action):
    """Validate the runtime action envelope; tool-input errors still count as calls."""
    if (not isinstance(action, dict) or
            set(action) != {"kind", "tool", "input", "predictions"} or
            action.get("kind") != "experiment" or
            not isinstance(action.get("tool"), str) or action["tool"] not in REPORT_TOOLS or
            not isinstance(action.get("predictions"), list) or not action["predictions"]):
        return False
    if any(not isinstance(prediction, dict)
           or set(prediction) != {"hypothesis", "outcome"}
           or not isinstance(prediction.get("hypothesis"), str)
           or not prediction["hypothesis"].strip()
           for prediction in action["predictions"]):
        return False
    return "input" in action


def _unknown_constructor_evidence(status):
    return {"status": status, "schema_valid_tool_attempts_by_name": None,
            "fit_relation_attempts": None, "fit_relation_proposed_not_called": None,
            "fit_relation_rejections": None, "fit_relation_attempt_metadata": None,
            "derivation_receipt_count": None, "derivation_receipt_statuses": None,
            "derivation_receipts": None, "receipt_evidence_scope": "finite_fit_receipt_only"}


def _constructor_evidence(events):
    """Summarize executable tool and finite-fit receipt evidence without payloads."""
    if not isinstance(events, list):
        return _unknown_constructor_evidence("journal_missing_or_invalid")
    attempts = Counter()
    proposed_valid = Counter()
    attempted_fit = 0
    fit_not_called = 0
    fit_rejections = 0
    fit_metadata = []
    receipts = []
    malformed = False
    index = 0
    while index < len(events):
        event = events[index]
        if not isinstance(event, dict) or not isinstance(event.get("kind"), str) or not isinstance(event.get("body"), dict):
            malformed = True
            index += 1
            continue
        if event["kind"] == "proposal":
            action = event["body"]
            if _schema_valid_tool_action(action):
                tool = action["tool"]
                proposed_valid[tool] += 1
                next_event = events[index + 1] if index + 1 < len(events) else None
                invoked = (isinstance(next_event, dict) and next_event.get("kind") == "tool_attempt"
                           and isinstance(next_event.get("body"), dict)
                           and next_event["body"].get("kind") == "experiment")
                if invoked:
                    attempts[tool] += 1
                    if tool == "fit_relation":
                        attempted_fit += 1
                        request = action.get("input")
                        request = request if isinstance(request, dict) else {}
                        raw_order, raw_degree = request.get("order"), request.get("degree")
                        order = raw_order if type(raw_order) is int and 1 <= raw_order <= 4 else None
                        degree = raw_degree if type(raw_degree) is int and 0 <= raw_degree <= 4 else None
                        tail = events[index + 2:index + 5]
                        rejected = any(isinstance(item, dict) and item.get("kind") in ("tool_error", "tool_rejection")
                                       for item in tail)
                        if rejected:
                            fit_rejections += 1
                        fit_metadata.append({"order": order, "degree": degree,
                                             "sample_count": None,
                                             "outcome": "tool_rejected" if rejected else "invoked"})
                elif tool == "fit_relation":
                    fit_not_called += 1
            index += 1
            continue
        if event["kind"] == "derivation_receipt":
            body = event["body"]
            if (type(body.get("order")) is int and type(body.get("degree")) is int
                    and type(body.get("sample_count")) is int and body["sample_count"] >= 0
                    and isinstance(body.get("status"), str)
                    and isinstance(event.get("hash"), str) and ID_RE.fullmatch(event["hash"])):
                status = body["status"] if body["status"] in {"unique", "underdetermined", "no_fit"} else "unknown"
                receipts.append({"receipt_id": event["hash"], "status": status,
                                 "order": body["order"], "degree": body["degree"],
                                 "sample_count": body["sample_count"]})
                for metadata in reversed(fit_metadata):
                    if (metadata["order"] == body["order"] and metadata["degree"] == body["degree"]
                            and metadata["outcome"] == "invoked" and metadata["sample_count"] is None):
                        metadata["sample_count"] = body["sample_count"]
                        metadata["outcome"] = "finite_fit_receipt"
                        break
            else:
                malformed = True
        index += 1
    if malformed:
        return _unknown_constructor_evidence("malformed_event_shape")
    return {"status": "available",
            "schema_valid_tool_attempts_by_name": dict(sorted(attempts.items())),
            "schema_valid_tool_proposals_not_called_by_name": {
                name: max(0, count - attempts[name]) for name, count in sorted(proposed_valid.items())
                if count > attempts[name]},
            "fit_relation_attempts": attempted_fit,
            "fit_relation_proposed_not_called": fit_not_called,
            "fit_relation_rejections": fit_rejections,
            "fit_relation_attempt_metadata": fit_metadata,
            "derivation_receipt_count": len(receipts),
            "derivation_receipt_statuses": _counter(item["status"] for item in receipts),
            "derivation_receipts": receipts,
            "receipt_evidence_scope": "finite_fit_receipt_only"}


def _failure_categories(events):
    model_errors = [event["body"] for event in events if event["kind"] == "model_error"]
    tool_errors = [event["body"] for event in events
                   if event["kind"] in ("tool_error", "tool_rejection")]
    sandbox_results = [event["body"] for event in events
                       if event["kind"] == "program_execution_result"]
    responses = [event["body"] for event in events if event["kind"] == "model_response"]
    raw_finish_reasons = [body.get("usage", {}).get("finish_reason")
                          for body in responses if isinstance(body.get("usage"), dict)]
    allowed_finish_reasons = {"stop", "length", "tool_calls", "content_filter"}
    finish_reasons = [value if value in allowed_finish_reasons else "other"
                      for value in raw_finish_reasons if isinstance(value, str)]
    action_rejections = [event["body"] for event in events if event["kind"] == "action_rejected"]
    model_replies = [event["body"] for event in events if event["kind"] == "model_reply"]
    allowed_error_types = {"TransportFailure", "HTTPError", "URLError", "TimeoutError",
                           "ConnectionError", "ConnectionResetError", "OSError"}
    allowed_transport_kinds = {"provider_http_error", "request_timeout", "request_transport_error",
                               "response_read_error", "empty_stream", "incomplete_stream", "stream_error"}
    safe_error_types = [value if isinstance(value, str) and value in allowed_error_types
                        else "OtherModelError" for value in
                        (body.get("type") for body in model_errors)]
    safe_error_kinds = [value if isinstance(value, str) and value in allowed_transport_kinds
                        else "Other" for value in (body.get("kind") for body in model_errors)]
    # Keep endpoint HTTP failures separate from timeouts/transport failures.
    # In particular, a 200 response that fails while streaming is not an HTTP
    # provider rejection, even though the adapter reports TransportFailure.
    provider_failures = [body for body in model_errors
                         if (type(body.get("status")) is int and 400 <= body["status"] <= 599)
                         or body.get("kind") == "provider_http_error"]
    transport_failures = [body for body in model_errors
                          if body not in provider_failures
                          and (body.get("type") in allowed_error_types
                               or body.get("kind") in allowed_transport_kinds)]
    host_context_refusals = [body for body in model_errors
                             if body.get("type") == "ContextBudgetRefusal"]
    adapter_failures = [body for body in model_errors
                        if body not in provider_failures and body not in transport_failures
                        and body not in host_context_refusals]
    empty_final_contents = sum(
        isinstance(body.get("action"), dict)
        and body["action"].get("kind") == "invalid"
        and body["action"].get("raw_reply") == ""
        for body in model_replies)
    return {
        "model_errors": _counter(safe_error_kinds),
        "model_error_types": _counter(safe_error_types),
        "model_error_events": len(model_errors),
        "provider_failure_events": len(provider_failures),
        "provider_failure_types": _counter(
            body.get("type") if isinstance(body.get("type"), str) else "HTTPStatusError"
            for body in provider_failures),
        "transport_failure_events": len(transport_failures),
        "transport_failure_types": _counter(
            body.get("kind") if body.get("kind") in allowed_transport_kinds
            else (body.get("type") if body.get("type") in allowed_error_types else "OtherTransportFailure")
            for body in transport_failures),
        "host_context_refusals": len(host_context_refusals),
        "adapter_failure_events": len(adapter_failures),
        "adapter_failure_types": _counter(safe_type for safe_type, body in zip(safe_error_types, model_errors)
                                           if body in adapter_failures),
        "model_http_statuses": _counter(str(body.get("status")) for body in provider_failures
                                         if type(body.get("status")) is int),
        "empty_stream_transport_failures": sum(body.get("kind") == "empty_stream"
                                                for body in transport_failures),
        "empty_final_contents": empty_final_contents,
        "response_finish_reasons": _counter(value for value in finish_reasons
                                             if isinstance(value, str)),
        "responses_with_unknown_finish_reason": sum(
            not isinstance(body.get("usage"), dict)
            or not isinstance(body["usage"].get("finish_reason"), str)
            for body in responses),
        "truncated_responses_finish_reason_length": sum(value == "length" for value in raw_finish_reasons),
        "action_rejections": len(action_rejections),
        "protocol_rejections": sum(body.get("status") == "protocol_rejection"
                                    for body in action_rejections),
        "tool_errors": _counter(body.get("type", "unknown") for body in tool_errors),
        "sandbox_results": _counter(body.get("status", "unknown") for body in sandbox_results),
        "study_run_failures": sum(event["kind"] == "study_run_failure" for event in events),
    }


def _usage_projection(events, record):
    responses = [event["body"].get("usage") for event in events
                 if event["kind"] == "model_response"]
    input_values = [_safe_int(item.get("input_tokens")) for item in responses
                    if isinstance(item, dict)]
    output_values = [_safe_int(item.get("output_tokens")) for item in responses
                     if isinstance(item, dict)]
    known_input = [value for value in input_values if value is not None]
    known_output = [value for value in output_values if value is not None]
    # These are sums of values explicitly reported in response rows. They are
    # subtotals, not total billed usage when the persisted unknown count is nonzero.
    return {
        "logical_model_attempts": (_safe_int(record.get("logical_model_attempts"))
                                   if isinstance(record, dict) else None),
        "provider_request_attempts": (_safe_int(record.get("provider_request_attempts"))
                                       if isinstance(record, dict) else None),
        "host_context_refusals": (_safe_int(record.get("host_context_refusals"))
                                   if isinstance(record, dict) else None),
        "reported_input_token_subtotal": sum(known_input) if known_input else None,
        "reported_input_rows": len(known_input),
        "unknown_input_usage_requests": (_safe_int(record.get("unknown_input_usage_requests"))
                                         if isinstance(record, dict) else None),
        "recorded_known_input_token_subtotal": (_safe_int(record.get("model_input_tokens"))
                                                 if isinstance(record, dict) else None),
        "recorded_input_token_total": (
            _safe_int(record.get("model_input_tokens"))
            if isinstance(record, dict) and record.get("unknown_input_usage_requests") == 0
            else None),
        "reported_output_token_subtotal": sum(known_output) if known_output else None,
        "reported_output_rows": len(known_output),
        "unknown_output_usage_requests": (_safe_int(record.get("unknown_output_usage_requests"))
                                          if isinstance(record, dict) else None),
        "recorded_known_output_token_subtotal": (_safe_int(record.get("model_output_tokens"))
                                                  if isinstance(record, dict) else None),
        "recorded_output_token_total": (
            _safe_int(record.get("model_output_tokens"))
            if isinstance(record, dict) and record.get("unknown_output_usage_requests") == 0
            else None),
        "unknown_usage_requests": (_safe_int(record.get("unknown_usage_requests"))
                                   if isinstance(record, dict) else None),
        "usage_complete_for_provider_requests": (
            record.get("usage_complete_for_provider_requests")
            if isinstance(record, dict) and type(record.get("usage_complete_for_provider_requests")) is bool
            else None),
    }


def _load_acquisition(run_root: Path, seed: int, arm: str):
    run_id = f"seed-{seed}-{arm.lower()}"
    run_dir = run_root / "acquisition" / run_id
    result_path = run_dir / "result.json"
    record = None
    issues = []
    if result_path.is_file():
        try:
            record = load_json(result_path)
            if not isinstance(record, dict):
                raise ReportError("result must be an object")
        except ReportError as error:
            issues.append("result_json_invalid")
            record = None
    else:
        issues.append("result_json_missing")

    journal_path = run_dir / "events.jsonl"
    events = None
    journal_state = "missing"
    if journal_path.is_file():
        try:
            actual_digest = sha256_file(journal_path)
            if record and record.get("journal_sha256") != actual_digest:
                raise ReportError("recorded journal digest mismatch")
            events = read_journal(journal_path)
            journal_state = "valid"
        except ReportError:
            journal_state = "invalid"
            issues.append("acquisition_journal_invalid")
    else:
        issues.append("acquisition_journal_missing")

    memory_path = run_dir / "memory" / "events.jsonl"
    memory_state = "missing"
    memory_events = None
    if memory_path.is_file():
        try:
            memory_events = read_journal(memory_path, artifact=True)
            expected_prefix = record.get("memory_events_sha256") if record else None
            if not isinstance(expected_prefix, str) or not ID_RE.fullmatch(expected_prefix):
                memory_state = "invalid"
                issues.append("memory_events_digest_missing_or_invalid")
            elif expected_prefix not in _journal_prefix_hashes(memory_path):
                memory_state = "invalid"
                issues.append("memory_events_prefix_digest_mismatch")
            else:
                memory_state = "hash_matched"
                memory_events = memory_events[:_journal_prefix_length(memory_path, expected_prefix)]
        except ReportError:
            memory_state = "invalid"
            issues.append("memory_events_invalid")

    result = record.get("result") if isinstance(record, dict) else None
    status = _status_for_acquisition(record)
    run_metrics = {}
    if events is not None:
        run_metrics = {
            "journal_event_count": len(events),
            "logical_model_attempts_from_journal": sum(e["kind"] == "model_attempt" for e in events),
            "tool_attempts_from_journal": sum(e["kind"] == "tool_attempt" for e in events),
            "python_execution_precommits": sum(e["kind"] == "program_execution_precommit" for e in events),
            "forecast_decisions": _forecast_summary(events),
            "constructor_evidence": _constructor_evidence(events),
            "failure_categories": _failure_categories(events),
            "usage": _usage_projection(events, record),
        }
        validation = [e["body"] for e in events if e["kind"] == "method_validation"]
        run_metrics["last_public_validation_status"] = validation[-1].get("status") if validation else "not_attempted"
        run_metrics["public_validation_attempts"] = len(validation)
    else:
        run_metrics = {"journal_event_count": None,
                       "logical_model_attempts_from_journal": None,
                       "tool_attempts_from_journal": None,
                       "python_execution_precommits": None,
                       "forecast_decisions": None,
                       "constructor_evidence": _unknown_constructor_evidence("journal_missing_or_invalid"),
                       "failure_categories": None,
                       "usage": _usage_projection([], record)}

    if memory_state == "hash_matched" and memory_events is not None:
        stored_summary = _stored_method_summary(run_dir / "memory", memory_events)
        run_metrics["stored_method_artifact_count"] = stored_summary["method_count"]
        run_metrics["stored_method_artifact_evidence_status"] = stored_summary["status"]
        submitted_id = record.get("submitted_artifact_id") if record else None
        if submitted_id and stored_summary["status"] == "verified":
            run_metrics["submitted_artifact_was_remembered"] = submitted_id in stored_summary["method_ids"]
        else:
            run_metrics["submitted_artifact_was_remembered"] = None
    else:
        run_metrics["stored_method_artifact_count"] = None
        run_metrics["stored_method_artifact_evidence_status"] = "unknown"
        run_metrics["submitted_artifact_was_remembered"] = None

    fixture_type = None
    fixture_path = run_dir / "fixture-private.json"
    if fixture_path.is_file():
        try:
            fixture = load_json(fixture_path)
            alteration = fixture.get("alteration") if isinstance(fixture, dict) else None
            fixture_type = "negative" if alteration is not None else "positive"
        except ReportError:
            issues.append("fixture_provenance_invalid")
    else:
        issues.append("fixture_provenance_missing")

    public_status = status
    if isinstance(result, dict) and isinstance(result.get("status"), str):
        public_status = result["status"]
    return {
        "seed": seed, "arm": arm, "run_id": run_id, "fixture_type": fixture_type,
        "status": status, "public_validation_status": public_status,
        "submitted_artifact_id": record.get("submitted_artifact_id") if record else None,
        "model_identity": _safe_model_identity(record.get("model_identity")) if record else None,
        "tool_calls_recorded": _safe_int(record.get("tool_calls")) if record else None,
        "python_executions_recorded": _safe_int(record.get("python_executions")) if record else None,
        "reveal_count_recorded": _safe_int(record.get("reveals")) if record else None,
        "elapsed_seconds": (result.get("elapsed_seconds")
                            if isinstance(result, dict) and type(result.get("elapsed_seconds")) in (int, float)
                            else None),
        "run_metrics": run_metrics,
        "integrity": {"journal": journal_state, "memory_events": memory_state,
                      "issues": issues},
        "_events": events,
        "_record": record,
        "_run_dir": run_dir,
    }


def _safe_model_identity(identity):
    if not isinstance(identity, dict):
        return None
    generation = identity.get("generation")
    endpoint = identity.get("endpoint")
    if isinstance(endpoint, str):
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(endpoint)
            endpoint = f"{parsed.scheme}://{parsed.hostname}" if parsed.scheme and parsed.hostname else None
            if endpoint and parsed.port:
                endpoint += f":{parsed.port}"
        except ValueError:
            endpoint = None
    return {"model": identity.get("model"), "protocol": identity.get("protocol"),
            "endpoint_origin": endpoint,
            "generation": generation if isinstance(generation, dict) else None,
            "instructions_sha256": identity.get("instructions_sha256")}


def _safe_model_profile(profile):
    if not isinstance(profile, dict):
        return None
    identity = _safe_model_identity(profile.get("identity"))
    return {"identity": identity, "timeout_seconds": _safe_int(profile.get("timeout_seconds")),
            "parse_mode": profile.get("parse_mode"),
            "sampling_fields": profile.get("sampling_fields")
            if type(profile.get("sampling_fields")) is bool else None}


def _load_reuse(run_root: Path, seed: int, arm: str, acquisition):
    run_id = f"seed-{seed}-{arm.lower()}"
    reuse_dir = run_root / "reuse" / run_id
    result_path = reuse_dir / "result.json"
    record = None
    issues = []
    if result_path.is_file():
        try:
            record = load_json(result_path)
            if not isinstance(record, dict):
                raise ReportError("reuse result must be an object")
        except ReportError:
            issues.append("reuse_result_invalid")
            record = None
    else:
        issues.append("reuse_result_missing")

    claims_no_attempt = (isinstance(record, dict)
                         and record.get("status") == "missing_final_artifact"
                         and record.get("attempted") is False)
    acquisition_record = acquisition.get("_record") if isinstance(acquisition, dict) else None
    acquisition_artifact_id = (acquisition.get("submitted_artifact_id")
                               if isinstance(acquisition, dict) else None)
    acquisition_journal_sha = (acquisition_record.get("journal_sha256")
                               if isinstance(acquisition_record, dict) else None)
    planned_no_attempt = (
        claims_no_attempt
        and record.get("artifact_id") is None
        and acquisition_artifact_id is None
        and type(record.get("model_calls")) is int
        and record.get("model_calls") == 0
        and isinstance(acquisition_journal_sha, str)
        and record.get("source_run_journal_sha256") == acquisition_journal_sha)
    if claims_no_attempt and not planned_no_attempt:
        issues.append("reuse_no_artifact_disposition_inconsistent")

    journal_path = reuse_dir / "cli-output" / "events.jsonl"
    journal_state = "missing"
    events = None
    if journal_path.is_file():
        try:
            actual_digest = sha256_file(journal_path)
            if record and record.get("reuse_journal_sha256") != actual_digest:
                raise ReportError("recorded reuse journal digest mismatch")
            events = read_journal(journal_path)
            journal_state = "valid"
        except ReportError:
            journal_state = "invalid"
            issues.append("reuse_journal_invalid")
    elif planned_no_attempt:
        journal_state = "not_applicable"
    else:
        issues.append("reuse_journal_missing")

    artifact_id = record.get("artifact_id") if record else None
    acquisition_id = acquisition.get("submitted_artifact_id")
    match = (artifact_id == acquisition_id) if artifact_id is not None and acquisition_id is not None else None
    check_scores = []
    raw_scores = record.get("check_scores") if record else None
    if isinstance(raw_scores, list) and len(raw_scores) == 2:
        for score in raw_scores:
            if not isinstance(score, dict):
                check_scores.append({"status": "invalid", "expected_count": None,
                                     "actual_count": None, "correct": None,
                                     "incorrect": None, "unknown": None,
                                     "format_matches": None, "format_mismatches": None,
                                     "missing": None, "extra": None})
                continue
            values = score.get("values")
            values = values if isinstance(values, list) else []
            true_count = sum(isinstance(v, dict) and v.get("mathematically_equal") is True for v in values)
            false_count = sum(isinstance(v, dict) and v.get("mathematically_equal") is False for v in values)
            math_known = true_count + false_count
            fmt_true = sum(isinstance(v, dict) and v.get("canonical_format_match") is True for v in values)
            fmt_false = sum(isinstance(v, dict) and v.get("canonical_format_match") is False for v in values)
            fmt_known = fmt_true + fmt_false
            check_scores.append({"status": score.get("checker_status", "unknown"),
                                 "expected_count": _safe_int(score.get("expected_count")),
                                 "actual_count": _safe_int(score.get("actual_count")),
                                 "correct": true_count,
                                 "incorrect": false_count,
                                 "unknown": max(0, len(values) - math_known),
                                 "format_matches": fmt_true,
                                 "format_mismatches": fmt_false,
                                 "format_unknown": max(0, len(values) - fmt_known),
                                 "missing": _safe_int(score.get("missing_count")),
                                 "extra": _safe_int(score.get("extra_count")),
                                 "complete": score.get("complete") if type(score.get("complete")) is bool else None})
    elif planned_no_attempt:
        pass
    else:
        issues.append("reuse_check_scores_missing_or_invalid")

    if events is not None:
        validation_events = [event["body"] for event in events if event["kind"] == "method_validation"]
        public_validation = validation_events[-1].get("status") if validation_events else "not_attempted"
    else:
        public_validation = None
    return {
        "seed": seed, "arm": arm, "run_id": run_id,
        "status": record.get("status") if record else "missing",
        "attempted": record.get("attempted") if record and type(record.get("attempted")) is bool else None,
        "fresh_process_public_validation_status": public_validation,
        "artifact_id": artifact_id,
        "same_exact_artifact_id": match,
        "model_calls": _safe_int(record.get("model_calls")) if record else None,
        "elapsed_seconds": record.get("elapsed_seconds")
        if record and type(record.get("elapsed_seconds")) in (int, float) else None,
        "checks": check_scores,
        "source_sha256_if_exact_artifact": (
            acquisition.get("artifact_source", {}).get("source_sha256")
            if match is True and isinstance(acquisition.get("artifact_source"), dict) else None),
        "integrity": {"journal": journal_state, "issues": issues},
        "_events": events,
        "_record": record,
        "_reuse_dir": reuse_dir,
    }


def _verify_source_snapshot(run_root: Path, source_root: Path, protocol, summary):
    issues = []
    if not isinstance(protocol, dict):
        return {"status": "protocol_missing", "issues": ["protocol_json_missing"],
                "verified_source_files": 0}
    hashes = protocol.get("source_hashes")
    if not isinstance(hashes, dict) or not hashes:
        issues.append("source_hash_manifest_missing")
        hashes = {}
    checked = 0
    for relative, expected in hashes.items():
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            issues.append("invalid_source_manifest_path")
            continue
        target = source_root / relative
        if not isinstance(expected, str) or not ID_RE.fullmatch(expected):
            issues.append("invalid_source_hash")
            continue
        if not target.is_file():
            issues.append("source_file_missing")
            continue
        checked += 1
        if sha256_file(target) != expected:
            issues.append("source_hash_mismatch")
    freeze_hash = protocol.get("freeze_json_sha256")
    freeze_path = source_root / "experiments" / "astra_constructor_live" / "freeze.json"
    if not isinstance(freeze_hash, str) or not ID_RE.fullmatch(freeze_hash):
        issues.append("freeze_hash_missing")
    elif not freeze_path.is_file() or sha256_file(freeze_path) != freeze_hash:
        issues.append("freeze_hash_mismatch")
    else:
        try:
            freeze = load_json(freeze_path)
            if not isinstance(freeze, dict):
                raise ReportError("freeze must be an object")
            if freeze.get("source_sha256") != hashes:
                issues.append("freeze_source_manifest_mismatch")
            if freeze.get("model") != protocol.get("model"):
                issues.append("freeze_model_profile_mismatch")
            if not isinstance(protocol.get("budgets"), dict):
                issues.append("protocol_budgets_missing")
            elif freeze.get("budgets") != protocol.get("budgets"):
                issues.append("freeze_budget_mismatch")
        except ReportError:
            issues.append("freeze_json_invalid")

    summary_state = "missing"
    if isinstance(summary, dict):
        expected_protocol = summary.get("protocol_sha256")
        protocol_path = run_root / "protocol.json"
        if isinstance(expected_protocol, str) and protocol_path.is_file():
            summary_state = "matched" if sha256_file(protocol_path) == expected_protocol else "mismatch"
            if summary_state == "mismatch":
                issues.append("run_protocol_digest_mismatch")
        else:
            summary_state = "unbound"
            issues.append("summary_protocol_digest_missing")
    else:
        issues.append("campaign_summary_missing_or_invalid")
    status = "verified" if not issues else "failed"
    return {"status": status, "issues": issues,
            "verified_source_files": checked,
            "source_hash_count": len(hashes), "summary_protocol_digest": summary_state,
            "freeze_json_sha256": freeze_hash if isinstance(freeze_hash, str) else None}


def _artifact_payload(memory_dir: Path, artifact_id: str):
    if not isinstance(artifact_id, str) or not ID_RE.fullmatch(artifact_id):
        raise ReportError("invalid artifact ID")
    path = memory_dir / "blobs" / f"{artifact_id}.json.zlib"
    try:
        raw = zlib.decompress(path.read_bytes())
    except (OSError, zlib.error) as error:
        raise ReportError("artifact blob missing or corrupt") from error
    if hashlib.sha256(raw).hexdigest() != artifact_id:
        raise ReportError("artifact content ID mismatch")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ReportError("artifact payload malformed") from error
    if _canonical(payload, artifact=True) != raw:
        raise ReportError("artifact payload is not canonical")
    return payload


def _stored_method_summary(memory_dir: Path, events):
    """Verify unique method blobs referenced by ArtifactMemory put events."""
    ids = set()
    malformed = False
    for event in events:
        if event.get("kind") != "put":
            continue
        body = event.get("body")
        artifact_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(artifact_id, str) or not ID_RE.fullmatch(artifact_id):
            malformed = True
            continue
        ids.add(artifact_id)
    method_ids = set()
    unavailable = False
    for artifact_id in ids:
        try:
            payload = _artifact_payload(memory_dir, artifact_id)
        except ReportError:
            unavailable = True
            continue
        body = payload.get("body") if isinstance(payload, dict) else None
        if isinstance(payload, dict) and payload.get("kind") == "method":
            if (set(payload) == {"kind", "body", "dependencies", "provenance", "applicability"}
                    and isinstance(payload.get("dependencies"), list)
                    and isinstance(body, dict)
                    and body.get("language") == "python"
                    and isinstance(body.get("name"), str)
                    and isinstance(body.get("description"), str)
                    and isinstance(body.get("source"), str)):
                method_ids.add(artifact_id)
            else:
                unavailable = True
    status = "incomplete" if malformed or unavailable else "verified"
    return {"status": status,
            "method_count": len(method_ids) if status == "verified" else None,
            "method_ids": method_ids if status == "verified" else None}


def _artifact_source_summary(memory_dir: Path, artifact_id: str, *, include_sources=False,
                             export_dir: Path | None = None):
    if artifact_id is None:
        return {"status": "missing_final_artifact", "artifact_id": None,
                "source_sha256": None, "source_bytes": None, "dependencies": None,
                "recorded_provenance_fit_relation_receipts": None,
                "recorded_provenance_fit_relation_receipt_count": None,
                "legacy_field_semantics": "unknown_no_final_artifact",
                "fit_receipt_ids": None, "fit_receipt_count": None,
                "provenance_status": "unknown_no_final_artifact"}
    try:
        payload = _artifact_payload(memory_dir, artifact_id)
        if payload.get("kind") != "method" or not isinstance(payload.get("body"), dict):
            raise ReportError("final artifact is not a method")
        source = payload["body"].get("source")
        if not isinstance(source, str):
            raise ReportError("method source missing")
        raw_source = source.encode("utf-8")
        dependencies = payload.get("dependencies")
        if not isinstance(dependencies, list) or any(not isinstance(item, str) or not ID_RE.fullmatch(item)
                                                     for item in dependencies):
            raise ReportError("invalid method dependency list")
        provenance = payload.get("provenance")
        derivation = provenance.get("derivation") if isinstance(provenance, dict) else None
        recorded_values = derivation.get("fit_relation_receipts") if isinstance(derivation, dict) else None
        if recorded_values is None and derivation is None:
            recorded_ids, provenance_status = [], "no_recorded_receipts"
        elif (isinstance(recorded_values, list)
              and all(isinstance(item, str) and ID_RE.fullmatch(item) for item in recorded_values)):
            recorded_ids = sorted(set(recorded_values))
            provenance_status = "valid"
        else:
            recorded_ids, provenance_status = None, "invalid"
        source_digest = hashlib.sha256(raw_source).hexdigest()
        if include_sources and export_dir is not None:
            _export_source_closure(memory_dir, artifact_id, export_dir)
        return {"status": "verified", "artifact_id": artifact_id,
                "source_sha256": source_digest, "source_bytes": len(raw_source),
                "dependencies": dependencies,
                # This is the literal stored provenance field name. The runner
                # may place probe/validation receipt IDs in this list too.
                "recorded_provenance_fit_relation_receipts": recorded_ids,
                "recorded_provenance_fit_relation_receipt_count": (
                    len(recorded_ids) if recorded_ids is not None else None),
                "legacy_field_semantics": "provenance.derivation.fit_relation_receipts may include probe/validation IDs",
                "fit_receipt_ids": None,
                "fit_receipt_count": None,
                "provenance_status": provenance_status}
    except ReportError as error:
        return {"status": "unavailable", "artifact_id": artifact_id,
                "source_sha256": None, "source_bytes": None,
                "dependencies": None, "recorded_provenance_fit_relation_receipts": None,
                "recorded_provenance_fit_relation_receipt_count": None,
                "legacy_field_semantics": "unavailable",
                "fit_receipt_ids": None,
                "fit_receipt_count": None, "provenance_status": "unavailable",
                "error_type": type(error).__name__}


def _link_fit_receipts(artifact_summary, constructor_evidence):
    recorded = artifact_summary.get("recorded_provenance_fit_relation_receipts")
    if (not isinstance(constructor_evidence, dict)
            or constructor_evidence.get("status") != "available"
            or not isinstance(constructor_evidence.get("derivation_receipts"), list)
            or not isinstance(recorded, list)):
        artifact_summary["recorded_ids_matching_finite_fit_receipts"] = None
        artifact_summary["finite_fit_receipt_evidence_scope"] = "unknown"
        return artifact_summary
    known = {item["receipt_id"] for item in constructor_evidence["derivation_receipts"]
             if isinstance(item, dict) and isinstance(item.get("receipt_id"), str)
             and ID_RE.fullmatch(item["receipt_id"])}
    matched = sorted(set(recorded) & known)
    artifact_summary["fit_receipt_ids"] = matched
    artifact_summary["fit_receipt_count"] = len(matched)
    artifact_summary["recorded_ids_matching_finite_fit_receipts"] = matched
    artifact_summary["finite_fit_receipt_evidence_scope"] = "matching derivation_receipt journal events only"
    return artifact_summary


def _export_source_closure(memory_dir: Path, root_id: str, export_dir: Path):
    seen = set()
    pending = [root_id]
    export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    while pending:
        artifact_id = pending.pop()
        if artifact_id in seen:
            continue
        if len(seen) >= MAX_DEPENDENCIES:
            raise ReportError("artifact dependency closure exceeds report limit")
        seen.add(artifact_id)
        payload = _artifact_payload(memory_dir, artifact_id)
        body = payload.get("body")
        if payload.get("kind") != "method" or not isinstance(body, dict) or not isinstance(body.get("source"), str):
            raise ReportError("dependency closure contains a non-method")
        target = export_dir / f"{artifact_id}.py"
        target.write_text(body["source"], encoding="utf-8")
        dependencies = payload.get("dependencies")
        if not isinstance(dependencies, list):
            raise ReportError("invalid dependency list")
        pending.extend(dependencies)
    index = {"root_artifact_id": root_id,
             "artifacts": sorted(seen)}
    (export_dir / "manifest.json").write_bytes(_canonical(index) + b"\n")


def _summary_comparison(summary, acquisitions, reuses):
    if not isinstance(summary, dict):
        return {"status": "missing_or_invalid", "acquisition_rows_match": None,
                "reuse_rows_match": None}
    def rows_match(field, rows, expected_keys):
        source = summary.get(field)
        if not isinstance(source, list) or len(source) != len(expected_keys):
            return False
        if not all(isinstance(row, dict) for row in source):
            return False
        if any(type(row.get("seed")) is not int or row.get("arm") not in ARMS
               for row in source):
            return False
        keys = [(row.get("seed"), row.get("arm")) for row in source]
        if len(set(keys)) != len(keys):
            return False
        actual = dict(zip(keys, source))
        if set(actual) != set(expected_keys):
            return False
        for key in expected_keys:
            current = rows.get(key)
            if current is None or actual[key] != current:
                return False
        return True
    acq_map = {(row["seed"], row["arm"]): row["_record"] for row in acquisitions.values()
               if row.get("_record") is not None}
    reuse_map = {(row["seed"], row["arm"]): row["_record"] for row in reuses.values()
                 if row.get("_record") is not None}
    expected = set(EXPECTED_RUNS)
    acq_match = rows_match("acquisition", acq_map, expected) if set(acq_map) == expected else False
    reuse_match = rows_match("fresh_process_reuse", reuse_map, expected) if set(reuse_map) == expected else False
    return {"status": "matched" if acq_match and reuse_match else "mismatch_or_incomplete",
            "acquisition_rows_match": acq_match, "reuse_rows_match": reuse_match}


def build_report(run_root: Path, source_root: Path, *, verify_source=True):
    run_root, source_root = Path(run_root), Path(source_root)
    protocol = summary = None
    protocol_path = run_root / "protocol.json"
    summary_path = run_root / "summary.json"
    issues = []
    try:
        protocol = load_json(protocol_path)
    except FileNotFoundError:
        issues.append("protocol_json_missing")
    except ReportError:
        issues.append("protocol_json_invalid")
    try:
        summary = load_json(summary_path)
    except FileNotFoundError:
        issues.append("summary_json_missing")
    except ReportError:
        issues.append("summary_json_invalid")
    source_provenance = (_verify_source_snapshot(run_root, source_root, protocol, summary)
                         if verify_source else {"status": "not_checked", "issues": []})
    if verify_source and source_provenance.get("status") != "verified":
        issues.append("source_or_protocol_integrity_failed")

    acquisitions = {}
    reuses = {}
    for seed, arm in EXPECTED_RUNS:
        acq = _load_acquisition(run_root, seed, arm)
        if source_provenance.get("status") == "failed" and verify_source:
            # Keep outcomes visible, but do not derive claims from an unbound source.
            acq["status"] = "source_integrity_failed"
            acq["public_validation_status"] = "unverified"
        artifact_summary = _artifact_source_summary(
            acq["_run_dir"] / "memory", acq["submitted_artifact_id"])
        constructor = acq.get("run_metrics", {}).get("constructor_evidence")
        artifact_summary = _link_fit_receipts(artifact_summary, constructor)
        acq["artifact_source"] = artifact_summary
        acquisitions[(seed, arm)] = acq
        reuse = _load_reuse(run_root, seed, arm, acq)
        if source_provenance.get("status") == "failed" and verify_source:
            reuse["status"] = "source_integrity_failed"
            reuse["checks"] = []
        reuses[(seed, arm)] = reuse

    pair_rows = []
    for seed in SEEDS:
        left = acquisitions[(seed, "WITH_CONSTRUCTOR")]
        right = acquisitions[(seed, "MODEL_ONLY")]
        fixture_type = left.get("fixture_type") if left.get("fixture_type") == right.get("fixture_type") else None
        pair_rows.append({"seed": seed, "fixture_type": fixture_type,
                          "WITH_CONSTRUCTOR": _public_acquisition(left),
                          "MODEL_ONLY": _public_acquisition(right),
                          "reuse": {arm: _public_reuse(reuses[(seed, arm)]) for arm in ARMS}})

    by_arm = {}
    for arm in ARMS:
        by_arm[arm] = {}
        for fixture_type in ("positive", "negative", "unknown"):
            rows = [acquisitions[(seed, arm)] for seed in SEEDS
                    if (acquisitions[(seed, arm)].get("fixture_type") or "unknown") == fixture_type]
            forecasts = [row["run_metrics"].get("forecast_decisions") for row in rows
                         if isinstance(row.get("run_metrics"), dict)
                         and isinstance(row["run_metrics"].get("forecast_decisions"), dict)]
            by_arm[arm][fixture_type] = {
                "instances": len(rows),
                "public_validation_statuses": _counter(row.get("public_validation_status", "unknown")
                                                        for row in rows),
                "runs_with_valid_forecast_journal": len(forecasts),
                "runs_without_forecast_journal": len(rows) - len(forecasts),
                "known_run_release_subtotal": sum(item["observation_releases"] for item in forecasts) if forecasts else None,
                "known_run_correct_subtotal": sum(item["correct"] for item in forecasts) if forecasts else None,
                "known_run_incorrect_subtotal": sum(item["incorrect"] for item in forecasts) if forecasts else None,
                "known_run_unknown_outcome_subtotal": sum(item["unknown_outcome"] for item in forecasts) if forecasts else None,
            }

    completeness = {
        "expected_acquisitions": len(EXPECTED_RUNS),
        "acquisitions_present": sum(row["status"] != "missing" for row in acquisitions.values()),
        "acquisition_journals_valid": sum(row["integrity"]["journal"] == "valid" for row in acquisitions.values()),
        "expected_reuse_runs": len(EXPECTED_RUNS),
        "reuse_results_present": sum(row["status"] != "missing" for row in reuses.values()),
        "reuse_journals_valid": sum(row["integrity"]["journal"] == "valid" for row in reuses.values()),
        "all_six_reuse_results_exact_id_bound": all(
            reuses[key].get("same_exact_artifact_id") is True
            and acquisitions[key].get("submitted_artifact_id") is not None
            for key in EXPECTED_RUNS),
        "acquisition_final_artifacts_missing": sum(
            acquisitions[key].get("submitted_artifact_id") is None for key in EXPECTED_RUNS),
    }
    summary_check = _summary_comparison(summary, acquisitions, reuses)
    if summary_check["status"] != "matched":
        issues.append("summary_rows_missing_or_mismatched")
    if any(row["integrity"]["journal"] != "valid" for row in acquisitions.values()):
        issues.append("one_or_more_acquisition_journals_missing_or_invalid")
    if any(row["integrity"]["journal"] != "valid" for row in reuses.values()
           if row.get("attempted") is True):
        issues.append("one_or_more_reuse_journals_missing_or_invalid")

    return {
        "format": "astra-constructor-live-report-v1",
        "source_provenance": {
            "run_root_label": run_root.name,
            "source_snapshot_label": source_root.name,
            "protocol_sha256": sha256_file(protocol_path) if protocol_path.is_file() else None,
            "protocol_status": protocol.get("status") if isinstance(protocol, dict) else None,
                "model_profile": _safe_model_profile(protocol.get("model")) if isinstance(protocol, dict) else None,
            "budgets": protocol.get("budgets") if isinstance(protocol, dict) and isinstance(protocol.get("budgets"), dict) else None,
            "arm_order": protocol.get("arm_order") if isinstance(protocol, dict) else None,
            "attribution": protocol.get("attribution") if isinstance(protocol, dict) else None,
            "freeze_json_sha256": protocol.get("freeze_json_sha256") if isinstance(protocol, dict) else None,
            "source_integrity": source_provenance,
            "campaign_summary_consistency": summary_check,
            "campaign_summary_status": "present" if isinstance(summary, dict) else "missing_or_invalid",
        },
        "completeness": completeness,
        "pairs": pair_rows,
        "descriptive_counts_by_arm_and_fixture_type": by_arm,
        "issues": sorted(set(issues)),
        "claim_scope": "descriptive authored-fixture mechanism study; three pairs; no cross-domain transfer claim",
        "_acquisitions": acquisitions,
        "_reuses": reuses,
        "_summary": summary,
        "_run_root": run_root,
    }


def _public_acquisition(row):
    return {key: value for key, value in row.items()
            if not key.startswith("_")}


def _public_reuse(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _write_markdown(report):
    lines = ["# Constructor-use live study report", "",
             "This is a descriptive report over three authored fixture pairs. It does not establish cross-domain transfer.", "",
             f"Source snapshot: `{report['source_provenance']['source_snapshot_label']}`",
             f"Source integrity: **{report['source_provenance']['source_integrity']['status']}**",
             f"Summary consistency: **{report['source_provenance']['campaign_summary_consistency']['status']}**", "",
             "| Seed | Fixture | Arm | Status | Public validation | Forecasts correct / incorrect / unknown | Reuse status | Exact ID reused |",
             "|---:|---|---|---|---|---:|---|---|"]
    for pair in report["pairs"]:
        for arm in ARMS:
            row = pair[arm]
            forecast = row.get("run_metrics", {}).get("forecast_decisions") or {}
            reuse = pair["reuse"][arm]
            counts = (f"{forecast.get('correct', 'unknown')} / "
                      f"{forecast.get('incorrect', 'unknown')} / "
                      f"{forecast.get('unknown_outcome', 'unknown')}")
            lines.append(f"| {pair['seed']} | {pair.get('fixture_type') or 'unknown'} | {arm} | "
                         f"{row.get('status')} | {row.get('public_validation_status')} | {counts} | "
                         f"{reuse.get('status')} | {reuse.get('same_exact_artifact_id')} |")
    lines.extend(["", "## Integrity and missing evidence", "",
                  f"Acquisitions present: {report['completeness']['acquisitions_present']}/6; "
                  f"valid acquisition journals: {report['completeness']['acquisition_journals_valid']}/6.",
                  f"Reuse results present: {report['completeness']['reuse_results_present']}/6; "
                  f"valid reuse journals: {report['completeness']['reuse_journals_valid']}/6.", ""])
    if report["issues"]:
        lines.append("Report issues: " + ", ".join(f"`{item}`" for item in report["issues"]) + ".")
    else:
        lines.append("No missing integrity or summary-consistency checks were detected.")
    return "\n".join(lines) + "\n"


def write_report(report, output: Path, *, include_artifact_source=False):
    output = Path(output)
    if output.exists():
        raise FileExistsError("report output already exists; choose a new directory")
    previous_umask = os.umask(0o077)
    try:
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        artifact_exports = []
        if include_artifact_source:
            for key in EXPECTED_RUNS:
                acquisition = report.get("_acquisitions", {}).get(key)
                if not isinstance(acquisition, dict):
                    continue
                artifact_id = acquisition.get("submitted_artifact_id")
                if not isinstance(artifact_id, str):
                    continue
                export_dir = output / "artifact-source" / f"seed-{key[0]}-{key[1].lower()}"
                try:
                    _export_source_closure(acquisition["_run_dir"] / "memory", artifact_id, export_dir)
                    artifact_exports.append({"seed": key[0], "arm": key[1],
                                             "artifact_id": artifact_id, "status": "exported",
                                             "path": str(export_dir.relative_to(output))})
                except (ReportError, OSError):
                    artifact_exports.append({"seed": key[0], "arm": key[1],
                                             "artifact_id": artifact_id, "status": "unavailable"})
        public = {key: value for key, value in report.items() if not key.startswith("_")}
        if include_artifact_source:
            public["artifact_source_exports"] = artifact_exports
        public["reporter_sha256"] = sha256_file(Path(__file__).resolve())
        (output / "report.json").write_bytes(_canonical(public) + b"\n")
        (output / "report.md").write_text(_write_markdown(report), encoding="utf-8")
    finally:
        os.umask(previous_umask)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path,
                        help="completed private run directory")
    parser.add_argument("--source-root", required=True, type=Path,
                        help="immutable d5c143a source snapshot root")
    parser.add_argument("--output", required=True, type=Path,
                        help="new directory for safe JSON and Markdown projections")
    parser.add_argument("--include-artifact-source", action="store_true",
                        help="privately export exact final method source/dependency closure")
    args = parser.parse_args(argv)
    report = build_report(args.run_root, args.source_root)
    write_report(report, args.output, include_artifact_source=args.include_artifact_source)
    print(json.dumps({"status": "complete", "output": str(args.output),
                      "issues": report["issues"]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
