"""Strict adapter for bounded executor results and known diagnostic metadata."""

import json
import math

from .store import canonical


MAX_METADATA_BYTES = 64 * 1024
_CORE_REQUIRED = {'status', 'exit_code', 'output'}
_CORE_OPTIONAL = {'diagnostics'}
_METADATA_FIELDS = {
    'container_name', 'timings', 'inspect_state', 'cleanup', 'dependency_calls',
}
_TIMING_FIELDS = {
    'stdin_complete_seconds', 'first_output_seconds', 'process_exit_seconds',
    'process_exit_forced', 'total_wall_seconds',
}
_COMMAND_FIELDS = {
    'status', 'return_code', 'timed_out', 'elapsed_seconds', 'stdout', 'stderr',
}
_COMMAND_OPTIONAL = {'error_type'}
_COMMAND_STATUSES = {'completed', 'timeout', 'output_limit', 'error'}


def _is_finite_number(value):
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)


def _validate_command_receipt(value, field):
    if value is None:
        return
    if not isinstance(value, dict) or not _COMMAND_FIELDS <= set(value):
        raise ValueError(f'invalid {field} metadata')
    if set(value) - (_COMMAND_FIELDS | _COMMAND_OPTIONAL):
        raise ValueError(f'invalid {field} metadata')
    if not isinstance(value['status'], str) or value['status'] not in _COMMAND_STATUSES:
        raise ValueError(f'invalid {field} metadata')
    if value['return_code'] is not None and type(value['return_code']) is not int:
        raise ValueError(f'invalid {field} metadata')
    if type(value['timed_out']) is not bool:
        raise ValueError(f'invalid {field} metadata')
    if value['timed_out'] != (value['status'] == 'timeout'):
        raise ValueError(f'invalid {field} metadata')
    if not _is_finite_number(value['elapsed_seconds']) or value['elapsed_seconds'] < 0:
        raise ValueError(f'invalid {field} metadata')
    if not isinstance(value['stdout'], str) or not isinstance(value['stderr'], str):
        raise ValueError(f'invalid {field} metadata')
    if ((value['status'] == 'error') != ('error_type' in value)
            or ('error_type' in value and
                (not isinstance(value['error_type'], str) or not value['error_type']))):
        raise ValueError(f'invalid {field} metadata')


def _validate_timings(value):
    if not isinstance(value, dict) or set(value) != _TIMING_FIELDS:
        raise ValueError('invalid timings metadata')
    for key in ('stdin_complete_seconds', 'first_output_seconds', 'process_exit_seconds'):
        item = value[key]
        if item is not None and (not _is_finite_number(item) or item < 0):
            raise ValueError('invalid timings metadata')
    if type(value['process_exit_forced']) is not bool:
        raise ValueError('invalid timings metadata')
    if not _is_finite_number(value['total_wall_seconds']) or value['total_wall_seconds'] < 0:
        raise ValueError('invalid timings metadata')


def _validate_artifact_id(value):
    return (isinstance(value, str) and len(value) == 64
            and all(character in '0123456789abcdef' for character in value))


def split_execution_receipt(response):
    """Split a known executor receipt into strict core and detached metadata.

    Core keys are `status`, `exit_code`, and `output`, with optional `diagnostics`.
    Known executor telemetry is returned separately so callers can journal it
    without weakening the core protocol's unknown-key rejection.
    """
    if not isinstance(response, dict) or not _CORE_REQUIRED <= set(response):
        raise ValueError('invalid executor receipt')
    allowed = _CORE_REQUIRED | _CORE_OPTIONAL | _METADATA_FIELDS
    if set(response) - allowed:
        raise ValueError('invalid executor receipt')
    if not isinstance(response['status'], str) or not response['status']:
        raise ValueError('invalid executor receipt')
    exit_code = response['exit_code']
    if exit_code is not None and type(exit_code) is not int:
        raise ValueError('invalid executor receipt')
    if exit_code is None and response['status'] == 'completed':
        raise ValueError('invalid executor receipt')
    if not isinstance(response['output'], str):
        raise ValueError('invalid executor receipt')
    if 'diagnostics' in response and not isinstance(response['diagnostics'], str):
        raise ValueError('invalid executor receipt')

    metadata = {key: value for key, value in response.items() if key in _METADATA_FIELDS}
    if 'container_name' in metadata and not isinstance(metadata['container_name'], str):
        raise ValueError('invalid executor receipt metadata')
    if 'timings' in metadata:
        _validate_timings(metadata['timings'])
    for field in ('inspect_state', 'cleanup'):
        if field in metadata:
            _validate_command_receipt(metadata[field], field)
    if 'dependency_calls' in metadata:
        calls = metadata['dependency_calls']
        if not isinstance(calls, list) or any(not _validate_artifact_id(item) for item in calls):
            raise ValueError('invalid executor receipt metadata')

    try:
        metadata_bytes = canonical(metadata)
    except (TypeError, ValueError, RecursionError, OverflowError) as error:
        raise ValueError('invalid executor receipt metadata') from error
    if len(metadata_bytes) > MAX_METADATA_BYTES:
        raise ValueError('executor receipt metadata exceeds 64 KiB')

    core = {key: response[key] for key in ('status', 'exit_code', 'output')}
    if 'diagnostics' in response:
        core['diagnostics'] = response['diagnostics']
    try:
        return json.loads(canonical(core)), json.loads(metadata_bytes)
    except (TypeError, ValueError) as error:
        raise ValueError('invalid executor receipt') from error
