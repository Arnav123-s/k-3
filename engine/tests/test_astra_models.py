"""Transport contract tests use mocked HTTP, never model calls."""
import io
import hashlib
import json
import os
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from kuttaka_runtime.models import JsonModel, NoRedirect, endpoint, parse_action
from kuttaka_runtime.runtime import TransportFailure


class ModelTests(unittest.TestCase):
    def test_custom_protocol_is_sent_and_identified(self):
        instructions = 'Return one investigation action as JSON.'
        model = JsonModel('test', 'https://example.com/v1', protocol='openai',
                          instructions=instructions)
        def response(req, timeout):
            self.assertEqual(json.loads(req.data)['messages'][0]['content'], instructions)
            return io.BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')
        with patch.object(model._opener, 'open', response):
            self.assertEqual(model.propose({}), {})
        self.assertEqual(model.identity['instructions_sha256'],
                         hashlib.sha256(instructions.encode()).hexdigest())
        for invalid in ('', '  ', None, 42):
            with self.assertRaises(ValueError):
                JsonModel('test', 'https://example.com', instructions=invalid)

    def test_reasoning_generation_parameters_sent_and_recorded(self):
        model = JsonModel('meta/muse-glimmer-30b', 'https://integrate.api.nvidia.com/v1',
                          protocol='openai', temperature=.95, top_p=1, reasoning_effort='high', max_tokens=8192)
        def response(req, timeout):
            data = json.loads(req.data)
            self.assertEqual((data['temperature'], data['top_p'], data['reasoning_effort'], data['max_tokens']),
                             (.95, 1, 'high', 8192))
            self.assertNotIn('/no_think', data['messages'][0]['content'])
            return io.BytesIO(b'{"choices":[{"message":{"content":"{\\"kind\\":\\"unresolved\\",\\"reason\\":\\"test\\"}"}}]}')
        with patch.object(model._opener, 'open', response):
            self.assertEqual(model.propose({})['kind'], 'unresolved')
        self.assertEqual(model.identity['generation']['reasoning_effort'], 'high')
        self.assertIn('kind', model.last_response['body'])
        self.assertIsNone(model.last_response['status'])

    def test_invalid_generation_limits_rejected_before_network(self):
        for kwargs in ({'temperature': float('nan')}, {'temperature': True}, {'top_p': 0},
                       {'max_tokens': -1}, {'timeout': 0}, {'reasoning_effort': 'invented'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                JsonModel('test', 'https://example.com', **kwargs)

    def test_endpoint_validation(self):
        for url in ('http://external.example', 'https://user:secret@example.com',
                    'https://example.com?key=secret', 'https://example.com#key', 'file:///tmp/a'):
            with self.assertRaises(ValueError):
                endpoint(url)
        self.assertEqual(endpoint('http://127.0.0.1:11434/'), 'http://127.0.0.1:11434')
        self.assertEqual(endpoint('https://example.com/v1'), 'https://example.com/v1')

    def test_remote_credential_not_in_prompt_or_identity(self):
        model = JsonModel('test', 'https://example.com/v1', protocol='openai', key_env='ASTRA_TEST_KEY')
        seen = []
        def response(req, timeout):
            seen.append(req)
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'{"kind":"answer","answer":42}'}}],
                                           'usage':{'prompt_tokens':12,'completion_tokens':4}}).encode())
        with patch.dict(os.environ, {'ASTRA_TEST_KEY':'private-test-key'}), patch.object(model._opener, 'open', response):
            self.assertEqual(model.propose({'request':'test'})['answer'], 42)
        self.assertEqual(seen[0].get_header('Authorization'), 'Bearer private-test-key')
        self.assertNotIn(b'private-test-key', seen[0].data)
        self.assertNotIn('private-test-key', json.dumps(model.identity))
        self.assertEqual(model.last_usage, {'input_tokens':12, 'output_tokens':4})

    def test_ollama_contract_and_unknown_usage(self):
        model = JsonModel('test', 'http://localhost:11434')
        def response(req, timeout):
            self.assertEqual(req.full_url, 'http://localhost:11434/api/chat')
            self.assertFalse(json.loads(req.data)['stream'])
            return io.BytesIO(b'{"message":{"content":"not json"}}')
        with patch.object(model._opener, 'open', response):
            self.assertEqual(model.propose({})['kind'], 'invalid')
        self.assertIsNone(model.last_usage['input_tokens'])

    def test_raw_response_is_bounded_and_secret_redacted(self):
        model = JsonModel('test', 'https://example.com/v1', protocol='openai', key_env='ASTRA_TEST_KEY')
        payload = {'choices':[{'message':{'content':'not-json'}}], 'usage':{'prompt_tokens':3}}
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        with patch.dict(os.environ, {'ASTRA_TEST_KEY':'private-test-key'}), \
             patch.object(model._opener, 'open', lambda req, timeout: Response(json.dumps(payload).encode())):
            result = model.propose({})
        self.assertEqual(result['kind'], 'invalid')
        self.assertEqual(model.last_usage, {'input_tokens':3, 'output_tokens':None})
        self.assertEqual(model.last_response['status'], 200)
        self.assertIn('not-json', model.last_response['body'])
        self.assertLessEqual(len(model.last_response['body'].encode()), 4 * 1024 * 1024)
        self.assertNotIn('private-test-key', json.dumps(model.last_response))

    def test_http_error_keeps_status_and_redacted_bounded_body(self):
        model = JsonModel('test', 'https://example.com/v1', protocol='openai', key_env='ASTRA_TEST_KEY')
        def fail(req, timeout):
            raise HTTPError(req.full_url, 429, 'limited', {}, io.BytesIO(b'limited private-test-key'))
        with patch.dict(os.environ, {'ASTRA_TEST_KEY':'private-test-key'}), \
             patch.object(model._opener, 'open', fail), self.assertRaises(HTTPError):
            model.propose({})
        self.assertEqual(model.last_response['status'], 429)
        self.assertIn('limited', model.last_response['body'])
        self.assertNotIn('private-test-key', json.dumps(model.last_response))
        self.assertEqual(model.last_usage, {})

    def test_reply_extraction_is_explicit_opt_in(self):
        prose = 'The result is: ```json\n{"kind":"answer","answer":42}\n```'
        self.assertEqual(parse_action(prose)["kind"], "invalid")
        self.assertEqual(parse_action(prose, parse_mode="extract"),
                         {"kind": "answer", "answer": 42})
        with self.assertRaises(ValueError):
            parse_action(prose, parse_mode="permissive")

    def test_stream_joins_content_excludes_reasoning_and_records_usage(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai",
                          stream=True, reasoning_hint="low", sampling_fields=False)
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "private reasoning"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": '{"kind":"answer",'}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": '"answer":42}'}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 12, "completion_tokens": 5}},
        ]
        raw = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        requests = []
        def response(req, timeout):
            requests.append(req)
            return Response(raw.encode())
        with patch.object(model._opener, "open", response):
            action = model.propose({})
        self.assertEqual(action, {"kind": "answer", "answer": 42})
        sent = json.loads(requests[0].data)
        self.assertTrue(sent["stream"])
        self.assertNotIn("temperature", sent)
        self.assertTrue(sent["messages"][0]["content"].startswith("Reasoning strength: low."))
        self.assertEqual(model.last_usage, {"input_tokens": 12, "output_tokens": 5,
                                            "reasoning_chars": len("private reasoning"),
                                            "finish_reason": "stop"})
        self.assertNotEqual(model.identity["instructions_sha256"],
                            JsonModel("test", "https://example.com/v1", protocol="openai",
                                      sampling_fields=False).identity["instructions_sha256"])
        self.assertNotIn("private reasoning", action.__repr__())

    def test_stream_finish_length_with_empty_content_is_a_recorded_reply(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai", stream=True)
        raw = ('data: ' + json.dumps({"choices": [{"delta": {}, "finish_reason": "length"}]})
               + "\n\ndata: [DONE]\n\n")
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        with patch.object(model._opener, "open", lambda req, timeout: Response(raw.encode())):
            action = model.propose({})
        self.assertEqual(action["kind"], "invalid")
        self.assertEqual(model.last_response["status"], 200)
        self.assertEqual(model.last_usage["finish_reason"], "length")
        self.assertEqual(model.last_usage["output_tokens"], None)

    def test_empty_stream_and_http_5xx_keep_receipt_and_safe_status(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai", stream=True)
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        with patch.object(model._opener, "open", lambda req, timeout: Response(b"")):
            with self.assertRaises(TransportFailure) as caught:
                model.propose({})
        self.assertEqual(caught.exception.kind, "empty_stream")
        self.assertEqual(caught.exception.status, 200)
        self.assertEqual(model.last_response["status"], 200)

        def fail(req, timeout):
            raise HTTPError(req.full_url, 502, "failed", {}, io.BytesIO(b"upstream detail"))
        with patch.object(model._opener, "open", fail):
            with self.assertRaises(TransportFailure) as caught:
                model.propose({})
        self.assertEqual(caught.exception.kind, "provider_http_error")
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(model.last_response["status"], 502)
        self.assertIn("upstream detail", model.last_response["body"])

    def test_network_secret_is_explicit_placeholder_and_identity_pinned(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai",
                          key_env="ASTRA_NETWORK_SECRET", network_secret=True,
                          sampling_fields=False)
        requests = []
        response_body = json.dumps({"choices": [{"message": {"content": json.dumps({"kind": "answer", "answer": 1})}}]}).encode()
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        with patch.dict(os.environ, {}, clear=True), patch.object(
                model._opener, "open", lambda req, timeout: (requests.append(req) or Response(response_body))):
            model.propose({"goal": "safe"})
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer placeholder")
        self.assertNotIn(b"Bearer placeholder", requests[0].data)
        self.assertEqual(model.identity["generation"]["credential_mode"],
                         "network_secret_placeholder_bearer")
        self.assertNotIn("ASTRA_NETWORK_SECRET", json.dumps(model.identity))

    def test_default_model_identity_preserves_pre_option_generation_shape(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai")
        self.assertEqual(model.identity["generation"], {
            "temperature": 0, "top_p": None, "reasoning_effort": None, "max_tokens": 2048,
        })
        self.assertEqual(model.identity["instructions_sha256"],
                         hashlib.sha256(model.instructions.encode()).hexdigest())

    def test_incomplete_stream_with_content_is_failure_and_does_not_reuse_usage(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai", stream=True)
        successful = ('data: ' + json.dumps({"choices": [{"delta": {"content":
                       '{"kind":"answer","answer":1}'}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 9, "completion_tokens": 2}})
                      + "\n\ndata: [DONE]\n\n")
        incomplete = ('data: ' + json.dumps({"choices": [{"delta": {"content":
                      '{"kind":"answer","answer":2}'}, "finish_reason": None}],
                      "usage": {"prompt_tokens": 7, "completion_tokens": 1}}) + "\n\n")
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        responses = iter([successful.encode(), incomplete.encode()])
        with patch.object(model._opener, "open", lambda req, timeout: Response(next(responses))):
            self.assertEqual(model.propose({}), {"kind": "answer", "answer": 1})
            with self.assertRaises(TransportFailure) as caught:
                model.propose({})
        self.assertEqual(caught.exception.kind, "incomplete_stream")
        self.assertEqual(model.last_response["status"], 200)
        self.assertEqual(model.last_usage["input_tokens"], 7)
        self.assertEqual(model.last_usage["output_tokens"], 1)

    def test_corrupt_sse_frames_and_shapes_fail_closed_preserving_usage(self):
        frames = [
            'data: not-json\n\ndata: [DONE]\n\n',
            'data: ' + json.dumps({"choices": {"wrong": "shape"},
                                   "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
            + "\n\ndata: [DONE]\n\n",
            'data: ' + json.dumps({"choices": [{"delta": {}, "finish_reason": 7}],
                                   "usage": {"prompt_tokens": 4, "completion_tokens": 1}})
            + "\n\ndata: [DONE]\n\n",
        ]
        for raw, expected_input in zip(frames, (None, 3, 4)):
            with self.subTest(raw=raw[:32]):
                model = JsonModel("test", "https://example.com/v1", protocol="openai", stream=True)
                class Response(io.BytesIO):
                    def getcode(self):
                        return 200
                with patch.object(model._opener, "open", lambda req, timeout: Response(raw.encode())):
                    with self.assertRaises(TransportFailure) as caught:
                        model.propose({})
                self.assertEqual(caught.exception.kind, "stream_error")
                self.assertEqual(model.last_response["status"], 200)
                self.assertEqual(model.last_usage["input_tokens"], expected_input)

    def test_done_only_stream_is_empty_but_length_finish_is_a_reply(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai", stream=True)
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        with patch.object(model._opener, "open", lambda req, timeout: Response(b"data: [DONE]\n\n")):
            with self.assertRaises(TransportFailure) as caught:
                model.propose({})
        self.assertEqual(caught.exception.kind, "empty_stream")
        raw = ('data: ' + json.dumps({"choices": [{"delta": {}, "finish_reason": "length"}]})
               + "\n\ndata: [DONE]\n\n")
        with patch.object(model._opener, "open", lambda req, timeout: Response(raw.encode())):
            self.assertEqual(model.propose({})["kind"], "invalid")
        self.assertEqual(model.last_usage["finish_reason"], "length")

    def test_stream_error_event_is_classified_without_exposing_error_text(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai", stream=True)
        raw = ('data: ' + json.dumps({"error": {"message": "private provider detail"}})
               + "\n\ndata: [DONE]\n\n")
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        with patch.object(model._opener, "open", lambda req, timeout: Response(raw.encode())):
            with self.assertRaises(TransportFailure) as caught:
                model.propose({})
        self.assertEqual(caught.exception.kind, "stream_error")
        self.assertEqual(model.last_response["status"], 200)
        self.assertNotIn("private provider detail", str(caught.exception))

    def test_connection_reset_is_classified_as_transport_failure(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai")
        with patch.object(model._opener, "open", side_effect=ConnectionResetError("private detail")):
            with self.assertRaises(TransportFailure) as caught:
                model.propose({})
        self.assertEqual(caught.exception.kind, "request_transport_error")
        self.assertIsNone(caught.exception.status)
        self.assertNotIn("private detail", str(caught.exception))

    def test_transport_failure_resets_prior_receipt_and_usage(self):
        model = JsonModel("test", "https://example.com/v1", protocol="openai")
        ok = json.dumps({"choices": [{"message": {"content": "{}"}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 2}}).encode()
        class Response(io.BytesIO):
            def getcode(self):
                return 200
        def open_request(req, timeout):
            if getattr(open_request, "calls", 0) == 0:
                open_request.calls = 1
                return Response(ok)
            raise OSError("private network failure")
        open_request.calls = 0
        with patch.object(model._opener, "open", open_request):
            model.propose({})
            self.assertEqual(model.last_usage["input_tokens"], 10)
            with self.assertRaises(TransportFailure):
                model.propose({})
        self.assertIsNone(model.last_response)
        self.assertEqual(model.last_usage, {})

    def test_non_boolean_transport_options_rejected(self):
        for name in ("stream", "sampling_fields", "network_secret"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                JsonModel("test", "https://example.com/v1", protocol="openai", **{name: 1})

    def test_missing_key_fails_before_network(self):
        model = JsonModel('test', 'https://example.com/v1', protocol='openai', key_env='ASTRA_ABSENT_TEST_KEY')
        with patch.dict(os.environ, {}, clear=True), patch.object(model._opener, 'open') as opened:
            with self.assertRaises(ValueError):
                model.propose({})
            opened.assert_not_called()

    def test_redirect_does_not_forward_credentials(self):
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')
