"""Publish a reviewable evidence projection, retaining original journals privately."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from kuttaka_runtime.store import Journal
from .acquire import write_json


PRIVATE_EVENT_KINDS = {
    'model_context', 'model_attempt', 'policy_attempt',
    'policy_execution_receipt', 'sandbox_receipt', 'final_scoring',
}


def _usage(value):
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in ('input_tokens', 'output_tokens')
            if type(value.get(key)) is int}


_PRIVATE_PROJECTION_KEYS = {
    'rationale', 'reasoning', 'chain_of_thought', 'expected', 'expected_output',
    'expected_outputs', 'hidden_expected', 'hidden_expected_output',
    'hidden_expected_outputs', 'case_results', 'private_checks', 'scoring_code',
    'api_key', 'authorization', 'access_token', 'credential', 'credentials',
}


def _sanitize(value):
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()
                if key.lower() not in _PRIVATE_PROJECTION_KEYS}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _response_metadata(receipt):
    receipt = receipt if isinstance(receipt, dict) else {}
    raw = receipt.get('body', '')
    raw_bytes = raw.encode() if isinstance(raw, str) else b''
    result = {'response_body_sha256': hashlib.sha256(raw_bytes).hexdigest(),
              'response_body_bytes': len(raw_bytes)}
    if type(receipt.get('status')) is int:
        result['http_status'] = receipt['status']
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        if isinstance(parsed.get('model'), str):
            result['provider_model'] = parsed['model'][:160]
        choices = parsed.get('choices')
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            if isinstance(choices[0].get('finish_reason'), str):
                result['finish_reason'] = choices[0]['finish_reason'][:80]
    return result


def project_event(event):
    kind, body = event['kind'], event['body']
    if kind == 'model_response':
        body = {'usage': _usage(body.get('usage')), **_response_metadata(body.get('receipt'))}
    elif kind in PRIVATE_EVENT_KINDS:
        return None
    elif kind == 'model_reply':
        # Keep a provenance marker without publishing provider-generated prose,
        # candidate source, or reasoning. Reviewed artifacts are exported below.
        body = {'reply_sha256': hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'field_names': sorted(body) if isinstance(body, dict) else []}
    elif kind == 'policy_result':
        action = _sanitize(body.get('action') or {})
        body = {'action': action, 'state_sha256': hashlib.sha256(json.dumps(body.get('state'), sort_keys=True).encode()).hexdigest()}
    elif kind == 'investigator_finish':
        body = {key: body[key] for key in ('status', 'reason', 'model_calls',
                'model_input_tokens', 'model_output_tokens', 'model_tokens_known',
                'policy_executions', 'steps', 'tool_calls') if key in body}
    elif kind == 'experiment_manifest':
        body = {key: body[key] for key in ('arm', 'family', 'seed', 'limits',
                'policy_artifact', 'source_observations_or_solvers_imported',
                'starting_memory_artifacts', 'task_hashes') if key in body}
    elif kind == 'experiment_finish':
        body = {key: body[key] for key in ('arm', 'family', 'seed', 'costs',
                'elapsed_seconds', 'memory_stats', 'result', 'score',
                'reported_usage', 'journal_tip', 'journal_sha256') if key in body}
        result = body.get('result')
        if isinstance(result, dict):
            body['result'] = {key: result[key] for key in ('status', 'model_calls',
                'tool_calls', 'policy_executions', 'steps', 'model_input_tokens',
                'model_output_tokens', 'model_tokens_known', 'reason') if key in result}
        score = body.get('score')
        if isinstance(score, dict):
            body['score'] = {key: score[key] for key in ('submitted', 'status',
                'passed', 'correct_cases', 'total_cases', 'unobserved_cases',
                'unobserved_correct') if key in score}
    elif kind == 'finish' and 'usage' in body:
        body = {key: body[key] for key in ('status', 'error_type', 'elapsed_seconds',
                                           'timeout_seconds', 'target_exposure') if key in body}
        usage = _usage(event['body'].get('usage'))
        if usage is not None:
            body['usage'] = usage
    return {'source_seq': event['seq'], 'source_event_hash': event['hash'],
            'kind': kind, 'body': _sanitize(body)}


def _stage(relative):
    parts = relative.parts
    # Fixtures may be nested under an acquisition directory. Their scripted
    # model calls must never enter live acquisition cost totals.
    if any(part in ('smoke', 'fixtures') or part.startswith(('smoke-', 'challenge-', 'fixture-'))
           or part.endswith('-threshold-review') for part in parts):
        return 'scripted_fixture'
    if any(part.startswith(('acquisition-', 'alternative-', 'model-design-', 'substantive-'))
           for part in parts):
        return 'acquisition'
    if any(part.startswith(('qualification-', 'source-diagnostic-')) or
           part.endswith('-qualification') for part in parts):
        return 'source_qualification'
    if any(_is_target_run_part(part) for part in parts):
        return 'target_campaign'
    return 'development_evidence'


def _is_target_run_part(part):
    pieces = part.rsplit('-', 2)
    return len(pieces) == 3 and pieces[1].isdigit() and pieces[2] in {
        'ordinary', 'policy', 'retrieval', 'removed', 'restored'}


def _journal_usage(events):
    responses = [event['body'] for event in events if event['kind'] == 'model_response']
    attempts = sum(event['kind'] == 'model_attempt' for event in events)
    totals = {'input_tokens': 0, 'output_tokens': 0}
    complete = attempts == len(responses)
    for response in responses:
        usage = _usage(response.get('usage')) or {}
        for key in totals:
            value = usage.get(key)
            if type(value) is int:
                totals[key] += value
            else:
                complete = False
    return {'model_attempts': attempts, 'model_responses': len(responses),
            'reported_usage': {**totals, 'complete': complete}}


def _file_digest(path):
    payload = path.read_bytes()
    return {'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}


def _safe_result_status(path):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    status = value.get('status') if isinstance(value, dict) else None
    return status if isinstance(status, str) else None


def _project_individual_attempts(private, output):
    records = []
    for profile_path in sorted(private.rglob('profile.json')):
        folder = profile_path.parent
        relative = folder.relative_to(private)
        if not any(part.startswith('substantive-') for part in relative.parts):
            continue
        request_path = folder / 'request.json'
        if not request_path.exists() or (folder / 'events.jsonl').exists():
            continue

        filenames = ('request.json', 'response.json', 'receipt.json',
                     'profile.json', 'result.json', 'failure.json',
                     'evidence-summary.json', 'blocked-attempt.json',
                     'usage.json', 'candidate.json', 'candidate.py')
        file_hashes = {name: _file_digest(folder / name) for name in filenames
                       if (folder / name).is_file()}
        target = output / relative
        target.mkdir(parents=True, exist_ok=True)
        safe_json = ('profile.json', 'result.json', 'failure.json',
                     'evidence-summary.json', 'blocked-attempt.json',
                     'usage.json', 'candidate.json')
        for filename in safe_json:
            source = folder / filename
            if not source.exists():
                continue
            try:
                raw_value = json.loads(source.read_text())
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if filename == 'failure.json' and isinstance(raw_value, dict):
                projected = {key: raw_value[key] for key in
                             ('type', 'policy', 'status', 'error_type', 'timeout_seconds')
                             if key in raw_value}
            else:
                projected = _sanitize(raw_value)
            write_json(target / filename, projected)
        candidate_source = folder / 'candidate.py'
        if candidate_source.is_file():
            (target / 'candidate.py').write_bytes(candidate_source.read_bytes())
        receipt_path = folder / 'receipt.json'
        if receipt_path.is_file():
            write_json(target / 'provider-response-metadata.json',
                       _response_metadata(json.loads(receipt_path.read_text())))

        usage = {}
        usage_path = folder / 'usage.json'
        if usage_path.exists():
            try:
                usage = _usage(json.loads(usage_path.read_text())) or {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        response_count = int((folder / 'response.json').is_file() or
                             (folder / 'receipt.json').is_file())
        usage_complete = (response_count == 1 and
                          type(usage.get('input_tokens')) is int and
                          type(usage.get('output_tokens')) is int)
        failure_path = folder / 'failure.json'
        failure_type = None
        if failure_path.exists():
            failure = json.loads(failure_path.read_text())
            failure_type = failure.get('type') or failure.get('error_type')
        records.append({
            'run': str(relative), 'stage': 'acquisition',
            'evidence_format': 'individual_file_hashes',
            'journal_chain_verified': False,
            'model_attempts': 1, 'model_responses': response_count,
            'reported_usage': {
                'input_tokens': usage.get('input_tokens', 0),
                'output_tokens': usage.get('output_tokens', 0),
                'complete': usage_complete,
            },
            'status': _safe_result_status(folder / 'result.json') or
                      ('incomplete' if failure_path.exists() else None),
            'failure_type': failure_type,
            'result_missing': not (folder / 'result.json').is_file(),
            'file_hashes': file_hashes,
        })
    return records


def _project_scripted_fixtures(private, output):
    records = []
    for source in sorted(private.glob('substantive-*/fixture-results/*.json')):
        relative = source.relative_to(private)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        value = _sanitize(json.loads(source.read_text()))
        write_json(target, value)
        records.append({
            'run': str(relative.with_suffix('')),
            'stage': 'scripted_fixture', 'live_model': False,
            'attribution': 'Scripted policy fixture output; no live provider call.',
            'source_sha256': _file_digest(source)['sha256'],
        })
    return records


def export(private, output):
    output.mkdir(parents=True, exist_ok=False)
    credential = os.environ.get('NVIDIA_API_KEY')
    records = []
    for path in sorted(private.rglob('events.jsonl')):
        if 'memory' in path.relative_to(private).parts or 'source' in path.relative_to(private).parts:
            continue
        events = Journal.read(path)
        relative = path.parent.relative_to(private)
        result_path = path.parent / 'result.json'
        result = _sanitize(json.loads(result_path.read_text())) if result_path.exists() else None
        if result is None and path.parent.name.startswith('smoke-'):
            finish = next((e for e in reversed(events) if e['kind'] == 'investigator_finish'), None)
            if finish:
                result = {'attribution': 'Scripted model protocol fixture, actual Docker; not live model evidence.',
                          'derived_from_event': finish['hash'], 'live_model': False, 'result': finish['body']}
        record = {'run': str(relative), 'stage': _stage(relative), 'result': result,
                  'journal_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'journal_tip': events[-1]['hash'] if events else None,
                  'journal_chain_verified': True, 'result_missing': result is None,
                  **_journal_usage(events)}
        records.append(record)
        projection = {'attribution': 'Projection, not original hash chain. Raw provider bodies and full model contexts retained privately.',
                      'source_journal_sha256': record['journal_sha256'],
                      'events': [x for event in events if (x := project_event(event)) is not None]}
        target = output / relative
        target.mkdir(parents=True, exist_ok=True)
        write_json(target / 'events-projection.json', projection)
        if result:
            write_json(target / 'result.json', _sanitize(result))
    records.extend(_project_individual_attempts(private, output))
    records.extend(_project_scripted_fixtures(private, output))
    for path in private.rglob('campaign.json'):
        target = output / path.relative_to(private)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, _sanitize(json.loads(path.read_text())))
    for filename in ('bundle.json', 'candidate.json', 'candidate-review.json',
                     'planned-runs.json', 'bundle-audit.json', 'design.json',
                     'recovered-design.json', 'host-failure.json',
                     'review-correction.json', 'evidence-summary.json',
                     'blocked-attempt.json', 'findings.json'):
        for path in private.rglob(filename):
            if 'source' in path.relative_to(private).parts:
                continue
            target = output / path.relative_to(private)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_json(target, _sanitize(json.loads(path.read_text())))
    for filename in ('environment-ready.json', 'doctor.json'):
        path = private / filename
        if path.exists():
            write_json(output / filename, _sanitize(json.loads(path.read_text())))
    for path in private.glob('focused-tests*.txt'):
        (output / path.name).write_text(path.read_text())
    for path in private.glob('challenge-*/feedback.json'):
        target = output / path.relative_to(private)
        target.parent.mkdir(parents=True, exist_ok=True)
        feedback = json.loads(path.read_text())
        write_json(target, _sanitize(feedback))
        records.append({'run': str(path.parent.relative_to(private)), 'stage': 'scripted_fixture',
                        'attribution': feedback['attribution'], 'live_model': False,
                        'identical_policy_outputs': feedback['identical_policy_outputs'],
                        'source_feedback_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    stage_totals = {}
    for record in records:
        stage = record.get('stage', 'development_evidence')
        total = stage_totals.setdefault(stage, {'records': 0, 'journal_runs': 0,
            'model_attempts': 0, 'model_responses': 0, 'usage_unknown_runs': 0,
            'reported_input_tokens': 0, 'reported_output_tokens': 0})
        total['records'] += 1
        if record.get('evidence_format') == 'individual_file_hashes':
            total['file_evidence_runs'] = total.get('file_evidence_runs', 0) + 1
            usage = record['reported_usage']
            total['model_attempts'] += record['model_attempts']
            total['model_responses'] += record['model_responses']
            total['usage_unknown_runs'] += int(not usage['complete'])
            total['reported_input_tokens'] += usage['input_tokens']
            total['reported_output_tokens'] += usage['output_tokens']
            continue
        if 'journal_sha256' not in record:
            continue
        total['journal_runs'] += 1
        usage = record['reported_usage']
        total['model_attempts'] += record['model_attempts']
        total['model_responses'] += record['model_responses']
        total['usage_unknown_runs'] += int(not usage['complete'])
        total['reported_input_tokens'] += usage['input_tokens']
        total['reported_output_tokens'] += usage['output_tokens']
    write_json(output / 'results.json', {'live_model': 'meta/muse-glimmer-30b',
                'training_performed': False, 'private_evidence': str(private.resolve()),
                'attribution': 'Development pilot; scripted smoke fixtures are identified separately.',
                'response_accounting': 'model_responses counts retained response records, including empty error receipts. HTTP status is recorded separately; a record is not proof of a provider reply. Reported token sums are known partial totals when complete is false.',
                'stage_totals': stage_totals, 'records': records})
    for path in output.rglob('*'):
        if path.is_file() and credential and credential.encode() in path.read_bytes():
            raise ValueError('Configured credential detected in projection')
    print(json.dumps({'projected_runs': len(records), 'output': str(output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    export(args.private, args.output)
