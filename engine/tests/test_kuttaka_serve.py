import io
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from kuttaka_runtime.serve import Service, http_handler, mcp_reply, serve_mcp
from kuttaka_runtime.solve import Workspace, settings
from tests.test_kuttaka_campaign import local_executor
from tests.test_kuttaka_solve import Actor, Planner


def service(max_calls=40):
    return Service(settings(model='scripted', max_calls=max_calls), Workspace(tempfile.mkdtemp()),
                   models=lambda: (Planner(), Actor(), None, None, None), executor=local_executor)


class ServiceTests(unittest.TestCase):
    def test_solve_returns_checked_method_and_records_cost(self):
        s = service()
        result = s.solve({'task': 'Square a number.'})
        self.assertEqual(result['status'], 'checked')
        self.assertIn('x * x', result['source'])
        self.assertGreater(result['cost']['model_calls'], 0)
        self.assertEqual(len(s.stats()['history']), 1)

    def test_request_can_lower_but_not_raise_the_call_limit(self):
        s = service(max_calls=40)
        self.assertEqual(s.solve({'task': 'Square a number.', 'max_calls': 1})['cost']['model_calls'], 1)
        big = s.solve({'task': 'Square a number.', 'max_calls': 10 ** 6})
        self.assertLessEqual(big['cost']['model_calls'], 40)

    def test_bad_requests_are_refused(self):
        s = service()
        for bad in ({}, {'task': ''}, {'task': 'x', 'examples': [{'input': 1}]}, {'task': 'x', 'max_calls': 0}):
            with self.assertRaises(ValueError):
                s.solve(bad)


class HttpTests(unittest.TestCase):
    def test_post_solve_and_get_stats(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), http_handler(service()))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            req = urllib.request.Request(base + '/solve', data=json.dumps({'task': 'Square a number.'}).encode(),
                                         headers={'Content-Type': 'application/json'})
            body = json.loads(urllib.request.urlopen(req, timeout=60).read())
            self.assertEqual(body['status'], 'checked')
            stats = json.loads(urllib.request.urlopen(base + '/stats', timeout=10).read())
            self.assertEqual(len(stats['history']), 1)
            library = json.loads(urllib.request.urlopen(base + '/library', timeout=10).read())
            self.assertEqual(library['counts']['methods'], 1)
            self.assertEqual(library['methods'][0]['task'], 'Square a number.')
            with self.assertRaises(urllib.error.HTTPError) as bad:
                urllib.request.urlopen(urllib.request.Request(base + '/solve', data=b'[1]'), timeout=10)
            self.assertEqual(bad.exception.code, 400)
        finally:
            server.shutdown()


class McpTests(unittest.TestCase):
    def test_initialize_list_and_call(self):
        lines = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                 {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                 {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                 {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                  'params': {'name': 'solve', 'arguments': {'task': 'Square a number.'}}},
                 {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'nope'}},
                 {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'library', 'arguments': {}}}]
        out = io.StringIO()
        serve_mcp(service(), io.StringIO(''.join(json.dumps(l) + '\n' for l in lines) + 'not json\n'), out)
        replies = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4, 5, None])  # no reply to the notification
        library = json.loads(replies[4]['result']['content'][0]['text'])
        self.assertFalse(replies[4]['result']['isError'])
        self.assertEqual(library['counts']['methods'], 1)
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'kuttaka')
        self.assertEqual({t['name'] for t in replies[1]['result']['tools']}, {'solve', 'stats', 'library'})
        call = replies[2]['result']
        self.assertFalse(call['isError'])
        self.assertEqual(json.loads(call['content'][0]['text'])['status'], 'checked')
        self.assertEqual(replies[3]['error']['code'], -32602)
        self.assertEqual(replies[5]['error']['code'], -32700)

    def test_unknown_method_is_an_error(self):
        self.assertEqual(mcp_reply(service(), {'jsonrpc': '2.0', 'id': 9, 'method': 'x'})['error']['code'], -32601)


if __name__ == '__main__':
    unittest.main()


class RoundsTests(unittest.TestCase):
    def test_incomplete_round_continues_on_the_same_workspace_until_checked(self):
        from kuttaka_runtime.solve import solve_task

        ws = Workspace(tempfile.mkdtemp())

        class Flaky(Actor):
            """Gives up in every attempt of the first round (no round is recorded yet)."""
            def propose(self, context):
                if not ws.history():
                    return {'kind': 'unresolved', 'reason': 'not this time'}
                return super().propose(context)

        result = solve_task('Square a number.', settings=settings(model='scripted', max_calls=8, rounds=4),
                            workspace=ws, models=(Planner(), Flaky(), None, None, None), executor=local_executor)
        self.assertEqual(result['status'], 'checked')
        self.assertGreaterEqual(len(result['rounds']), 2)
        self.assertNotEqual(result['rounds'][0]['status'], 'checked')
        self.assertEqual(result['cost']['model_calls'], sum(r['model_calls'] for r in result['rounds']))
        self.assertEqual(len(ws.history()), len(result['rounds']))

    def test_one_round_by_default(self):
        from kuttaka_runtime.solve import solve_task
        result = solve_task('Square a number.', settings=settings(model='scripted'), workspace=Workspace(tempfile.mkdtemp()),
                            models=(Planner(), Actor(), None, None, None), executor=local_executor)
        self.assertEqual(len(result['rounds']), 1)


class McpClientCompatibilityTests(unittest.TestCase):
    def test_the_client_protocol_version_is_kept_when_supported(self):
        reply = mcp_reply(service(), {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                      'params': {'protocolVersion': '2025-06-18'}})
        self.assertEqual(reply['result']['protocolVersion'], '2025-06-18')
        reply = mcp_reply(service(), {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                      'params': {'protocolVersion': '1999-01-01'}})
        self.assertEqual(reply['result']['protocolVersion'], '2024-11-05')

    def test_list_methods_of_a_tools_only_server_are_empty(self):
        for method, key in (('resources/list', 'resources'), ('resources/templates/list', 'resourceTemplates'),
                            ('prompts/list', 'prompts')):
            self.assertEqual(mcp_reply(service(), {'jsonrpc': '2.0', 'id': 2, 'method': method})['result'], {key: []})

    def test_a_failing_solve_is_reported_and_the_server_keeps_serving(self):
        class Broken:
            def solve(self, request):
                raise RuntimeError('sandbox gone')
        reply = mcp_reply(Broken(), {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                     'params': {'name': 'solve', 'arguments': {'task': 'x'}}})
        self.assertTrue(reply['result']['isError'])
        self.assertIn('RuntimeError', reply['result']['content'][0]['text'])
        out = io.StringIO()
        serve_mcp(Broken(), io.StringIO('[1, 2]\n' + json.dumps({'jsonrpc': '2.0', 'id': 4, 'method': 'ping'}) + '\n'), out)
        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(replies[0]['error']['code'], -32600)
        self.assertEqual(replies[1], {'jsonrpc': '2.0', 'id': 4, 'result': {}})
