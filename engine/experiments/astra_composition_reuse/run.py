"""Zero-model-call reuse of a public live-model method through ProgramTools."""

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from kuttaka_runtime.artifacts import ArtifactMemory, canonical
from kuttaka_runtime.execution import PythonExecutor, preflight
from kuttaka_runtime.programs import ProgramTask, ProgramTools
from kuttaka_runtime.store import Journal


ROOT = Path(__file__).resolve().parent
RECORDED = ROOT / 'recorded'
SOURCE_ARTIFACT = '24864fb307094d52eaa6f7e14b3aff33a9cf3e18ee2b782a6e727f5fc110223c'
SOURCE_COMMIT = 'a97845b2b0a513c331fa4ae996548b8e9e4b3be0'

CRT_BATCHES = [
    [
        {'a': 2, 'm': 3, 'b': 3, 'n': 5},
        {'a': 1, 'm': 4, 'b': 5, 'n': 6},
        {'a': 1, 'm': 4, 'b': 2, 'n': 6},
    ],
    [
        {'a': -1, 'm': 5, 'b': 2, 'n': 3},
        {'a': -2, 'm': 6, 'b': 4, 'n': 8},
        {'a': -3, 'm': 7, 'b': -1, 'n': 5},
    ],
]
CONTROL_CHECKS = [{'input': {'value': 'control'}, 'expected': 'control'}]


def make_tools(memory, journal, executor, request, checks):
    return ProgramTools(
        ProgramTask(request, checks), memory, journal, executor,
        max_executions=8,
        provenance={'producer': 'engineered-demonstration', 'phase': 'development'},
    )


def remember(tools, name, description, source, uses=None):
    request = {'name': name, 'description': description, 'source': source}
    if uses is not None:
        request['uses'] = uses
    return tools.remember_method(request)['artifact']


def latest_results(events, artifact):
    return [event['body'] for event in events
            if event['kind'] == 'program_execution_result'
            and event['body'].get('artifact') == artifact]


def reference_crt(case):
    """Independent finite enumeration of one CRT period, used only by the host."""
    a, m, b, n = (case[key] for key in ('a', 'm', 'b', 'n'))
    if type(m) is not int or type(n) is not int or m <= 0 or n <= 0:
        raise ValueError('the development cases require positive integer moduli')
    period = m * n // math.gcd(m, n)
    for candidate in range(period):
        if candidate % m == a % m and candidate % n == b % n:
            return candidate
    return None


