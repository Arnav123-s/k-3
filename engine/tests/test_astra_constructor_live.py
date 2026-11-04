"""Local, non-live checks for the constructor study harness."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from experiments.astra_constructor_live import run as study
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.programs import ProgramTask, ProgramTools
from kuttaka_runtime.runtime import Budget, ToolInputError, investigate
from kuttaka_runtime.store import Journal, canonical


LINEAR_SOURCE = '''def run(input):
    prefix = [int(value) for value in input["prefix"]]
    steps = input["steps"]
    delta = prefix[-1] - prefix[-2]
    return [str(prefix[-1] + delta * offset) for offset in range(1, steps + 1)]
'''


class StubModel:
    identity = {"model": "local-test-stub", "generation": {"max_tokens": 8192}}
    max_tokens = 8192

    def __init__(self):
        self.calls = 0
        self.last_usage = {"input_tokens": None, "output_tokens": None}
        self.last_response = None
        self.contexts = []

    def propose(self, context):
        self.calls += 1
        self.contexts.append(json.loads(canonical(context)))
        if self.calls == 1:
            return {"kind": "experiment", "tool": "remember_method",
                    "input": {"name": "test linear method", "description": "test-only",
                              "source": LINEAR_SOURCE},
                    "predictions": [{"hypothesis": "one method handles these inputs",
                                     "outcome": "stored"}]}
        artifact = context["history"][0]["observation"]["artifact"]
        return {"kind": "answer", "answer": {"artifact": artifact}}


class TestExecutor:
    """Runs only this file's fixed test method; no external executor or Docker."""

    def __call__(self, job):
        namespace = {}
        exec(compile(job["source"], "<fixed-test-method>", "exec"), namespace)
        result = namespace["run"](job["input"])
        return {"status": "completed", "exit_code": 0,
                "output": json.dumps({"result": result}, separators=(",", ":"))}


class ObserverModel:
    identity = {"model": "local-observer-stub"}
    max_tokens = 8192
    last_usage = {"input_tokens": 0, "output_tokens": 0}
    last_response = None

    def __init__(self, actual):
        self.actual = actual
        self.calls = 0

    def propose(self, context):
        self.calls += 1
        if self.calls == 1:
            return {"kind": "experiment", "tool": "observe_next",
                    "input": {"prediction": "999"},
                    "predictions": [{"hypothesis": "unmatched commitment",
                                     "outcome": "998"}]}
        if self.calls == 2:
            return {"kind": "experiment", "tool": "observe_next",
                    "input": {"prediction": "999"},
                    "predictions": [{"hypothesis": "committed but false forecast",
                                     "outcome": "999"}]}
        return {"kind": "unresolved", "reason": "fixture test complete"}


class FailedFinalModel:
    identity = {"model": "local-failed-final-stub"}
    max_tokens = 8192
    last_usage = {"input_tokens": 0, "output_tokens": 0}
    last_response = None

    def __init__(self):
        self.calls = 0

    def propose(self, context):
        self.calls += 1
        return {"kind": "answer", "answer": {"artifact": "f" * 64}}


