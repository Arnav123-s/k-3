"""Count all planned target runs, including missing and unsuccessful attempts."""

import argparse
import json
from pathlib import Path

from .acquire import write_json
from .export import _response_metadata
from kuttaka_runtime.store import Journal


SAFE_TRANSPORT_KINDS = {
    'provider_http_error', 'request_timeout', 'request_transport_error',
    'response_read_error', 'empty_stream', 'incomplete_stream', 'stream_error',
}

TRANSPORT_ERROR_TYPES = {
    'Timeout', 'TimeoutError', 'URLError', 'ConnectionError',
    'ConnectionResetError', 'RemoteDisconnected', 'IncompleteRead',
}


def _safe_failure_metadata(root, name):
    """Count events and safe response/error metadata without projecting raw bodies."""
    path = root / name / 'events.jsonl'
    if not path.exists():
        return [], 0, False, None, None, None
    # A present but invalid journal is evidence corruption, not an absent
    # metadata condition. Let Journal.read integrity/parse failures propagate.
    events = Journal.read(path)
    errors = []
    rejected = 0
    revisions = sum(event.get('kind') == 'representation_revision' for event in events)
    length_finishes = 0
    response_statuses = {}
    known_types = TRANSPORT_ERROR_TYPES | {'HTTPError', 'TransportFailure'}
    for event in events:
        if event.get('kind') == 'model_error':
            body = event.get('body', {})
            body = body if isinstance(body, dict) else {}
            error_type = body.get('type')
            status = body.get('status')
            error_kind = body.get('kind')
            metadata = {
                'type': error_type if isinstance(error_type, str) and error_type in known_types else 'OtherModelError',
                'status': status if type(status) is int and 100 <= status <= 599 else None,
            }
            if isinstance(error_kind, str) and error_kind in SAFE_TRANSPORT_KINDS:
                metadata['kind'] = error_kind
            errors.append(metadata)
        elif event.get('kind') == 'action_rejected':
            rejected += 1
        elif event.get('kind') == 'model_response':
            receipt = event.get('body', {}).get('receipt')
            metadata = _response_metadata(receipt)
            if metadata.get('finish_reason') == 'length':
                length_finishes += 1
            status = metadata.get('http_status')
            if type(status) is int and 100 <= status <= 599:
                key = str(status)
                response_statuses[key] = response_statuses.get(key, 0) + 1
    return errors, rejected, True, revisions, length_finishes, response_statuses


def _safe_memory_stats(report):
    stats = report.get('memory_stats')
    if not isinstance(stats, dict):
        return None
    allowed = {'artifacts', 'events', 'canonical_unique_blob_bytes',
               'compressed_blob_bytes', 'journal_bytes'}
    return {key: value for key, value in stats.items()
            if key in allowed and type(value) is int and value >= 0}


def _safe_invocation_metadata(root, name):
    """Read process outcome only; invocation reports may contain candidate source."""
    path = root / (name + '-invocation.json')
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {key: value.get(key) for key in ('timeout', 'exit_code')}


def _failure_category(report, invocation, model_errors, event_metadata_available):
    if not report:
        if invocation.get('timeout') is True:
            return 'process_timeout_no_result'
        if type(invocation.get('exit_code')) is int:
            return 'process_exit_no_result'
        return 'missing_or_unfinished_no_result'

    result = report.get('result', {})
    score = report.get('score', {})
    if result.get('status') == 'checked' and score.get('passed') is True:
        return 'passed'
    if score.get('status') == 'incorrect':
        return 'scored_incorrect'
    if score.get('status') in ('execution_failed', 'invalid_output'):
        return 'scorer_execution_or_output_failure'
    if score.get('status') in ('invalid_answer', 'source_too_large'):
        return 'invalid_submission'

    for error in model_errors:
        status = error.get('status')
        error_type = error.get('type')
        if type(status) is int and not 200 <= status < 300:
            return 'provider_http_error'
        if error_type == 'HTTPError' or error.get('kind') == 'provider_http_error':
            return 'provider_http_error'
        if error.get('kind') in SAFE_TRANSPORT_KINDS - {'provider_http_error'}:
            return 'request_timeout_or_transport_error'
        if error_type in TRANSPORT_ERROR_TYPES:
            return 'request_timeout_or_transport_error'

    reason = str(result.get('reason', '')).casefold()
    if 'budget' in reason:
        return 'budget_exhausted'
    if result.get('status') == 'unresolved':
        return 'abstention'
    if result.get('status') == 'incomplete' and model_errors:
        return 'model_failure_unclassified'
    if result.get('status') == 'incomplete' and not event_metadata_available:
        return 'incomplete_cause_unverified'
    if result.get('status') == 'incomplete':
        return 'protocol_action_or_runtime_nonpass'
    if result.get('status') == 'failed':
        return 'scorer_nonpass_unclassified'
    return 'nonpass_unclassified'


