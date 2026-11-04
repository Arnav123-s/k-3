"""Kuttaka as a service: the same solver as the CLI, for programs and agents.

    python -m kuttaka_runtime.serve http --port 8765      # POST /solve, GET /stats
    python -m kuttaka_runtime.serve mcp                   # MCP over stdio: tools "solve" and "stats"

Model settings come from the same flags or KUTTAKA_* variables as the CLI. One task runs at a time
per workspace, so the workspace files stay consistent. A request may lower max_calls, never raise
it above the server's limit. Checks the model proposes are used without asking (there is no one to
ask); every result says who wrote its checks.

HTTP: POST /solve {"task": str, "examples": [{"input": ..., "expected": ...}], "max_calls": int}
returns the result as JSON (status "checked" or "incomplete", the method source, its checks,
cost, "evidence": the support of each check and what the method held on beyond them, and, when
nothing is delivered, "void": what is known, kept in its place). GET /stats returns the workspace
history; GET /library, the method library as a whole (kuttaka_runtime/shastra.py).
"""

import argparse
import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .execution import preflight
from .solve import Workspace, _checks, settings, solve_task, stats

PROTOCOL_VERSION = '2024-11-05'
# Protocol versions whose tool methods this server implements unchanged; the client's choice is kept.
PROTOCOL_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18')
EMPTY_LISTS = {'resources/list': 'resources', 'resources/templates/list': 'resourceTemplates',
               'prompts/list': 'prompts'}
TOOLS = [
    {'name': 'solve',
     'description': ('Solve a task with checked code: the model writes run(input), Kuttaka runs it in a '
                     'sandbox against worked checks, repairs, splits and reuses stored methods until it '
                     'passes. Returns the method source, the checks it passed and who wrote them, and the cost.'),
     'inputSchema': {'type': 'object', 'required': ['task'], 'properties': {
         'task': {'type': 'string', 'description': 'the task in plain words'},
         'examples': {'type': 'array', 'description': 'checks you trust, each {"input": ..., "expected": ...}',
                      'items': {'type': 'object'}},
         'max_calls': {'type': 'integer', 'description': 'model-call limit for this task (at most the server limit)'}}}},
    {'name': 'stats', 'description': 'Per-task cost and reuse history of this workspace.',
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'library',
     'description': ('The method library as a whole: each stored method with the rule it implements, the '
                     'form of input it applies to, the support of its checks, what it held on beyond them, '
                     'what it uses, its lineage (what it superseded, whether it was shown wrong), and the '
                     'open places where no verified answer exists yet.'),
     'inputSchema': {'type': 'object', 'properties': {}}},
]


class Service:
    def __init__(self, config, workspace, models=None, executor=None):
        self.config, self.workspace = config, workspace
        self.models, self.executor = models, executor
        self.lock = threading.Lock()

    def solve(self, request):
        task = request.get('task')
        if not isinstance(task, str) or not task.strip():
            raise ValueError('"task" must be a non-empty string')
        examples = request.get('examples') or []
        if examples and _checks(examples) is None:
            raise ValueError('each example must be {"input": ..., "expected": ...}')
        limit = request.get('max_calls', self.config.max_calls)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError('"max_calls" must be a positive integer')
        config = settings(**dict(vars(self.config), max_calls=min(limit, self.config.max_calls)))
        with self.lock:
            return solve_task(task, examples, settings=config, workspace=self.workspace,
                              models=self.models() if self.models else None, executor=self.executor,
                              say=lambda text: print(text, file=sys.stderr, flush=True))

    def library(self):
        from .shastra import build
        return build(self.workspace)

    def stats(self):
        out = io.StringIO()
        stats(self.workspace, out)
        return {'history': self.workspace.history(), 'text': out.getvalue()}


