import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiments.astra_constructor_live import report
from kuttaka_runtime.artifacts import ArtifactMemory


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _journal(path, rows):
    previous = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        for seq, (kind, body) in enumerate(rows):
            payload = {"seq": seq, "kind": kind, "body": body, "prev": previous}
            digest = hashlib.sha256(_canon(payload)).hexdigest()
            stream.write(_canon({**payload, "hash": digest}) + b"\n")
            previous = digest
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_journal(path, rows):
    """Write ArtifactMemory-compatible UTF-8 canonical journal rows."""
    previous = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        for seq, (kind, body) in enumerate(rows):
            payload = {"seq": seq, "kind": kind, "body": body, "prev": previous}
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False).encode("utf-8")
            digest = hashlib.sha256(canonical).hexdigest()
            event = {**payload, "hash": digest}
            encoded = json.dumps(event, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, allow_nan=False).encode("utf-8")
            stream.write(encoded + b"\n")
            previous = digest
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReportingTests(unittest.TestCase):
    @staticmethod
    def _fit_action(order=2, degree=1):
        return {"kind": "experiment", "tool": "fit_relation",
                "input": {"order": order, "degree": degree},
                "predictions": [{"hypothesis": "bounded test", "outcome": {"ok": True}}]}

    def test_constructor_evidence_distinguishes_proposal_rejection_and_finite_receipt(self):
        proposed_only = report._constructor_evidence([
            {"kind": "proposal", "body": self._fit_action()}])
        self.assertEqual(proposed_only["fit_relation_proposed_not_called"], 1)
        self.assertEqual(proposed_only["fit_relation_attempts"], 0)
        self.assertEqual(proposed_only["derivation_receipt_count"], 0)

        rejected = report._constructor_evidence([
            {"kind": "proposal", "body": self._fit_action(order=5)},
            {"kind": "tool_attempt", "body": {"number": 1, "kind": "experiment"}},
            {"kind": "tool_error", "body": {"type": "ToolInputError"}},
            {"kind": "observation", "body": {"observation": {"status": "tool_rejection"}}},
        ])
        self.assertEqual(rejected["schema_valid_tool_attempts_by_name"], {"fit_relation": 1})
        self.assertEqual(rejected["fit_relation_attempts"], 1)
        self.assertEqual(rejected["fit_relation_rejections"], 1)
        self.assertEqual(rejected["fit_relation_attempt_metadata"], [
            {"order": None, "degree": 1, "sample_count": None, "outcome": "tool_rejected"}])
        self.assertEqual(rejected["derivation_receipt_count"], 0)

        completed = report._constructor_evidence([
            {"kind": "proposal", "body": self._fit_action()},
            {"kind": "tool_attempt", "body": {"number": 1, "kind": "experiment"}},
            {"kind": "derivation_receipt", "hash": "a" * 64, "body": {
                "kind": "exact_polynomial_shift_fit", "order": 2, "degree": 1,
                "sample_count": 12, "status": "unique",
                "training_values": ["private-training-value"],
                "certificate": {"coefficients": ["private-coefficient"]}}},
            {"kind": "observation", "body": {"observation": {"status": "unique"}}},
        ])
        self.assertEqual(completed["fit_relation_attempt_metadata"], [
            {"order": 2, "degree": 1, "sample_count": 12, "outcome": "finite_fit_receipt"}])
        self.assertEqual(completed["derivation_receipt_statuses"], {"unique": 1})
        self.assertEqual(completed["derivation_receipts"][0]["receipt_id"], "a" * 64)
        serialized = json.dumps(completed)
        self.assertNotIn("training_values", serialized)
        self.assertNotIn("private-training-value", serialized)
        self.assertNotIn("private-coefficient", serialized)

    def test_constructor_tool_attempts_are_allowlisted_schema_valid_and_missing_is_unknown(self):
        events = [
            {"kind": "proposal", "body": self._fit_action(order=True)},
            {"kind": "tool_attempt", "body": {"number": 1, "kind": "experiment"}},
            {"kind": "proposal", "body": {"kind": "experiment", "tool": "unknown_tool",
                "input": {}, "predictions": [{"hypothesis": "x", "outcome": 1}]}},
        ]
        evidence = report._constructor_evidence(events)
        self.assertEqual(evidence["schema_valid_tool_attempts_by_name"], {"fit_relation": 1})
        self.assertEqual(evidence["fit_relation_attempts"], 1)
        self.assertIsNone(evidence["fit_relation_attempt_metadata"][0]["order"])
        self.assertEqual(report._constructor_evidence(None)["fit_relation_attempts"], None)
        self.assertIsNone(report._constructor_evidence([{"kind": "proposal"}])[
            "schema_valid_tool_attempts_by_name"])

    def test_final_artifact_provenance_is_separate_from_executable_dependencies(self):
        self.assertEqual(report._artifact_source_summary(Path("/does/not/exist"), None)[
            "fit_receipt_count"], None)
        self.assertEqual(report._artifact_source_summary(Path("/does/not/exist"), None)[
            "provenance_status"], "unknown_no_final_artifact")
        with tempfile.TemporaryDirectory() as directory:
            memory = Path(directory)
            receipt_id, dependency_id = "a" * 64, "b" * 64
            payload = {"kind": "method", "body": {"source": "def run(x): return x"},
                       "dependencies": [dependency_id], "provenance": {
                           "derivation": {"fit_relation_receipts": [receipt_id]}}}
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()
            artifact_id = hashlib.sha256(raw).hexdigest()
            blob = memory / "blobs" / f"{artifact_id}.json.zlib"
            blob.parent.mkdir(parents=True)
            blob.write_bytes(report.zlib.compress(raw))
            summary = report._artifact_source_summary(memory, artifact_id)
        self.assertEqual(summary["dependencies"], [dependency_id])
        self.assertEqual(summary["recorded_provenance_fit_relation_receipts"], [receipt_id])
        self.assertIsNone(summary["fit_receipt_ids"])
        self.assertEqual(summary["provenance_status"], "valid")
        linked = report._link_fit_receipts(summary, {
            "status": "available", "derivation_receipts": [
                {"receipt_id": receipt_id, "status": "unique"}]})
        self.assertEqual(linked["fit_receipt_ids"], [receipt_id])
        self.assertEqual(linked["fit_receipt_count"], 1)

        probe_receipt = "c" * 64
        payload["provenance"]["derivation"]["fit_relation_receipts"] = [receipt_id, probe_receipt]
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode()
        artifact_id = hashlib.sha256(raw).hexdigest()
        (memory / "blobs").mkdir(parents=True, exist_ok=True)
        (memory / "blobs" / f"{artifact_id}.json.zlib").write_bytes(report.zlib.compress(raw))
        with_probe = report._artifact_source_summary(memory, artifact_id)
        linked_probe = report._link_fit_receipts(with_probe, {
            "status": "available", "derivation_receipts": [
                {"receipt_id": receipt_id, "status": "unique"}]})
        self.assertEqual(linked_probe["recorded_provenance_fit_relation_receipts"],
                         [receipt_id, probe_receipt])
        self.assertEqual(linked_probe["fit_receipt_ids"], [receipt_id])
        self.assertEqual(linked_probe["fit_receipt_count"], 1)

    def test_stored_method_candidates_are_unique_verified_puts_and_do_not_imply_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "acquisition" / "seed-1171-with_constructor"
            _journal(run_dir / "events.jsonl", [])
            memory_path = run_dir / "memory"
            payload = {"kind": "method", "body": {"language": "python", "name": "m",
                       "description": "safe", "source": "def run(x): return x"},
                       "dependencies": [], "provenance": {}, "applicability": {}}
            representation = {"kind": "representation", "body": {}, "dependencies": [],
                              "provenance": {}, "applicability": {}}
            memory = ArtifactMemory(memory_path)
            method_id = memory.put(payload)
            memory.put(payload)  # repeated put must remain one unique stored method
            memory.put(representation)
            memory.close()
            memory_hash = hashlib.sha256((memory_path / "events.jsonl").read_bytes()).hexdigest()
            record = {"status": "completed", "journal_sha256": hashlib.sha256(b"").hexdigest(),
                      "memory_events_sha256": memory_hash, "submitted_artifact_id": None,
                      "result": {"status": "incomplete"}}
            (run_dir / "result.json").write_bytes(_canon(record))
            loaded = report._load_acquisition(root, 1171, "WITH_CONSTRUCTOR")
        self.assertEqual(loaded["run_metrics"]["stored_method_artifact_count"], 1)
        self.assertEqual(loaded["run_metrics"]["stored_method_artifact_evidence_status"], "verified")
        self.assertIsNone(loaded["run_metrics"]["submitted_artifact_was_remembered"])
        self.assertIsNone(loaded["submitted_artifact_id"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "acquisition" / "seed-1171-with_constructor"
            _journal(run_dir / "events.jsonl", [])
            memory_path = run_dir / "memory"
            memory = ArtifactMemory(memory_path)
            missing_id = memory.put(payload)
            memory.close()
            digest = hashlib.sha256((memory_path / "events.jsonl").read_bytes()).hexdigest()
            (memory_path / "blobs" / f"{missing_id}.json.zlib").unlink()
            record = {"status": "completed", "journal_sha256": hashlib.sha256(b"").hexdigest(),
                      "memory_events_sha256": digest, "submitted_artifact_id": missing_id,
                      "result": {"status": "incomplete"}}
            (run_dir / "result.json").write_bytes(_canon(record))
            loaded = report._load_acquisition(root, 1171, "WITH_CONSTRUCTOR")
        self.assertIsNone(loaded["run_metrics"]["stored_method_artifact_count"])
        self.assertEqual(loaded["run_metrics"]["stored_method_artifact_evidence_status"], "incomplete")
        self.assertIsNone(loaded["run_metrics"]["submitted_artifact_was_remembered"])

    def test_journal_hash_chain_is_strict_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            _journal(path, [("model_attempt", {}),
                            ("live_observation_release", {"matches": False})])
            events = report.read_journal(path)
            self.assertEqual(report._forecast_summary(events)["incorrect"], 1)

            path.write_bytes(path.read_bytes().replace(b'"matches":false', b'"matches":true'))
            with self.assertRaisesRegex(report.ReportError, "hash mismatch"):
                report.read_journal(path)

    def test_partial_usage_keeps_known_subtotals_and_unknowns_explicit(self):
        events = [
            {"kind": "model_response", "body": {"usage": {"input_tokens": 12, "output_tokens": 4}}},
            {"kind": "model_response", "body": {"usage": None}},
        ]
        usage = report._usage_projection(events, {
            "logical_model_attempts": 2, "provider_request_attempts": 2,
            "unknown_input_usage_requests": 1, "unknown_output_usage_requests": 1,
            "model_input_tokens": None, "model_output_tokens": None,
            "unknown_usage_requests": 1,
        })
        self.assertEqual(usage["reported_input_token_subtotal"], 12)
        self.assertEqual(usage["reported_input_rows"], 1)
        self.assertEqual(usage["unknown_input_usage_requests"], 1)
        self.assertIsNone(usage["recorded_input_token_total"])
        self.assertEqual(usage["reported_output_token_subtotal"], 4)
        self.assertIsNone(report._usage_projection([], {})["logical_model_attempts"])

        runner_shape = report._usage_projection([], {
            "model_input_tokens": 12, "model_output_tokens": 0,
            "unknown_input_usage_requests": 1, "unknown_output_usage_requests": 2,
            "unknown_usage_requests": 2,
        })
        self.assertEqual(runner_shape["recorded_known_input_token_subtotal"], 12)
        self.assertEqual(runner_shape["recorded_known_output_token_subtotal"], 0)
        self.assertIsNone(runner_shape["recorded_input_token_total"])
        self.assertIsNone(runner_shape["recorded_output_token_total"])

        complete_runner_shape = report._usage_projection([], {
            "model_input_tokens": 0, "model_output_tokens": 0,
            "unknown_input_usage_requests": 0, "unknown_output_usage_requests": 0,
            "unknown_usage_requests": 0,
        })
        self.assertEqual(complete_runner_shape["recorded_input_token_total"], 0)
        self.assertEqual(complete_runner_shape["recorded_output_token_total"], 0)

    def test_failure_projection_separates_empty_stream_truncation_and_rejection(self):
        events = [
            {"kind": "model_response", "body": {"usage": {"finish_reason": "length"}}},
            {"kind": "model_error", "body": {"type": "TransportFailure", "kind": "empty_stream"}},
            {"kind": "action_rejected", "body": {"status": "protocol_rejection", "feedback": "private"}},
            {"kind": "model_reply", "body": {"action": {"kind": "invalid", "raw_reply": ""}}},
            {"kind": "model_error", "body": {"type": "ContextBudgetRefusal"}},
            {"kind": "model_error", "body": {"type": "HTTPError", "status": 503}},
            {"kind": "model_error", "body": {"type": "ResponseParseError", "status": 200,
                                                   "raw": "private provider text"}},
            {"kind": "model_response", "body": {"usage": {"finish_reason": "provider-secret"}}},
            {"kind": "model_response", "body": {"usage": {"input_tokens": 3}}},
            {"kind": "model_response", "body": {"usage": {"finish_reason": None}}},
            {"kind": "model_error", "body": {"type": "TransportFailure", "kind": "stream_error",
                                                  "status": 200}},
        ]
        result = report._failure_categories(events)
        self.assertEqual(result["empty_stream_transport_failures"], 1)
        self.assertEqual(result["empty_final_contents"], 1)
        self.assertEqual(result["truncated_responses_finish_reason_length"], 1)
        self.assertEqual(result["protocol_rejections"], 1)
        self.assertEqual(result["action_rejections"], 1)
        self.assertEqual(result["provider_failure_events"], 1)
        self.assertEqual(result["transport_failure_events"], 2)
        self.assertEqual(result["transport_failure_types"], {"empty_stream": 1, "stream_error": 1})
        self.assertEqual(result["host_context_refusals"], 1)
        self.assertEqual(result["adapter_failure_events"], 1)
        self.assertEqual(result["model_http_statuses"], {"503": 1})
        self.assertEqual(result["responses_with_unknown_finish_reason"], 2)
        self.assertIn("OtherModelError", result["model_error_types"])
        self.assertIn("other", result["response_finish_reasons"])
        self.assertNotIn("provider-secret", json.dumps(result))
        self.assertNotIn("private", json.dumps(result))

    def test_http_errors_and_success_status_stream_failures_are_distinct(self):
        result = report._failure_categories([
            {"kind": "model_error", "body": {"type": "TransportFailure",
                                                    "kind": "provider_http_error", "status": 500}},
            {"kind": "model_error", "body": {"type": "TransportFailure",
                                                    "kind": "incomplete_stream", "status": 200}},
            {"kind": "model_error", "body": {"type": "TimeoutError", "kind": "request_timeout"}},
        ])
        self.assertEqual(result["provider_failure_events"], 1)
        self.assertEqual(result["provider_failure_types"], {"TransportFailure": 1})
        self.assertEqual(result["model_http_statuses"], {"500": 1})
        self.assertEqual(result["transport_failure_events"], 2)
        self.assertEqual(result["transport_failure_types"], {
            "incomplete_stream": 1, "request_timeout": 1})

    def test_missing_run_roots_are_explicit_and_never_vacuously_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = report.build_report(root / "absent-run", root / "source", verify_source=False)
        self.assertEqual(result["completeness"]["expected_acquisitions"], 6)
        self.assertEqual(result["completeness"]["acquisitions_present"], 0)
        self.assertEqual(result["completeness"]["reuse_results_present"], 0)
        self.assertFalse(result["completeness"]["all_six_reuse_results_exact_id_bound"])
        for arm in report.ARMS:
            unknown = result["descriptive_counts_by_arm_and_fixture_type"][arm]["unknown"]
            self.assertEqual(unknown["runs_without_forecast_journal"], 3)
            self.assertIsNone(unknown["known_run_release_subtotal"])
            self.assertIsNone(unknown["known_run_correct_subtotal"])
        self.assertTrue(all(row["status"] == "missing" for pair in result["pairs"]
                            for row in (pair["WITH_CONSTRUCTOR"], pair["MODEL_ONLY"])))

    def test_reuse_checks_preserve_missing_unknown_and_mismatch_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reuse_dir = root / "reuse" / "seed-1171-model_only"
            reuse_dir.mkdir(parents=True)
            six_values = [
                {"mathematically_equal": True, "canonical_format_match": True},
                {"mathematically_equal": False, "canonical_format_match": False},
                {"mathematically_equal": None, "canonical_format_match": None},
            ]
            record = {"artifact_id": "a" * 64, "status": "completed", "attempted": True,
                      "check_scores": [
                          {"checker_status": "completed", "expected_count": 6, "actual_count": 6,
                           "missing_count": 0, "extra_count": 0, "complete": True, "values": six_values},
                          {"checker_status": "missing", "expected_count": 6, "actual_count": 4,
                           "missing_count": 2, "extra_count": 0, "complete": False, "values": six_values[:2]},
                      ]}
            (reuse_dir / "result.json").write_bytes(_canon(record))
            digest = _journal(reuse_dir / "cli-output" / "events.jsonl", [])
            record["reuse_journal_sha256"] = digest
            (reuse_dir / "result.json").write_bytes(_canon(record))
            acq = {"submitted_artifact_id": "b" * 64,
                   "artifact_source": {"source_sha256": "c" * 64}}
            result = report._load_reuse(root, 1171, "MODEL_ONLY", acq)
        self.assertFalse(result["same_exact_artifact_id"])
        self.assertEqual(result["checks"][0]["correct"], 1)
        self.assertEqual(result["checks"][0]["incorrect"], 1)
        self.assertEqual(result["checks"][0]["unknown"], 1)
        self.assertEqual(result["checks"][0]["format_matches"], 1)
        self.assertEqual(result["checks"][0]["format_mismatches"], 1)
        self.assertEqual(result["checks"][1]["missing"], 2)
        self.assertIsNone(result["source_sha256_if_exact_artifact"])

    def test_memory_journal_binds_to_a_valid_prefix_including_empty_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "acquisition" / "seed-1171-with_constructor"
            memory = run_dir / "memory" / "events.jsonl"
            prefix_digest = _journal(memory, [("artifact_stored", {"id": "a"})])
            first_line = memory.read_bytes()
            _journal(memory, [("artifact_stored", {"id": "a"}),
                              ("artifact_qualified", {"id": "a"})])
            _journal(run_dir / "events.jsonl", [])
            record = {"status": "completed", "journal_sha256": hashlib.sha256(b"").hexdigest(),
                      "memory_events_sha256": prefix_digest, "result": {"status": "complete"}}
            (run_dir / "result.json").write_bytes(_canon(record))
            loaded = report._load_acquisition(root, 1171, "WITH_CONSTRUCTOR")
            self.assertEqual(loaded["integrity"]["memory_events"], "hash_matched")
            self.assertEqual(first_line, memory.read_bytes().splitlines(keepends=True)[0])

            empty_run = root / "acquisition" / "seed-1173-model_only"
            empty_memory = empty_run / "memory" / "events.jsonl"
            empty_memory.parent.mkdir(parents=True)
            empty_memory.write_bytes(b"")
            _journal(empty_run / "events.jsonl", [])
            empty_record = {"status": "completed", "journal_sha256": hashlib.sha256(b"").hexdigest(),
                            "memory_events_sha256": hashlib.sha256(b"").hexdigest(),
                            "result": {"status": "complete"}}
            (empty_run / "result.json").write_bytes(_canon(empty_record))
            empty_loaded = report._load_acquisition(root, 1173, "MODEL_ONLY")
            self.assertEqual(empty_loaded["integrity"]["memory_events"], "hash_matched")

    def test_memory_journal_rejects_tamper_and_non_line_boundary_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "acquisition" / "seed-1171-with_constructor"
            memory = run_dir / "memory" / "events.jsonl"
            first_digest = _journal(memory, [("artifact_stored", {"value": 1})])
            first_line = memory.read_bytes()
            _journal(memory, [("artifact_stored", {"value": 1}),
                              ("artifact_qualified", {"value": 2})])
            _journal(run_dir / "events.jsonl", [])
            nonboundary = hashlib.sha256(first_line + b'{"partial"').hexdigest()
            record = {"status": "completed", "journal_sha256": hashlib.sha256(b"").hexdigest(),
                      "memory_events_sha256": nonboundary, "result": {"status": "complete"}}
            (run_dir / "result.json").write_bytes(_canon(record))
            loaded = report._load_acquisition(root, 1171, "WITH_CONSTRUCTOR")
            self.assertEqual(loaded["integrity"]["memory_events"], "invalid")
            self.assertIn("memory_events_prefix_digest_mismatch", loaded["integrity"]["issues"])

            tampered_run = root / "acquisition" / "seed-1173-model_only"
            tampered_memory = tampered_run / "memory" / "events.jsonl"
            prefix = _journal(tampered_memory, [("artifact_stored", {"value": 1})])
            _journal(tampered_memory, [("artifact_stored", {"value": 1}),
                                       ("artifact_qualified", {"value": 2})])
            _journal(tampered_run / "events.jsonl", [])
            tampered_record = {"status": "completed", "journal_sha256": hashlib.sha256(b"").hexdigest(),
                               "memory_events_sha256": prefix, "result": {"status": "complete"}}
            (tampered_run / "result.json").write_bytes(_canon(tampered_record))
            tampered_memory.write_bytes(tampered_memory.read_bytes().replace(b'"value":1', b'"value":9'))
            tampered = report._load_acquisition(root, 1173, "MODEL_ONLY")
            self.assertEqual(tampered["integrity"]["memory_events"], "invalid")
            self.assertIn("memory_events_invalid", tampered["integrity"]["issues"])

    def test_artifact_memory_journal_uses_utf8_canonical_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory" / "events.jsonl"
            _artifact_journal(path, [("artifact_stored", {"label": "café 🧭"})])
            self.assertEqual(report.read_journal(path, artifact=True)[0]["body"]["label"],
                             "café 🧭")
            with self.assertRaisesRegex(report.ReportError, "hash mismatch"):
                report.read_journal(path)

    def test_planned_no_artifact_reuse_is_not_misreported_as_missing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reuse_dir = root / "reuse" / "seed-1171-model_only"
            reuse_dir.mkdir(parents=True)
            (reuse_dir / "result.json").write_bytes(_canon({
                "status": "missing_final_artifact", "attempted": False,
                "artifact_id": None, "model_calls": 0,
                "source_run_journal_sha256": "a" * 64}))
            acquisition = {"submitted_artifact_id": None,
                           "_record": {"journal_sha256": "a" * 64}}
            loaded = report._load_reuse(root, 1171, "MODEL_ONLY", acquisition)
        self.assertEqual(loaded["status"], "missing_final_artifact")
        self.assertEqual(loaded["integrity"]["journal"], "not_applicable")
        self.assertEqual(loaded["integrity"]["issues"], [])
        self.assertEqual(loaded["checks"], [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reuse_dir = root / "reuse" / "seed-1171-model_only"
            reuse_dir.mkdir(parents=True)
            (reuse_dir / "result.json").write_bytes(_canon({
                "status": "missing_final_artifact", "attempted": False,
                "artifact_id": None, "model_calls": 1,
                "source_run_journal_sha256": "b" * 64}))
            inconsistent = report._load_reuse(root, 1171, "MODEL_ONLY", acquisition)
        self.assertIn("reuse_no_artifact_disposition_inconsistent",
                      inconsistent["integrity"]["issues"])
        self.assertIn("reuse_journal_missing", inconsistent["integrity"]["issues"])

    def test_summary_comparison_rejects_duplicate_extra_rows(self):
        acquisitions, reuses = {}, {}
        acq_rows, reuse_rows = [], []
        for index, (seed, arm) in enumerate(report.EXPECTED_RUNS):
            acq_record = {"seed": seed, "arm": arm, "result": index}
            reuse_record = {"seed": seed, "arm": arm, "result": index}
            acquisitions[(seed, arm)] = {"seed": seed, "arm": arm, "_record": acq_record}
            reuses[(seed, arm)] = {"seed": seed, "arm": arm, "_record": reuse_record}
            acq_rows.append(acq_record)
            reuse_rows.append(reuse_record)
        summary = {"acquisition": acq_rows + [dict(acq_rows[0])],
                   "fresh_process_reuse": reuse_rows}
        result = report._summary_comparison(summary, acquisitions, reuses)
        self.assertEqual(result["status"], "mismatch_or_incomplete")
        self.assertFalse(result["acquisition_rows_match"])
        self.assertTrue(result["reuse_rows_match"])

        summary["acquisition"] = [dict(row) for row in acq_rows]
        summary["acquisition"][0]["seed"] = []
        result = report._summary_comparison(summary, acquisitions, reuses)
        self.assertFalse(result["acquisition_rows_match"])

    def test_report_refuses_overwrite_and_strips_endpoint_secrets(self):
        identity = report._safe_model_identity({"model": "m", "protocol": "openai",
                                                "endpoint": "https://user:pass@example.test/v1?token=secret",
                                                "generation": {"max_tokens": 8}})
        self.assertEqual(identity["endpoint_origin"], "https://example.test")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                report.write_report({"_acquisitions": {}, "_reuses": {}}, output)


if __name__ == "__main__":
    unittest.main()
