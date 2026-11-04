"""Live development acquisition, retained-memory retrieval, and explicit reuse.

No solver is supplied to the model. These public, authored development cases
are not a sealed benchmark and do not establish transfer or investigator learning.
Each invocation uses a new evidence directory; failures never trigger retries.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from kuttaka_runtime.store import Journal, canonical
from kuttaka_runtime.execution import IMAGE


TASKS = ('stable-unique', 'integer-equation', 'elastic-collision', 'grid-path')
# Authored deployment checks, fixed before acquisition and excluded from prompts.
# They are extra development evidence, not independent held-out evaluation.
DEPLOYMENTS = {
    'stable-unique': {'input': ['z', 'x', 'z', 'y', 'x'], 'expected': ['z', 'x', 'y']},
    'integer-equation': {'input': {'a': 12, 'b': 35, 'c': -5},
                         'expected': {'x': 20, 'y': -7}},
    'elastic-collision': {'input': {'m1': 2, 'm2': 5, 'u1': 3, 'u2': -4},
                          'expected': {'v1': [-7, 1], 'v2': [0, 1]}},
    'grid-path': {'input': {'rows': 3, 'cols': 4, 'start': [2, 0], 'goal': [0, 3],
                            'blocked': [[1, 1], [1, 2]]},
                  'expected': [[2, 0], [1, 0], [0, 0], [0, 1], [0, 2], [0, 3]]},
}
BUDGET = {'model_calls': 16, 'tool_calls': 64, 'max_executions': 128,
          'max_tokens': 8192, 'model_timeout': 180, 'execution_timeout': 10}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def summarize_events(path):
    events = Journal.read(path)
    responses = [e['body'] for e in events if e['kind'] == 'model_response']
    usage = [r.get('usage', {}) for r in responses]
    return {
        'journal_verified': True,
        'journal_tip': events[-1]['hash'] if events else None,
        'model_responses': len(responses),
        'usage': {key: sum(u[key] for u in usage if type(u.get(key)) is int)
                  for key in ('input_tokens', 'output_tokens')},
        'usage_complete': all(type(u.get(key)) is int
                              for u in usage for key in ('input_tokens', 'output_tokens'))
                          and len(responses) == sum(e['kind'] == 'model_attempt' for e in events),
        'errors': [{'kind': e['kind'], 'body': e['body']} for e in events
                   if e['kind'] in ('model_error', 'tool_error', 'action_rejected')],
        'validation_statuses': [e['body']['status'] for e in events
                                if e['kind'] == 'method_validation'],
    }


def invoke(root, task, stage, artifact=None):
    task_root = root / task
    manifest = task_root / ('acquisition-task.json' if stage == 'acquire' else 'deployment-task.json')
    command = [sys.executable, '-B', '-m', 'kuttaka_runtime',
               'reuse' if stage == 'reuse' else 'run', '--task', str(manifest),
               '--memory', str(task_root / 'memory'), '--output', str(task_root / stage),
               '--max-executions', str(BUDGET['max_executions']),
               '--execution-timeout', str(BUDGET['execution_timeout'])]
    if stage == 'reuse':
        command += ['--artifact', artifact]
    else:
        command += ['--profile', 'muse-glimmer']
        for key in ('model_calls', 'tool_calls', 'max_tokens', 'model_timeout'):
            command += ['--' + key.replace('_', '-'), str(BUDGET[key])]
    entry = {'task': task, 'stage': stage, 'command': command,
             'started_at': datetime.now(timezone.utc).isoformat()}
    write_json(task_root / (stage + '-invocation.json'), entry)
    started = time.monotonic()
    with (task_root / (stage + '-stdout.json')).open('x') as stdout, \
            (task_root / (stage + '-stderr.txt')).open('x') as stderr:
        completed = subprocess.run(command, cwd=root / 'source', stdout=stdout, stderr=stderr, check=False)
    entry.update(exit_code=completed.returncode, elapsed_seconds=round(time.monotonic() - started, 3))
    result_path = task_root / stage / 'result.json'
    if result_path.exists():
        entry['result'] = json.loads(result_path.read_text())
    else:
        entry['result_missing'] = True
    journal = task_root / stage / 'events.jsonl'
    if journal.exists():
        try:
            entry['evidence'] = summarize_events(journal)
        except (ValueError, OSError) as error:
            entry['evidence'] = {'journal_verified': False, 'error_type': type(error).__name__}
    if stage != 'acquire':
        delivered = entry.get('result', {}).get('deployment', {})
        entry['deployment_matches_expected'] = (delivered.get('status') == 'completed'
            and canonical(delivered.get('result')) == canonical(DEPLOYMENTS[task]['expected']))
        entry['same_acquired_artifact'] = entry.get('result', {}).get('answer', {}) == {'artifact': artifact}
    write_json(task_root / (stage + '-invocation.json'), entry)
    print(json.dumps({'task': task, 'stage': stage, 'exit_code': completed.returncode,
                      'status': entry.get('result', {}).get('status', 'missing'),
                      'elapsed_seconds': entry['elapsed_seconds']}), flush=True)
    return entry


def run(root, workers):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    source_root = root / 'source' / 'kuttaka_runtime'
    source_root.mkdir(parents=True)
    for source in (repo / 'kuttaka_runtime').glob('*.py'):
        (source_root / source.name).write_bytes(source.read_bytes())
    (root / 'source' / 'run.py').write_bytes(Path(__file__).read_bytes())
    manifests = {}
    for task in TASKS:
        task_root = root / task
        task_root.mkdir()
        manifest = json.loads((repo / 'examples' / 'astra' / (task + '.json')).read_text())
        write_json(task_root / 'acquisition-task.json', manifest)
        manifests[task] = hashlib.sha256(canonical(manifest).encode()).hexdigest()
        manifest['input'] = DEPLOYMENTS[task]['input']
        write_json(task_root / 'deployment-task.json', manifest)
    campaign = {'live_llm': True, 'model': 'meta/muse-glimmer-30b',
                'profile': 'muse-glimmer', 'budget_per_model_run': BUDGET,
                'execution_image': IMAGE,
                'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'child_import_root': str(root / 'source'),
                'workers': workers, 'task_manifest_sha256': manifests,
                'deployments': DEPLOYMENTS,
                'attribution': 'Public authored development tasks; no independent transfer claim',
                'stages': ['acquire with fresh per-task memory',
                           'warm with retained memory and the same model/tool/execution budgets',
                           'reuse exact acquired artifact with zero model calls'],
                'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
                'source_files_sha256': {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in sorted((repo / 'kuttaka_runtime').glob('*.py'))}}
    write_json(root / 'campaign.json', campaign)
    summary = {'campaign': campaign, 'runs': []}
    # Finish all four acquisition attempts before the success-dependent stages.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {task: pool.submit(invoke, root, task, 'acquire') for task in TASKS}
        for task, future in futures.items():
            summary['runs'].append(future.result())
            write_json(root / 'summary.json', summary)
    acquired = {entry['task']: entry['result']['answer']['artifact'] for entry in summary['runs']
                if entry.get('result', {}).get('status') == 'checked'}
    for stage in ('warm', 'reuse'):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {task: pool.submit(invoke, root, task, stage, artifact)
                       for task, artifact in acquired.items()}
            for task, future in futures.items():
                summary['runs'].append(future.result())
                write_json(root / 'summary.json', summary)
    summary['skipped_followups'] = [task for task in TASKS if task not in acquired]
    write_json(root / 'summary.json', summary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New private evidence directory')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    run(args.output, args.workers)
