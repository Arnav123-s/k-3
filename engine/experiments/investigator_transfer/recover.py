"""Reconstruct a campaign summary from completed, immutable run records."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from kuttaka_runtime.investigators import import_investigator
from kuttaka_runtime.store import Journal, digest


_APPENDED_RESULT_FIELDS = {'reported_usage', 'journal_tip', 'journal_sha256'}


class RecoveryError(ValueError):
    """The source records do not support a faithful summary reconstruction."""


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RecoveryError(f'Cannot read valid JSON from {path.name}') from error


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_value(command, option):
    try:
        index = command.index(option)
        return command[index + 1]
    except (ValueError, IndexError):
        return None


def _validate_run(root, spec, campaign, bundle_digest):
    family, seed, arm = spec
    name = f'{family}-{seed}-{arm}'
    directory = root / name
    invocation_path = root / f'{name}-invocation.json'
    result_path = directory / 'result.json'
    journal_path = directory / 'events.jsonl'
    task_manifest_path = directory / 'task-manifest.json'
    for path in (invocation_path, result_path, journal_path, task_manifest_path):
        if not path.is_file():
            raise RecoveryError(f'Planned run {name} is missing {path.name}')

    invocation = _read_json(invocation_path)
    result = _read_json(result_path)
    task_manifest = _read_json(task_manifest_path)
    if invocation.get('name') != name:
        raise RecoveryError(f'Invocation name does not match planned run {name}')
    if type(invocation.get('exit_code')) is not int or invocation['exit_code'] != 0:
        raise RecoveryError(f'Invocation did not exit successfully for {name}')
    if invocation.get('timeout') or invocation.get('result_missing') is True:
        raise RecoveryError(f'Invocation is incomplete for {name}')
    command = invocation.get('command')
    if (not isinstance(command, list)
            or _command_value(command, '--family') != family
            or _command_value(command, '--seed') != str(seed)
            or _command_value(command, '--arm') != arm
            or Path(_command_value(command, '--output') or '').name != name):
        raise RecoveryError(f'Invocation command identifiers do not match planned run {name}')
    report = invocation.get('report')
    if not isinstance(report, dict) or report != result:
        raise RecoveryError(f'Invocation report differs from result file for {name}')
    if (result.get('family'), result.get('seed'), result.get('arm')) != (family, seed, arm):
        raise RecoveryError(f'Result identifiers do not match planned run {name}')
    if (task_manifest.get('family'), task_manifest.get('seed')) != (family, seed):
        raise RecoveryError(f'Task manifest identifiers do not match planned run {name}')

    events = Journal.read(journal_path)
    if not events or events[-1]['kind'] != 'experiment_finish':
        raise RecoveryError(f'Journal is not sealed by experiment_finish for {name}')
    if result.get('journal_sha256') != _sha256(journal_path):
        raise RecoveryError(f'Result journal hash does not match for {name}')
    if result.get('journal_tip') != events[-1]['hash']:
        raise RecoveryError(f'Result journal tip does not match for {name}')

    manifests = [event['body'] for event in events if event['kind'] == 'experiment_manifest']
    starts = [event['body'] for event in events if event['kind'] == 'investigator_start']
    finishes = [event['body'] for event in events if event['kind'] == 'experiment_finish']
    if len(manifests) != 1 or len(starts) != 1 or len(finishes) != 1:
        raise RecoveryError(f'Journal lacks unique campaign identity events for {name}')
    run_manifest = manifests[0]
    if (run_manifest.get('family'), run_manifest.get('seed'), run_manifest.get('arm')) != (family, seed, arm):
        raise RecoveryError(f'Journal manifest identifiers do not match planned run {name}')
    if run_manifest.get('task_hashes') != task_manifest:
        raise RecoveryError(f'Journal task manifest differs for {name}')
    if (campaign.get('model') is None or starts[0].get('model') != campaign['model']):
        raise RecoveryError(f'Run model identity differs from campaign for {name}')
    if starts[0].get('budget') != campaign.get('budget'):
        raise RecoveryError(f'Run budget differs from campaign for {name}')
    if run_manifest.get('limits') != campaign.get('limits'):
        raise RecoveryError(f'Run limits differ from campaign for {name}')
    expected_artifact = None if arm in ('ordinary', 'removed') else bundle_digest
    if (run_manifest.get('policy_artifact') != expected_artifact
            or result.get('policy_artifact') != expected_artifact):
        raise RecoveryError(f'Run policy artifact differs from campaign bundle for {name}')

    result_core = {key: value for key, value in result.items()
                   if key not in _APPENDED_RESULT_FIELDS}
    if set(result) != set(finishes[0]) | _APPENDED_RESULT_FIELDS or result_core != finishes[0]:
        raise RecoveryError(f'Experiment finish differs from result report for {name}')

    source_paths = (invocation_path, result_path, journal_path, task_manifest_path)
    return invocation, {str(path.relative_to(root)): _sha256(path) for path in source_paths}


def recover(campaign_root, output):
    root = Path(campaign_root).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    campaign_path = root / 'campaign.json'
    plan_path = root / 'planned-runs.json'
    bundle_path = root / 'bundle.json'
    for path in (campaign_path, plan_path, bundle_path):
        if not path.is_file():
            raise RecoveryError(f'Campaign is missing {path.name}')

    campaign = _read_json(campaign_path)
    planned = _read_json(plan_path)
    bundle = _read_json(bundle_path)
    if not isinstance(planned, list) or not planned:
        raise RecoveryError('Planned run list is empty or invalid')
    if any(not isinstance(spec, list) or len(spec) != 3
           or not isinstance(spec[0], str) or type(spec[1]) is not int
           or not isinstance(spec[2], str) for spec in planned):
        raise RecoveryError('Planned run entry has an invalid identifier')
    if len({tuple(spec) for spec in planned}) != len(planned):
        raise RecoveryError('Planned run list contains duplicate entries')

    bundle_digest = digest(bundle)
    if campaign.get('policy_sha256') != bundle_digest:
        raise RecoveryError('Campaign bundle hash does not match campaign manifest')
    try:
        validated_bundle = import_investigator(bundle)
    except (TypeError, ValueError, KeyError) as error:
        raise RecoveryError('Campaign bundle is not a valid investigator artifact') from error
    source_digest = hashlib.sha256(validated_bundle['body']['source'].encode('utf-8')).hexdigest()
    if campaign.get('policy_source_sha256') != source_digest:
        raise RecoveryError('Campaign policy source hash does not match bundle source')

    runs = []
    source_hashes = {
        'campaign.json': _sha256(campaign_path),
        'planned-runs.json': _sha256(plan_path),
        'bundle.json': _sha256(bundle_path),
    }
    for spec in planned:
        invocation, hashes = _validate_run(root, spec, campaign, bundle_digest)
        runs.append(invocation)
        source_hashes.update(hashes)

    expected_invocations = {f'{family}-{seed}-{arm}-invocation.json'
                            for family, seed, arm in planned}
    actual_invocations = {path.name for path in root.glob('*-invocation.json')}
    if actual_invocations != expected_invocations:
        raise RecoveryError('Campaign contains missing or unplanned invocation records')

    summary = {
        'campaign': campaign,
        'runs': runs,
        'recovery_provenance': {
            'format': 'reconstructed_from_completed_run_records_v1',
            'reason': 'Summary publication unavailable after execution access interruption; cause not established.',
            'source_campaign': str(root),
            'source_files_sha256': source_hashes,
            'planned_runs_validated': len(planned),
            'journal_chains_validated': len(planned),
            'synthetic_journals_created': 0,
            'runs_reexecuted': 0,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(output, flags, 0o644)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(summary, stream, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    summary = recover(args.campaign, args.output)
    print(json.dumps({'recovered_runs': len(summary['runs']),
                      'output': str(args.output.resolve()),
                      'provenance_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))
