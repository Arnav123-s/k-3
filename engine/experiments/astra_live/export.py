"""Prepare a reviewable projection of a completed private development campaign.

Raw provider responses and reasoning remain private. The projected event records
are not a hash-chain journal; source hashes identify the retained originals.
Review all task data before publishing this output.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import zlib

from experiments.astra_live.run import summarize_events
from kuttaka_runtime.store import Journal


EVENTS = {'program_task', 'start', 'model_reply', 'model_error', 'tool_error',
          'action_rejected', 'checker_feedback', 'checker_feedback_error',
          'method_remembered', 'method_check_receipt', 'method_validation',
          'program_execution_precommit', 'program_execution_result',
          'program_execution_blocked', 'reuse_request', 'program_finish'}


def event_projection_text(data):
    """Keep one projected event per line without changing its JSON data."""
    metadata = {key: value for key, value in data.items() if key != 'events'}
    prefix = json.dumps(metadata, indent=2, allow_nan=False)[:-2]
    rows = ',\n'.join('    ' + json.dumps(event, separators=(',', ':'), allow_nan=False)
                     for event in data['events'])
    return prefix + ',\n  "events": [\n' + rows + '\n  ]\n}'


def provider_metadata(event):
    receipt = event['body'].get('receipt') or {}
    raw = receipt.get('body', '')
    metadata = {'status': receipt.get('status'), 'body_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                'body_bytes': len(raw.encode()), 'usage': event['body'].get('usage', {})}
    try:
        response = json.loads(raw)
    except (ValueError, TypeError):
        metadata['body_is_json'] = False
    else:
        metadata.update({key: response.get(key) for key in ('id', 'model', 'created', 'system_fingerprint', 'usage')})
        metadata['finish_reasons'] = [choice.get('finish_reason') for choice in response.get('choices', [])]
    return metadata


def run(private, output):
    private = private.resolve()
    output.mkdir(parents=True, exist_ok=False)
    credential = os.environ.get('NVIDIA_API_KEY')

    def save(path, data, *, event_lines=False):
        serialized = event_projection_text(data) if event_lines else json.dumps(data, indent=2, allow_nan=False)
        if credential and credential in serialized:
            raise ValueError('Configured credential found in publication projection')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized + '\n')

    records = []
    artifacts = set()
    for result_path in sorted(private.rglob('result.json')):
        journal_path = result_path.parent / 'events.jsonl'
        events = Journal.read(journal_path)
        result = json.loads(result_path.read_text())
        relative = result_path.parent.relative_to(private)
        projection = []
        for event in events:
            if event['kind'] == 'model_response':
                body = provider_metadata(event)
            elif event['kind'] in EVENTS:
                body = event['body']
            else:
                continue
            projection.append({'source_seq': event['seq'], 'source_event_hash': event['hash'],
                               'kind': event['kind'], 'body': body})
        record = {'run': str(relative), 'result': result,
                  'evidence': summarize_events(journal_path),
                  'source_journal_sha256': hashlib.sha256(journal_path.read_bytes()).hexdigest()}
        records.append(record)
        save(output / relative / 'events-projection.json', {
            'attribution': 'Reviewed task-event projection; not the original hash-chain journal',
            'source_journal_sha256': record['source_journal_sha256'], 'events': projection}, event_lines=True)
        identifiers = {e['body']['artifact'] for e in events if e['kind'] == 'method_remembered'}
        if result.get('answer'):
            identifiers.add(result['answer']['artifact'])
        for artifact in identifiers:
            if artifact in artifacts:
                continue
            raw = zlib.decompress((Path(result['memory_directory']) / 'blobs' / (artifact + '.json.zlib')).read_bytes())
            if hashlib.sha256(raw).hexdigest() != artifact:
                raise ValueError('Artifact content identity mismatch')
            save(output / 'payloads' / (artifact + '.json'), json.loads(raw))
            artifacts.add(artifact)
    missing_setup = []
    if (private / 'connection.json').exists():
        connection = json.loads((private / 'connection.json').read_text())
        response = connection.get('response', {})
        save(output / 'connection.json', {
            'http_status': connection.get('http_status'), 'elapsed_seconds': connection['elapsed_seconds'],
            'credential_present': connection['credential_present'], 'model': response.get('model'),
            'request': connection['request'], 'usage': response.get('usage'),
            'reply': response.get('choices', [{}])[0].get('message', {}).get('content')})
    else:
        missing_setup.append('connection.json')
    for filename in ('doctor-initial.json', 'doctor-ready.json', 'environment-ready.json'):
        if (private / filename).exists():
            save(output / 'setup' / filename, json.loads((private / filename).read_text()))
        else:
            missing_setup.append(filename)
    save(output / 'results.json', {'live_llm': True, 'training_performed': False,
         'attribution': 'Public authored development tasks and targeted revision, not independent transfer evaluation',
         'private_evidence_directory': str(private), 'records': records,
         'artifact_count': len(artifacts), 'setup_evidence_absent': missing_setup})
    print(json.dumps({'published_projection_runs': len(records), 'artifacts': len(artifacts)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.private, args.output)
