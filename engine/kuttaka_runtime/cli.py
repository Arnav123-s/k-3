"""Runnable frozen-model program construction and persistent method reuse."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

from .artifacts import ArtifactMemory
from .execution import PythonExecutor, preflight
from .models import JsonModel
from .programs import ProgramTask, ProgramTools
from .runtime import Budget, investigate
from .store import Journal, canonical


class ConfigurationError(ValueError):
    """A configuration problem with a deliberately credential-free message."""


def _emit(value):
    print(json.dumps(value, indent=2, allow_nan=False))


def _model_settings(args):
    protocol = args.protocol
    key_env = args.key_env
    if key_env is None and protocol == 'openai' and not args.no_api_key:
        key_env = 'GLIMMER_API_KEY'
    if args.no_api_key:
        key_env = None
    return protocol, key_env


def doctor(args):
    executor = PythonExecutor(timeout=args.execution_timeout, output_limit=args.output_limit)
    protocol, key_env = _model_settings(args)
    sandbox = preflight()
    configured = bool(args.base_url and args.model)
    credential = key_env is None or bool(os.environ.get(key_env))
    _emit({'sandbox': sandbox, 'model_configured': configured, 'protocol': protocol,
           'credential_variable': key_env, 'credential_present': credential,
           'credential_mode': ('environment' if key_env and credential else
                               'network_secret_declared_unverified' if args.network_secret else
                               'environment_absent' if key_env else 'unauthenticated'),
           'ready_for_live_run': bool(sandbox['ready'] and configured and credential),
           'model_endpoint_contacted': False})
    return 0 if sandbox['ready'] and configured and credential else 2


def _paths(args):
    workspace = Path(args.workspace)
    memory = Path(args.memory) if args.memory else workspace / 'memory'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = Path(args.output) if args.output else workspace / 'runs' / (stamp + '-' + uuid.uuid4().hex[:10])
    return memory, output


def execute(args):
    manifest = json.loads(Path(args.task).read_text())
    task = ProgramTask.from_dict(manifest)
    executor = PythonExecutor(timeout=args.execution_timeout, output_limit=args.output_limit)
    readiness = preflight()
    if not readiness['ready']:
        raise ConfigurationError('Docker or the pinned Python image is unavailable; run astra doctor')
    model = None
    if args.command == 'run':
        if not args.base_url or not args.model:
            raise ConfigurationError('Set ASTRA_BASE_URL and ASTRA_MODEL, or pass --base-url and --model')
        protocol, key_env = _model_settings(args)
        if key_env and not os.environ.get(key_env) and not args.network_secret:
            raise ConfigurationError('The selected credential environment variable is absent; configure it outside chat')
        model = JsonModel(args.model, args.base_url, protocol=protocol, key_env=key_env,
                          timeout=args.model_timeout, max_tokens=args.max_tokens,
                          temperature=args.temperature, top_p=args.top_p,
                          reasoning_effort=args.reasoning_effort, stream=args.stream,
                          reasoning_hint=args.reasoning_hint,
                          sampling_fields=not args.provider_sampling_defaults,
                          network_secret=args.network_secret, parse_mode=args.reply_format)
    memory_path, output = _paths(args)
    output.mkdir(parents=True, exist_ok=False)
    with Journal(output / 'events.jsonl') as journal, ArtifactMemory(memory_path) as memory:
        provenance = {'producer': model.identity if model else 'explicit artifact reuse',
                      'run': str(output), 'execution_image': executor.image}
        broker = ProgramTools(task, memory, journal, executor,
                              max_executions=args.max_executions, provenance=provenance)
        journal.append('program_task', {'request': task.request, 'scope': task.scope_id,
                       'manifest': manifest,
                       'attribution': 'host-supplied development checks, not a general correctness proof'})
        if model:
            result = investigate(task.request, model, broker.tools(), broker.checker, journal,
                                 Budget(model_calls=args.model_calls, tool_calls=args.tool_calls),
                                 checker_feedback=broker.checker_feedback,
                                 normalize=args.normalize_replies,
                                 automatic_submission=args.automatic_submission,
                                 continue_transport_failures=args.continue_transport_failures,
                                 outage_limit=args.outage_limit)
            report = asdict(result)
        else:
            answer = {'artifact': args.artifact}
            journal.append('reuse_request', {'answer': answer, 'scope': task.scope_id})
            checked = broker.checker(task.request, answer)
            report = {'status': 'checked' if checked else 'incomplete',
                      'answer': answer if checked else None, 'model_calls': 0, 'tool_calls': 1,
                      'reason': 'accepted by supplied development checks' if checked else 'artifact validation did not pass'}
        if report['status'] == 'checked':
            artifact_id = report['answer']['artifact']
            retained = memory.get_qualified(artifact_id, task.scope_id)
            (output / 'solution.py').write_text(retained['body']['source'])
            if 'input' in manifest:
                delivered = broker.probe_method({'artifact': artifact_id, 'input': manifest['input']})
                report['deployment'] = delivered
                if delivered['status'] != 'completed':
                    report['status'] = 'incomplete'
                    report['reason'] = 'method passed supplied checks but deployment execution failed'
        report.update({'scope': task.scope_id, 'execution_attempts': broker.execution_attempts,
                       'output_directory': str(output), 'memory_directory': str(memory_path),
                       'qualification': 'supplied finite development checks only'})
        journal.append('program_finish', report)
    (output / 'result.json').write_text(canonical(report) + '\n')
    _emit(report)
    return 0 if report['status'] == 'checked' else 1


def parser():
    root = argparse.ArgumentParser(prog='astra', description=__doc__)
    sub = root.add_subparsers(dest='command', required=True)
    for name in ('doctor', 'run', 'reuse'):
        command = sub.add_parser(name)
        command.add_argument('--execution-timeout', type=float, default=10)
        command.add_argument('--output-limit', type=int, default=65536)
        if name in ('doctor', 'run'):
            command.add_argument('--profile', choices=['muse-glimmer'])
            command.add_argument('--base-url', default=os.environ.get('ASTRA_BASE_URL'))
            command.add_argument('--model', default=os.environ.get('ASTRA_MODEL'))
            command.add_argument('--protocol', choices=['openai', 'ollama'],
                                 default=os.environ.get('ASTRA_PROTOCOL', 'openai'))
            auth = command.add_mutually_exclusive_group()
            auth.add_argument('--key-env', default=None, help='Environment variable name, never the key value')
            auth.add_argument('--no-api-key', action='store_true', help='For an explicitly unauthenticated endpoint')
            command.add_argument('--network-secret', action='store_true',
                                 help='Explicitly use a managed network credential binding; does not verify it exists')
        if name != 'doctor':
            command.add_argument('--task', required=True, help='JSON task with request and input/expected checks')
            command.add_argument('--workspace', default='.astra')
            command.add_argument('--memory', help='Persistent artifact directory; defaults to workspace/memory')
            command.add_argument('--output', help='New directory for this run; existing directories are refused')
            command.add_argument('--max-executions', type=int, default=128)
        if name == 'run':
            command.add_argument('--model-calls', type=int, default=16)
            command.add_argument('--tool-calls', type=int, default=64)
            command.add_argument('--model-timeout', type=float, default=60)
            command.add_argument('--max-tokens', type=int)
            command.add_argument('--temperature', type=float)
            command.add_argument('--top-p', type=float)
            command.add_argument('--reasoning-effort', choices=['none', 'minimal', 'low', 'medium', 'high', 'max'])
            command.add_argument('--stream', action='store_true', help='Request OpenAI-compatible SSE responses')
            command.add_argument('--reasoning-hint', choices=['low', 'medium', 'high'],
                                 help='Explicit instruction prefix, separately recorded from reasoning_effort')
            command.add_argument('--provider-sampling-defaults', action='store_true',
                                 help='Omit sampling fields; cannot combine with explicit sampling options')
            command.add_argument('--reply-format', choices=['strict', 'extract'], default='strict',
                                 help='Strict JSON by default; extract explicitly allows prose/fences')
            command.add_argument('--normalize-replies', action='store_true',
                                 help='Opt in to legacy action repairs, including empty predictions')
            command.add_argument('--automatic-submission', action='store_true',
                                 help='Submit tool deliverables automatically, spending the checker budget')
            command.add_argument('--continue-transport-failures', action='store_true',
                                 help='Continue after soft transport failures within the original call budget')
            command.add_argument('--outage-limit', type=int,
                                 help='Consecutive failure limit; required with --continue-transport-failures')
        if name == 'reuse':
            command.add_argument('--artifact', required=True, help='Exact stored method content ID')
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        muse = getattr(args, 'profile', None) == 'muse-glimmer'
        if getattr(args, 'network_secret', False) and args.no_api_key:
            raise ConfigurationError('--network-secret requires an authenticated endpoint')
        if muse:
            if args.protocol != 'openai':
                raise ConfigurationError('The Muse Glimmer profile uses the OpenAI-compatible protocol')
            args.base_url = args.base_url or 'https://integrate.api.nvidia.com/v1'
            args.model = args.model or 'meta/muse-glimmer-30b'
            args.key_env = args.key_env or 'NVIDIA_API_KEY'
        if args.command == 'run':
            if args.provider_sampling_defaults and any(value is not None for value in
                    (args.temperature, args.top_p, args.reasoning_effort)):
                raise ConfigurationError('Provider sampling defaults cannot combine with explicit sampling options')
            if args.continue_transport_failures != (args.outage_limit is not None):
                raise ConfigurationError('Set both --continue-transport-failures and a positive --outage-limit')
            if args.outage_limit is not None and args.outage_limit <= 0:
                raise ConfigurationError('--outage-limit must be positive')
            args.max_tokens = args.max_tokens if args.max_tokens is not None else (8192 if muse else 4096)
            args.temperature = args.temperature if args.temperature is not None else (0.95 if muse else 0)
            args.top_p = args.top_p if args.top_p is not None else (1.0 if muse else None)
            args.reasoning_effort = args.reasoning_effort or ('high' if muse else None)
        return doctor(args) if args.command == 'doctor' else execute(args)
    except ConfigurationError as error:
        _emit({'status': 'configuration_error', 'reason': str(error)})
        return 2
    except Exception as error:
        # Task/provider/execution errors can include task data or credentials.
        _emit({'status': 'error', 'error_type': type(error).__name__,
               'reason': 'Run could not complete; inspect the private journal if a run directory was created'})
        return 2


if __name__ == '__main__':
    sys.exit(main())
