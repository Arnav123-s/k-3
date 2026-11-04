"""Opt-in container controls; no model calls and no capability claim."""
import json
import os
import subprocess
import threading
import unittest
from unittest import mock

from kuttaka_runtime.execution import (IMAGE, RUNNER, PythonExecutor,
                                       _safe_docker_command, preflight)


class ExecutionContractTests(unittest.TestCase):
    def test_invalid_input_before_process(self):
        for value in ({'source': 'x'}, {'source': 3, 'input': None}, None):
            with self.assertRaises(ValueError):
                PythonExecutor()(value)
        with self.assertRaises(ValueError):
            PythonExecutor()({'source': 'x' * 262145, 'input': None})

    def test_limits_reject_booleans_and_nonliteral_numbers(self):
        for kwargs in ({'timeout': True}, {'timeout': '2'}, {'output_limit': True},
                       {'output_limit': 10.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PythonExecutor(**kwargs)

    def test_job_is_read_from_stdin_not_command_line(self):
        marker = 'PRIVATE-PAYLOAD-MARKER'
        process = {}

        def fake_popen(command, **kwargs):
            self.assertNotIn(marker, repr(command))
            self.assertIn('--interactive', command)
            self.assertEqual(kwargs['stdin'], subprocess.PIPE)
            input_read, input_write = os.pipe()
            output_read, output_write = os.pipe()
            error_read, error_write = os.pipe()

            class FakeProcess:
                stdin = os.fdopen(input_write, 'wb', buffering=0)
                stdout = os.fdopen(output_read, 'rb', buffering=0)
                stderr = os.fdopen(error_read, 'rb', buffering=0)
                returncode = None

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    worker.join(timeout)
                    if worker.is_alive():
                        raise subprocess.TimeoutExpired(command, timeout)
                    self.returncode = 0
                    return self.returncode

                def kill(self):
                    self.returncode = -9

            def consume_job():
                with os.fdopen(input_read, 'rb') as stream:
                    job = json.loads(stream.read())
                encoded = json.dumps({'result': job['input']}, separators=(',', ':')).encode()
                os.write(output_write, encoded)
                os.close(output_write)
                os.close(error_write)

            worker = threading.Thread(target=consume_job, daemon=True)
            process['worker'] = worker
            process['fake'] = FakeProcess()
            worker.start()
            return process['fake']

        cleanup_receipt = {'status': 'completed', 'return_code': 0, 'timed_out': False,
                           'elapsed_seconds': 0.001, 'stdout': '', 'stderr': ''}
        with mock.patch('kuttaka_runtime.execution.subprocess.Popen', side_effect=fake_popen), \
                mock.patch('kuttaka_runtime.execution._bounded_docker_command',
                           return_value=cleanup_receipt) as cleanup:
            result = PythonExecutor()({'source': 'def run(x): return x  # ' + marker,
                                       'input': marker})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(json.loads(result['output']), {'result': marker})
        self.assertFalse(process['worker'].is_alive())
        cleanup.assert_called_once()
        self.assertTrue(result['container_name'].startswith('astra-exec-'))
        self.assertEqual(result['cleanup'], cleanup_receipt)
        self.assertEqual(set(result['timings']), {'stdin_complete_seconds', 'first_output_seconds',
                                                   'process_exit_seconds', 'process_exit_forced',
                                                   'total_wall_seconds'})
        self.assertIn('json.load(sys.stdin)', RUNNER)
        self.assertNotIn('sys.argv', RUNNER)

    def test_timeout_records_state_only_inspect_timing_and_cleanup_outcome(self):
        process = {}
        def fake_popen(command, **kwargs):
            input_read, input_write = os.pipe()
            output_read, output_write = os.pipe()
            error_read, error_write = os.pipe()
            class FakeProcess:
                stdin = os.fdopen(input_write, 'wb', buffering=0)
                stdout = os.fdopen(output_read, 'rb', buffering=0)
                stderr = os.fdopen(error_read, 'rb', buffering=0)
                returncode = None
                def poll(self):
                    return self.returncode
                def wait(self, timeout=None):
                    if self.returncode is None:
                        raise subprocess.TimeoutExpired(command, timeout)
                    return self.returncode
                def kill(self):
                    self.returncode = -9
            process['fake'] = FakeProcess()
            process['pipes'] = (input_read, output_write, error_write)
            process['name'] = command[command.index('--name') + 1]
            return process['fake']

        calls = []
        def docker_command(command, env, *, timeout, output_limit):
            calls.append(command)
            self.assertLessEqual(timeout, 2 if 'inspect' in command else 10)
            self.assertLessEqual(output_limit, 8192)
            self.assertEqual(process['fake'].returncode, -9)  # CLI stopped before inspect/remove.
            if 'inspect' in command:
                self.assertEqual(command[-3:], ['--format', '{{json .State}}', process['name']])
                return {'status': 'completed', 'return_code': 0, 'timed_out': False,
                        'elapsed_seconds': .01, 'stdout': '{"Status":"created","Running":false}',
                        'stderr': ''}
            self.assertEqual(command[-3:], ['rm', '-f', process['name']])
            return {'status': 'timeout', 'return_code': -9, 'timed_out': True,
                    'elapsed_seconds': 2.0, 'stdout': '', 'stderr': 'cleanup timed out'}

        with mock.patch('kuttaka_runtime.execution.subprocess.Popen', side_effect=fake_popen), \
                mock.patch('kuttaka_runtime.execution._bounded_docker_command', side_effect=docker_command):
            result = PythonExecutor(timeout=.02)({'source': 'def run(x): return x', 'input': 1})
        for fd in process['pipes']:
            os.close(fd)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['exit_code'], -9)
        self.assertEqual(result['inspect_state']['stdout'], '{"Status":"created","Running":false}')
        self.assertEqual(result['cleanup']['status'], 'timeout')
        self.assertTrue(result['cleanup']['timed_out'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][-3:], ['--format', '{{json .State}}', result['container_name']])
        self.assertEqual(calls[0][-1], result['container_name'])
        self.assertEqual(calls[1][-1], result['container_name'])
        self.assertTrue(result['timings']['process_exit_forced'])
        self.assertIsNotNone(result['timings']['stdin_complete_seconds'])
        self.assertIsNone(result['timings']['first_output_seconds'])
        self.assertGreaterEqual(result['timings']['total_wall_seconds'], .02)

    def test_diagnostic_exception_is_structured_and_does_not_escape_cleanup(self):
        with mock.patch('kuttaka_runtime.execution._bounded_docker_command',
                        side_effect=RuntimeError('private diagnostic detail')):
            result = _safe_docker_command(['docker', 'rm', '-f', 'astra-exec-test'], {},
                                          timeout=2, output_limit=8192)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error_type'], 'RuntimeError')
        self.assertNotIn('private diagnostic detail', repr(result))

    @mock.patch('kuttaka_runtime.execution.shutil.which', return_value='/usr/bin/docker')
    @mock.patch('kuttaka_runtime.execution.subprocess.run')
    def test_preflight_checks_daemon_then_pinned_image_without_pull(self, run, _which):
        run.side_effect = [subprocess.CompletedProcess([], 0, b'27.0\n'),
                           subprocess.CompletedProcess([], 0, b'sha256:abc\n')]
        self.assertEqual(preflight(), {'ready': True, 'reason': 'ready', 'image': IMAGE})
        self.assertEqual(run.call_count, 2)
        self.assertIn('info', run.call_args_list[0].args[0])
        image_args = run.call_args_list[1].args[0]
        self.assertEqual(image_args[-4:], ['inspect', '--format', '{{.Id}}', IMAGE])
        self.assertNotIn('pull', image_args)

    @mock.patch('kuttaka_runtime.execution.shutil.which', return_value='/usr/bin/docker')
    @mock.patch('kuttaka_runtime.execution.subprocess.run')
    def test_preflight_reports_safe_failure_without_raw_diagnostic(self, run, _which):
        run.return_value = subprocess.CompletedProcess([], 1, b'', b'sensitive diagnostic')
        result = preflight()
        self.assertEqual(result, {'ready': False, 'reason': 'docker_daemon_unavailable', 'image': IMAGE})
        self.assertNotIn('sensitive diagnostic', repr(result))


@unittest.skipUnless(os.environ.get('ASTRA_DOCKER_TESTS') == '1', 'explicit local Docker opt-in')
class ExecutionIntegrationTests(unittest.TestCase):
    def test_execution(self):
        r = PythonExecutor()({'source':'def run(x): return list(reversed(x))', 'input':[2, 7, 9]})
        self.assertEqual(r['exit_code'], 0, r)
        self.assertEqual(r['diagnostics'], '')
        self.assertEqual(json.loads(r['output'])['result'], [9, 7, 2])

    def test_candidate_prints_do_not_corrupt_result_json(self):
        source = 'def run(x):\n    print("debug output")\n    return x + 1\n'
        r = PythonExecutor()({'source':source, 'input':4})
        self.assertEqual((r['status'], r['exit_code']), ('completed', 0), r)
        self.assertEqual(json.loads(r['output']), {'result': 5})
        self.assertIn('debug output', r['diagnostics'])

    def test_host_files_and_environment_absent(self):
        source = '''def run(x):
    import os
    return [os.path.exists('/workspace/kuttaka'), os.environ.get('ASTRA_PRIVATE_TEST'),
            any(os.environ.get(k) for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy'))]
'''
        os.environ['ASTRA_PRIVATE_TEST'] = 'not-forwarded'
        try:
            r = PythonExecutor()({'source':source, 'input':None})
        finally:
            del os.environ['ASTRA_PRIVATE_TEST']
        self.assertEqual(r['exit_code'], 0, r)
        self.assertEqual(json.loads(r['output'])['result'], [False, None, False])

    def test_timeout_and_cleanup(self):
        r = PythonExecutor(timeout=2)({'source':'def run(x):\n while True: pass', 'input':None})
        self.assertEqual(r['status'], 'timeout')

    def test_output_cap(self):
        r = PythonExecutor(output_limit=100)({'source':'def run(x):\n print("x"*10000)', 'input':None})
        self.assertEqual(r['status'], 'output_limit')
        self.assertEqual(len(r['output'].encode()) + len(r['diagnostics'].encode()), 100)

    def test_read_only_root(self):
        r = PythonExecutor()({'source':'def run(x):\n open("/owned", "w").write("x")', 'input':None})
        self.assertNotEqual(r['exit_code'], 0)