def main():
    source_path = RECORDED / 'original-method.json'
    source_bytes = source_path.read_bytes()
    payload = json.loads(source_bytes)
    actual_identity = hashlib.sha256(canonical(payload)).hexdigest()
    if actual_identity != SOURCE_ARTIFACT:
        raise ValueError(f'original payload identity mismatch: {actual_identity}')
    if (payload.get('kind') != 'method'
            or payload.get('provenance', {}).get('producer', {}).get('model') != 'meta/muse-glimmer-30b'):
        raise ValueError('source payload is not the expected live-model method')

    sandbox = preflight()
    if not sandbox['ready']:
        raise RuntimeError(f"pinned Docker sandbox unavailable: {sandbox['reason']}")

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_dir = RECORDED / 'runs' / f'{stamp}-pid{os.getpid()}'
    run_dir.mkdir(parents=True, exist_ok=False)

    executor = PythonExecutor(timeout=10)
    with ArtifactMemory(run_dir / 'memory') as memory, Journal(run_dir / 'events.jsonl') as journal:
        imported_id = memory.put(payload)
        if imported_id != SOURCE_ARTIFACT:
            raise ValueError(f'imported artifact identity mismatch: {imported_id}')

        wrapper_checks = [
            {'input': batch, 'expected': [reference_crt(case) for case in batch]}
            for batch in CRT_BATCHES
        ]
        wrapper_tools = make_tools(
            memory, journal, executor,
            'For each pair of integer congruences in a batch, return the least nonnegative solution modulo the joint period, or null if inconsistent.',
            wrapper_checks)
        wrapper_source = (
            f'def run(batch):\n'
            f'    solve_integer_equation = use("{SOURCE_ARTIFACT}")\n'
            f'    import math\n'
            f'    solutions = []\n'
            f'    for case in batch:\n'
            f'        a, m, b, n = case["a"], case["m"], case["b"], case["n"]\n'
            f'        equation = solve_integer_equation({{"a": m, "b": n, "c": b - a}})\n'
            f'        if equation is None:\n'
            f'            solutions.append(None)\n'
            f'        else:\n'
            f'            period = m * n // math.gcd(m, n)\n'
            f'            solutions.append((a + m * equation["x"]) % period)\n'
            f'    return solutions\n'
        )
        wrapper_id = remember(
            wrapper_tools, 'crt_from_integer_equations',
            'Solve pairs of congruences by composing the imported integer-equation method.',
            wrapper_source, uses=[SOURCE_ARTIFACT])
        wrapper_validation = wrapper_tools.validate_method({'artifact': wrapper_id})
        component = wrapper_tools.inspect_method({'artifact': SOURCE_ARTIFACT})

        control_tools = make_tools(
            memory, journal, executor,
            'Return the value in the control input.', CONTROL_CHECKS)
        control_source = 'def run(value):\n    return value["value"]\n'
        control_id = remember(
            control_tools, 'declared_unused_control',
            'Trace control: returns a field without invoking the declared method.',
            control_source, uses=[SOURCE_ARTIFACT])
        control_validation = control_tools.validate_method({'artifact': control_id})

        events = Journal.read(run_dir / 'events.jsonl')
        wrapper_results = latest_results(events, wrapper_id)
        control_results = latest_results(events, control_id)
        execution_metadata = [event['body'] for event in events
                              if event['kind'] == 'program_execution_metadata']
        wrapper_calls = [row.get('dependency_calls') for row in wrapper_results]
        control_calls = [row.get('dependency_calls') for row in control_results]
        expected_wrapper_calls = [[SOURCE_ARTIFACT] * len(batch) for batch in CRT_BATCHES]

        if wrapper_validation.get('status') != 'passed':
            raise AssertionError(f'wrapper failed exact checks: {wrapper_validation}')
        if component.get('status') != 'component_only':
            raise AssertionError(f'original method role was not component_only: {component}')
        if (len(wrapper_results) != len(wrapper_checks)
                or wrapper_calls != expected_wrapper_calls):
            raise AssertionError(f'wrapper invocation receipts mismatch: {wrapper_calls}')
        if control_validation.get('status') != 'passed':
            raise AssertionError(f'declared-unused control failed checks: {control_validation}')
        if len(control_results) != len(CONTROL_CHECKS) or any(control_calls):
            raise AssertionError(f'declared-unused control recorded calls: {control_calls}')

        cleanup_failures = [row for row in execution_metadata
                            if not isinstance(row.get('metadata', {}).get('cleanup'), dict)
                            or row['metadata']['cleanup'].get('status') != 'completed'
                            or row['metadata']['cleanup'].get('return_code') != 0]

        report = {
            'status': 'passed' if not cleanup_failures else 'passed_with_cleanup_failures',
            'attribution': 'prior live-model method + newly engineered wrapper; development demonstration only',
            'model_calls': 0,
            'source': {
                'commit': SOURCE_COMMIT,
                'payload_path_at_source': 'experiments/astra_live/recorded/payloads/' + SOURCE_ARTIFACT + '.json',
                'payload_sha256_content_identity': actual_identity,
                'model': payload['provenance']['producer']['model'],
                'acquisition_run': 'tasks-v2/integer-equation/acquire',
                'acquisition_summary': 'public results row: 5 model calls; checked; qualified finite checks',
                'source_bytes_sha256': hashlib.sha256(source_bytes).hexdigest(),
            },
            'sandbox': sandbox,
            'wrapper': {
                'authoring': 'engineered for this demonstration',
                'artifact': wrapper_id,
                'source': wrapper_source,
                'checks': wrapper_checks,
                'check_method': 'independent enumeration over each joint period',
                'validation': wrapper_validation,
                'dependency_calls_by_check': wrapper_calls,
                'imported_method_role_in_wrapper_scope': component['status'],
            },
            'declared_unused_control': {
                'artifact': control_id,
                'source': control_source,
                'validation': control_validation,
                'dependency_calls_by_check': control_calls,
            },
            'execution_results': {
                'wrapper': wrapper_results,
                'declared_unused_control': control_results,
            },
            'executor_metadata': execution_metadata,
            'cleanup_failures': cleanup_failures,
            'trace_limit': 'sandbox-reported cooperative invocation metadata; not tamper-proof or causal proof',
            'run_directory': str(run_dir.relative_to(ROOT)),
        }
        (run_dir / 'result.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
