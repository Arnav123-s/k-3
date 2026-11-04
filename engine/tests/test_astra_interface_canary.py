import unittest
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from io import BytesIO
from urllib.error import HTTPError

from experiments.astra_interface_canary import run as canary
from experiments.astra_interface_canary.run import (
    CANARY_REQUEST,
    MAX_CONTEXT_BYTES,
    MODEL_REQUESTS_PER_ARM,
    TOOL_BUDGET,
    ActualRequestBudgetExceeded,
    ContextBoundModel,
    ContextBudgetRefusal,
    OfflineToyCanary,
    RequestCountingOpener,
    ToyToolBudgetExceeded,
    ToyToolError,
    build_offline_canary,
)
from kuttaka_runtime.runtime import ToolInputError, investigate
from kuttaka_runtime.store import Journal


class OfflineInterfaceCanaryTests(unittest.TestCase):
    def _copy_canary_checkout(self, destination):
        source_root = canary._repo_root()
        destination = Path(destination)
        files = [source_root / "experiments/astra_interface_canary/run.py",
                 source_root / "experiments/astra_interface_canary/PROTOCOL.md",
                 source_root / "tests/test_astra_interface_canary.py"]
        files.extend((source_root / "kuttaka_runtime").glob("*.py"))
        for source in files:
            target = destination / source.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return destination

    def _validate_in_checkout(self, root):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(root)
        return subprocess.run(
            [sys.executable, "-B", "-c",
             "from experiments.astra_interface_canary.run import validate_freeze; print(validate_freeze())"],
            cwd=root, env=environment, capture_output=True, text=True, check=False)

    def _complete_sequence(self, broker):
        lookup = broker.tools["lookup_item"]({"key": "sample"})
        self.assertEqual(lookup, {"found": True, "value": "cobalt"})
        stored = broker.tools["store_item"]({"name": "copy", "value": lookup["value"]})
        self.assertEqual(stored, {"stored": True})
        readback = broker.tools["read_item"]({"name": "copy"})
        self.assertEqual(readback, {"found": True, "value": "cobalt"})
        return readback

    def test_complete_host_observed_sequence_and_checker_fit_four_call_budget(self):
        request, broker, tools, checker = build_offline_canary()
        self.assertEqual(request, CANARY_REQUEST)
        self.assertEqual(set(tools), {"lookup_item", "store_item", "read_item"})
        readback = self._complete_sequence(broker)
        self.assertTrue(checker(request, {"value": readback["value"]}))
        self.assertEqual(broker.calls_used, TOOL_BUDGET)
        self.assertEqual([event["kind"] for event in broker.events],
                         ["lookup_item", "store_item", "read_item", "checker"])

    def test_guessed_public_value_without_tool_sequence_is_rejected(self):
        broker = OfflineToyCanary()
        self.assertFalse(broker.check_answer(CANARY_REQUEST, {"value": "cobalt"}))
        self.assertEqual(broker.events, [{"kind": "checker", "status": "rejected"}])

    def test_tool_sequence_must_be_successful_and_in_order(self):
        broker = OfflineToyCanary()
        with self.assertRaises(ToyToolError):
            broker.store_item({"name": "copy", "value": "cobalt"})
        self.assertFalse(broker.check_answer(CANARY_REQUEST, {"value": "cobalt"}))
        self.assertEqual(broker.events[0]["status"], "sequence_error")

    def test_schemas_are_exact_and_bad_calls_are_charged(self):
        broker = OfflineToyCanary()
        with self.assertRaises(ToyToolError):
            broker.lookup_item({"key": "sample", "extra": True})
        self.assertEqual(broker.calls_used, 1)
        self.assertEqual(broker.events[0]["status"], "schema_error")

    def test_checker_requires_exact_answer_shape(self):
        broker = OfflineToyCanary()
        self._complete_sequence(broker)
        self.assertFalse(broker.check_answer(CANARY_REQUEST, {"value": "cobalt", "extra": True}))
        self.assertEqual(broker.events[-1]["status"], "rejected")

    def test_host_log_is_defensive_and_checker_cannot_be_replayed_after_budget(self):
        broker = OfflineToyCanary()
        self._complete_sequence(broker)
        log = broker.events
        log[0]["observation"]["value"] = "forged"
        self.assertTrue(broker.check_answer(CANARY_REQUEST, {"value": "cobalt"}))

        exhausted = OfflineToyCanary()
        for _ in range(TOOL_BUDGET):
            exhausted.lookup_item({"key": "sample"})
        with self.assertRaises(ToyToolBudgetExceeded):
            exhausted.check_answer(CANARY_REQUEST, {"value": "cobalt"})

    def test_freeze_manifest_binds_profiles_order_code_tests_and_protocol(self):
        manifest = canary.freeze_manifest()
        self.assertEqual(manifest["arm_order"], ["high", "low"])
        self.assertEqual(manifest["profiles"]["high"]["reasoning_effort"], "high")
        self.assertEqual(manifest["profiles"]["low"]["reasoning_effort"], "low")
        self.assertTrue(all(manifest["profiles"][arm]["reasoning_hint"] is None
                            for arm in canary.ARM_ORDER))
        self.assertTrue(all(manifest["profiles"][arm]["chat_template_kwargs"] is None
                            for arm in canary.ARM_ORDER))
        self.assertTrue(all(manifest["profiles"][arm]["detailed_response_feedback"] is False
                            for arm in canary.ARM_ORDER))
        self.assertEqual(manifest["budgets"]["actual_http_requests_total"], 10)
        self.assertIn("experiments/astra_interface_canary/PROTOCOL.md", manifest["source_sha256"])
        self.assertIn("tests/test_astra_interface_canary.py", manifest["source_sha256"])
        runtime_sources = {path.relative_to(canary._repo_root()).as_posix()
                           for path in (canary._repo_root() / "kuttaka_runtime").glob("*.py")}
        self.assertTrue(runtime_sources.issubset(manifest["source_sha256"]))
        self.assertEqual(manifest["repository_root"], ".")
        self.assertEqual(manifest["canary_source_path"], "experiments/astra_interface_canary/run.py")
        self.assertEqual(manifest["freeze_manifest_path"], "experiments/astra_interface_canary/freeze.json")
        self.assertEqual({key for key, value in manifest["profiles"]["high"].items()
                          if value != manifest["profiles"]["low"][key]}, {"reasoning_effort"})

    def test_separate_freeze_validation_accepts_exact_manifest_and_rejects_change(self):
        manifest = canary.freeze_manifest()
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_canary_checkout(Path(directory) / "snapshot")
            path = root / manifest["freeze_manifest_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            validated = self._validate_in_checkout(root)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            digest = validated.stdout.strip()
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            manifest["arm_order"] = ["low", "high"]
            path.write_text(json.dumps(manifest), encoding="utf-8")
            changed = self._validate_in_checkout(root)
            self.assertNotEqual(changed.returncode, 0)
            self.assertIn("differs", changed.stderr)

            manifest = canary.freeze_manifest()
            manifest["source_sha256"]["kuttaka_runtime/__init__.py"] = "0" * 64
            path.write_text(json.dumps(manifest), encoding="utf-8")
            source_changed = self._validate_in_checkout(root)
            self.assertNotEqual(source_changed.returncode, 0)
            self.assertIn("differs", source_changed.stderr)

    def test_freeze_is_bound_to_this_repository_and_canary_sibling(self):
        manifest = canary.freeze_manifest()
        with tempfile.TemporaryDirectory() as directory:
            root = self._copy_canary_checkout(Path(directory) / "snapshot")
            path = root / manifest["freeze_manifest_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            for field in ("repository_root", "canary_source_path", "freeze_manifest_path"):
                with self.subTest(field=field):
                    rebound = dict(manifest)
                    rebound[field] = "other-location"
                    path.write_text(json.dumps(rebound), encoding="utf-8")
                    changed = self._validate_in_checkout(root)
                    self.assertNotEqual(changed.returncode, 0)
                    self.assertIn("differs", changed.stderr)

    def test_unchanged_freeze_manifest_validates_after_checkout_relocation(self):
        manifest = canary.freeze_manifest()
        with tempfile.TemporaryDirectory() as directory:
            relocated = self._copy_canary_checkout(Path(directory) / "relocated-source")
            path = relocated / manifest["freeze_manifest_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            result = self._validate_in_checkout(relocated)
            expected_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), expected_digest)

    def test_validate_freeze_rejects_manifest_outside_actual_sibling(self):
        manifest = canary.freeze_manifest()
        with tempfile.TemporaryDirectory() as directory:
            foreign = Path(directory) / "freeze.json"
            foreign.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "adjacent"):
                canary.validate_freeze(foreign)

    def test_freeze_rejects_runtime_package_loaded_from_another_checkout(self):
        rogue = SimpleNamespace(__file__="/tmp/other-checkout/kuttaka_runtime/__init__.py",
                                __path__=["/tmp/other-checkout/kuttaka_runtime"])
        with patch.dict("sys.modules", {"kuttaka_runtime": rogue}):
            with self.assertRaisesRegex(RuntimeError, "outside the frozen checkout"):
                canary.freeze_manifest()

    def test_explicit_opt_in_is_required_before_freeze_or_network_setup(self):
        with patch.object(canary, "validate_freeze", side_effect=AssertionError("must not be read")):
            with self.assertRaisesRegex(RuntimeError, "explicit opt_in"):
                canary.run_live(Path("/tmp/canary-unused"), opt_in=False)

    def test_live_entry_fails_closed_on_missing_reviewed_freeze(self):
        with patch.object(canary, "validate_freeze",
                          side_effect=RuntimeError("reviewed canary freeze manifest is missing")), \
                patch.object(canary, "_private_output_path",
                             side_effect=AssertionError("output setup must follow freeze")), \
                patch.object(canary, "_run_arm",
                             side_effect=AssertionError("model creation must follow freeze")):
            with self.assertRaisesRegex(RuntimeError, "freeze manifest is missing"):
                canary.run_live(Path("/tmp/canary-unused"), opt_in=True)

    def test_output_must_be_fresh_and_inside_repository_astra(self):
        # Exercise the actual path guard without relying on a private directory
        # left by a live run in the developer's checkout.
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repository"
            private_root = repository / ".astra"
            private_root.mkdir(parents=True)
            with patch.object(canary, "_repo_root", return_value=repository):
                target = private_root / "new-canary"
                self.assertEqual(canary._private_output_path(target), target.resolve())
                target.mkdir(mode=0o700)
                with self.assertRaisesRegex(FileExistsError, "fresh"):
                    canary._private_output_path(target)
                with self.assertRaisesRegex(ValueError, "repository .astra"):
                    canary._private_output_path(Path(directory) / "not-private")

    def test_missing_credential_preflight_stops_before_output_or_model_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            with patch.object(canary, "validate_freeze", return_value="f" * 64), \
                    patch.object(canary, "_private_output_path", return_value=output), \
                    patch.dict(canary.os.environ, {canary.API_KEY_ENV: ""}), \
                    patch.object(canary, "_run_arm", side_effect=AssertionError("must not run")):
                with self.assertRaisesRegex(RuntimeError, "credential environment variable"):
                    canary.run_live(output, opt_in=True)
            self.assertFalse(output.exists())

    def test_live_orchestrator_uses_fixed_order_and_never_exceeds_ten_mocked_requests(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"

            def fake_run_arm(path, effort, *, max_actual_requests, model_factory):
                self.assertIs(model_factory, canary.JsonModel)
                calls.append((effort, max_actual_requests))
                return {"arm": effort, "accounting": {"actual_http_requests": 5}}

            with patch.object(canary, "validate_freeze", return_value="a" * 64), \
                    patch.object(canary, "_private_output_path", return_value=output), \
                    patch.dict(canary.os.environ, {canary.API_KEY_ENV: "dummy-test-token"}), \
                    patch.object(canary, "_run_arm", side_effect=fake_run_arm):
                result = canary.run_live(output, opt_in=True)
            self.assertEqual(calls, [("high", 5), ("low", 5)])
            self.assertEqual(result["arm_order"], ["high", "low"])
            self.assertEqual(result["actual_http_requests_total"], 10)
            self.assertEqual((output / "protocol.json").stat().st_mode & 0o777, 0o600)
            self.assertNotIn("dummy-test-token", (output / "protocol.json").read_text())

    def test_terminal_first_arm_still_records_second_arm_without_retry(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"

            def fake_run_arm(path, effort, *, max_actual_requests, model_factory):
                calls.append(effort)
                status = "incomplete" if effort == "high" else "checked"
                return {"arm": effort, "result": {"status": status},
                        "accounting": {"actual_http_requests": 1},
                        "journal_sha256": effort}

            with patch.object(canary, "validate_freeze", return_value="a" * 64), \
                    patch.object(canary, "_private_output_path", return_value=output), \
                    patch.dict(canary.os.environ, {canary.API_KEY_ENV: "dummy-test-token"}), \
                    patch.object(canary, "_run_arm", side_effect=fake_run_arm):
                result = canary.run_live(output, opt_in=True)
        self.assertEqual(calls, ["high", "low"])
        self.assertEqual([row["result"]["status"] for row in result["outcomes"]],
                         ["incomplete", "checked"])
        self.assertEqual(result["actual_http_requests_total"], 2)

    def test_http_five_xx_error_is_terminal_without_model_retry(self):
        class ServerErrorOpener:
            def open(self, *_args, **_kwargs):
                raise HTTPError("https://invalid.test", 503, "Unavailable", {}, BytesIO(b""))

        class ServerErrorModel:
            def __init__(self, *args, **kwargs):
                self.identity = {"model": args[0]}
                self.max_tokens = kwargs["max_tokens"]
                self._opener = ServerErrorOpener()
                self.last_usage = {}
                self.last_response = None

            def propose(self, _context):
                return self._opener.open(None)

        with tempfile.TemporaryDirectory() as directory:
            outcome = canary._run_arm(Path(directory) / "high", "high",
                                      model_factory=ServerErrorModel)
            events = Journal.read(Path(directory) / "high" / "events.jsonl")
        self.assertEqual(outcome["result"]["status"], "incomplete")
        self.assertEqual(outcome["accounting"]["logical_model_attempts"], 1)
        self.assertEqual(outcome["accounting"]["actual_http_requests"], 1)
        self.assertEqual(outcome["accounting"]["hard_4xx_terminal_errors"], 0)
        self.assertEqual(outcome["accounting"]["provider_http_or_transport_failures"], 1)
        self.assertEqual(sum(event["kind"] == "model_attempt" for event in events), 1)

    def test_context_refusal_spends_logical_call_without_http_request(self):
        class Model:
            identity = {"model": "test"}
            max_tokens = 8192
            last_usage = {"input_tokens": 99}
            last_response = {"status": 200}

            def propose(self, context):
                raise AssertionError("oversize context must be refused before model call")

        counter = RequestCountingOpener(None, 5)
        bounded = ContextBoundModel(Model(), counter, MAX_CONTEXT_BYTES)
        with self.assertRaises(ContextBudgetRefusal):
            bounded.propose({"oversized": "x" * MAX_CONTEXT_BYTES})
        self.assertEqual(counter.per_logical_attempt, [0])
        self.assertEqual(len(counter.elapsed_per_logical_attempt), 1)
        self.assertIsInstance(counter.elapsed_per_logical_attempt[0], float)
        self.assertGreaterEqual(counter.elapsed_per_logical_attempt[0], 0)
        self.assertEqual(counter.actual_requests, 0)
        self.assertEqual(bounded.last_usage, {})
        self.assertIsNone(bounded.last_response)

    def test_mocked_arm_runs_strict_actions_with_accounted_local_requests_only(self):
        class FakeOpener:
            def open(self, *_args, **_kwargs):
                return None

        class ScriptedModel:
            def __init__(self, *args, **kwargs):
                self.identity = {"model": args[0], "generation": kwargs}
                self.max_tokens = kwargs["max_tokens"]
                self.last_usage = {}
                self.last_response = None
                self._opener = FakeOpener()
                self.calls = 0

            def propose(self, context):
                self._opener.open(None)  # counting wrapper only; delegate is inert
                self.last_usage = {"input_tokens": 11, "output_tokens": 7,
                                   "finish_reason": "stop"}
                self.last_response = {"status": 200, "body": "synthetic test receipt"}
                self.calls += 1
                if self.calls == 1:
                    return {"kind": "experiment", "tool": "lookup_item",
                            "input": {"key": "sample"},
                            "predictions": [{"hypothesis": "one toy record exists",
                                             "outcome": {"found": True}}]}
                observation = context["history"][-1]["observation"]
                if self.calls == 2:
                    return {"kind": "experiment", "tool": "store_item",
                            "input": {"name": "copy", "value": observation["value"]},
                            "predictions": [{"hypothesis": "the value is stored",
                                             "outcome": {"stored": True}}]}
                if self.calls == 3:
                    return {"kind": "experiment", "tool": "read_item",
                            "input": {"name": "copy"},
                            "predictions": [{"hypothesis": "the stored value is readable",
                                             "outcome": observation}]}
                return {"kind": "answer", "answer": {"value": observation["value"]}}

        captured = {}

        def investigator_spy(*args, **kwargs):
            captured.update(kwargs)
            return investigate(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            outcome = canary._run_arm(Path(directory) / "high", "high",
                                      model_factory=ScriptedModel,
                                      investigator_fn=investigator_spy)
            events = Journal.read(Path(directory) / "high" / "events.jsonl")
        self.assertEqual(outcome["result"]["status"], "checked")
        self.assertEqual(outcome["accounting"]["logical_model_attempts"], 4)
        self.assertEqual(outcome["accounting"]["actual_http_requests"], 4)
        self.assertEqual(outcome["accounting"]["input_token_total"], 44)
        self.assertEqual(outcome["accounting"]["output_token_total"], 28)
        self.assertEqual(len(outcome["accounting"]["attempts"]), 4)
        self.assertTrue(all(type(row["elapsed_seconds"]) is float
                            and row["elapsed_seconds"] >= 0
                            for row in outcome["accounting"]["attempts"]))
        self.assertEqual(captured["detailed_response_feedback"], False)
        self.assertEqual(outcome["toy_host_events"][-1]["status"], "accepted")
        response_receipts = [event for event in events if event["kind"] == "model_response"]
        self.assertEqual(len(response_receipts), 4)
        self.assertTrue(all(event["body"]["receipt"]["body"] == "synthetic test receipt"
                            for event in response_receipts))

    def test_toy_error_is_adapted_to_static_host_tool_input_error(self):
        class FakeOpener:
            def open(self, *_args, **_kwargs):
                return None

        class InvalidToolModel:
            def __init__(self, *args, **kwargs):
                self.identity = {"model": args[0]}
                self.max_tokens = kwargs["max_tokens"]
                self._opener = FakeOpener()
                self.last_usage = {"input_tokens": 1, "output_tokens": 1}
                self.last_response = {"status": 200, "body": "private mock"}

            def propose(self, _context):
                self._opener.open(None)
                return {"kind": "experiment", "tool": "lookup_item",
                        "input": {"key": "sample", "unexpected": True},
                        "predictions": [{"hypothesis": "toy lookup",
                                         "outcome": {"found": True}}]}

        with tempfile.TemporaryDirectory() as directory:
            result = canary._run_arm(Path(directory) / "high", "high",
                                     model_factory=InvalidToolModel)
            events = Journal.read(Path(directory) / "high" / "events.jsonl")
        tool_errors = [event["body"] for event in events if event["kind"] == "tool_error"]
        self.assertEqual(result["result"]["status"], "incomplete")
        self.assertTrue(tool_errors)
        self.assertTrue(all(error == {"type": "ToolInputError", "feedback": True}
                            for error in tool_errors))
        rejections = [event["body"]["observation"] for event in events if event["kind"] == "observation"]
        self.assertTrue(all(observation["feedback"] ==
                            "toy canary tool rejected its schema or call order"
                            for observation in rejections))
        self.assertEqual(result["accounting"]["logical_model_attempts"], MODEL_REQUESTS_PER_ARM)
        self.assertEqual(result["accounting"]["actual_http_requests"], MODEL_REQUESTS_PER_ARM)

    def test_request_counter_stops_before_over_budget_http_open(self):
        class FakeOpener:
            def __init__(self):
                self.opens = 0

            def open(self, *_args, **_kwargs):
                self.opens += 1
                return None

        delegate = FakeOpener()
        counter = RequestCountingOpener(delegate, 1)
        counter.begin_attempt()
        counter.open(None)
        with self.assertRaises(ActualRequestBudgetExceeded):
            counter.open(None)
        counter.end_attempt()
        self.assertEqual(counter.actual_requests, 1)
        self.assertEqual(delegate.opens, 1)

    def test_missing_provider_usage_stays_unknown_per_actual_request(self):
        events = [
            {"kind": "model_attempt", "body": {"number": 1}},
            {"kind": "model_response", "body": {"receipt": {"status": 200}, "usage": {}}},
        ]
        accounting = canary._account_attempts(events, [1], [0.025])
        self.assertEqual(accounting["actual_http_requests"], 1)
        self.assertEqual(accounting["unknown_input_usage_requests"], 1)
        self.assertEqual(accounting["unknown_output_usage_requests"], 1)
        self.assertIsNone(accounting["input_token_total"])
        self.assertIsNone(accounting["output_token_total"])
        self.assertEqual(accounting["attempts"][0]["elapsed_seconds"], 0.025)

        multiply_opened = canary._account_attempts(
            [{"kind": "model_attempt", "body": {"number": 1}},
             {"kind": "model_response", "body": {
                 "receipt": {"status": 200},
                 "usage": {"input_tokens": 12, "output_tokens": 3}}}],
            [2], [0.05])
        self.assertEqual(multiply_opened["known_input_token_subtotal"], 12)
        self.assertEqual(multiply_opened["unknown_input_usage_requests"], 1)
        self.assertEqual(multiply_opened["unknown_output_usage_requests"], 1)

    def test_missing_request_count_cannot_make_token_totals_look_complete(self):
        events = [
            {"kind": "model_attempt", "body": {"number": 1}},
            {"kind": "model_response", "body": {
                "receipt": {"status": 200},
                "usage": {"input_tokens": 0, "output_tokens": 0, "finish_reason": "stop"}}},
        ]
        accounting = canary._account_attempts(events, [], [0.01], actual_http_requests=1)
        self.assertEqual(accounting["known_http_request_subtotal"], 0)
        self.assertEqual(accounting["actual_http_requests"], 1)
        self.assertFalse(accounting["request_accounting_complete"])
        self.assertEqual(accounting["unknown_http_request_count_attempts"], 1)
        self.assertEqual(accounting["unmatched_http_request_count_rows"], 1)
        self.assertIsNone(accounting["unknown_input_usage_requests"])
        self.assertIsNone(accounting["unknown_output_usage_requests"])
        self.assertIsNone(accounting["input_token_total"])
        self.assertIsNone(accounting["output_token_total"])

    def test_attempt_report_has_safe_parser_schema_and_tool_categories(self):
        rows = [
            ("invalid", {"kind": "invalid", "raw_reply": ""},
             "stop", "rejected", None),
            ("invalid", {"kind": "invalid", "raw_reply": "   "},
             "length", "rejected", None),
            ("invalid", {"kind": "invalid", "raw_reply": "{private malformed"},
             "length", "rejected", None),
            ("invalid", {"kind": "invalid", "raw_reply": "private invalid JSON"},
             "stop", "rejected", None),
            ("schema", {"kind": "experiment", "tool": "lookup_item", "input": {"key": "sample"},
                        "predictions": []}, "stop", "rejected", None),
            ("accepted", {"kind": "experiment", "tool": "lookup_item", "input": {"key": "sample"},
                           "predictions": [{"hypothesis": "lookup", "outcome": {"found": True}}]},
             "stop", "accepted", {"observation": {"found": True, "value": "cobalt"}}),
            ("tool_rejected", {"kind": "experiment", "tool": "lookup_item", "input": {"key": "bad"},
                               "predictions": [{"hypothesis": "lookup", "outcome": {"found": False}}]},
             "stop", "accepted", {"observation": {"status": "tool_rejection",
                                                        "feedback": "private tool detail"}}),
        ]
        events = []
        for index, (label, action, finish, schema_status, observation) in enumerate(rows, 1):
            events.extend([
                {"kind": "model_attempt", "body": {"number": index}},
                {"kind": "model_response", "body": {"receipt": {"status": 200},
                    "usage": {"input_tokens": 1, "output_tokens": 1, "finish_reason": finish}}},
                {"kind": "model_reply", "body": {"action": action,
                    "usage": {"input_tokens": 1, "output_tokens": 1, "finish_reason": finish}}},
            ])
            if schema_status == "rejected":
                events.append({"kind": "action_rejected", "body": {"feedback": "safe generic rejection"}})
            if label in ("accepted", "tool_rejected"):
                events.append({"kind": "proposal", "body": action})
                events.append({"kind": "tool_attempt", "body": {"number": index, "kind": "experiment"}})
                if label == "tool_rejected":
                    events.append({"kind": "tool_error", "body": {"type": "ToolInputError", "feedback": True}})
                events.append({"kind": "observation", "body": {"action": action,
                    "observation": observation["observation"]}})
        accounting = canary._account_attempts(events, [1] * len(rows), [0.01] * len(rows),
                                               actual_http_requests=len(rows))
        attempts = accounting["attempts"]
        self.assertEqual([row["action_category"] for row in attempts[:5]], [
            "empty_final_stop", "empty_final_length", "nonempty_invalid_json_length",
            "nonempty_invalid_json_other", "schema_rejected_action"])
        self.assertEqual([row["action_kind"] for row in attempts[:5]], [
            "invalid", "invalid", "invalid", "invalid", "experiment"])
        self.assertEqual(attempts[4]["schema_rejection_category"], "experiment_predictions_required")
        self.assertEqual(attempts[5]["action_schema_status"], "accepted")
        self.assertEqual(attempts[5]["tool_call_status"], "accepted")
        self.assertEqual(attempts[6]["tool_call_status"], "rejected")
        self.assertEqual(accounting["schema_accepted_actions"], 2)
        self.assertEqual(accounting["schema_rejected_actions"], 5)
        self.assertEqual(accounting["accepted_tool_calls"], 1)
        self.assertEqual(accounting["rejected_tool_calls"], 1)
        projection = json.dumps(accounting)
        for private_text in ("private malformed", "private invalid JSON", "private tool detail", "cobalt"):
            self.assertNotIn(private_text, projection)

        malformed_kind_events = [
            {"kind": "model_attempt", "body": {"number": 1}},
            {"kind": "model_reply", "body": {"action": {"kind": []}, "usage": {}}},
            {"kind": "action_rejected", "body": {"feedback": "safe generic rejection"}},
        ]
        malformed_kind = canary._account_attempts(malformed_kind_events, [0], [0.0])
        self.assertEqual(malformed_kind["attempts"][0]["schema_rejection_category"],
                         "unsupported_action_kind")

    def test_hard_four_xx_error_is_terminal_without_model_retry(self):
        class HardFourXXOpener:
            def open(self, *_args, **_kwargs):
                raise HTTPError("https://invalid.test", 401, "Unauthorized", {}, BytesIO(b""))

        class HardFourXXModel:
            def __init__(self, *args, **kwargs):
                self.identity = {"model": args[0]}
                self.max_tokens = kwargs["max_tokens"]
                self._opener = HardFourXXOpener()
                self.last_usage = {}
                self.last_response = None

            def propose(self, _context):
                return self._opener.open(None)

        with tempfile.TemporaryDirectory() as directory:
            outcome = canary._run_arm(Path(directory) / "high", "high",
                                      model_factory=HardFourXXModel)
        self.assertEqual(outcome["result"]["status"], "incomplete")
        self.assertEqual(outcome["accounting"]["logical_model_attempts"], 1)
        self.assertEqual(outcome["accounting"]["actual_http_requests"], 1)
        self.assertEqual(outcome["accounting"]["hard_4xx_terminal_errors"], 1)
        self.assertIsInstance(outcome["accounting"]["attempts"][0]["elapsed_seconds"], float)


if __name__ == "__main__":
    unittest.main()