def http_handler(service):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/stats':
                self._send(200, service.stats())
            elif self.path == '/library':
                self._send(200, service.library())
            else:
                self._send(404, {'error': 'use POST /solve, GET /stats or GET /library'})

        def do_POST(self):
            if self.path != '/solve':
                return self._send(404, {'error': 'use POST /solve or GET /stats'})
            try:
                length = int(self.headers.get('Content-Length') or 0)
                request = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(request, dict):
                    raise ValueError('the body must be a JSON object')
                self._send(200, service.solve(request))
            except (ValueError, json.JSONDecodeError) as error:
                self._send(400, {'error': str(error)})

        def log_message(self, format, *args):
            print(format % args, file=sys.stderr)
    return Handler


def mcp_reply(service, message):
    """One JSON-RPC message in, the reply out (None for notifications)."""
    if not isinstance(message, dict):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'invalid request'}}
    method, ident = message.get('method'), message.get('id')
    if ident is None:
        return None
    if method == 'initialize':
        asked = (message.get('params') or {}).get('protocolVersion')
        result = {'protocolVersion': asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSION,
                  'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'kuttaka', 'version': '0.1.0'}}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': TOOLS}
    elif method in EMPTY_LISTS:
        result = {EMPTY_LISTS[method]: []}   # a tools-only server: clients that ask anyway get an empty list
    elif method == 'tools/call':
        params = message.get('params') or {}
        name, arguments = params.get('name'), params.get('arguments') or {}
        try:
            if name == 'solve':
                body = service.solve(arguments)
            elif name == 'stats':
                body = service.stats()
            elif name == 'library':
                body = service.library()
            else:
                return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': f'unknown tool {name}'}}
            result = {'content': [{'type': 'text', 'text': json.dumps(body, default=str)}],
                      'isError': name == 'solve' and body.get('status') != 'checked'}
        except ValueError as error:
            result = {'content': [{'type': 'text', 'text': str(error)}], 'isError': True}
        except Exception as error:   # the endpoint or the sandbox failed: report it, keep serving
            result = {'content': [{'type': 'text', 'text': f'{type(error).__name__}: {error}'[:500]}],
                      'isError': True}
    else:
        return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': f'unknown method {method}'}}
    return {'jsonrpc': '2.0', 'id': ident, 'result': result}


def serve_mcp(service, stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}
        else:
            reply = mcp_reply(service, message)
        if reply is not None:
            stdout.write(json.dumps(reply) + '\n')
            stdout.flush()


def main(argv=None):
    p = argparse.ArgumentParser(prog='kuttaka-serve', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('mode', choices=['http', 'mcp'])
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--workspace')
    p.add_argument('--model')
    p.add_argument('--base-url')
    p.add_argument('--protocol', choices=['ollama', 'openai'])
    p.add_argument('--key-env', help='name of the environment variable holding the API key, never the key')
    p.add_argument('--network-secret', action='store_true')
    p.add_argument('--max-calls', type=int, help='hard per-task limit (default 80)')
    p.add_argument('--max-executions', type=int)
    p.add_argument('--rounds', type=int, help='rounds per task until checked (default 1)')
    args = p.parse_args(argv)
    config = settings(model=args.model, base_url=args.base_url, protocol=args.protocol, key_env=args.key_env,
                      network_secret=args.network_secret or None, max_calls=args.max_calls,
                      max_executions=args.max_executions, rounds=args.rounds)
    config.network_secret = bool(config.key_env) and bool(config.network_secret)
    if not config.model:
        p.error('set --model or KUTTAKA_MODEL')
    if not preflight()['ready']:
        print('Docker sandbox not ready; methods run only inside it.', file=sys.stderr)
        return 2
    service = Service(config, Workspace(args.workspace or os.environ.get('KUTTAKA_HOME', '~/.kuttaka')))
    if args.mode == 'mcp':
        serve_mcp(service)
        return 0
    server = ThreadingHTTPServer((args.host, args.port), http_handler(service))
    print(f'kuttaka serving on http://{args.host}:{args.port}  (POST /solve, GET /stats)', file=sys.stderr)
    server.serve_forever()


if __name__ == '__main__':
    sys.exit(main())
