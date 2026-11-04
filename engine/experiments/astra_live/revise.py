"""Challenge a live-acquired method with expanded development checks.

This is a disclosed development revision, not an independent evaluation. The
host supplies new checks but no replacement source or solver to the model.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys

from experiments.astra_live.run import BUDGET, summarize_events, write_json
from kuttaka_runtime.artifacts import ArtifactMemory


def run(campaign, root):
    campaign, root = campaign.resolve(), root.resolve()
    acquired = campaign / 'stable-unique'
    result = json.loads((acquired / 'acquire/result.json').read_text())
    if result['status'] != 'checked':
        raise ValueError('A checked live-acquired stable-unique method is required')
    artifact = result['answer']['artifact']
    # ArtifactMemory leaves this inode in place after closing. Hold its OS lock
    # throughout the snapshot, so a writer cannot start between a check and copy.
    with (acquired / 'memory/.writer.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Wait for the source memory writer to finish') from None
        root.mkdir(parents=True, exist_ok=False)
        shutil.copytree(acquired / 'memory', root / 'memory',
                        ignore=shutil.ignore_patterns('.writer.lock'))
    shutil.copytree(campaign / 'source', root / 'source')
    original = json.loads((acquired / 'acquisition-task.json').read_text())
    expanded = json.loads(json.dumps(original))
    expanded['checks'] += [
        {'input': [[1], [1], [2], [1]], 'expected': [[1], [2]]},
        {'input': [{'x': 1}, {'x': 1}, {'x': 2}], 'expected': [{'x': 1}, {'x': 2}]},
    ]
    expanded['input'] = [[3], {'a': [2]}, [3], None, {'a': [2]}, None]
    write_json(root / 'original-task.json', original)
    write_json(root / 'expanded-task.json', expanded)
    expected = [[3], {'a': [2]}, None]
    write_json(root / 'revision-plan.json', {
        'attribution': 'Expanded authored development checks after seeing a set-based live candidate',
        'started_at': datetime.now(timezone.utc).isoformat(),
        'original_artifact': artifact, 'expected_deployment': expected,
        'budget_per_model_run': BUDGET,
        'source_campaign': str(campaign),
        'child_import_root': str(root / 'source'),
        'no_solution_source_supplied': True})
    results = {}
    for stage in ('challenge', 'revise', 'reuse-revised', 'original-scope'):
        if stage == 'reuse-revised' and results['revise'].get('status') != 'checked':
            continue
        is_run = stage == 'revise'
        task = 'original-task.json' if stage == 'original-scope' else 'expanded-task.json'
        command = [sys.executable, '-B', '-m', 'kuttaka_runtime', 'run' if is_run else 'reuse',
                   '--task', str(root / task), '--memory', str(root / 'memory'),
                   '--output', str(root / stage), '--execution-timeout', '10',
                   '--max-executions', str(BUDGET['max_executions'])]
        if is_run:
            command += ['--profile', 'muse-glimmer']
            for key in ('model_calls', 'tool_calls', 'max_tokens', 'model_timeout'):
                command += ['--' + key.replace('_', '-'), str(BUDGET[key])]
        else:
            selected = results['revise']['answer']['artifact'] if stage == 'reuse-revised' else artifact
            command += ['--artifact', selected]
        write_json(root / (stage + '-command.json'), command)
        with (root / (stage + '-stdout.json')).open('x') as stdout, \
                (root / (stage + '-stderr.txt')).open('x') as stderr:
            process = subprocess.run(command, cwd=root / 'source', stdout=stdout, stderr=stderr, check=False)
        path = root / stage / 'result.json'
        results[stage] = json.loads(path.read_text()) if path.exists() else {'status': 'missing_result'}
        results[stage]['exit_code'] = process.returncode
        journal = root / stage / 'events.jsonl'
        if journal.exists():
            results[stage]['evidence'] = summarize_events(journal)
        if stage in ('revise', 'reuse-revised'):
            results[stage]['deployment_matches_expected'] = (
                results[stage].get('deployment', {}).get('result') == expected)
        write_json(root / 'results.json', results)
        print(json.dumps({'stage': stage, 'status': results[stage]['status'],
                          'exit_code': process.returncode}), flush=True)
    with ArtifactMemory(root / 'memory') as memory:
        from kuttaka_runtime.programs import ProgramTask
        results['original_scope_status'] = memory.status(artifact, ProgramTask.from_dict(original).scope_id)
    write_json(root / 'results.json', results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.campaign, args.output)
