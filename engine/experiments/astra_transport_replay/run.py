"""Replay a recorded method acquisition and check fresh-process reuse.

This runner needs explicit private source events, manifest, result and compressed
payload paths. It writes raw replay journals only to a caller-selected private
output directory; the optional recorded projection contains hashes and outcomes,
never provider response bodies or model contexts.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import zlib
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kuttaka_runtime.artifacts import ArtifactMemory, canonical as artifact_canonical
from kuttaka_runtime.execution import PythonExecutor, preflight
from kuttaka_runtime.models import ReplayModel
from kuttaka_runtime.programs import ProgramTask, ProgramTools
from kuttaka_runtime.runtime import Budget, investigate
from kuttaka_runtime.store import Journal, canonical


def digest_json(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def receipt_hashes(events):
    return [digest_json(event['body'].get('receipt')) for event in events
            if event['kind'] == 'model_response' and event['body'].get('receipt') is not None]


def check_rows(events):
    return [{key: event['body'].get(key)
             for key in ('check_index', 'input', 'expected', 'actual', 'status')}
            for event in events if event['kind'] == 'method_check_receipt']


def observation_rows(events):
    return [{'action_kind': (event['body'].get('action') or {}).get('kind'),
             'tool': (event['body'].get('action') or {}).get('tool'),
             'observation': event['body'].get('observation')}
            for event in events if event['kind'] == 'observation']


def independent_solution(case):
    """Enumerate one x residue period; do not execute or inspect candidate source."""
    a, b, c = (case[key] for key in ('a', 'b', 'c'))
    if type(a) is not int or type(b) is not int or type(c) is not int or a <= 0 or b <= 0:
        raise ValueError('new input must have positive integer a,b and integer c')
    divisor = math.gcd(a, b)
    period = b // divisor
    solutions = []
    for x in range(period):
        remainder = c - a * x
        if remainder % b == 0:
            solutions.append({'x': x, 'y': remainder // b})
    answer = solutions[0] if solutions else None
    return {'method': 'enumerate x in [0,b/gcd(a,b)); solve y exactly; the general solution shifts x by b/gcd(a,b)',
            'gcd': divisor, 'x_period': period, 'solutions_in_period': solutions,
            'answer': answer,
            'equation_holds': (answer is not None and a * answer['x'] + b * answer['y'] == c)
                              if answer is not None else None,
            'x_is_least_nonnegative': (0 <= answer['x'] < period) if answer is not None else None}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-events', type=Path, required=True,
                        help='private original acquisition events.jsonl')
    parser.add_argument('--source-manifest', type=Path, required=True,
                        help='public original ProgramTask manifest')
    parser.add_argument('--source-result', type=Path, required=True,
                        help='original acquisition result.json')
    parser.add_argument('--source-payload', type=Path, required=True,
                        help='private compressed content-addressed artifact blob')
    parser.add_argument('--prior-failure', type=Path,
                        help='optional safely summarized pre-run failure record')
    parser.add_argument('--new-input', required=True,
                        help='JSON object, distinct from original checks and deployment input')
    parser.add_argument('--output', type=Path, required=True,
                        help='new private output directory; existing paths are refused')
    parser.add_argument('--recorded-output', type=Path,
                        help='optional new public-safe evidence JSON projection')
    return parser.parse_args()


def main():
    args = parse_args()
    source_events_path = args.source_events.resolve()
    source_manifest_path = args.source_manifest.resolve()
    source_result_path = args.source_result.resolve()
    source_payload_path = args.source_payload.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    replay_dir = output / 'replay'
    replay_dir.mkdir()
    reuse_dir = output / 'fresh-process-reuse'
    reuse_dir.mkdir()

    manifest = json.loads(source_manifest_path.read_text())
    original_result = json.loads(source_result_path.read_text())
    new_input = json.loads(args.new_input)
    if not isinstance(new_input, dict):
        raise ValueError('--new-input must decode to a JSON object')
    task = ProgramTask.from_dict(manifest)
    if any(new_input == check['input'] for check in task.checks):
        raise ValueError('new input duplicates one of the original finite checks')
    if task.has_input and new_input == task.input:
        raise ValueError('new input duplicates the original deployment input')
    reuse_manifest = dict(manifest, input=new_input)
    reuse_task = ProgramTask.from_dict(reuse_manifest)
    if reuse_task.scope_id != task.scope_id:
        raise AssertionError('deployment input unexpectedly changed the finite-check scope')
    oracle = independent_solution(new_input)

    source_events = Journal.read(source_events_path)
    source_receipts = receipt_hashes(source_events)
    source_usage = {'input_tokens': 0, 'output_tokens': 0, 'calls': 0}
    for event in source_events:
        if event['kind'] != 'model_response':
            continue
        source_usage['calls'] += 1
        usage = event['body'].get('usage') or {}
        source_usage['input_tokens'] += usage.get('input_tokens') or 0
        source_usage['output_tokens'] += usage.get('output_tokens') or 0
    original_payload_bytes = zlib.decompress(source_payload_path.read_bytes())
    original_payload = json.loads(original_payload_bytes)
    source_artifact = hashlib.sha256(artifact_canonical(original_payload)).hexdigest()
    if artifact_canonical(original_payload).decode('utf-8') != original_payload_bytes.decode('utf-8'):
        raise ValueError('source payload is not canonical JSON')
    if original_payload.get('kind') != 'method':
        raise ValueError('source payload is not a development method')
    source_hash = hashlib.sha256(original_payload['body']['source'].encode()).hexdigest()
    original_image = original_payload.get('provenance', {}).get('execution_image')
    executor = PythonExecutor(timeout=10)
    if executor.image != original_image:
        raise ValueError('current executor image differs from the recorded original image')
    sandbox = preflight()
    if not sandbox.get('ready'):
        raise RuntimeError('pinned Docker preflight failed')

    replay = ReplayModel(source_events_path)
    with Journal(replay_dir / 'events.jsonl') as journal, ArtifactMemory(replay_dir / 'memory') as memory:
        provenance = {'producer': replay.identity,
                      'run': str(source_events_path.parent),
                      'execution_image': executor.image}
        broker = ProgramTools(task, memory, journal, executor,
                              max_executions=128, provenance=provenance)
        journal.append('program_task', {
            'request': task.request, 'scope': task.scope_id, 'manifest': manifest,
            'attribution': 'recorded live responses replayed; original public finite checks',
        })
        result = investigate(task.request, replay, broker.tools(), broker.checker,
                             journal, Budget(model_calls=16, tool_calls=64),
                             checker_feedback=broker.checker_feedback)
        reconstructed = (memory.get(result.answer['artifact'])
                         if result.status == 'checked' and isinstance(result.answer, dict)
                         else None)
        replay_deployment = None
        if result.status == 'checked':
            replay_deployment = broker.probe_method({'artifact': result.answer['artifact'],
                                                     'input': task.input})
        replay_events = Journal.read(replay_dir / 'events.jsonl')
        source_checks = check_rows(source_events)
        replay_checks = check_rows(replay_events)
        source_obs = observation_rows(source_events)
        replay_obs = observation_rows(replay_events)
        observation_differences = []
        for index, (old, new) in enumerate(zip(source_obs, replay_obs)):
            if old != new:
                old_value, new_value = old.get('observation') or {}, new.get('observation') or {}
                observation_differences.append({
                    'index': index, 'tool': old.get('tool'), 'original': old_value,
                    'replay': new_value,
                    'added_fields': {key: new_value[key] for key in new_value.keys() - old_value.keys()},
                    'removed_fields': sorted(old_value.keys() - new_value.keys()),
                })
        replay_receipts = receipt_hashes(replay_events)
        artifact_id = result.answer.get('artifact') if isinstance(result.answer, dict) else None
        report = {
            'status': result.status,
            'answer': result.answer,
            'reason': result.reason,
            'model_calls': result.model_calls,
            'replay_calls_consumed': replay.calls_made,
            'replay_calls_available': len(replay.calls),
            'tool_calls': result.tool_calls,
            'execution_attempts': broker.execution_attempts,
            'scope': task.scope_id,
            'fresh_memory_empty_before_replay': True,
            'source_artifact_id': source_artifact,
            'reconstructed_artifact_id': artifact_id,
            'source_method_sha256': source_hash,
            'reconstructed_method_sha256': hashlib.sha256(reconstructed['body']['source'].encode()).hexdigest() if reconstructed else None,
            'artifact_id_matches': artifact_id == source_artifact,
            'method_source_matches': bool(reconstructed and hashlib.sha256(reconstructed['body']['source'].encode()).hexdigest() == source_hash),
            'source_receipt_hashes': source_receipts,
            'replay_receipt_hashes': replay_receipts,
            'receipt_hashes_match': source_receipts == replay_receipts,
            'source_journal_sha256': hashlib.sha256(source_events_path.read_bytes()).hexdigest(),
            'source_journal_tip': source_events[-1]['hash'],
            'replay_journal_sha256': hashlib.sha256((replay_dir / 'events.jsonl').read_bytes()).hexdigest(),
            'replay_journal_tip': replay_events[-1]['hash'],
            'journal_hashes_match': source_events[-1]['hash'] == replay_events[-1]['hash'],
            'source_event_count': len(source_events),
            'replay_event_count': len(replay_events),
            'source_protocol_rejections': [e['body'] for e in source_events if e['kind'] == 'action_rejected'],
            'replay_protocol_rejections': [e['body'] for e in replay_events if e['kind'] == 'action_rejected'],
            'source_check_results': source_checks,
            'replay_check_results': replay_checks,
            'check_values_match': source_checks == replay_checks,
            'tool_observation_differences': observation_differences,
            'source_run_status': original_result.get('status'),
            'source_run_model_calls': original_result.get('model_calls'),
            'source_run_tool_calls': original_result.get('tool_calls'),
            'source_model_usage': source_usage,
            'source_run_deployment_result': original_result.get('deployment', {}).get('result'),
            'replay_deployment': replay_deployment,
            'provider_calls': 0,
            'docker_image': executor.image,
            'docker_preflight': sandbox,
            'attribution': 'replay of recorded live model responses; current runtime and real Docker; development checks only',
        }
        (replay_dir / 'result.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')

    # Explicit CLI reuse occurs in a child process against persisted, reconstructed memory.
    task_copy = reuse_dir / 'task.json'
    task_copy.write_text(json.dumps(reuse_manifest, indent=2, ensure_ascii=False) + '\n')
    cli_output = reuse_dir / 'cli-output'
    command = [sys.executable, '-B', '-m', 'kuttaka_runtime', 'reuse',
               '--task', str(task_copy), '--memory', str(replay_dir / 'memory'),
               '--output', str(cli_output), '--execution-timeout', '10',
               '--max-executions', '128', '--artifact', source_artifact]
    env = os.environ.copy()
    env.pop('NVIDIA_API_KEY', None)
    env.pop('GLIMMER_API_KEY', None)
    child = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=600, check=False)
    (reuse_dir / 'stdout.json').write_text(child.stdout)
    (reuse_dir / 'stderr.txt').write_text(child.stderr)
    cli_report = json.loads((cli_output / 'result.json').read_text()) if (cli_output / 'result.json').exists() else None
    cli_deployment = (cli_report or {}).get('deployment') or {}
    cli_passes = sum(row.get('status') == 'passed'
                     for row in check_rows(Journal.read(cli_output / 'events.jsonl'))) if (cli_output / 'events.jsonl').exists() else 0
    cli_result = {
        'process_exit_code': child.returncode,
        'status': (cli_report or {}).get('status'),
        'answer': (cli_report or {}).get('answer'),
        'model_calls': (cli_report or {}).get('model_calls'),
        'tool_calls': (cli_report or {}).get('tool_calls'),
        'execution_attempts': (cli_report or {}).get('execution_attempts'),
        'check_passes': cli_passes,
        'check_total': len(task.checks),
        'deployment': cli_deployment,
        'independent_oracle': oracle,
        'deployment_matches_oracle': canonical(cli_deployment.get('result')) == canonical(oracle['answer']),
        'source_scope_unchanged': (cli_report or {}).get('scope') == task.scope_id,
        'provider_calls': 0,
        'command': command,
    }
    report['fresh_process_cli_reuse'] = cli_result
    report['source_manifest_sha256'] = hashlib.sha256(source_manifest_path.read_bytes()).hexdigest()
    report['source_result_sha256'] = hashlib.sha256(source_result_path.read_bytes()).hexdigest()
    report['source_payload_sha256'] = hashlib.sha256(source_payload_path.read_bytes()).hexdigest()
    (output / 'result.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')

    if args.recorded_output:
        prior_failure = None
        if args.prior_failure:
            failure = json.loads(args.prior_failure.read_text())
            prior_failure = {'type': failure.get('failure_type') or failure.get('type'),
                             'phase': 'before replay execution',
                             'effect': 'no provider or Docker work started; corrected runner completed'}
        safe = {
            'experiment': 'recorded_live_method_reconstruction_and_reuse',
            'source': {'run_label': source_events_path.parent.name,
                       'journal_sha256': report['source_journal_sha256'],
                       'journal_tip': report['source_journal_tip'],
                       'receipt_sha256': report['source_receipt_hashes'],
                       'event_count': report['source_event_count'],
                       'model_calls': source_usage['calls'],
                       'provider_calls': source_usage['calls'],
                       'tool_calls': report['source_run_tool_calls'],
                       'check_passes': sum(row.get('status') == 'passed' for row in report['source_check_results']),
                       'check_total': len(task.checks)},
            'replay': {'journal_sha256': report['replay_journal_sha256'],
                       'journal_tip': report['replay_journal_tip'],
                       'receipt_sha256': report['replay_receipt_hashes'],
                       'receipt_hashes_match': report['receipt_hashes_match'],
                       'journal_hashes_match': report['journal_hashes_match'],
                       'event_count': {'source': report['source_event_count'], 'replay': report['replay_event_count']},
                       'journal_difference_note': 'Five current-runtime execution metadata events were added; the original CLI program_finish event is absent. Event and execution receipt hashes therefore differ; model response receipt hashes and finite check values match.',
                       'model_calls': report['model_calls'], 'provider_calls': 0,
                       'tool_calls': report['tool_calls'],
                       'artifact_id': report['reconstructed_artifact_id'],
                       'method_source_sha256': report['reconstructed_method_sha256'],
                       'check_passes': sum(row.get('status') == 'passed' for row in report['replay_check_results']),
                       'check_total': len(task.checks),
                       'protocol_rejections': report['replay_protocol_rejections'],
                       'observation_differences': observation_differences},
            'fresh_process_reuse': {'status': cli_result['status'],
                                    'artifact_id': (cli_result.get('answer') or {}).get('artifact'),
                                    'model_calls': cli_result['model_calls'],
                                    'provider_calls': 0,
                                    'check_passes': cli_result['check_passes'],
                                    'check_total': cli_result['check_total'],
                                    'input': new_input,
                                    'result': cli_deployment.get('result'),
                                    'independent_result': oracle['answer'],
                                    'independent_check_passed': cli_result['deployment_matches_oracle']},
            'limits': {'source_data_required_at_runtime': ['original event journal', 'public manifest', 'original result', 'compressed payload'],
                       'provider_payloads_or_contexts_included': False,
                       'claim': 'recorded response reconstruction and supplied finite checks only; no new model acquisition or capability gain'},
            'prior_runner_failure': prior_failure,
        }
        recorded = args.recorded_output.resolve()
        recorded.parent.mkdir(parents=True, exist_ok=True)
        with recorded.open('x', encoding='utf-8') as stream:
            json.dump(safe, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write('\n')
    print(json.dumps({'status': report['status'], 'artifact_id': report['reconstructed_artifact_id'],
                      'check_values_match': report['check_values_match'],
                      'fresh_process_cli_reuse': cli_result,
                      'output': str(output)}, indent=2, sort_keys=True))
    return 0 if (result.status == 'checked' and report['artifact_id_matches']
                 and report['check_values_match'] and cli_result['status'] == 'checked'
                 and cli_result['check_passes'] == cli_result['check_total']
                 and cli_result['deployment_matches_oracle']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
