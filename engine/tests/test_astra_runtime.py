"""Authored mechanism controls, not evidence of model intelligence or learning."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from kuttaka_runtime import Budget, Journal, investigate
from kuttaka_runtime.models import JsonModel, ReplayModel
from kuttaka_runtime.runtime import TransportFailure


class Scripted:
    def __init__(self, *actions):
        self.actions = iter(actions)

    def propose(self, context):
        return next(self.actions)


class AstraTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'events.jsonl'

    def run_case(self, actions, tools=None, checker=None, budget=None):
        with Journal(self.path) as journal:
            result = investigate('Find the answer', Scripted(*actions), tools or {},
                                 checker or (lambda request, answer: answer == 42), journal,
                                 budget or Budget())
        return result, Journal.read(self.path)

    def test_prediction_committed_before_observation(self):
        def observe(value):
            events = Journal.read(self.path)
            self.assertEqual(events[-2]['kind'], 'proposal')
            return value * 2
        action = dict(kind='experiment', tool='observe', input=21,
                      predictions=[dict(hypothesis='doubling', outcome=42)])
        result, events = self.run_case([action, dict(kind='answer', answer=42)], {'observe': observe})
        self.assertEqual(result.status, 'checked')
        self.assertEqual((result.model_calls, result.tool_calls), (2, 2))
        self.assertEqual(events[-1]['body']['answer'], 42)

    def test_wrong_answer_does_not_finish(self):
        result, _ = self.run_case([dict(kind='answer', answer=0), dict(kind='answer', answer=42)])
        self.assertEqual(result.answer, 42)
        self.assertEqual(result.model_calls, 2)

    def test_no_budget_does_not_call_model(self):
        result, _ = self.run_case([], budget=Budget(0, 0))
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(result.model_calls, 0)

    def test_checker_shares_tool_budget(self):
        result, _ = self.run_case([dict(kind='answer', answer=42)], budget=Budget(1, 0))
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(result.tool_calls, 0)

    def test_unknown_tool_gets_correction_without_tool_call(self):
        valid = dict(kind='experiment', tool='observe', input=1,
                     predictions=[dict(hypothesis='test', outcome='one')])
        result, events = self.run_case([
            dict(kind='experiment', tool='missing', input=0,
                 predictions=[dict(hypothesis='test', outcome='one')]),
            valid, dict(kind='answer', answer=42)], {'observe': lambda value: value})
        rejection = next(event for event in events if event['kind'] == 'action_rejected')
        self.assertIn('available tool names: observe', rejection['body']['feedback'])
        self.assertEqual(result.tool_calls, 2)
        self.assertEqual(result.status, 'checked')

    def test_missing_predictions_gets_correction_without_tool_call(self):
        invalid = dict(kind='experiment', tool='observe', input=1, predictions=[])
        valid = dict(kind='experiment', tool='observe', input=1,
                     predictions=[dict(hypothesis='test', outcome='one')])
        result, events = self.run_case([invalid, valid, dict(kind='answer', answer=42)],
                                       {'observe': lambda value: value})
        rejected = next(event for event in events if event['kind'] == 'action_rejected')
        self.assertIn('nonempty predictions list', rejected['body']['feedback'])
        self.assertEqual(result.tool_calls, 2)
        self.assertEqual(result.status, 'checked')

    def test_non_object_prose_null_gets_correction(self):
        result, events = self.run_case([None, dict(kind='answer', answer=42)])
        rejected = next(event for event in events if event['kind'] == 'action_rejected')
        self.assertIn('one JSON object', rejected['body']['feedback'])
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.status, 'checked')

    def test_permanently_invalid_action_consumes_model_budget_only(self):
        invalid = dict(kind='experiment', tool='observe', input=1, predictions=[])
        result, events = self.run_case([invalid, invalid], {'observe': lambda value: value},
                                       budget=Budget(2, 1))
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(len([event for event in events if event['kind'] == 'action_rejected']), 2)

    def test_checker_feedback_failure_does_not_change_false_verdict(self):
        with Journal(self.path) as journal:
            result = investigate('Find the answer', Scripted(dict(kind='answer', answer=0)), {},
                                 lambda request, answer: False, journal, Budget(1, 1),
                                 checker_feedback=lambda *args: (_ for _ in ()).throw(RuntimeError('secret')))
        events = Journal.read(self.path)
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(next(e for e in events if e['kind'] == 'observation')['body']['observation'], False)
        error = next(e for e in events if e['kind'] == 'checker_feedback_error')
        self.assertEqual(error['body']['type'], 'RuntimeError')
        self.assertNotIn('secret', self.path.read_text())

    def test_checker_feedback_is_recorded_without_changing_false_verdict(self):
        class Correcting:
            def __init__(self):
                self.actions = iter([dict(kind='answer', answer=0), dict(kind='answer', answer=42)])
            def propose(self, context):
                if context['history']:
                    self.assert_feedback = context['history'][-1].get('checker_feedback')
                return next(self.actions)
        model = Correcting()
        with Journal(self.path) as journal:
            result = investigate('Find the answer', model, {}, lambda request, answer: answer == 42,
                                 journal, Budget(2, 2),
                                 checker_feedback=lambda request, answer: {'expected_hint': 42})
        events = Journal.read(self.path)
        self.assertEqual(result.status, 'checked')
        self.assertEqual(model.assert_feedback, {'expected_hint': 42})
        self.assertEqual(next(e for e in events if e['kind'] == 'checker_feedback')['body'],
                         {'expected_hint': 42})

    def test_failure_accounted_without_exception_secret(self):
        def failure(*args):
            raise RuntimeError('secret-provider-token')
        result, _ = self.run_case([dict(kind='answer', answer=42)], checker=failure)
        self.assertEqual(result.tool_calls, 1)
        self.assertNotIn('secret-provider-token', self.path.read_text())
        self.assertEqual(result.status, 'incomplete')

    def test_truthy_checker_is_not_accepted(self):
        result, _ = self.run_case([dict(kind='answer', answer=42)], checker=lambda *args: 'yes')
        self.assertEqual(result.status, 'incomplete')

    def test_explicit_unresolved(self):
        result, _ = self.run_case([dict(kind='unresolved', reason='missing measurement')])
        self.assertEqual(result.status, 'unresolved')

    def test_tampered_history_rejected(self):
        self.run_case([dict(kind='answer', answer=42)])
        rows = self.path.read_text().splitlines()
        value = json.loads(rows[0])
        value['body']['request'] = 'different goal'
        rows[0] = json.dumps(value)
        self.path.write_text('\n'.join(rows) + '\n')
        with self.assertRaises(ValueError):
            Journal.read(self.path)

    def test_overwrite_refused(self):
        self.run_case([dict(kind='answer', answer=42)])
        with self.assertRaises(FileExistsError):
            Journal(self.path)

    def test_truncated_history_rejected(self):
        self.run_case([dict(kind='answer', answer=42)])
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            Journal.read(self.path)

    def test_budget_validation(self):
        for value in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                Budget(model_calls=value)

    def test_checker_cannot_mutate_submitted_answer(self):
        def checker(request, answer):
            self.assertEqual(request, 'Find the answer')
            answer['value'] = 'changed'
            return True
        result, events = self.run_case(
            [dict(kind='answer', answer={'value': 'original'})], checker=checker)
        self.assertEqual(result.answer, {'value': 'original'})
        self.assertEqual(events[-1]['body']['answer'], result.answer)

    def test_false_checker_exhausts_budget_without_delivery(self):
        result, events = self.run_case([dict(kind='answer', answer=0)], budget=Budget(1, 1))
        self.assertEqual(result.status, 'incomplete')
        self.assertIsNone(result.answer)
        self.assertEqual(events[-2]['body']['observation'], False)

    def test_transport_failure_receipt_and_safe_status_are_journaled(self):
        class TransportFailure:
            last_usage = {}
            last_response = {'status': 429, 'body': 'provider says slow down'}
            def propose(self, context):
                raise RuntimeError('secret-provider-token')
        with Journal(self.path) as journal:
            result = investigate('Find the answer', TransportFailure(), {}, lambda *args: True,
                                 journal, Budget(1, 1))
        events = Journal.read(self.path)
        receipt = next(event for event in events if event['kind'] == 'model_response')
        error = next(event for event in events if event['kind'] == 'model_error')
        self.assertEqual(receipt['body']['receipt']['status'], 429)
        self.assertEqual(error['body']['status'], 429)
        self.assertNotIn('secret-provider-token', self.path.read_text())
        self.assertEqual(result.status, 'incomplete')

    def test_soft_transport_failure_receipt_is_charged_and_replays_before_success(self):
        class Sequence:
            identity = {"model": "scripted"}
            def __init__(self):
                self.actions = iter(["failure", {"kind": "answer", "answer": 42}])
                self.last_response = None
                self.last_usage = {}
            def propose(self, context):
                item = next(self.actions)
                if item == "failure":
                    self.last_response = {"status": 502, "body": "bounded provider receipt"}
                    self.last_usage = {"input_tokens": None, "output_tokens": None}
                    raise TransportFailure("provider_http_error", status=502)
                self.last_response = {"status": 200, "body": "ok"}
                self.last_usage = {"input_tokens": 11, "output_tokens": 4}
                return item

        with Journal(self.path) as journal:
            result = investigate("Find the answer", Sequence(), {}, lambda req, ans: ans == 42,
                                 journal, Budget(2, 2), continue_transport_failures=True,
                                 outage_limit=3)
        events = Journal.read(self.path)
        self.assertEqual((result.status, result.model_calls, result.tool_calls), ("checked", 2, 1))
        errors = [event for event in events if event["kind"] == "model_error"]
        self.assertEqual(errors[0]["body"], {"type": "TransportFailure",
                                              "kind": "provider_http_error",
                                              "status": 502, "soft": True})
        responses = [event["body"] for event in events if event["kind"] == "model_response"]
        self.assertEqual(responses[0]["receipt"]["status"], 502)
        self.assertIsNone(responses[0]["usage"]["input_tokens"])
        replay_path = Path(self.tmp.name) / "replay.jsonl"
        with Journal(replay_path) as journal:
            replayed = investigate("Find the answer", ReplayModel(self.path), {},
                                   lambda req, ans: ans == 42, journal, Budget(2, 2),
                                   continue_transport_failures=True, outage_limit=3)
        self.assertEqual(replayed, result)
        self.assertEqual([e["hash"] for e in Journal.read(replay_path)],
                         [e["hash"] for e in events])

    def test_scripted_http_502_then_success_replays_byte_identically(self):
        model = JsonModel("scripted", "https://example.com/v1", protocol="openai",
                          max_tokens=64, sampling_fields=False)
        call_count = 0
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        def open_request(request, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise HTTPError(request.full_url, 502, "upstream", {},
                                io.BytesIO(b'{"error":"transient"}'))
            payload = {"choices": [{"message": {"content": json.dumps(
                {"kind": "answer", "answer": 42})}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4}}
            return Response(json.dumps(payload).encode())
        with patch.object(model._opener, "open", open_request):
            with Journal(self.path) as journal:
                result = investigate("Find the answer", model, {}, lambda req, ans: ans == 42,
                                     journal, Budget(2, 2),
                                     continue_transport_failures=True, outage_limit=3)
        events = Journal.read(self.path)
        self.assertEqual(events[0]["body"]["runtime_options"], {
            "normalize": False, "automatic_submission": False,
            "continue_transport_failures": True, "outage_limit": 3})
        self.assertEqual((result.status, result.model_calls), ("checked", 2))
        responses = [e["body"] for e in events if e["kind"] == "model_response"]
        self.assertEqual(responses[0]["receipt"]["status"], 502)
        self.assertEqual(responses[0]["usage"], {"input_tokens": None, "output_tokens": None})
        self.assertEqual(responses[1]["usage"], {"input_tokens": 12, "output_tokens": 4})
        replay_path = Path(self.tmp.name) / "http-replay.jsonl"
        with Journal(replay_path) as journal:
            replayed = investigate("Find the answer", ReplayModel(self.path), {},
                                   lambda req, ans: ans == 42, journal, Budget(2, 2),
                                   continue_transport_failures=True, outage_limit=3)
        replay_events = Journal.read(replay_path)
        self.assertEqual(replayed, result)
        self.assertEqual([e["hash"] for e in replay_events], [e["hash"] for e in events])

    def test_outer_json_parse_failure_keeps_receipt_status_and_unknown_usage(self):
        model = JsonModel("scripted", "https://example.com/v1", protocol="openai")
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        with patch.object(model._opener, "open", lambda req, timeout: Response(b"not-json")):
            with Journal(self.path) as journal:
                result = investigate("Find the answer", model, {}, lambda *args: True,
                                     journal, Budget(1, 1))
        events = Journal.read(self.path)
        response = next(e["body"] for e in events if e["kind"] == "model_response")
        error = next(e["body"] for e in events if e["kind"] == "model_error")
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(response["receipt"]["status"], 200)
        self.assertEqual(response["receipt"]["body"], "not-json")
        self.assertEqual(response["usage"], {"input_tokens": None, "output_tokens": None})
        self.assertEqual(error, {"type": "JSONDecodeError", "status": 200})

    def test_failed_attempt_without_receipt_still_records_unknown_usage_and_replays(self):
        class Sequence:
            identity = {"model": "missing-receipt"}
            def __init__(self):
                self.responses = iter(["fail", {"kind": "answer", "answer": 42}])
                self.last_response = None
                self.last_usage = {}
            def propose(self, context):
                value = next(self.responses)
                self.last_response = None
                self.last_usage = {}
                if value == "fail":
                    raise TransportFailure("request_transport_error")
                return value
        with Journal(self.path) as journal:
            result = investigate("Find the answer", Sequence(), {}, lambda req, ans: ans == 42,
                                 journal, Budget(2, 2), continue_transport_failures=True,
                                 outage_limit=2)
        events = Journal.read(self.path)
        failed_response = next(e["body"] for e in events if e["kind"] == "model_response")
        self.assertIsNone(failed_response["receipt"])
        self.assertEqual(failed_response["usage"], {"input_tokens": None, "output_tokens": None})
        replay_path = Path(self.tmp.name) / "missing-receipt-replay.jsonl"
        with Journal(replay_path) as journal:
            replayed = investigate("Find the answer", ReplayModel(self.path), {},
                                   lambda req, ans: ans == 42, journal, Budget(2, 2),
                                   continue_transport_failures=True, outage_limit=2)
        self.assertEqual(replayed, result)
        self.assertEqual(Journal.read(replay_path), events)

    def test_replay_refuses_changed_nondefault_runtime_options(self):
        with Journal(self.path) as journal:
            investigate("Find the answer", Scripted({"kind": "unresolved", "reason": "test"}),
                        {}, lambda *args: True, journal, Budget(1, 1), normalize=True)
        replay_path = Path(self.tmp.name) / "mismatched-replay.jsonl"
        with Journal(replay_path) as journal:
            with self.assertRaisesRegex(ValueError, "runtime options do not match"):
                investigate("Find the answer", ReplayModel(self.path), {},
                            lambda *args: True, journal, Budget(1, 1))
        self.assertEqual(Journal.read(replay_path), [])

    def test_default_run_start_keeps_legacy_shape_and_replays(self):
        model = Scripted({"kind": "unresolved", "reason": "test"})
        model.identity = {"model": "scripted"}
        with Journal(self.path) as journal:
            result = investigate("Find the answer", model, {}, lambda *args: True,
                                 journal, Budget())
        events = Journal.read(self.path)
        self.assertEqual(result.status, "unresolved")
        self.assertNotIn("runtime_options", events[0]["body"])
        replay_path = Path(self.tmp.name) / "default-replay.jsonl"
        with Journal(replay_path) as journal:
            replayed = investigate("Find the answer", ReplayModel(self.path), {},
                                   lambda *args: True, journal, Budget())
        self.assertEqual(replayed, result)
        self.assertEqual(Journal.read(replay_path), events)

    def test_transport_outage_limit_stops_within_model_budget(self):
        class Failing:
            def __init__(self):
                self.calls = 0
                self.last_response = {"status": 503, "body": ""}
                self.last_usage = {}
            def propose(self, context):
                self.calls += 1
                raise TransportFailure("provider_http_error", status=503)
        model = Failing()
        with Journal(self.path) as journal:
            result = investigate("Find the answer", model, {}, lambda *args: True, journal,
                                 Budget(8, 2), continue_transport_failures=True, outage_limit=2)
        self.assertEqual(model.calls, 2)
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.reason, "transport outage threshold reached")

    def test_hard_failure_receipt_replays_with_matching_error_and_status(self):
        class HTTPError(Exception):
            status = 429
        class Failing:
            identity = {"model": "scripted-hard"}
            last_response = {"status": 429, "body": "safe bounded receipt"}
            last_usage = {"input_tokens": None, "output_tokens": None}
            def propose(self, context):
                raise HTTPError("private server detail")
        with Journal(self.path) as journal:
            original = investigate("Find the answer", Failing(), {}, lambda *args: True,
                                   journal, Budget(1, 1))
        replay_path = Path(self.tmp.name) / "hard-replay.jsonl"
        with Journal(replay_path) as journal:
            replayed = investigate("Find the answer", ReplayModel(self.path), {},
                                   lambda *args: True, journal, Budget(1, 1))
        self.assertEqual(replayed, original)
        self.assertEqual(Journal.read(replay_path), Journal.read(self.path))
        event = next(e for e in Journal.read(replay_path) if e["kind"] == "model_error")
        self.assertEqual(event["body"], {"type": "HTTPError", "status": 429})
        self.assertNotIn("private server detail", replay_path.read_text())

    def test_action_normalization_and_automatic_submission_are_opt_in(self):
        missing_predictions = {"kind": "experiment", "tool": "observe", "input": 1}
        tool = {"observe": lambda value: {"deliverable": 42}}
        strict_result, strict_events = self.run_case([missing_predictions], tool)
        self.assertEqual(strict_result.tool_calls, 0)
        self.assertTrue(any(e["kind"] == "action_rejected" for e in strict_events))

        with Journal(Path(self.tmp.name) / "normalized.jsonl") as journal:
            normalized = investigate("Find the answer", Scripted(missing_predictions), tool,
                                     lambda req, answer: answer == 42, journal, Budget(1, 2),
                                     normalize=True, automatic_submission=True)
        self.assertEqual((normalized.status, normalized.model_calls, normalized.tool_calls),
                         ("checked", 1, 2))

        with Journal(Path(self.tmp.name) / "submission-default.jsonl") as journal:
            no_auto = investigate("Find the answer", Scripted(
                {"kind": "experiment", "tool": "observe", "input": 1,
                 "predictions": [{"hypothesis": "observed deliverable", "outcome": 42}]},
                {"kind": "answer", "answer": 42}), tool, lambda req, ans: ans == 42,
                journal, Budget(2, 2))
        self.assertEqual((no_auto.status, no_auto.model_calls, no_auto.tool_calls),
                         ("checked", 2, 2))

    def test_outage_options_are_validated(self):
        cases = ({"continue_transport_failures": True},
                 {"continue_transport_failures": True, "outage_limit": True},
                 {"outage_limit": 2}, {"normalize": 1}, {"automatic_submission": 1})
        for index, kwargs in enumerate(cases):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                with Journal(Path(self.tmp.name) / ("invalid-options-" + str(index) + ".jsonl")) as journal:
                    investigate("Find the answer", Scripted(), {}, lambda *args: True,
                                journal, Budget(0, 0), **kwargs)

    def test_model_failure_is_charged_without_retry(self):
        result, events = self.run_case([])
        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.tool_calls, 0)
        self.assertEqual(result.status, 'incomplete')
        self.assertEqual(events[-2]['kind'], 'model_error')
