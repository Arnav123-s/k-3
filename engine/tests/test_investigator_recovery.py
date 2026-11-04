"""Fail closed when reconstructing a missing campaign summary."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiments.investigator_transfer.recover import recover
from kuttaka_runtime.investigators import investigator_payload
from kuttaka_runtime.store import Journal, digest


MODEL = {
    'model': 'test-model', 'protocol': 'openai', 'endpoint': 'https://example.invalid',
    'generation': {'temperature': 0.95, 'top_p': 1, 'reasoning_effort': 'high',
                   'max_tokens': 8192},
    'instructions_sha256': 'instruction-hash',
}
PLAN = [['source_affine', 907, 'qualification'],
        ['source_piecewise', 911, 'qualification']]
BUDGET = {'model_calls': 10, 'tool_calls': 9, 'policy_executions': 48,
          'max_steps': 48, 'input_bytes_per_call': 24000,
          'total_model_tokens': None, 'max_response_tokens': 8192}
LIMITS = {'observations': 8, 'sandbox_executions': 49,
          'execution_timeout': 30, 'model_timeout': 300, 'process_timeout': 3900}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def _campaign(root, plan=PLAN):
    root.mkdir(parents=True, exist_ok=True)
    bundle = investigator_payload(
        "def run(input):\n    return {'state': None, 'action': {'kind': 'unresolved', 'reason': 'test'}}\n",
        name='test investigator', description='Recovery fixture')
    _write_json(root / 'bundle.json', bundle)
    campaign = {'policy_sha256': digest(bundle),
                'policy_source_sha256': bundle['provenance']['source_sha256'],
                'model': MODEL, 'budget': BUDGET, 'limits': LIMITS,
                'stage': 'qualify', 'task_plan': plan}
    _write_json(root / 'campaign.json', campaign)
    _write_json(root / 'planned-runs.json', plan)

    for index, (family, seed, arm) in enumerate(plan):
        name = f'{family}-{seed}-{arm}'
        run_dir = root / name
        run_dir.mkdir()
        task_manifest = {'task_id': f'{family}:{seed}', 'family': family, 'seed': seed,
                         'truth_sha256': f'truth-{index}', 'checks_sha256': f'checks-{index}'}
        _write_json(run_dir / 'task-manifest.json', task_manifest)
        policy_artifact = None if arm in ('ordinary', 'removed') else digest(bundle)
        run_manifest = {'family': family, 'seed': seed, 'arm': arm,
                        'task_hashes': task_manifest, 'policy_artifact': policy_artifact,
                        'limits': LIMITS}
        report = {
            'family': family, 'seed': seed, 'arm': arm,
            'result': {'status': 'checked' if index == 0 else 'incorrect',
                       'model_calls': 4, 'model_tokens_known': True},
            'score': {'passed': index == 0, 'correct_cases': 25 if index == 0 else 21,
                      'total_cases': 25},
            'costs': {'observations': 8, 'executions': 22},
            'policy_artifact': policy_artifact, 'elapsed_seconds': 98.2 + index,
            'memory_stats': {'artifacts': 1, 'events': 10},
        }
        with Journal(run_dir / 'events.jsonl') as journal:
            journal.append('experiment_manifest', run_manifest)
            journal.append('investigator_start', {'model': MODEL, 'budget': BUDGET})
            journal.append('experiment_finish', report)
        events = Journal.read(run_dir / 'events.jsonl')
        result = {**report, 'reported_usage': {'input_tokens': 7000, 'output_tokens': 1200,
                                                'complete': True},
                  'journal_tip': events[-1]['hash'],
                  'journal_sha256': hashlib.sha256((run_dir / 'events.jsonl').read_bytes()).hexdigest()}
        _write_json(run_dir / 'result.json', result)
        invocation = {
            'name': name,
            'command': ['python', '-m', 'experiments.investigator_transfer.run', '--one',
                        '--output', str(run_dir), '--family', family, '--seed', str(seed),
                        '--arm', arm],
            'started_at': '2026-10-28T00:00:00+00:00', 'exit_code': 0,
            'elapsed_seconds': report['elapsed_seconds'], 'report': result,
        }
        _write_json(root / f'{name}-invocation.json', invocation)
    return campaign


class InvestigatorRecoveryTests(unittest.TestCase):
    def test_reconstructs_completed_runs_and_preserves_failed_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'campaign'
            output = Path(directory) / 'recovered-summary.json'
            campaign = _campaign(root)

            summary = recover(root, output)

            self.assertEqual(summary['campaign'], campaign)
            self.assertEqual([run['report']['result']['status'] for run in summary['runs']],
                             ['checked', 'incorrect'])
            provenance = summary['recovery_provenance']
            self.assertEqual(provenance['planned_runs_validated'], 2)
            self.assertEqual(provenance['journal_chains_validated'], 2)
            self.assertEqual(provenance['synthetic_journals_created'], 0)
            self.assertEqual(provenance['runs_reexecuted'], 0)
            self.assertIn('cause not established', provenance['reason'])
            self.assertIn('source_affine-907-qualification/events.jsonl',
                          provenance['source_files_sha256'])
            self.assertEqual(json.loads(output.read_text()), summary)

    def test_refuses_incomplete_campaign(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'campaign'
            output = Path(directory) / 'recovered-summary.json'
            _campaign(root)
            (root / 'source_piecewise-911-qualification' / 'result.json').unlink()

            with self.assertRaises(ValueError):
                recover(root, output)
            self.assertFalse(output.exists())

    def test_refuses_tampered_journal_report_and_identifiers(self):
        for mutation in ('journal', 'report', 'identifier'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'campaign'
                output = Path(directory) / 'recovered-summary.json'
                _campaign(root)
                run = 'source_affine-907-qualification'
                if mutation == 'journal':
                    path = root / run / 'events.jsonl'
                    path.write_bytes(path.read_bytes() + b'{tampered}\n')
                elif mutation == 'report':
                    path = root / f'{run}-invocation.json'
                    invocation = json.loads(path.read_text())
                    invocation['report']['elapsed_seconds'] += 1
                    _write_json(path, invocation)
                else:
                    result_path = root / run / 'result.json'
                    result = json.loads(result_path.read_text())
                    result['seed'] = 999
                    _write_json(result_path, result)
                    invocation_path = root / f'{run}-invocation.json'
                    invocation = json.loads(invocation_path.read_text())
                    invocation['report'] = result
                    _write_json(invocation_path, invocation)

                with self.assertRaises(ValueError):
                    recover(root, output)
                self.assertFalse(output.exists())

    def test_refuses_to_overwrite_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'campaign'
            output = Path(directory) / 'recovered-summary.json'
            _campaign(root)
            output.write_text('keep existing file')

            with self.assertRaises(FileExistsError):
                recover(root, output)
            self.assertEqual(output.read_text(), 'keep existing file')

    def test_direct_control_artifact_is_none_and_recovers_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'campaign'
            output = Path(directory) / 'recovered-summary.json'
            _campaign(root, [['target_string', 811, 'ordinary']])

            summary = recover(root, output)

            report = summary['runs'][0]['report']
            self.assertEqual(report['arm'], 'ordinary')
            self.assertIsNone(report['policy_artifact'])

    def test_requires_integer_exit_and_campaign_budget_limit_source_identity(self):
        for mutation in ('bool_exit', 'budget', 'limits', 'source_hash'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'campaign'
                output = Path(directory) / 'recovered-summary.json'
                _campaign(root)
                run_name = 'source_affine-907-qualification'
                if mutation == 'bool_exit':
                    path = root / f'{run_name}-invocation.json'
                    invocation = json.loads(path.read_text())
                    invocation['exit_code'] = True
                    _write_json(path, invocation)
                else:
                    path = root / 'campaign.json'
                    campaign = json.loads(path.read_text())
                    if mutation == 'budget':
                        campaign['budget']['model_calls'] += 1
                    elif mutation == 'limits':
                        campaign['limits']['observations'] += 1
                    else:
                        campaign['policy_source_sha256'] = '0' * 64
                    _write_json(path, campaign)

                with self.assertRaises(ValueError):
                    recover(root, output)
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