def analyze(root):
    plan = json.loads((root / 'planned-runs.json').read_text())
    rows, arms, qualifications = [], {}, {'passed': 0, 'planned': 0,
                                          'usage_unknown_runs': 0,
                                          'reported_input_tokens': 0,
                                          'reported_output_tokens': 0}
    planned_names = set()

    def make_row(family, seed, arm, planned):
        name = f'{family}-{seed}-{arm}'
        path = root / name / 'result.json'
        report = json.loads(path.read_text()) if path.exists() else {}
        invocation = _safe_invocation_metadata(root, name) if not report else {}
        (model_errors, action_rejections, event_metadata_available,
         representation_revisions, length_finish_responses, response_http_status_counts) = (
            _safe_failure_metadata(root, name) if path.exists() else
            ([], 0, False, None, None, None))
        result = report.get('result', {})
        status = result.get('status', 'missing')
        success = status == 'checked' and report.get('score', {}).get('passed') is True
        row = {'family': family, 'seed': seed, 'arm': arm,
               'stage': 'source_qualification' if arm == 'qualification' else 'target_campaign',
               'planned': planned, 'status': status, 'pass': success,
               'model_calls': result.get('model_calls'),
               'input_tokens': report.get('reported_usage', {}).get('input_tokens', result.get('model_input_tokens')),
               'output_tokens': report.get('reported_usage', {}).get('output_tokens', result.get('model_output_tokens')),
               'tokens_known': result.get('model_tokens_known', False),
               'observations': report.get('costs', {}).get('observations'),
               'sandbox_executions': report.get('costs', {}).get('executions'),
               'elapsed_seconds': report.get('elapsed_seconds'),
               'score': report.get('score', {}),
               'failure_category': _failure_category(
                   report, invocation, model_errors, event_metadata_available),
               'model_errors': model_errors,
               'action_rejections': action_rejections,
               'event_metadata_available': event_metadata_available,
               'policy_executions': result.get('policy_executions'),
               'representation_revisions': representation_revisions,
               'memory_stats': _safe_memory_stats(report),
               'length_finish_responses': length_finish_responses,
               'response_http_status_counts': response_http_status_counts}
        return name, row

    for family, seed, arm in plan:
        name, row = make_row(family, seed, arm, True)
        planned_names.add(name)
        rows.append(row)
        if arm == 'qualification':
            group = qualifications
        else:
            group = arms.setdefault(arm, {'passed': 0, 'planned': 0,
                'usage_unknown_runs': 0, 'reported_input_tokens': 0,
                'reported_output_tokens': 0})
        group['planned'] += 1
        group['passed'] += int(row['pass'])
        group['usage_unknown_runs'] += int(not row['tokens_known'])
        for target, source in [('reported_input_tokens', 'input_tokens'),
                               ('reported_output_tokens', 'output_tokens')]:
            if type(row[source]) is int:
                group[target] += row[source]

    # Recovery and diagnostic executions are additional observations. Keep
    # them visible and counted without allowing them to replace planned rows.
    for result_path in sorted(root.glob('*/result.json')):
        name = result_path.parent.name
        if name in planned_names:
            continue
        parts = name.rsplit('-', 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        _, row = make_row(parts[0], int(parts[1]), parts[2], False)
        row['stage'] = 'additional_source_qualification' if row['arm'] == 'qualification' else 'additional_target_run'
        rows.append(row)

    additional = [row for row in rows if not row['planned']]
    for row in rows:
        if row['stage'] not in ('additional_source_qualification', 'additional_target_run'):
            continue
        group = qualifications if row['arm'] == 'qualification' else arms.setdefault(row['arm'], {
            'passed': 0, 'planned': 0, 'usage_unknown_runs': 0,
            'reported_input_tokens': 0, 'reported_output_tokens': 0})
        group['additional_runs'] = group.get('additional_runs', 0) + 1
        group['additional_passes'] = group.get('additional_passes', 0) + int(row['pass'])
        group['usage_unknown_additional_runs'] = group.get('usage_unknown_additional_runs', 0) + int(not row['tokens_known'])
        for target, source in [('reported_input_tokens_additional', 'input_tokens'),
                               ('reported_output_tokens_additional', 'output_tokens')]:
            if type(row[source]) is int:
                group[target] = group.get(target, 0) + row[source]
    paired = []
    target_rows = [r for r in rows if r['planned'] and r['arm'] != 'qualification']
    for family, seed in sorted({(r['family'], r['seed']) for r in target_rows}):
        entries = {r['arm']: r for r in rows if r['planned'] and
                   r['arm'] != 'qualification' and
                   (r['family'], r['seed']) == (family, seed)}
        paired.append({'family': family, 'seed': seed,
                       'passes': {a: r['pass'] for a, r in entries.items()},
                       'policy_minus_ordinary': int(entries.get('policy', {}).get('pass', False))
                                              - int(entries.get('ordinary', {}).get('pass', False))})
    taxonomy = {'planned': {}, 'additional': {}}
    for row in rows:
        section = 'planned' if row['planned'] else 'additional'
        category = row['failure_category']
        taxonomy[section][category] = taxonomy[section].get(category, 0) + 1
    return {'attribution': 'Descriptive development pilot. On/off repeats use unseeded stochastic decoding; no significance or generality conclusion.',
            'arms': arms, 'source_qualification': qualifications,
            'additional_runs': additional, 'paired': paired, 'runs': rows,
            'failure_taxonomy': taxonomy}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    write_json(args.output, analyze(args.campaign))
