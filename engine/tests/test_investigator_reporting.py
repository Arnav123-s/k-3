"""Evidence accounting and privacy checks for the investigator pilot reports."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.investigator_transfer.analyze import analyze
from experiments.investigator_transfer.export import (
    _journal_usage, _stage, export, project_event,
)
from kuttaka_runtime.store import Journal


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _report(status, passed, *, correct=None, total=None, tokens_known=True,
            input_tokens=None, output_tokens=None, reported_usage=None):
    score = {"passed": passed}
    if correct is not None:
        score["correct_cases"] = correct
    if total is not None:
        score["total_cases"] = total
    result = {"status": status, "model_tokens_known": tokens_known}
    if input_tokens is not None:
        result["model_input_tokens"] = input_tokens
    if output_tokens is not None:
        result["model_output_tokens"] = output_tokens
    report = {"result": result, "score": score}
    if reported_usage is not None:
        report["reported_usage"] = reported_usage
    return report


class InvestigatorReportingTests(unittest.TestCase):
    def test_response_length_failure_is_preserved_without_provider_content(self):
        raw = json.dumps({'model': 'provider-model', 'choices': [
            {'finish_reason': 'length', 'message': {'content': 'private response',
                                                  'reasoning_content': 'private reasoning'}}]})
        event = {'seq': 1, 'hash': 'test-hash', 'kind': 'model_response',
                 'body': {'receipt': {'status': 200, 'body': raw},
                          'usage': {'input_tokens': 20, 'output_tokens': 4096}}}
        projection = project_event(event)
        self.assertEqual(projection['body']['finish_reason'], 'length')
        self.assertEqual(projection['body']['provider_model'], 'provider-model')
        self.assertEqual(projection['body']['usage']['output_tokens'], 4096)
        self.assertNotIn('private response', json.dumps(projection))
        self.assertNotIn('private reasoning', json.dumps(projection))

    def test_stage_classifies_versioned_acquisition_and_named_target_runs(self):
        self.assertEqual(_stage(Path("substantive-v8/events.jsonl")), "acquisition")
        self.assertEqual(_stage(Path("substantive-v9/events.jsonl")), "acquisition")
        self.assertEqual(_stage(Path("substantive-v10/smoke-v10/events.jsonl")),
                         "scripted_fixture")
        self.assertEqual(_stage(Path("substantive-v10/smoke/events.jsonl")), "scripted_fixture")
        self.assertEqual(_stage(Path("substantive-v8-threshold-review/events.jsonl")),
                         "scripted_fixture")
        self.assertEqual(_stage(Path("campaign/source_target_string-811-policy/events.jsonl")),
                         "target_campaign")
        self.assertEqual(_stage(Path("campaign/target_mechanical-827-restored/events.jsonl")),
                         "target_campaign")

    def test_usage_is_complete_when_no_model_call_was_attempted(self):
        self.assertEqual(_journal_usage([]), {
            "model_attempts": 0, "model_responses": 0,
            "reported_usage": {"input_tokens": 0, "output_tokens": 0, "complete": True},
        })

        unmatched = _journal_usage([{"kind": "model_attempt", "body": {"number": 1}}])
        self.assertFalse(unmatched["reported_usage"]["complete"])

    def test_planned_denominators_retain_missing_incomplete_and_incorrect_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = [
                ["target_string", 1, "ordinary"], ["target_string", 1, "policy"],
                ["target_string", 1, "removed"], ["target_string", 1, "restored"],
                ["target_string", 2, "ordinary"], ["target_string", 2, "policy"],
                ["target_string", 2, "removed"], ["target_string", 2, "restored"],
            ]
            _write_json(root / "planned-runs.json", plan)
            reports = {
                "target_string-1-ordinary": _report("checked", True, correct=16, total=16),
                "target_string-1-policy": _report("checked", True, correct=16, total=16),
                "target_string-1-removed": _report(
                    "incorrect", False, correct=8, total=16, tokens_known=False,
                    input_tokens=100, output_tokens=200,
                    reported_usage={"input_tokens": 13},
                ),
                "target_string-1-restored": _report("checked", True, correct=16, total=16),
                # Planned ordinary run 2 deliberately has no result file.
                "target_string-2-policy": _report("incomplete", True, correct=15, total=16),
                "target_string-2-removed": _report("incorrect", False, correct=15, total=16),
                "target_string-2-restored": _report("incomplete", False, correct=15, total=16),
            }
            for name, report in reports.items():
                _write_json(root / name / "result.json", report)

            result = analyze(root)

        self.assertEqual(set(result["arms"]), {"ordinary", "policy", "removed", "restored"})
        self.assertEqual({arm: stats["planned"] for arm, stats in result["arms"].items()},
                         {"ordinary": 2, "policy": 2, "removed": 2, "restored": 2})
        self.assertEqual({arm: stats["passed"] for arm, stats in result["arms"].items()},
                         {"ordinary": 1, "policy": 1, "removed": 0, "restored": 1})
        rows = {(row["family"], row["seed"], row["arm"]): row for row in result["runs"]}
        self.assertEqual(rows[("target_string", 2, "ordinary")]["status"], "missing")
        self.assertFalse(rows[("target_string", 2, "ordinary")]["pass"])
        self.assertEqual(rows[("target_string", 2, "policy")]["status"], "incomplete")
        self.assertFalse(rows[("target_string", 2, "policy")]["pass"])
        self.assertFalse(rows[("target_string", 2, "removed")]["pass"])

        # Repeat conditions remain distinct arms and paired entries, never pooled.
        self.assertIn("removed", result["paired"][0]["passes"])
        self.assertIn("restored", result["paired"][0]["passes"])
        self.assertEqual(result["arms"]["removed"]["planned"], 2)
        self.assertEqual(result["arms"]["restored"]["planned"], 2)

    def test_failure_taxonomy_separates_provider_budget_scoring_and_process_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = [
                ["target_string", 1, "ordinary"],
                ["target_string", 1, "policy"],
                ["target_string", 1, "removed"],
                ["target_string", 1, "restored"],
                ["target_string", 2, "policy"],
                ["target_string", 2, "ordinary"],
                ["target_string", 2, "removed"],
                ["target_string", 2, "restored"],
            ]
            _write_json(root / "planned-runs.json", plan)
            _write_json(root / "target_string-1-ordinary" / "result.json", {
                "result": {"status": "incomplete", "reason": "model failure",
                           "model_tokens_known": False},
                "score": {"submitted": False},
            })
            with Journal(root / "target_string-1-ordinary" / "events.jsonl") as journal:
                journal.append("model_response", {"receipt": {"status": 500,
                    "body": "private raw response and reasoning"}, "usage": {}})
                journal.append("model_error", {"type": "HTTPError", "status": 500})

            _write_json(root / "target_string-1-policy" / "result.json", {
                "result": {"status": "incomplete", "reason": "model failure",
                           "model_tokens_known": False}, "score": {"submitted": False}})
            with Journal(root / "target_string-1-policy" / "events.jsonl") as journal:
                journal.append("model_error", {"type": "TimeoutError", "status": None})

            _write_json(root / "target_string-1-removed" / "result.json", {
                "result": {"status": "incomplete", "reason": "model budget exhausted",
                           "model_tokens_known": True}, "score": {"submitted": False}})
            _write_json(root / "target_string-1-restored" / "result.json", {
                "result": {"status": "failed", "reason": "answer rejected",
                           "model_tokens_known": True},
                "score": {"submitted": True, "status": "incorrect", "passed": False,
                          "correct_cases": 2, "total_cases": 10}})
            _write_json(root / "target_string-2-restored" / "result.json", {
                "result": {"status": "checked", "model_tokens_known": True},
                "score": {"submitted": True, "passed": True,
                          "correct_cases": 10, "total_cases": 10}})

            _write_json(root / "target_string-2-policy-invocation.json", {"timeout": True})
            _write_json(root / "target_string-2-ordinary-invocation.json", {"exit_code": 2})
            # target_string-2-removed deliberately has neither result nor invocation.
            result = analyze(root)

        rows = {(row["family"], row["seed"], row["arm"]): row for row in result["runs"]}
        self.assertEqual(result["arms"]["ordinary"]["planned"], 2)
        self.assertEqual(len(result["paired"]), 2)
        self.assertEqual(result["arms"]["restored"]["passed"], 1)
        self.assertEqual(rows[("target_string", 2, "restored")]["failure_category"], "passed")
        self.assertFalse(rows[("target_string", 2, "removed")]["event_metadata_available"])

        self.assertEqual(rows[("target_string", 1, "ordinary")]["model_errors"], [{
            "type": "HTTPError", "status": 500}])
        self.assertEqual(rows[("target_string", 1, "ordinary")]["failure_category"],
                         "provider_http_error")
        self.assertEqual(rows[("target_string", 1, "policy")]["failure_category"],
                         "request_timeout_or_transport_error")
        self.assertEqual(rows[("target_string", 1, "removed")]["failure_category"],
                         "budget_exhausted")
        self.assertEqual(rows[("target_string", 1, "restored")]["failure_category"],
                         "scored_incorrect")
        self.assertEqual(rows[("target_string", 2, "policy")]["failure_category"],
                         "process_timeout_no_result")
        self.assertEqual(rows[("target_string", 2, "ordinary")]["failure_category"],
                         "process_exit_no_result")
        self.assertEqual(rows[("target_string", 2, "removed")]["failure_category"],
                         "missing_or_unfinished_no_result")
        self.assertEqual(result["failure_taxonomy"]["planned"], {
            "provider_http_error": 1, "request_timeout_or_transport_error": 1,
            "budget_exhausted": 1, "scored_incorrect": 1,
            "process_timeout_no_result": 1, "process_exit_no_result": 1,
            "missing_or_unfinished_no_result": 1, "passed": 1})
        self.assertNotIn("private raw response", json.dumps(result))

    def test_transportfailure_kinds_are_classified_without_raw_provider_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = [["target_mechanical", seed, "ordinary"] for seed in (1, 2, 3, 4)]
            _write_json(root / "planned-runs.json", plan)
            errors = [
                {"type": "TransportFailure", "kind": "request_timeout", "status": None},
                {"type": "TransportFailure", "kind": "stream_error", "status": 200},
                {"type": "TransportFailure", "kind": "provider_http_error", "status": 400},
                {"type": "TransportFailure", "kind": "provider_http_error", "status": 500},
            ]
            for seed, error in enumerate(errors, 1):
                name = f"target_mechanical-{seed}-ordinary"
                _write_json(root / name / "result.json", {
                    "result": {"status": "incomplete", "reason": "model failure",
                               "model_tokens_known": False},
                    "score": {"submitted": False},
                })
                with Journal(root / name / "events.jsonl") as journal:
                    journal.append("model_response", {"receipt": {
                        "status": error["status"],
                        "body": "private raw provider body sentinel",
                    }, "usage": {"input_tokens": None, "output_tokens": None}})
                    journal.append("model_error", error)
            result = analyze(root)

        rows = {(row["family"], row["seed"], row["arm"]): row for row in result["runs"]}
        self.assertEqual(result["arms"]["ordinary"]["planned"], 4)
        self.assertEqual(len(result["paired"]), 4)
        self.assertEqual(rows[("target_mechanical", 1, "ordinary")]["failure_category"],
                         "request_timeout_or_transport_error")
        self.assertEqual(rows[("target_mechanical", 2, "ordinary")]["failure_category"],
                         "request_timeout_or_transport_error")
        self.assertEqual(rows[("target_mechanical", 3, "ordinary")]["failure_category"],
                         "provider_http_error")
        self.assertEqual(rows[("target_mechanical", 4, "ordinary")]["failure_category"],
                         "provider_http_error")
        self.assertEqual(result["failure_taxonomy"]["planned"], {
            "request_timeout_or_transport_error": 2, "provider_http_error": 2})
        self.assertNotIn("private raw provider body sentinel", json.dumps(result))
        self.assertEqual(rows[("target_mechanical", 2, "ordinary")]["model_errors"], [{
            "type": "TransportFailure", "kind": "stream_error", "status": 200}])

    def test_present_corrupt_event_journal_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(root / "planned-runs.json", [["target_string", 1, "ordinary"]])
            _write_json(root / "target_string-1-ordinary" / "result.json", {
                "result": {"status": "incomplete", "reason": "unfinished"},
                "score": {"submitted": False}})
            event_path = root / "target_string-1-ordinary" / "events.jsonl"
            event_path.write_text('{\"seq\":0,\"kind\":\"model_error\",\"body\":{},'
                                  '\"prev\":\"\",\"hash\":\"corrupt\"}\n')
            with self.assertRaisesRegex(ValueError, "journal integrity failure"):
                analyze(root)

    def test_secondary_journal_metadata_is_counted_without_publishing_response_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(root / "planned-runs.json", [["target_string", 3, "policy"]])
            _write_json(root / "target_string-3-policy" / "result.json", {
                "result": {"status": "incomplete", "policy_executions": 7,
                           "model_tokens_known": False},
                "score": {"submitted": False},
                "memory_stats": {"artifacts": 2, "events": 9,
                                 "journal_bytes": 1234, "private": "do not export"},
            })
            with Journal(root / "target_string-3-policy" / "events.jsonl") as journal:
                journal.append("representation_revision", {"before": {}, "after": {}})
                journal.append("model_response", {"receipt": {
                    "status": 200,
                    "body": json.dumps({"model": "provider-model", "choices": [{
                        "finish_reason": "length",
                        "message": {"content": "private raw body sentinel"},
                    }]}),
                }, "usage": {}})
                journal.append("model_response", {"receipt": {
                    "status": 429, "body": "private error body sentinel",
                }, "usage": {}})
            result = analyze(root)

        row = result["runs"][0]
        self.assertEqual(row["policy_executions"], 7)
        self.assertEqual(row["representation_revisions"], 1)
        self.assertEqual(row["memory_stats"], {"artifacts": 2, "events": 9,
                                               "journal_bytes": 1234})
        self.assertEqual(row["length_finish_responses"], 1)
        self.assertEqual(row["response_http_status_counts"], {"200": 1, "429": 1})
        self.assertNotIn("private raw body sentinel", json.dumps(result))
        self.assertNotIn("private error body sentinel", json.dumps(result))
        self.assertNotIn("private", json.dumps(row["memory_stats"]))

    def test_absent_journal_marks_secondary_event_counts_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(root / "planned-runs.json", [["target_string", 4, "ordinary"]])
            _write_json(root / "target_string-4-ordinary" / "result.json", {
                "result": {"status": "checked", "policy_executions": 0},
                "score": {"passed": True},
            })
            row = analyze(root)["runs"][0]

        self.assertFalse(row["event_metadata_available"])
        self.assertIsNone(row["representation_revisions"])
        self.assertIsNone(row["length_finish_responses"])
        self.assertIsNone(row["response_http_status_counts"])

    def test_unknown_usage_is_retained_alongside_partial_reported_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(root / "planned-runs.json", [["target_mechanical", 4, "removed"]])
            _write_json(root / "target_mechanical-4-removed" / "result.json", _report(
                "incorrect", False, correct=7, total=24, tokens_known=False,
                input_tokens=100, output_tokens=200,
                reported_usage={"input_tokens": 13},
            ))
            result = analyze(root)

        arm = result["arms"]["removed"]
        row = result["runs"][0]
        self.assertFalse(row["tokens_known"])
        self.assertEqual(arm["usage_unknown_runs"], 1)
        self.assertEqual(arm["reported_input_tokens"], 13)
        # Missing reported output usage falls back to the result's reported total.
        self.assertEqual(arm["reported_output_tokens"], 200)
        self.assertEqual((row["score"]["correct_cases"], row["score"]["total_cases"]), (7, 24))

    def test_qualification_failures_and_additional_runs_are_kept_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(root / "planned-runs.json", [
                ["source_affine", 907, "qualification"],
                ["target_string", 1, "ordinary"],
            ])
            _write_json(root / "source_affine-907-qualification" / "result.json",
                        {"result": {"status": "incorrect", "model_tokens_known": False},
                         "score": {"passed": False}})
            _write_json(root / "target_string-2-policy" / "result.json",
                        {"result": {"status": "timeout", "model_tokens_known": False}})
            result = analyze(root)

        self.assertEqual(result["source_qualification"]["planned"], 1)
        self.assertEqual(result["source_qualification"]["passed"], 0)
        self.assertEqual(result["source_qualification"]["usage_unknown_runs"], 1)
        self.assertEqual(len(result["additional_runs"]), 1)
        self.assertEqual(result["additional_runs"][0]["status"], "timeout")
        self.assertFalse(result["additional_runs"][0]["planned"])
        # Additional recovery outcomes do not replace or alter planned pairs.
        self.assertEqual(len(result["paired"]), 1)

    def test_event_projection_hashes_raw_provider_body_and_drops_private_events(self):
        raw = '{"choices":[{"message":{"content":"private response text"}}]}'
        event = {
            "seq": 12,
            "hash": "journal-event-hash",
            "kind": "model_response",
            "body": {
                "usage": {"input_tokens": 31, "output_tokens": 9},
                "receipt": {"status": 200, "body": raw},
            },
        }

        projected = project_event(event)

        self.assertEqual(projected["source_seq"], 12)
        self.assertEqual(projected["source_event_hash"], "journal-event-hash")
        self.assertEqual(projected["body"]["response_body_sha256"], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(projected["body"]["response_body_bytes"], len(raw.encode()))
        self.assertEqual(projected["body"]["usage"], {"input_tokens": 31, "output_tokens": 9})
        self.assertNotIn(raw, json.dumps(projected))

        for kind in ("model_context", "model_attempt", "policy_attempt"):
            with self.subTest(kind=kind):
                private_event = {"seq": 13, "hash": "private-hash", "kind": kind,
                                 "body": {"private_context": "do not export"}}
                self.assertIsNone(project_event(private_event))

    def test_model_reply_and_full_scoring_payload_are_not_projected(self):
        reply = {"rationale": "private provider reasoning", "source": "return hidden_expected"}
        event = {"seq": 2, "hash": "reply-hash", "kind": "model_reply", "body": reply}
        projected = project_event(event)
        encoded = json.dumps(projected)
        self.assertNotIn("private provider reasoning", encoded)
        self.assertNotIn("hidden_expected", encoded)
        self.assertIn(hashlib.sha256(json.dumps(reply, sort_keys=True,
                         separators=(",", ":")).encode()).hexdigest(), encoded)

        scored = {"seq": 3, "hash": "finish-hash", "kind": "experiment_finish",
                  "body": {"family": "target", "seed": 1, "arm": "policy",
                           "score": {"passed": False, "case_results": [True, False],
                                     "expected_outputs": ["secret"]},
                           "result": {"status": "checked", "answer": {"source": "x"}}}}
        score_projection = json.dumps(project_event(scored))
        self.assertNotIn("expected_outputs", score_projection)
        self.assertNotIn("secret", score_projection)
        self.assertNotIn('"answer"', score_projection)

    def test_export_keeps_attempt_chains_recovery_and_scripted_attribution(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            private, output = base / "private", base / "projection"
            for name, kind in (("acquisition-v1", "finish"),
                               ("acquisition-v7", "finish"),
                               ("alternative-v1", "finish"),
                               ("source-diagnostic-v7/source_affine-907-qualification", "investigator_finish"),
                               ("smoke-v7", "investigator_finish")):
                folder = private / name
                folder.mkdir(parents=True)
                with Journal(folder / "events.jsonl") as journal:
                    journal.append(kind, {"status": "checked"})
                if not name.startswith("smoke-"):
                    _write_json(folder / "result.json", {"status": "checked",
                        "score": {"case_results": [True], "expected_outputs": ["hidden"]}})
            alt = private / "alternative-v1"
            _write_json(alt / "host-failure.json", {"error": "TypeError", "model_call_consumed": 1})
            _write_json(alt / "recovered-design.json", {"rationale": "private", "schema": "safe"})
            _write_json(private / "source-diagnostic-v7" / "planned-runs.json",
                        [["source_affine", 907, "qualification"]])
            _write_json(private / "candidate-review.json", {"approved": False, "findings": []})
            with patch.dict(os.environ, {"NVIDIA_API_KEY": "test-credential-value"}):
                export(private, output)
            projection = json.loads((output / "results.json").read_text())
            records = projection["records"]
            self.assertEqual(set(projection["stage_totals"]), {
                "acquisition", "source_qualification", "scripted_fixture"})
            self.assertEqual(projection["stage_totals"]["acquisition"]["journal_runs"], 3)
            records_json = json.dumps(records)
            self.assertNotIn("case_results", records_json)
            self.assertNotIn("hidden", records_json)
            names = {record["run"] for record in records}
            self.assertTrue({"acquisition-v1", "acquisition-v7", "alternative-v1",
                             "source-diagnostic-v7/source_affine-907-qualification",
                             "smoke-v7"}.issubset(names))
            for record in records:
                if "journal_sha256" in record:
                    self.assertTrue(record["journal_chain_verified"])
            self.assertTrue((output / "alternative-v1" / "host-failure.json").exists())
            self.assertTrue((output / "candidate-review.json").exists())
            self.assertNotIn("rationale", json.loads(
                (output / "alternative-v1" / "recovered-design.json").read_text()))
            self.assertEqual(json.loads((output / "smoke-v7" / "result.json").read_text())[
                "attribution"], "Scripted model protocol fixture, actual Docker; not live model evidence.")
            for path in output.rglob("*"):
                if path.is_file():
                    self.assertNotIn("test-credential-value", path.read_text())

    def test_individual_file_attempts_are_hash_projected_without_raw_provider_data(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            private, output = base / "private", base / "projection"
            root = private / "substantive-v8"
            root.mkdir(parents=True)
            _write_json(root / "profile.json", {"model": "test-model"})
            _write_json(root / "request.json", {"context": {"instructions": "raw private request"}})
            _write_json(root / "response.json", {"reasoning": "raw provider reasoning", "candidate": "safe"})
            _write_json(root / "receipt.json", {"status": 200, "body": "raw provider body"})
            _write_json(root / "usage.json", {"input_tokens": 17, "output_tokens": 8})
            _write_json(root / "result.json", {"status": "candidate"})
            _write_json(root / "evidence-summary.json", {"promotion": "held"})
            _write_json(root / "blocked-attempt.json", {"status": "blocked"})
            _write_json(root / "candidate.json", {"name": "candidate", "source": "def run(x): return x"})
            (root / "candidate.py").write_text("def run(x): return x\n")

            first = root / "attempt-01"
            _write_json(first / "profile.json", {"model": "test-model"})
            _write_json(first / "request.json", {"context": "interrupted request"})
            # A journaled directory is emitted only through the verified journal path.
            journaled = root / "attempt-02"
            _write_json(journaled / "profile.json", {"model": "test-model"})
            _write_json(journaled / "request.json", {"context": "journal-backed"})
            with Journal(journaled / "events.jsonl") as journal:
                journal.append("model_attempt", {"number": 1})

            design = root / "design-01"
            _write_json(design / "profile.json", {"model": "test-model"})
            _write_json(design / "request.json", {"context": "design request"})
            _write_json(design / "failure.json", {"type": "TimeoutError"})
            _write_json(root / "fixture-results" / "fixture.json", {
                "fixture": "scripted", "context": {"model_reply": {"kind": "answer"}},
                "receipt": {"exit_code": 0}, "decoded": {"action": {"kind": "answer"}},
            })

            export(private, output)
            report = json.loads((output / "results.json").read_text())
            individual = [row for row in report["records"]
                          if row.get("evidence_format") == "individual_file_hashes"]
            by_run = {row["run"]: row for row in individual}
            self.assertEqual(set(by_run), {"substantive-v8", "substantive-v8/attempt-01",
                                           "substantive-v8/design-01"})
            self.assertFalse(by_run["substantive-v8"]["journal_chain_verified"])
            self.assertTrue(by_run["substantive-v8"]["reported_usage"]["complete"])
            self.assertEqual(by_run["substantive-v8"]["file_hashes"]["request.json"]["sha256"],
                             hashlib.sha256((root / "request.json").read_bytes()).hexdigest())
            self.assertIn("response.json", by_run["substantive-v8"]["file_hashes"])
            self.assertIn("receipt.json", by_run["substantive-v8"]["file_hashes"])
            self.assertFalse((output / "substantive-v8" / "request.json").exists())
            self.assertFalse((output / "substantive-v8" / "response.json").exists())
            self.assertFalse((output / "substantive-v8" / "receipt.json").exists())
            self.assertEqual(by_run["substantive-v8/attempt-01"]["model_responses"], 0)
            self.assertFalse(by_run["substantive-v8/attempt-01"]["reported_usage"]["complete"])
            self.assertTrue((output / "substantive-v8" / "candidate.py").exists())
            self.assertTrue((output / "substantive-v8" / "design-01" / "failure.json").exists())
            self.assertTrue((output / "substantive-v8" / "fixture-results" / "fixture.json").exists())
            fixture_record = next(row for row in report["records"]
                                  if row.get("run") == "substantive-v8/fixture-results/fixture")
            self.assertEqual(fixture_record["stage"], "scripted_fixture")
            self.assertFalse(fixture_record["live_model"])
            projection_text = "\n".join(path.read_text() for path in output.rglob("*.json")
                                         if path.name != "request.json")
            self.assertNotIn("raw provider reasoning", projection_text)
            self.assertNotIn("raw provider body", projection_text)
            self.assertNotIn("raw private request", projection_text)


if __name__ == "__main__":
    unittest.main()
