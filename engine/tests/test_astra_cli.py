"""CLI boundaries; real HTTP/container execution is exercised by the operational runner."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.cli import main
from kuttaka_runtime.programs import ProgramTask


class CliTests(unittest.TestCase):
    @mock.patch('kuttaka_runtime.cli.execute', return_value=0)
    def test_default_muse_profile_and_protocol_remain_explicit(self, execute):
        with mock.patch.dict(os.environ, {'ASTRA_PROTOCOL': 'openai'}):
            self.assertEqual(main(['run', '--profile', 'muse-glimmer', '--task', 'fixture.json']), 0)
        args = execute.call_args.args[0]
        self.assertEqual((args.max_tokens, args.temperature, args.top_p, args.reasoning_effort),
                         (8192, 0.95, 1.0, 'high'))
        self.assertEqual(args.reply_format, 'strict')
        for option in ('stream', 'network_secret', 'normalize_replies', 'automatic_submission',
                       'continue_transport_failures', 'provider_sampling_defaults'):
            self.assertFalse(getattr(args, option), option)
        self.assertIsNone(args.outage_limit)

    @mock.patch('kuttaka_runtime.cli.preflight', return_value={'ready': True})
    def test_declared_network_secret_is_not_reported_verified(self, _preflight):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            status = main(['doctor', '--profile', 'muse-glimmer', '--network-secret'])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertFalse(report['credential_present'])
        self.assertFalse(report['ready_for_live_run'])
        self.assertEqual(report['credential_mode'], 'network_secret_declared_unverified')
        self.assertFalse(report['model_endpoint_contacted'])

    @mock.patch('kuttaka_runtime.cli.execute')
    def test_conflicting_or_unbounded_options_refused_before_execution(self, execute):
        for options in (['--network-secret', '--no-api-key'],
                        ['--provider-sampling-defaults', '--temperature', '0.5'],
                        ['--continue-transport-failures'], ['--outage-limit', '5'],
                        ['--continue-transport-failures', '--outage-limit', '0']):
            with self.subTest(options=options), redirect_stdout(io.StringIO()):
                self.assertEqual(main(['run', '--task', 'fixture.json', *options]), 2)
        execute.assert_not_called()

    @mock.patch('kuttaka_runtime.cli.execute', return_value=0)
    def test_transport_and_repair_options_require_explicit_selection(self, execute):
        status = main(['run', '--task', 'fixture.json', '--profile', 'muse-glimmer',
                       '--stream', '--reasoning-hint', 'low', '--provider-sampling-defaults',
                       '--reply-format', 'extract', '--normalize-replies', '--automatic-submission',
                       '--continue-transport-failures', '--outage-limit', '5'])
        self.assertEqual(status, 0)
        args = execute.call_args.args[0]
        self.assertTrue(args.stream)
        self.assertTrue(args.provider_sampling_defaults)
        self.assertEqual((args.reasoning_hint, args.reply_format, args.outage_limit), ('low', 'extract', 5))
        self.assertTrue(args.normalize_replies)
        self.assertTrue(args.automatic_submission)

    @mock.patch('kuttaka_runtime.cli.preflight', return_value={'ready': True})
    def test_muse_profile_uses_user_named_secret_variable(self, _preflight):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {'NVIDIA_API_KEY': 'test-private-key', 'ASTRA_PROTOCOL': 'openai'}), redirect_stdout(output):
            status = main(['doctor', '--profile', 'muse-glimmer'])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())['credential_variable'], 'NVIDIA_API_KEY')
        self.assertNotIn('test-private-key', output.getvalue())

    @mock.patch('kuttaka_runtime.cli.preflight', return_value={'ready': True, 'reason': 'ready'})
    def test_doctor_reports_credential_presence_without_value_or_request(self, _preflight):
        output = io.StringIO()
        with mock.patch.dict(os.environ, {'ASTRA_TEST_KEY': 'do-not-print-this-value'}), redirect_stdout(output):
            status = main(['doctor', '--base-url', 'https://example.invalid/v1', '--model', 'test',
                           '--key-env', 'ASTRA_TEST_KEY'])
        self.assertEqual(status, 0)
        self.assertTrue(json.loads(output.getvalue())['ready_for_live_run'])
        self.assertFalse(json.loads(output.getvalue())['model_endpoint_contacted'])
        self.assertNotIn('do-not-print-this-value', output.getvalue())

    @mock.patch('kuttaka_runtime.cli.preflight', return_value={'ready': True, 'reason': 'ready'})
    def test_missing_model_configuration_does_not_create_run(self, _preflight):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / 'task.json'
            task.write_text(json.dumps({'request': 'echo', 'checks': [{'input': 1, 'expected': 1}]}))
            output = io.StringIO()
            with mock.patch.dict(os.environ, {'ASTRA_BASE_URL': '', 'ASTRA_MODEL': ''}), redirect_stdout(output):
                status = main(['run', '--task', str(task), '--output', str(root / 'run')])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(output.getvalue())['status'], 'configuration_error')
            self.assertFalse((root / 'run').exists())

    def reuse_case(self, executor_response):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        manifest = {'request': 'double', 'checks': [{'input': 2, 'expected': 4}], 'input': 3}
        task = root / 'task.json'
        task.write_text(json.dumps(manifest))
        with ArtifactMemory(root / 'memory') as memory:
            aid = memory.put({'kind': 'method', 'body': {'language': 'python', 'name': 'double',
                'description': 'double integer', 'source': 'def run(x): return 2*x'},
                'dependencies': [], 'provenance': 'unit fixture', 'applicability': {}})
            # A fresh broker must independently recheck even an opaque old attestation.
            memory.qualify(aid, ProgramTask.from_dict(manifest).scope_id, 'untrusted-old-checker', ['old-evidence'])
        executor = mock.Mock(side_effect=executor_response)
        executor.image = 'test-image'
        output = io.StringIO()
        with mock.patch('kuttaka_runtime.cli.preflight', return_value={'ready': True}), \
                mock.patch('kuttaka_runtime.cli.PythonExecutor', return_value=executor), redirect_stdout(output):
            status = main(['reuse', '--task', str(task), '--artifact', aid,
                           '--memory', str(root / 'memory'), '--output', str(root / 'run')])
        return root, status, json.loads(output.getvalue()), executor

    def test_reuse_revalidates_then_executes_new_input_without_model(self):
        root, status, report, executor = self.reuse_case(lambda job: {
            'status': 'completed', 'exit_code': 0, 'output': json.dumps({'result': job['input'] * 2}),
            'diagnostics': ''})
        self.assertEqual(status, 0, report)
        self.assertEqual(report['deployment']['result'], 6)
        self.assertEqual((report['model_calls'], report['execution_attempts']), (0, 2))
        self.assertEqual([call.args[0]['input'] for call in executor.call_args_list], [2, 3])
        self.assertEqual(json.loads((root / 'run/result.json').read_text()), report)
        self.assertEqual((root / 'run/solution.py').read_text(), 'def run(x): return 2*x')

    def test_deployment_failure_does_not_report_completed_task(self):
        _, status, report, _ = self.reuse_case([
            {'status': 'completed', 'exit_code': 0, 'output': '{"result":4}', 'diagnostics': ''},
            {'status': 'timeout', 'exit_code': -9, 'output': '', 'diagnostics': ''}])
        self.assertEqual(status, 1)
        self.assertEqual(report['status'], 'incomplete')
        self.assertIn('deployment execution failed', report['reason'])
