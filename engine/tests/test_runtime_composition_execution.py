"""Method-library runner behavior, using only authored fixtures and the trusted runner."""

import json
import subprocess
import sys
import unittest
from unittest import mock

from kuttaka_runtime.execution import PythonExecutor, RUNNER


def run_trusted_runner(job):
    """Exercise the fixed runner in a child Python process with authored candidate code."""
    return subprocess.run([sys.executable, '-I', '-c', RUNNER],
                          input=json.dumps(job).encode('utf-8'),
                          capture_output=True, timeout=5, check=False)


class CompositionRunnerTests(unittest.TestCase):
    def test_declared_but_unused_dependency_has_no_call(self):
        artifact = 'a' * 64
        proc = run_trusted_runner({
            'source': 'def run(value):\n    return value\n', 'input': 7,
            'libraries': {artifact: 'def run(value):\n    return value + 1\n'}})
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout), {'result': 7, 'dependency_calls': []})

    def test_looking_up_dependency_without_call_has_no_call(self):
        artifact = 'b' * 64
        source = f'dependency = use("{artifact}")\ndef run(value):\n    return value\n'
        proc = run_trusted_runner({'source': source, 'input': 8,
                                   'libraries': {artifact: 'def run(value): return value + 1\n'}})
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout)['dependency_calls'], [])

    def test_invocation_records_id_when_dependency_run_is_called(self):
        artifact = 'c' * 64
        source = f'def run(value):\n    return use("{artifact}")(value)\n'
        proc = run_trusted_runner({'source': source, 'input': 8,
                                   'libraries': {artifact: 'def run(value): return value + 1\n'}})
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout), {'result': 9, 'dependency_calls': [artifact]})

    def test_nested_calls_are_recorded_in_invocation_order(self):
        outer, inner = 'd' * 64, 'e' * 64
        libraries = {
            outer: f'def run(value):\n    return use("{inner}")(value) * 2\n',
            inner: 'def run(value): return value + 1\n',
        }
        source = f'def run(value):\n    return use("{outer}")(value)\n'
        proc = run_trusted_runner({'source': source, 'input': 4, 'libraries': libraries})
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout),
                         {'result': 10, 'dependency_calls': [outer, inner]})

    def test_undeclared_dependency_fails(self):
        absent = 'f' * 64
        source = f'def run(value):\n    return use("{absent}")(value)\n'
        proc = run_trusted_runner({'source': source, 'input': 1, 'libraries': {}})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(b'undeclared method dependency', proc.stderr)

    def test_executor_receipt_separates_trace_and_preserves_core_output(self):
        artifact = '1' * 64
        source = f'def run(value):\n    return use("{artifact}")(value)\n'
        proc = run_trusted_runner({'source': source, 'input': 10,
                                   'libraries': {artifact: 'def run(value): return value * 3\n'}})
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        output, calls = PythonExecutor._parse_runner_output(
            proc.stdout.decode(), libraries_present=True)
        self.assertEqual(json.loads(output), {'result': 30})
        self.assertEqual(calls, [artifact])

    def test_legacy_no_library_runner_output_is_accepted(self):
        output, calls = PythonExecutor._parse_runner_output(
            '{"result": [1, 2]}', libraries_present=False)
        self.assertEqual(json.loads(output), {'result': [1, 2]})
        self.assertEqual(calls, [])

    def test_invalid_and_oversize_library_jobs_reject_before_docker(self):
        executor = PythonExecutor()
        with mock.patch('kuttaka_runtime.execution.subprocess.Popen',
                        side_effect=AssertionError('Docker process must not start')):
            for libraries in (
                    {'not-an-artifact-id': 'def run(x): return x'},
                    {'a' * 64: ''},
                    {str(i).zfill(64): 'def run(x): return x' for i in range(17)},
                    {'a' * 64: 'x' * 262145}):
                with self.subTest(size=len(str(libraries))), self.assertRaises(ValueError):
                    executor({'source': 'def run(x): return x', 'input': None,
                              'libraries': libraries})


if __name__ == '__main__':
    unittest.main()
