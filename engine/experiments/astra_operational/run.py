"""Exercise the real CLI, HTTP transport, Docker checks, and cross-process reuse.

The local endpoint is a deterministic protocol stub, NOT an LLM. Only its JSON
proposals are scripted; execution, validation, disk persistence and reuse run
through the production components in fresh CLI processes.
"""

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading

from kuttaka_runtime import ArtifactMemory, Journal
from kuttaka_runtime.execution import preflight
from kuttaka_runtime.programs import ProgramTask


GOOD = '''def run(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    print("checked input length", len(values))
    return result
'''


def proposal(context):
    history = context['history']
    if not history:
        return {'kind': 'experiment', 'tool': 'find_methods',
                'input': {'query': 'stable unique', 'limit': 10},
                'predictions': [{'hypothesis': 'memory may contain a checked method', 'outcome': 'method catalog'}]}
    latest = history[-1]
    if latest['action'].get('tool') == 'find_methods':
        matching = [m for m in latest['observation']['methods'] if m['status'] == 'qualified']
        if matching:
            return {'kind': 'answer', 'answer': {'artifact': matching[0]['id']}}
        source = 'def run(values):\n    return values\n'
        name = 'stable unique initial candidate'
    elif latest['action'].get('tool') == 'remember_method':
        return {'kind': 'answer', 'answer': {'artifact': latest['observation']['artifact']}}
    elif latest['action']['kind'] == 'answer' and latest['observation'] is False:
        source, name = GOOD, 'stable unique repaired candidate'
    else:
        return {'kind': 'unresolved', 'reason': 'unexpected protocol-stub state'}
    return {'kind': 'experiment', 'tool': 'remember_method',
            'input': {'name': name, 'description': 'Remove repeated values, preserving first occurrence', 'source': source},
            'predictions': [{'hypothesis': 'this candidate implements stable uniqueness', 'outcome': 'stored method ID'}]}


def run(root):
    readiness = preflight()
    if not readiness['ready']:
        raise RuntimeError('Local Docker and the pinned image are required')
    root.mkdir(parents=True, exist_ok=False)
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            context = json.loads(body['messages'][-1]['content'])
            action = proposal(context)
            requests.append({'context': context, 'action': action})
            response = json.dumps({'choices': [{'message': {'content': json.dumps(action)}}],
                                   'usage': {}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_):
            pass

    task = {'request': 'Write run(values) that removes repeated JSON values while preserving their first occurrence.',
            'context': {'input': 'JSON list', 'equality': 'Python equality for these supplied cases'},
            'checks': [{'input': [3, 3, 1, 3], 'expected': [3, 1]},
                       {'input': [], 'expected': []},
                       {'input': ['a', 'b', 'a'], 'expected': ['a', 'b']}],
            'input': [9, 7, 9, 5]}
    task_path = root / 'task.json'
    task_path.write_text(json.dumps(task, indent=2) + '\n')
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f'http://127.0.0.1:{server.server_port}/v1'
    base = [sys.executable, '-B', '-m', 'kuttaka_runtime']
    shared = ['--task', str(task_path), '--memory', str(root / 'memory'), '--execution-timeout', '10']
    model = ['--base-url', endpoint, '--model', 'authored-protocol-stub', '--no-api-key']
    results = {}
    try:
        for stage in ('cold', 'warm'):
            before = len(requests)
            completed = subprocess.run(base + ['run'] + shared + model + ['--output', str(root / stage)],
                                       capture_output=True, text=True, timeout=180)
            if completed.returncode != 0:
                raise AssertionError(f'{stage} CLI failed: {completed.stdout} {completed.stderr}')
            result = json.loads(completed.stdout)
            assert result['status'] == 'checked', result
            assert result['deployment']['result'] == [9, 7, 5], result
            result['observed_http_requests'] = len(requests) - before
            results[stage] = result
        artifact = results['cold']['answer']['artifact']
        assert results['warm']['answer']['artifact'] == artifact
        assert results['warm']['model_calls'] < results['cold']['model_calls']
        task['input'] = ['x', 'x', 'y']
        task_path.write_text(json.dumps(task, indent=2) + '\n')
        before = len(requests)
        completed = subprocess.run(base + ['reuse'] + shared + ['--artifact', artifact, '--output', str(root / 'reuse')],
                                   capture_output=True, text=True, timeout=180)
        assert completed.returncode == 0, completed.stdout
        results['reuse'] = json.loads(completed.stdout)
        assert results['reuse']['deployment']['result'] == ['x', 'y']
        assert results['reuse']['model_calls'] == 0 and len(requests) == before
        original_scope = ProgramTask.from_dict(task).scope_id
        task['checks'] = [{'input': [3, 3, 1], 'expected': [1, 3, 3]}]
        task['request'] = 'Return the input values in sorted order with duplicates retained.'
        task_path.write_text(json.dumps(task, indent=2) + '\n')
        completed = subprocess.run(base + ['reuse'] + shared + ['--artifact', artifact, '--output', str(root / 'wrong-contract')],
                                   capture_output=True, text=True, timeout=180)
        assert completed.returncode == 1, completed.stdout
        results['wrong_contract'] = json.loads(completed.stdout)
        assert results['wrong_contract']['status'] == 'incomplete'
        assert 'deployment' not in results['wrong_contract']
        with ArtifactMemory(root / 'memory') as memory:
            assert memory.status(artifact, original_scope) == 'qualified'
        for stage in ('cold', 'warm', 'reuse', 'wrong-contract'):
            Journal.read(root / stage / 'events.jsonl')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    result = {'attribution': 'scripted protocol endpoint; real CLI subprocesses, HTTP, Docker execution and persistent memory',
              'live_llm': False, 'results': results,
              'verified': ['bad candidate rejected before repair', 'same artifact reused in a fresh run',
                           'explicit reuse makes no model calls', 'new input executed',
                           'wrong contract rejected without deployment', 'original scope preserved',
                           'all four evidence journals verify']}
    (root / 'protocol-requests.json').write_text(json.dumps(requests, indent=2) + '\n')
    (root / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    run(parser.parse_args().output)