class ConstructorLiveTests(unittest.TestCase):
    def test_fixture_and_prompt_exclude_private_labels_and_future_values(self):
        fixture = study.generate_fixture(1171)
        self.assertEqual(len(fixture["values"]), 22)
        task = study.task_for_acquisition(fixture["values"])
        prompt = task.request
        self.assertIn(canonical(fixture["values"][:12]), prompt)
        self.assertNotIn("1171", prompt)
        for value in fixture["values"][12:]:
            self.assertNotIn(value, prompt)
        altered = study.generate_fixture(1177)
        self.assertEqual(altered["values"][:12], ["0"] * 12)
        self.assertEqual(altered["values"][14], "1")
        self.assertEqual(study.generate_fixture(1181)["values"],
                         study.generate_fixture(1181)["values"])

    def test_negative_observer_requires_committed_exact_prediction_and_releases_one_value(self):
        fixture = study.generate_fixture(1171)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            with Journal(path) as journal:
                observer = study.ForwardObserver(fixture["values"][:12],
                                                 fixture["values"][12:], journal)
                journal.append("proposal", {
                    "kind": "experiment", "tool": "observe_next",
                    "input": {"prediction": fixture["values"][12]},
                    "predictions": [{"hypothesis": "candidate", "outcome": fixture["values"][12]}]})
                result = observer({"prediction": fixture["values"][12]})
                self.assertEqual(result, {"status": "observed",
                                          "value": fixture["values"][12], "matches": True,
                                          "canonical_format_match": True})
                self.assertEqual(observer.reveals, 1)
                self.assertNotIn("future_values", result)
                noncanonical_prediction = fixture["values"][13] + "/1"
                journal.append("proposal", {
                    "kind": "experiment", "tool": "observe_next",
                    "input": {"prediction": noncanonical_prediction},
                    "predictions": [{"hypothesis": "same value, another encoding",
                                     "outcome": noncanonical_prediction}]})
                noncanonical = observer({"prediction": noncanonical_prediction})
                self.assertTrue(noncanonical["matches"])
                self.assertFalse(noncanonical["canonical_format_match"])
                self.assertEqual(observer.reveals, 2)
                journal.append("proposal", {
                    "kind": "experiment", "tool": "observe_next",
                    "input": {"prediction": "wrong"},
                    "predictions": [{"hypothesis": "candidate", "outcome": "other"}]})
                with self.assertRaises(ToolInputError):
                    observer({"prediction": "wrong"})
                self.assertEqual(observer.reveals, 2)

    def test_runtime_commits_predictions_before_observe_and_charges_mismatch(self):
        fixture = study.generate_fixture(1171)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            with Journal(path) as journal:
                observer = study.ForwardObserver(fixture["values"][:12],
                                                 fixture["values"][12:], journal)
                model = ObserverModel(fixture["values"][12])
                result = investigate(
                    study.task_for_acquisition(fixture["values"]).request,
                    model, {"observe_next": observer.doc()},
                    checker=lambda *_: False, journal=journal,
                    budget=Budget(model_calls=3, tool_calls=2))
                self.assertEqual(result.status, "unresolved")
                self.assertEqual(result.model_calls, 3)
                self.assertEqual(result.tool_calls, 2)
                self.assertEqual(observer.reveals, 1)
                events = Journal.read(path)
                proposals = [e["body"] for e in events if e["kind"] == "proposal"]
                self.assertEqual(len(proposals), 2)
                self.assertNotEqual(proposals[0]["input"]["prediction"],
                                    proposals[0]["predictions"][0]["outcome"])
                releases = [e["body"] for e in events if e["kind"] == "live_observation_release"]
                self.assertEqual(releases, [{"reveal_number": 1, "matches": False,
                                             "canonical_format_match": False,
                                             "value": fixture["values"][12]}])
                tool_errors = [e for e in events if e["kind"] == "tool_error"]
                self.assertEqual(len(tool_errors), 1)

    def test_fit_tool_uses_only_exact_revealed_prefix_and_returns_no_forecast(self):
        fixture = study.generate_fixture(1171)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            with Journal(path) as journal:
                observer = study.ForwardObserver(fixture["values"][:12],
                                                 fixture["values"][12:], journal)
                fit = study.fit_tool(fixture["values"][:12], observer, journal)
                result = fit({"order": 2, "degree": 1})
                certificate = result["certificate"]
                self.assertTrue(study.verify_shift_relation(fixture["values"][:12], certificate))
                self.assertEqual(certificate["sample_count"], 12)
                self.assertEqual(certificate["training_values"], fixture["values"][:12])
                self.assertEqual(set(result), {"status", "certificate", "receipt"})
                self.assertNotIn("prediction", result)
                self.assertEqual(observer.reveals, 0)
                self.assertEqual(len(fit.fit_receipts), 1)
                with self.assertRaises(ToolInputError):
                    fit({"order": 4, "degree": 2})

    def test_fit_is_attached_as_derivation_receipt_not_executable_dependency(self):
        values = study.generate_fixture(1171)["values"]
        task = study.task_for_acquisition(values)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with Journal(root / "events.jsonl") as journal, ArtifactMemory(root / "memory") as memory:
                broker = ProgramTools(task, memory, journal, TestExecutor(), max_executions=47,
                                      provenance={"study": "test"})
                observer = study.ForwardObserver(values[:12], values[12:], journal)
                tools = study.build_tools(broker, observer, with_constructor=True,
                                          initial_prefix=values[:12], journal=journal)
                fit = tools["fit_relation"]({"order": 2, "degree": 1})
                artifact = tools["remember_method"]({
                    "name": "receipt-linked test method", "description": "test-only",
                    "source": LINEAR_SOURCE, "derivation": [fit["receipt"]]})["artifact"]
                payload = memory.get(artifact)
                derivation = payload["provenance"]["derivation"]
                self.assertEqual(derivation["fit_relation_receipts"], [fit["receipt"]])
                self.assertEqual(derivation["method_source_sha256"],
                                 hashlib.sha256(LINEAR_SOURCE.encode()).hexdigest())
                self.assertEqual(payload["dependencies"], [])
                fit_event = next(event for event in Journal.read(journal.path)
                                 if event["hash"] == fit["receipt"])
                self.assertEqual(fit_event["kind"], "derivation_receipt")
                self.assertEqual(fit_event["body"]["constructor_source_sha256"],
                                 study._sha256(Path(study.__file__).resolve().parents[2] /
                                               "kuttaka_runtime" / "shift_relation.py"))

    def test_tool_surfaces_match_except_for_optional_fit_tool(self):
        values = [str(i) for i in range(18)]
        task = study.task_for_acquisition(values)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with Journal(root / "events.jsonl") as journal, ArtifactMemory(root / "memory") as memory:
                broker = ProgramTools(task, memory, journal, TestExecutor(), max_executions=47,
                                      provenance={"model": "stub"})
                observer = study.ForwardObserver(values[:12], values[12:], journal)
                control = study.build_tools(broker, observer, with_constructor=False,
                                            initial_prefix=values[:12], journal=journal)
                treatment = study.build_tools(broker, observer, with_constructor=True,
                                              initial_prefix=values[:12], journal=journal)
                self.assertEqual(set(control) | {"fit_relation"}, set(treatment))
                self.assertEqual({name: control[name].__doc__ for name in control},
                                 {name: treatment[name].__doc__ for name in control})

    def test_stubbed_runtime_stores_exact_artifact_and_spends_declared_calls(self):
        values = [str(i) for i in range(18)]
        task = study.task_for_acquisition(values)
        model = StubModel()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with Journal(root / "events.jsonl") as journal, ArtifactMemory(root / "memory") as memory:
                broker = ProgramTools(task, memory, journal, TestExecutor(), max_executions=49,
                                      provenance={"model": "stub"})
                observer = study.ForwardObserver(values[:12], values[12:], journal)
                tools = study.build_tools(broker, observer, with_constructor=False,
                                          initial_prefix=values[:12], journal=journal)
                result = investigate(task.request, model, tools, broker.checker, journal,
                                     Budget(model_calls=10, tool_calls=9),
                                     checker_feedback=broker.checker_feedback)
                self.assertEqual(result.status, "checked")
                artifact = result.answer["artifact"]
                self.assertEqual(result.model_calls, 2)
                self.assertEqual(result.tool_calls, 2)
                self.assertEqual(broker.execution_attempts, 2)
                self.assertEqual(memory.status(artifact, task.scope_id), "qualified")
                payload = memory.get(artifact)
                self.assertEqual(payload["body"]["source"], LINEAR_SOURCE)
                self.assertNotIn("1171", canonical(model.contexts))
                memory.close()
            with ArtifactMemory(root / "memory") as reopened:
                self.assertEqual(reopened.get(artifact), payload)
                self.assertEqual(hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest(), artifact)
                altered = dict(payload)
                altered["body"] = dict(payload["body"], description="changed")
                different_id = reopened.put(altered)
                self.assertNotEqual(different_id, artifact)
                self.assertEqual(reopened.get(artifact), payload)

    def test_run_one_accounting_path_preserves_success_and_separates_host_calls(self):
        values = [str(i) for i in range(22)]
        fixture = {"seed": 1171, "coefficients": {"a": 0, "b": 0, "c": 0},
                   "values": values, "alteration": None, "generator": "test-only",
                   "initial_values": values[:2]}
        stub = study.ContextBoundModel(StubModel())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(study, "generate_fixture", return_value=fixture), \
                    patch.object(study, "_make_model", return_value=stub), \
                    patch.object(study, "PythonExecutor", return_value=TestExecutor()):
                account = study._run_one(root, 1171, "MODEL_ONLY", "unused", "stub")
            self.assertEqual(account["result"]["status"], "checked")
            self.assertEqual(account["model_calls_attempted"], 2)
            self.assertEqual(account["logical_model_attempts"], 2)
            self.assertEqual(account["provider_request_attempts"], 0)
            self.assertEqual(account["unknown_usage_requests"], 0)
            self.assertEqual(account["model_input_tokens"], 0)
            self.assertEqual(account["model_output_tokens"], 0)
            self.assertTrue(account["usage_complete_for_provider_requests"])
            self.assertTrue((root / "acquisition" / "seed-1171-model_only" /
                             "events.jsonl").is_file())

    def test_submitted_artifact_is_retained_even_when_public_check_rejects_it(self):
        values = [str(i) for i in range(22)]
        fixture = {"seed": 1171, "coefficients": {"a": 0, "b": 0, "c": 0},
                   "values": values, "alteration": None, "generator": "test-only",
                   "initial_values": values[:2]}
        stub = study.ContextBoundModel(FailedFinalModel())
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(study, "generate_fixture", return_value=fixture), \
                    patch.object(study, "_make_model", return_value=stub), \
                    patch.object(study, "PythonExecutor", return_value=TestExecutor()):
                account = study._run_one(Path(temp), 1171, "MODEL_ONLY", "unused", "stub")
            self.assertEqual(account["result"]["status"], "incomplete")
            self.assertEqual(account["submitted_artifact_id"], "f" * 64)

    def test_context_wrapper_enforces_bytes_before_call_and_forwards_usage(self):
        class Wrapped:
            identity = {"model": "stub"}
            max_tokens = 8192
            last_usage = {"input_tokens": 3, "output_tokens": 4}
            last_response = {"status": 200}
            calls = 0

            def propose(self, context):
                self.calls += 1
                self.last_usage = {"input_tokens": 3, "output_tokens": 4}
                self.last_response = {"status": 200}
                return {"kind": "unresolved", "reason": "test"}

        wrapped = Wrapped()
        model = study.ContextBoundModel(wrapped, max_context_bytes=32)
        with self.assertRaises(ValueError):
            model.propose({"large": "x" * 64})
        self.assertEqual(wrapped.calls, 0)
        self.assertEqual(model.calls_attempted, 1)
        self.assertEqual(model.provider_calls, 0)
        self.assertEqual(model.last_usage, {"input_tokens": 0, "output_tokens": 0})
        model.propose({"small": 1})
        self.assertEqual(model.last_usage, {"input_tokens": 3, "output_tokens": 4})
        self.assertEqual(model.last_response, {"status": 200})

    def test_usage_unknowns_count_only_actual_http_requests(self):
        class Attempts:
            http_requests_per_call = [1, 0]

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            with Journal(path) as journal:
                journal.append("model_attempt", {"number": 1})
                journal.append("model_response", {"receipt": None,
                                                   "usage": {"input_tokens": None,
                                                             "output_tokens": None}})
                journal.append("model_error", {"type": "TransportFailure"})
                journal.append("model_attempt", {"number": 2})
                journal.append("model_response", {"receipt": None,
                                                   "usage": {"input_tokens": 0,
                                                             "output_tokens": 0}})
                journal.append("model_error", {"type": "ContextBudgetRefusal"})
            account = study._model_usage_accounting(path, Attempts())
            self.assertEqual(account["logical_model_attempts"], 2)
            self.assertEqual(account["provider_request_attempts"], 1)
            self.assertEqual(account["host_context_refusals"], 1)
            self.assertEqual(account["unknown_usage_requests"], 1)
            self.assertIsNone(account["model_input_tokens"])
            self.assertIsNone(account["model_output_tokens"])
            refusal_path = Path(temp) / "refusal.jsonl"
            with Journal(refusal_path) as journal:
                journal.append("model_attempt", {"number": 1})
                journal.append("model_response", {"receipt": None,
                                                   "usage": {"input_tokens": 0,
                                                             "output_tokens": 0}})
                journal.append("model_error", {"type": "ContextBudgetRefusal"})
            refusal_only = study._model_usage_accounting(refusal_path, type(
                "NoRequest", (), {"http_requests_per_call": [0]})())
            self.assertEqual(refusal_only["unknown_usage_requests"], 0)
            self.assertEqual(refusal_only["model_input_tokens"], 0)
            self.assertEqual(refusal_only["model_output_tokens"], 0)

    def test_freeze_manifest_binds_runtime_tests_fixtures_profile_budgets_and_tools(self):
        manifest = study.freeze_manifest()
        runtime_files = {str(path.relative_to(Path(study.__file__).resolve().parents[2]))
                         for path in (Path(study.__file__).resolve().parents[2] /
                                      "kuttaka_runtime").glob("*.py")}
        self.assertTrue(runtime_files <= set(manifest["source_sha256"]))
        self.assertIn("tests/test_astra_constructor_live.py", manifest["source_sha256"])
        self.assertIn("experiments/astra_constructor_live/PROTOCOL.md", manifest["source_sha256"])
        self.assertEqual(set(manifest["fixture_sha256"]), {"1171", "1173", "1177", "1181"})
        self.assertEqual(manifest["model"]["identity"]["model"], "meta/muse-glimmer-30b")
        self.assertEqual(manifest["model"]["identity"]["endpoint"],
                         "https://integrate.api.nvidia.com/v1")
        self.assertEqual(manifest["budgets"]["python_executions_total"], 49)
        self.assertEqual(manifest["toolsets"]["with_constructor"][:-1],
                         manifest["toolsets"]["common"])

        with tempfile.TemporaryDirectory() as temp:
            freeze_path = Path(temp) / "freeze.json"
            freeze_path.write_text(canonical(manifest))
            with patch.object(study, "_protocol_is_frozen", return_value=True):
                self.assertEqual(study.validate_freeze(freeze_path), manifest)
                changed_config = dict(manifest)
                changed_config["model"] = dict(manifest["model"], temperature=0.1)
                freeze_path.write_text(canonical(changed_config))
                with self.assertRaises(RuntimeError):
                    study.validate_freeze(freeze_path)
                freeze_path.write_text(canonical(manifest))
                original_hash = study._sha256
                def changed_source_hash(path):
                    if Path(path).name == "runtime.py":
                        return "0" * 64
                    return original_hash(path)
                with patch.object(study, "_sha256", side_effect=changed_source_hash):
                    with self.assertRaises(RuntimeError):
                        study.validate_freeze(freeze_path)

    def test_output_path_must_be_private_astra_child_before_run(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            allowed = repo / ".astra" / "run-1"
            self.assertEqual(study.validate_private_output(allowed, repo_root=repo), allowed.resolve())
            with self.assertRaises(ValueError):
                study.validate_private_output(Path(temp) / "public-run", repo_root=repo)

    def test_docker_preflight_failure_is_retained_before_any_model_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            freeze_path = root / "freeze.json"
            freeze_path.write_text("{}")
            output = root / "study-output"
            with patch.object(study, "FREEZE_PATH", freeze_path), \
                    patch.object(study, "preflight", return_value={
                        "ready": False, "reason": "pinned_image_unavailable",
                        "image": "pinned-test-image"}), \
                    patch.object(study, "_make_model", side_effect=AssertionError(
                        "model creation must not follow preflight failure")):
                summary = study._run_frozen(output, study.freeze_manifest())
            self.assertEqual(summary["status"], "preflight_failed")
            self.assertEqual(summary["model_attempts"], 0)
            self.assertEqual(summary["provider_request_attempts"], 0)
            self.assertFalse((output / "acquisition").exists())
            self.assertEqual(json.loads((output / "preflight.json").read_text())["ready"], False)

    def test_reuse_task_scores_all_six_after_current_prefix_and_six_independent_values(self):
        acquisition = study.generate_fixture(1171)["values"]
        heldout = study.generate_fixture(1181)["values"]
        task = study.task_for_reuse(acquisition, heldout, 16)
        self.assertEqual(len(task.checks), 2)
        self.assertIsNone(task.input)
        self.assertEqual(task.checks[0]["input"], {"prefix": acquisition[:16], "steps": 6})
        self.assertEqual(task.checks[0]["expected"], acquisition[16:22])
        self.assertEqual(task.checks[1]["input"], {"prefix": heldout[:12], "steps": 6})
        self.assertEqual(task.checks[1]["expected"], heldout[12:18])
        self.assertEqual(task.checks[0]["input"]["prefix"], acquisition[:16])

    def test_fresh_process_manifest_omits_deployment_and_uses_only_two_executions(self):
        source = study.generate_fixture(1171)["values"]
        heldout = study.generate_fixture(1181)["values"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = {"seed": 1171, "arm": "MODEL_ONLY", "reveals": 0,
                       "submitted_artifact_id": "a" * 64, "journal_sha256": "b" * 64}

            def fake_run(command, **kwargs):
                task_path = Path(command[command.index("--task") + 1])
                output_path = Path(command[command.index("--output") + 1])
                manifest = json.loads(task_path.read_text())
                self.assertNotIn("input", manifest)
                self.assertFalse(ProgramTask.from_dict(manifest).has_input)
                self.assertEqual(manifest["checks"][0]["input"],
                                 {"prefix": source[:12], "steps": 6})
                self.assertEqual(manifest["checks"][1]["input"],
                                 {"prefix": heldout[:12], "steps": 6})
                self.assertEqual(command[command.index("--max-executions") + 1], "2")
                output_path.mkdir()
                (output_path / "result.json").write_text(json.dumps({"status": "checked"}))
                with Journal(output_path / "events.jsonl") as journal:
                    for index, check in enumerate(manifest["checks"]):
                        journal.append("method_check_receipt", {
                            "check_index": index, "expected": check["expected"],
                            "actual": check["expected"], "status": "passed"})
                return SimpleNamespace(returncode=0, stdout="{}\n", stderr="")

            with patch.object(study.subprocess, "run", side_effect=fake_run):
                record = study._fresh_process_reuse(root, account, source, heldout)
            self.assertEqual(record["status"], "checked")
            self.assertTrue(record["outputs_match_finite_checks"])
            self.assertTrue(record["all_values_mathematically_correct"])
            self.assertTrue(record["all_values_canonical_format"])

    def test_missing_corrupt_or_extra_reuse_output_never_passes_vacuously(self):
        source = study.generate_fixture(1171)["values"]
        heldout = study.generate_fixture(1181)["values"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            corrupt = root / "corrupt.jsonl"
            corrupt.write_text("not a valid journal\n")
            scores = study._reuse_check_scores(corrupt, source, heldout, 12)
            self.assertEqual(len(scores), 2)
            self.assertFalse(all(check["complete"] for check in scores))
            self.assertFalse(all(item["mathematically_equal"] is True
                                 for check in scores for item in check["values"]))

            task = study.task_for_reuse(source, heldout, 12)
            check_scores = []
            for index, check in enumerate(task.checks):
                actual = list(check["expected"])
                if index == 0:
                    actual.append("999")
                check_scores.append((index, check["expected"], actual))
            extra_path = root / "extra.jsonl"
            with Journal(extra_path) as journal:
                for index, expected, actual in check_scores:
                    journal.append("method_check_receipt", {
                        "check_index": index, "expected": expected, "actual": actual,
                        "status": "counterexample" if len(actual) != 6 else "passed"})
            extra_scores = study._reuse_check_scores(extra_path, source, heldout, 12)
            self.assertEqual(extra_scores[0]["extra_count"], 1)
            self.assertFalse(extra_scores[0]["complete"])
            self.assertEqual(len(extra_scores[0]["values"]), 6)

            account = {"seed": 1171, "arm": "MODEL_ONLY", "reveals": 0,
                       "submitted_artifact_id": "c" * 64, "journal_sha256": "d" * 64}
            def fake_extra_run(command, **kwargs):
                task_path = Path(command[command.index("--task") + 1])
                output_path = Path(command[command.index("--output") + 1])
                manifest = json.loads(task_path.read_text())
                output_path.mkdir()
                (output_path / "result.json").write_text(json.dumps({"status": "failed"}))
                with Journal(output_path / "events.jsonl") as journal:
                    for index, check in enumerate(manifest["checks"]):
                        actual = list(check["expected"])
                        if index == 0:
                            actual.append("999")
                        journal.append("method_check_receipt", {
                            "check_index": index, "expected": check["expected"],
                            "actual": actual,
                            "status": "counterexample" if len(actual) != 6 else "passed"})
                return SimpleNamespace(returncode=1, stdout="{}\n", stderr="")
            with patch.object(study.subprocess, "run", side_effect=fake_extra_run):
                record = study._fresh_process_reuse(root, account, source, heldout)
            self.assertEqual(record["check_scores"][0]["extra_count"], 1)
            self.assertFalse(record["all_values_mathematically_correct"])
            self.assertFalse(record["all_values_canonical_format"])


if __name__ == "__main__":
    unittest.main()
