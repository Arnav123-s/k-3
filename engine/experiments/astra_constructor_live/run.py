"""Paired live study of model-authored methods with optional exact fitting.

This runner is deliberately explicit: invoking ``run`` can make live model
requests. Tests and imports do not contact the endpoint. The study uses only
public authored fixtures, stores complete run journals, and never includes the
held-out continuation in model contexts or tool responses.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.execution import PythonExecutor, preflight
from kuttaka_runtime.models import INSTRUCTIONS, JsonModel
from kuttaka_runtime.programs import ProgramTask, ProgramTools
from kuttaka_runtime.runtime import Budget, ToolInputError, investigate
from kuttaka_runtime.shift_relation import fit_shift_relation, verify_shift_relation
from kuttaka_runtime.store import Journal, canonical


SEEDS = (1171, 1173, 1177)
REUSE_SEED = 1181
MODEL_NAME = "meta/muse-glimmer-30b"
MODEL_ENDPOINT = "https://integrate.api.nvidia.com/v1"
FREEZE_PATH = Path(__file__).with_name("freeze.json")
ARM_ORDER = (("WITH_CONSTRUCTOR", "MODEL_ONLY"),
             ("MODEL_ONLY", "WITH_CONSTRUCTOR"),
             ("WITH_CONSTRUCTOR", "MODEL_ONLY"))
MODEL_CALLS = 10
RESPONSE_TOKENS = 8192
CONTEXT_BYTES = 24000
TOOL_CALLS = 9
TOTAL_EXECUTION_CEILING = 49
MAX_EXECUTIONS = 47
REUSE_EXECUTIONS = 2
SANDBOX_SECONDS = 30
REQUEST_SECONDS = 300
OUTAGE_LIMIT = 5
MAX_REVEALS = 4
COMMON_TOOLS = ("remember_method", "inspect_method", "find_methods",
                "probe_method", "validate_method", "observe_next")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fraction_text(value: int) -> str:
    return str(value)


def generate_fixture(seed: int) -> dict:
    """Return one authored finite series and private generation provenance."""
    if type(seed) is not int or seed not in (*SEEDS, REUSE_SEED):
        raise ValueError("seed is outside the frozen fixture set")
    rng = random.Random(seed)
    a, b, c = rng.randint(1, 3), rng.randint(1, 5), rng.randint(1, 3)
    values = [0] * 22 if seed == 1177 else [2, 3] + [0] * 20
    if seed != 1177:
        for n in range(len(values) - 2):
            values[n + 2] = (a * n + b) * values[n + 1] + c * values[n]
    else:
        # Public negative fixture: the revealed prefix is all zero, then one
        # authored perturbation occurs at index 14 before the recurrence resumes.
        values[12] = values[13] = 0
        values[14] = 1
        for n in range(13, len(values) - 2):
            values[n + 2] = (a * n + b) * values[n + 1] + c * values[n]
    return {"seed": seed, "coefficients": {"a": a, "b": b, "c": c},
            "values": [_fraction_text(value) for value in values],
            "alteration": {"index": 14, "delta": 1} if seed == 1177 else None,
            "generator": "x[n+2]=(a*n+b)*x[n+1]+c*x[n]",
            "initial_values": ["0", "0"] if seed == 1177 else ["2", "3"]}


def freeze_manifest() -> dict:
    """Pure reviewed-state manifest; freeze.json stores this exact object."""
    repo_root = Path(__file__).resolve().parents[2]
    runtime_sources = sorted((repo_root / "kuttaka_runtime").glob("*.py"))
    sources = {str(path.relative_to(repo_root)): _sha256(path)
               for path in runtime_sources}
    sources.update({
        "experiments/astra_constructor_live/run.py": _sha256(Path(__file__).resolve()),
        "experiments/astra_constructor_live/PROTOCOL.md": _sha256(
            Path(__file__).with_name("PROTOCOL.md")),
        "tests/test_astra_constructor_live.py": _sha256(
            repo_root / "tests" / "test_astra_constructor_live.py"),
    })
    fixture_hashes = {str(seed): hashlib.sha256(
        canonical(generate_fixture(seed)).encode("utf-8")).hexdigest()
        for seed in (*SEEDS, REUSE_SEED)}
    return {
        "format": "astra-constructor-live-freeze-v1",
        "source_sha256": dict(sorted(sources.items())),
        "fixture_sha256": fixture_hashes,
        "model": {"identity": {"model": MODEL_NAME, "protocol": "openai",
                                "endpoint": MODEL_ENDPOINT,
                                "generation": {"temperature": 0.95, "top_p": 1.0,
                                               "reasoning_effort": "high",
                                               "max_tokens": RESPONSE_TOKENS,
                                               "stream": True},
                                "instructions_sha256": hashlib.sha256(
                                    INSTRUCTIONS.encode("utf-8")).hexdigest()},
                  "timeout_seconds": REQUEST_SECONDS,
                  "parse_mode": "strict", "reasoning_hint": None,
                  "sampling_fields": True, "credential_env": "NVIDIA_API_KEY"},
        "budgets": {"model_calls": MODEL_CALLS,
                    "response_tokens_per_call": RESPONSE_TOKENS,
                    "context_bytes_per_call": CONTEXT_BYTES,
                    "tool_calls": TOOL_CALLS,
                    "python_executions_total": TOTAL_EXECUTION_CEILING,
                    "acquisition_python_executions": MAX_EXECUTIONS,
                    "fresh_process_reuse_python_executions": REUSE_EXECUTIONS,
                    "sandbox_seconds": SANDBOX_SECONDS,
                    "request_seconds": REQUEST_SECONDS,
                    "outage_limit": OUTAGE_LIMIT,
                    "observation_reveals": MAX_REVEALS},
        "toolsets": {"common": list(COMMON_TOOLS),
                     "with_constructor": [*COMMON_TOOLS, "fit_relation"],
                     "constructor_parameters": ["order", "degree"],
                     "constructor_max_columns": 12},
        "arm_order": [list(pair) for pair in ARM_ORDER],
    }


def _protocol_is_frozen():
    return "Status: FROZEN" in Path(__file__).with_name("PROTOCOL.md").read_text(encoding="utf-8")


def validate_freeze(path: Path = FREEZE_PATH) -> dict:
    """Refuse live setup unless the checked manifest exactly matches this tree."""
    if not _protocol_is_frozen():
        raise RuntimeError("PROTOCOL.md must be reviewed and marked FROZEN before live calls")
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("freeze.json is missing or malformed; no study started") from error
    expected = freeze_manifest()
    if recorded != expected:
        raise RuntimeError("freeze.json does not match reviewed sources, fixtures, profile, or budgets")
    return expected


def validate_private_output(output: Path, repo_root: Path | None = None) -> Path:
    repo_root = Path(__file__).resolve().parents[2] if repo_root is None else Path(repo_root).resolve()
    private_root = (repo_root / ".astra").resolve()
    resolved = Path(output).resolve()
    if resolved == private_root or not resolved.is_relative_to(private_root):
        raise ValueError("study output must be a new child of the repository .astra directory")
    if resolved.exists():
        raise FileExistsError("study output directory must not already exist")
    return resolved


def _request(prefix: list[str]) -> str:
    # Fixture IDs, coefficients, and future values are intentionally absent.
    return ("Write a reusable Python method as source defining run(input). It must "
            "accept exactly a JSON object with prefix (a list of rational strings) "
            "and steps (a nonnegative integer), and return a list of canonical rational strings "
            "(reduced p/q with positive q; write integers as p) "
            "for the next steps. Infer the rule from the input prefix on every call; "
            "do not encode coefficients inferred only from the prefix shown here. "
            "The method must work on new prefixes, not only these values. The visible "
            "prefix is: " + canonical(prefix))


def task_for_acquisition(values: list[str]) -> ProgramTask:
    if not isinstance(values, list) or len(values) < 12:
        raise ValueError("acquisition task requires a 12-value visible prefix")
    checks = [
        {"input": {"prefix": values[:8], "steps": 2}, "expected": values[8:10]},
        {"input": {"prefix": values[:10], "steps": 2}, "expected": values[10:12]},
    ]
    return ProgramTask(_request(values[:12]), checks,
                       context={"contract": "finite exact-rational sequence method"})


def task_for_reuse(acquisition_values: list[str], heldout_values: list[str],
                   acquisition_prefix_length: int) -> ProgramTask:
    """Private no-model checker task for fresh-process exact-ID reuse."""
    checks = [
        {"input": {"prefix": acquisition_values[:acquisition_prefix_length], "steps": 6},
         "expected": acquisition_values[acquisition_prefix_length:acquisition_prefix_length + 6]},
        {"input": {"prefix": heldout_values[:12], "steps": 6},
         "expected": heldout_values[12:18]},
    ]
    return ProgramTask(_request(heldout_values[:12]), checks,
                       context={"contract": "fresh-process finite forward checks"})


class ContextBoundModel:
    """Enforce the per-call 24 KiB context limit before endpoint access."""

    def __init__(self, wrapped, *, max_context_bytes=CONTEXT_BYTES):
        self.wrapped = wrapped
        self.max_context_bytes = max_context_bytes
        self.identity = wrapped.identity
        self.max_tokens = wrapped.max_tokens
        self.last_usage = {}
        self.last_response = None
        self.calls_attempted = 0
        self.provider_calls = 0
        self.http_requests_per_call = []

    def propose(self, context):
        self.calls_attempted += 1
        self.last_usage = {}
        self.last_response = None
        opener = getattr(self.wrapped, "_opener", None)
        before_requests = getattr(opener, "request_attempts", 0)
        encoded = canonical(context).encode("utf-8")
        if len(encoded) > self.max_context_bytes:
            self.last_usage = {"input_tokens": 0, "output_tokens": 0}
            self.http_requests_per_call.append(0)
            raise ContextBudgetRefusal("model context exceeds the frozen byte limit")
        self.wrapped.last_usage = {}
        self.wrapped.last_response = None
        try:
            return self.wrapped.propose(context)
        finally:
            self.last_usage = self.wrapped.last_usage
            self.last_response = self.wrapped.last_response
            after_requests = getattr(opener, "request_attempts", before_requests)
            used = after_requests - before_requests
            self.http_requests_per_call.append(used)
            self.provider_calls += used


class ContextBudgetRefusal(ValueError):
    """Host-side refusal before any endpoint request; token use is exactly zero."""


class CountingOpener:
    """Count HTTP open attempts separately from context-refused model calls."""

    def __init__(self, opener):
        self.opener = opener
        self.request_attempts = 0

    def open(self, *args, **kwargs):
        self.request_attempts += 1
        return self.opener.open(*args, **kwargs)


class ForwardObserver:
    """One-step observation tool with prediction-before-release enforcement."""

    def __init__(self, initial_prefix, future_values, journal):
        self.initial_prefix = list(initial_prefix)
        self.future_values = list(future_values)
        self.journal = journal
        self.reveals = 0
        self.position = 0

    def __call__(self, request):
        if not isinstance(request, dict) or set(request) != {"prediction"}:
            raise ToolInputError("observe_next requires exactly a prediction string")
        prediction = request["prediction"]
        if not isinstance(prediction, str):
            raise ToolInputError("prediction must be an exact rational string")
        try:
            predicted_value = Fraction(prediction)
        except (ValueError, ZeroDivisionError):
            raise ToolInputError("prediction must be a finite rational string") from None
        if self.reveals >= MAX_REVEALS or self.position >= len(self.future_values):
            raise ToolInputError("observation budget exhausted")
        events = Journal.read(self.journal.path)
        proposal = next((event for event in reversed(events)
                         if event["kind"] == "proposal"), None)
        if proposal is None:
            raise ToolInputError("prediction was not committed")
        action = proposal["body"]
        if action.get("kind") != "experiment" or action.get("tool") != "observe_next":
            raise ToolInputError("prediction was not committed for observe_next")
        if not any(isinstance(item, dict) and item.get("outcome") == prediction
                   for item in action.get("predictions", [])):
            raise ToolInputError("observation prediction does not match committed prediction")
        actual = self.future_values[self.position]
        self.position += 1
        self.reveals += 1
        matches = predicted_value == Fraction(actual)
        canonical_match = prediction == actual
        self.journal.append("live_observation_release", {
            "reveal_number": self.reveals, "matches": matches,
            "canonical_format_match": canonical_match,
            "value": actual})
        return {"status": "observed", "value": actual,
                "matches": matches, "canonical_format_match": canonical_match}

    def doc(self):
        def tool(request):
            return self(request)
        tool.__doc__ = ("Reveal exactly one next rational string after the experiment "
                        "commits that exact predicted string. Input: {prediction: string}. "
                        "At most four values are available; no index or future data is returned.")
        return tool


def fit_tool(initial_prefix, observer, journal):
    """Build treatment-only exact constructor tool over revealed values."""
    receipts = []
    def fit_relation(request):
        if not isinstance(request, dict) or set(request) != {"order", "degree"}:
            raise ToolInputError("fit_relation requires order and degree")
        order, degree = request["order"], request["degree"]
        if (type(order) is not int or not 1 <= order <= 4 or
                type(degree) is not int or not 0 <= degree <= 4 or
                (order + 1) * (degree + 1) > 12):
            raise ToolInputError("fit_relation is limited to order 1..4, degree 0..4, and 12 columns")
        revealed = observer.initial_prefix + observer.future_values[:observer.position]
        try:
            result = fit_shift_relation(revealed, order, degree, start_index=0)
        except (ValueError, ArithmeticError) as error:
            raise ToolInputError("fit_relation rejected bounded input") from error
        if not verify_shift_relation(revealed, result):
            raise RuntimeError("exact constructor certificate failed independent verification")
        receipt = journal.append("derivation_receipt", {
            "kind": "exact_polynomial_shift_fit",
            "order": order, "degree": degree, "sample_count": len(revealed),
            "status": result["status"], "certificate": result,
            "certificate_sha256": hashlib.sha256(canonical(result).encode("utf-8")).hexdigest(),
            "max_study_columns": 12,
            "constructor_source_sha256": _sha256(Path(__file__).resolve().parents[2] /
                                                  "kuttaka_runtime" / "shift_relation.py")})
        receipts.append(receipt)
        return {"status": result["status"], "certificate": result, "receipt": receipt}
    fit_relation.__doc__ = (
        "Fit one model-selected polynomial-shift family to only the already revealed "
        "prefix and return its exact finite certificate. Input: {order: int, degree: int}. "
        "Study bounds are order 1..4, degree 0..4, and at most 12 coefficients. "
        "This tool does not choose dimensions, predict, observe, or store a method. "
        "A finite fit is not proof of continuation.")
    fit_relation.fit_receipts = receipts
    return fit_relation


def build_tools(program_tools, observer, *, with_constructor, initial_prefix, journal):
    tools = program_tools.tools()
    tools["observe_next"] = observer.doc()
    original_remember = tools["remember_method"]
    known_receipts = []
    for tool_name in ("probe_method", "validate_method"):
        original_tool = tools[tool_name]
        def tracked_tool(request, original_tool=original_tool):
            result = original_tool(request)
            receipt = result.get("receipt") if isinstance(result, dict) else None
            if isinstance(receipt, str):
                known_receipts.append(receipt)
            return result
        tracked_tool.__doc__ = original_tool.__doc__
        tools[tool_name] = tracked_tool
    def remember_method(request):
        if not isinstance(request, dict):
            raise ToolInputError("remember_method requires an object")
        request_copy = dict(request)
        derivation = request_copy.pop("derivation", [])
        if (not isinstance(derivation, list) or
                any(not isinstance(receipt, str) or receipt not in known_receipts
                    for receipt in derivation)):
            raise ToolInputError("derivation must list evidence receipt IDs returned earlier by tools")
        if derivation:
            method_source = request_copy.get("source")
            if not isinstance(method_source, str):
                raise ToolInputError("remember_method needs source")
            source_sha = hashlib.sha256(method_source.encode("utf-8")).hexdigest()
            return program_tools.remember_method(request_copy, derivation={
                "fit_relation_receipts": derivation,
                "method_source_sha256": source_sha})
        return original_remember(request_copy)
    remember_method.__doc__ = ((original_remember.__doc__ or "") +
        " Optional harness field derivation may list receipt IDs returned earlier by tools; "
        "these are provenance receipts, never executable method dependencies.")
    tools["remember_method"] = remember_method
    if with_constructor:
        fit_relation = fit_tool(initial_prefix, observer, journal)
        # Keep the exact server-issued fit receipt IDs available to method storage.
        original_fit = fit_relation
        def recorded_fit(request):
            result = original_fit(request)
            known_receipts.append(result["receipt"])
            return result
        recorded_fit.__doc__ = (fit_relation.__doc__ +
            " Its returned receipt ID may be cited in remember_method's optional derivation list.")
        tools["fit_relation"] = recorded_fit
    expected = set(COMMON_TOOLS)
    if with_constructor:
        expected.add("fit_relation")
    if set(tools) != expected:
        raise AssertionError("tool surfaces differ from the frozen protocol")
    return tools


def _make_model(base_url, model_name):
    if not os.environ.get("NVIDIA_API_KEY"):
        raise RuntimeError("NVIDIA_API_KEY is unavailable; no request was made")
    wrapped = JsonModel(model_name, base_url, protocol="openai", key_env="NVIDIA_API_KEY",
                        timeout=REQUEST_SECONDS, max_tokens=RESPONSE_TOKENS,
                        temperature=0.95, top_p=1.0, reasoning_effort="high",
                        stream=True, parse_mode="strict")
    wrapped._opener = CountingOpener(wrapped._opener)
    return ContextBoundModel(wrapped)


def _model_usage_accounting(journal_path: Path, model):
    """Aggregate usage per recorded attempt, counting unknowns only after HTTP open."""
    events = Journal.read(journal_path)
    attempts = sum(event["kind"] == "model_attempt" for event in events)
    responses = [event["body"] for event in events if event["kind"] == "model_response"]
    errors = [event["body"] for event in events if event["kind"] == "model_error"]
    request_counts = list(getattr(model, "http_requests_per_call", []))
    if len(request_counts) < attempts:
        request_counts.extend([0] * (attempts - len(request_counts)))
    input_total = output_total = 0
    input_unknown = output_unknown = unknown_calls = 0
    for index in range(min(attempts, len(responses))):
        request_count = request_counts[index]
        if request_count <= 0:
            continue
        usage = responses[index].get("usage")
        usage = usage if isinstance(usage, dict) else {}
        in_tokens, out_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        if type(in_tokens) is int and in_tokens >= 0:
            input_total += in_tokens
        else:
            input_unknown += 1
        if type(out_tokens) is int and out_tokens >= 0:
            output_total += out_tokens
        else:
            output_unknown += 1
        if (type(in_tokens) is not int or in_tokens < 0 or
                type(out_tokens) is not int or out_tokens < 0):
            unknown_calls += 1
    host_refusals = sum(error.get("type") == "ContextBudgetRefusal" for error in errors)
    return {"logical_model_attempts": attempts,
            "provider_request_attempts": sum(request_counts[:attempts]),
            "host_context_refusals": host_refusals,
            "unknown_input_usage_requests": input_unknown,
            "unknown_output_usage_requests": output_unknown,
            "unknown_usage_requests": unknown_calls,
            "model_input_tokens": input_total if input_unknown == 0 else None,
            "model_output_tokens": output_total if output_unknown == 0 else None,
            "usage_complete_for_provider_requests": input_unknown == 0 and output_unknown == 0}


def _last_submitted_artifact_id(journal_path: Path):
    try:
        events = Journal.read(journal_path)
    except Exception:
        return None
    for event in reversed(events):
        if event["kind"] != "proposal":
            continue
        action = event["body"]
        answer = action.get("answer") if action.get("kind") == "answer" else None
        if isinstance(answer, dict) and isinstance(answer.get("artifact"), str):
            return answer["artifact"]
    return None


def _run_one(output_root: Path, seed: int, arm: str, base_url: str, model_name: str):
    fixture = generate_fixture(seed)
    run_id = f"seed-{seed}-{arm.lower()}"
    run_dir = output_root / "acquisition" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    memory_path = run_dir / "memory"
    journal_path = run_dir / "events.jsonl"
    values = fixture["values"]
    task = task_for_acquisition(values)
    public_prefix = values[:12]
    # The fixture continuation remains in this host-side closure; tool docs and
    # model context contain no seed, coefficients, generator, or held-out values.
    model = None
    broker = None
    observer = None
    account = None
    with Journal(journal_path) as journal, ArtifactMemory(memory_path) as memory:
        try:
            executor = PythonExecutor(timeout=SANDBOX_SECONDS, output_limit=65536)
            model = _make_model(base_url, model_name)
            broker = ProgramTools(task, memory, journal, executor,
                                  max_executions=MAX_EXECUTIONS,
                                  provenance={"study": "astra-constructor-live",
                                              "model": model.identity})
            observer = ForwardObserver(public_prefix, values[12:], journal)
            tools = build_tools(broker, observer, with_constructor=(arm == "WITH_CONSTRUCTOR"),
                                initial_prefix=public_prefix, journal=journal)
            result = investigate(task.request, model, tools, broker.checker, journal,
                                 Budget(model_calls=MODEL_CALLS, tool_calls=TOOL_CALLS),
                                 checker_feedback=broker.checker_feedback,
                                 continue_transport_failures=True,
                                 outage_limit=OUTAGE_LIMIT)
            usage_account = _model_usage_accounting(journal_path, model)
            account = {"seed": seed, "arm": arm, "result": asdict(result),
                       "model_identity": model.identity,
                       "model_calls_attempted": model.calls_attempted,
                       **usage_account,
                       "tool_calls": result.tool_calls,
                       "python_executions": broker.execution_attempts,
                       "reveals": observer.reveals,
                       "submitted_artifact_id": _last_submitted_artifact_id(journal_path),
                       "journal_sha256": _sha256(journal_path),
                       "memory_events_sha256": _sha256(memory_path / "events.jsonl"),
                       "task_scope": task.scope_id}
            (run_dir / "result.json").write_bytes((canonical(account) + "\n").encode("utf-8"))
        except Exception as error:
            journal.append("study_run_failure", {"error_type": type(error).__name__})
            usage_account = (_model_usage_accounting(journal_path, model)
                             if model is not None else {
                                 "logical_model_attempts": 0,
                                 "provider_request_attempts": 0,
                                 "host_context_refusals": 0,
                                 "unknown_input_usage_requests": 0,
                                 "unknown_output_usage_requests": 0,
                                 "unknown_usage_requests": 0,
                                 "model_input_tokens": 0,
                                 "model_output_tokens": 0,
                                 "usage_complete_for_provider_requests": True})
            account = {"seed": seed, "arm": arm,
                       "status": "study_run_failure", "error_type": type(error).__name__,
                       "model_identity": getattr(model, "identity", None),
                       "model_calls_attempted": getattr(model, "calls_attempted", 0),
                       **usage_account,
                       "tool_calls": None,
                       "python_executions": getattr(broker, "execution_attempts", None),
                       "reveals": getattr(observer, "reveals", None),
                       "submitted_artifact_id": _last_submitted_artifact_id(journal_path),
                       "journal_sha256": _sha256(journal_path),
                       "memory_events_sha256": _sha256(memory_path / "events.jsonl"),
                       "task_scope": task.scope_id}
            (run_dir / "result.json").write_bytes((canonical(account) + "\n").encode("utf-8"))
    # Private fixture provenance is written only after the model run has ended.
    (run_dir / "fixture-private.json").write_bytes((canonical(fixture) + "\n").encode("utf-8"))
    return account


def _write_reuse_task(path: Path, source_values: list[str], heldout_values: list[str],
                      acquisition_prefix_length: int):
    task = task_for_reuse(source_values, heldout_values, acquisition_prefix_length)
    manifest = {"request": task.request, "checks": task.checks,
                "context": task.context}
    if task.has_input:
        manifest["input"] = task.input
    path.write_bytes((canonical(manifest) + "\n").encode("utf-8"))
    return _sha256(path)


def _fresh_process_reuse(output_root: Path, account: dict, source_values: list[str],
                         heldout_values: list[str], *, python_executable=sys.executable):
    artifact = account.get("submitted_artifact_id")
    arm, seed = account["arm"], account["seed"]
    reuse_root = output_root / "reuse" / f"seed-{seed}-{arm.lower()}"
    reuse_root.mkdir(parents=True, exist_ok=False)
    record = {"seed": seed, "arm": arm, "artifact_id": artifact,
              "model_calls": 0, "source_run_journal_sha256": account["journal_sha256"]}
    if artifact is None:
        record.update({"status": "missing_final_artifact", "attempted": False})
        (reuse_root / "result.json").write_bytes((canonical(record) + "\n").encode("utf-8"))
        return record
    task_path = reuse_root / "reuse-task.json"
    acquisition_prefix_length = 12 + (account.get("reveals") or 0)
    task_sha = _write_reuse_task(task_path, source_values, heldout_values,
                                 acquisition_prefix_length)
    cli_out = reuse_root / "cli-output"
    command = [python_executable, "-m", "kuttaka_runtime", "reuse",
               "--task", str(task_path), "--memory", str(output_root / "acquisition" /
                    f"seed-{seed}-{arm.lower()}" / "memory"),
               "--output", str(cli_out), "--artifact", artifact,
               "--max-executions", str(REUSE_EXECUTIONS),
               "--execution-timeout", str(SANDBOX_SECONDS)]
    started = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=Path(__file__).resolve().parents[2],
                              capture_output=True, text=True, errors="replace",
                              timeout=REQUEST_SECONDS + 60,
                              check=False)
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        timeout = False
        launch_error = None
    except subprocess.TimeoutExpired as error:
        exit_code = None
        stdout = error.stdout.decode("utf-8", errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        timeout = True
        launch_error = "TimeoutExpired"
    except Exception as error:
        exit_code, stdout, stderr = None, "", ""
        timeout = False
        launch_error = type(error).__name__
    (reuse_root / "stdout.txt").write_text(stdout)
    (reuse_root / "stderr.txt").write_text(stderr)
    cli_result_path = cli_out / "result.json"
    cli_result = None
    if cli_result_path.is_file():
        try:
            cli_result = json.loads(cli_result_path.read_text())
        except (OSError, ValueError):
            cli_result = None
    check_scores = _reuse_check_scores(cli_out / "events.jsonl", source_values,
                                       heldout_values, acquisition_prefix_length)
    record.update({"status": (cli_result or {}).get("status", "fresh_process_failure"),
                   "attempted": True, "exit_code": exit_code, "timeout": timeout,
                   "launch_error_type": launch_error,
                   "elapsed_seconds": round(time.monotonic() - started, 6),
                   "task_sha256": task_sha, "cli_result": cli_result,
                   "check_scores": check_scores,
                   "output_directory": str(cli_out),
                   "reuse_journal_sha256": _sha256(cli_out / "events.jsonl")
                   if (cli_out / "events.jsonl").is_file() else None,
                   "outputs_match_finite_checks": bool((cli_result or {}).get("status") == "checked"
                       and len(check_scores) == 2 and all(
                           check["checker_status"] == "passed" and check["complete"]
                           and check["extra_count"] == 0 and check["missing_count"] == 0
                           and check["receipt_count"] == 1 for check in check_scores)),
                   "all_values_mathematically_correct": (len(check_scores) == 2 and all(
                       check["checker_status"] in ("passed", "counterexample")
                       and check["receipt_count"] == 1
                       and check["complete"] and check["extra_count"] == 0
                       and len(check["values"]) == 6
                       and all(item["mathematically_equal"] is True for item in check["values"])
                       for check in check_scores)),
                   "all_values_canonical_format": (len(check_scores) == 2 and all(
                       check["checker_status"] in ("passed", "counterexample")
                       and check["receipt_count"] == 1
                       and check["complete"] and check["extra_count"] == 0
                       and len(check["values"]) == 6
                       and all(item["canonical_format_match"] is True for item in check["values"])
                       for check in check_scores))})
    (reuse_root / "result.json").write_bytes((canonical(record) + "\n").encode("utf-8"))
    return record


def _reuse_check_scores(journal_path: Path, source_values: list[str], heldout_values: list[str],
                        acquisition_prefix_length: int):
    expected_sets = [source_values[acquisition_prefix_length:acquisition_prefix_length + 6],
                     heldout_values[12:18]]
    if not journal_path.is_file():
        return [_missing_check_score(expected, "missing_journal") for expected in expected_sets]
    try:
        events = Journal.read(journal_path)
    except Exception as error:
        return [_missing_check_score(expected, "journal_read_failure",
                                     error_type=type(error).__name__)
                for expected in expected_sets]
    checks = [event["body"] for event in events if event["kind"] == "method_check_receipt"]
    result = []
    for check_index, expected in enumerate(expected_sets):
        matching = [entry for entry in checks if entry.get("check_index") == check_index]
        check = matching[0] if len(matching) == 1 else None
        check_status = (check.get("status") if isinstance(check, dict) else
                        ("missing" if not matching else "duplicate_receipts"))
        has_completed_value = check_status in ("passed", "counterexample")
        actual_values = (check.get("actual")
                         if isinstance(check, dict) and has_completed_value else None)
        scores = []
        for value_index, expected_value in enumerate(expected):
            actual = (actual_values[value_index]
                      if isinstance(actual_values, list) and value_index < len(actual_values) else None)
            try:
                mathematically_equal = (Fraction(actual) == Fraction(expected_value)
                                        if isinstance(actual, str) else False)
            except (ValueError, ZeroDivisionError):
                mathematically_equal = False
            scores.append({"expected": expected_value, "actual": actual,
                           "mathematically_equal": mathematically_equal,
                           "canonical_format_match": actual == expected_value})
        result.append({"values": scores,
                       "checker_status": check_status,
                       "receipt_count": len(matching),
                       "expected_count": len(expected),
                       "actual_count": len(actual_values) if isinstance(actual_values, list) else 0,
                       "missing_count": max(0, len(expected) - len(actual_values))
                       if isinstance(actual_values, list) else len(expected),
                       "extra_count": max(0, len(actual_values) - len(expected))
                       if isinstance(actual_values, list) else 0,
                       "extra_values": actual_values[len(expected):]
                       if isinstance(actual_values, list) else [],
                       "complete": isinstance(actual_values, list) and len(actual_values) == len(expected)})
    return result


def _missing_check_score(expected, status, *, error_type=None):
    result = {"checker_status": status, "expected_count": len(expected),
              "receipt_count": 0, "actual_count": 0,
              "missing_count": len(expected), "extra_count": 0,
              "extra_values": [], "complete": False,
              "values": [{"expected": value, "actual": None,
                          "mathematically_equal": None,
                          "canonical_format_match": None} for value in expected]}
    if error_type is not None:
        result["error_type"] = error_type
    return result


def _run_frozen(output: Path, freeze: dict):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    fixtures = {seed: generate_fixture(seed) for seed in (*SEEDS, REUSE_SEED)}
    protocol = {"status": "FROZEN; execution contract accepted",
                "seeds": list(SEEDS), "reuse_seed": REUSE_SEED,
                "arm_order": [list(pair) for pair in ARM_ORDER],
                "budgets": {"model_calls": MODEL_CALLS, "response_tokens_per_call": RESPONSE_TOKENS,
                            "context_bytes_per_call": CONTEXT_BYTES, "tool_calls": TOOL_CALLS,
                            "python_executions_total": TOTAL_EXECUTION_CEILING,
                            "acquisition_python_executions": MAX_EXECUTIONS,
                            "fresh_process_reuse_python_executions": REUSE_EXECUTIONS,
                            "sandbox_seconds": SANDBOX_SECONDS,
                            "request_seconds": REQUEST_SECONDS, "outage_limit": OUTAGE_LIMIT,
                            "observation_reveals": MAX_REVEALS},
                "model": freeze["model"],
                "source_hashes": freeze["source_sha256"],
                "freeze_json_sha256": _sha256(FREEZE_PATH),
                "fixtures_private_sha256": {str(seed): hashlib.sha256(canonical(fixtures[seed]).encode("utf-8")).hexdigest()
                                             for seed in fixtures},
                "attribution": "model authors method; optional exact finite fitter is engineered; no transfer claim"}
    (output / "protocol.json").write_bytes((canonical(protocol) + "\n").encode("utf-8"))
    sandbox = preflight()
    (output / "preflight.json").write_bytes((canonical(sandbox) + "\n").encode("utf-8"))
    if sandbox.get("ready") is not True:
        summary = {"status": "preflight_failed", "preflight": sandbox,
                   "acquisition": [], "fresh_process_reuse": [],
                   "model_attempts": 0, "provider_request_attempts": 0,
                   "reuse_model_calls": 0}
        (output / "summary.json").write_bytes((canonical(summary) + "\n").encode("utf-8"))
        return summary
    acquisition = []
    # Sequential execution is part of the frozen plan, avoiding parallel endpoint load.
    for seed, arms in zip(SEEDS, ARM_ORDER):
        for arm in arms:
            acquisition.append(_run_one(output, seed, arm, MODEL_ENDPOINT, MODEL_NAME))
    reuse = []
    for account in acquisition:
        seed = account["seed"]
        reuse.append(_fresh_process_reuse(output, account, fixtures[seed]["values"],
                                          fixtures[REUSE_SEED]["values"]))
    summary = {"protocol_sha256": _sha256(output / "protocol.json"),
               "acquisition": acquisition, "fresh_process_reuse": reuse,
               "model_attempts": sum(row["model_calls_attempted"] for row in acquisition),
               "provider_request_attempts": sum(row["provider_request_attempts"] for row in acquisition),
               "reuse_model_calls": 0}
    (output / "summary.json").write_bytes((canonical(summary) + "\n").encode("utf-8"))
    return summary


def run(output: Path, *, base_url=MODEL_ENDPOINT, model_name=MODEL_NAME):
    if base_url != MODEL_ENDPOINT or model_name != MODEL_NAME:
        raise RuntimeError("model and endpoint are frozen; CLI overrides are not permitted")
    freeze = validate_freeze()
    resolved_output = validate_private_output(output)
    previous_umask = os.umask(0o077)
    try:
        return _run_frozen(resolved_output, freeze)
    finally:
        os.umask(previous_umask)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="new output directory; existing paths are refused")
    args = parser.parse_args(argv)
    if not os.environ.get("NVIDIA_API_KEY"):
        parser.error("NVIDIA_API_KEY must be configured outside the command line")
    try:
        result = run(args.output)
    except Exception as error:
        # Do not print provider bodies, tool output, task values, or credentials.
        print(canonical({"status": "failed", "error_type": type(error).__name__}))
        return 1
    status = "complete" if result.get("status") != "preflight_failed" else "preflight_failed"
    print(canonical({"status": status,
                     "provider_request_attempts": result["provider_request_attempts"],
                     "output": str(args.output)}))
    return 0 if status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
