"""Amendment C2-b: the c2 profile is C1 with replay-stable execution receipts, nothing else."""

import tempfile
import unittest
import uuid
from pathlib import Path

from kuttaka_runtime.campaign import C2_PROFILE, C3_PROFILE, CAMPAIGN_PROFILE, Ceiling, run_campaign
from kuttaka_runtime.execution import PythonExecutor, preflight
from kuttaka_runtime.runtime import Budget
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import STREAM, Scripted, local_executor


def volatile_executor(job):
    """Like the Docker executor: every execution reports a fresh container name."""
    return dict(local_executor(job), container_name='kuttaka-exec-' + uuid.uuid4().hex)


def run(base, name, profile, executor, replay_from=None):
    model = None if replay_from else Ceiling(Scripted(), 20)
    return run_campaign(STREAM, base / name, 'KUTTAKA', model, budget=Budget(6, 12), executor=executor,
                        replay_from=replay_from, reply_repair=True, profile=profile)


CHAIN_REFS = {'execution_receipt', 'proposal', 'receipt', 'check_receipts', 'receipts'}


def without_chain_refs(value):
    """Journal hashes of earlier events legitimately differ once one event differs."""
    if isinstance(value, dict):
        return {k: without_chain_refs(v) for k, v in value.items() if k not in CHAIN_REFS}
    if isinstance(value, list):
        return [without_chain_refs(v) for v in value]
    return value


def events(base, name, task):
    return Journal.read(base / name / 'runs' / task / 'events.jsonl')


class C2ProfileTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())

    def test_c2_replays_identically_where_c1_cannot(self):
        rows = run(self.base, 'c2', C2_PROFILE, volatile_executor)
        self.assertEqual([r['status'] for r in rows], ['checked', 'checked'])
        again = run(self.base, 'c2-replay', C2_PROFILE, volatile_executor, replay_from=self.base / 'c2')
        self.assertEqual([r['journal_sha256'] for r in again], [r['journal_sha256'] for r in rows])
        c1 = run(self.base, 'c1', CAMPAIGN_PROFILE, volatile_executor)
        c1_again = run(self.base, 'c1-replay', CAMPAIGN_PROFILE, volatile_executor, replay_from=self.base / 'c1')
        self.assertNotEqual([r['journal_sha256'] for r in c1_again], [r['journal_sha256'] for r in c1])

    def test_c2_differs_from_c1_only_in_dropped_sandbox_metadata_and_profile_label(self):
        run(self.base, 'c1', CAMPAIGN_PROFILE, volatile_executor)
        run(self.base, 'c2', C2_PROFILE, volatile_executor)
        for task in ('t1', 't2'):
            a, b = events(self.base, 'c1', task), events(self.base, 'c2', task)
            self.assertEqual([e['kind'] for e in a], [e['kind'] for e in b])
            for x, y in zip(a, b):
                if x['kind'] == 'program_execution_metadata':
                    self.assertEqual(set(y['body']['metadata']), {'dependency_calls'} & set(x['body']['metadata']))
                    self.assertEqual(x['body']['metadata'].get('dependency_calls'),
                                     y['body']['metadata'].get('dependency_calls'))
                elif x['kind'] not in ('program_task', 'start'):
                    self.assertEqual(without_chain_refs(x['body']), without_chain_refs(y['body']), x['kind'])

    @unittest.skipUnless(preflight()['ready'], 'Docker or the pinned image is unavailable')
    def test_c2_replays_identically_in_the_docker_sandbox(self):
        executor = PythonExecutor(timeout=10)
        rows = run(self.base, 'c2', C2_PROFILE, executor)
        again = run(self.base, 'c2-replay', C2_PROFILE, executor, replay_from=self.base / 'c2')
        self.assertEqual([r['status'] for r in rows], ['checked', 'checked'])
        self.assertEqual([r['journal_sha256'] for r in again], [r['journal_sha256'] for r in rows])


class C2ModelTests(unittest.TestCase):
    """Amendment C2-c: the c2 model adapter is configured as C1's was at ef81a25."""

    def args(self):
        from argparse import Namespace
        return Namespace(model='meta/llama-3.2-90b-vision-instruct', base_url='https://integrate.api.nvidia.com/v1',
                         model_timeout=600, max_tokens=4096)

    def test_c2_uses_the_managed_credential_without_a_key_and_extracts_wrapped_json(self):
        import os
        from unittest import mock
        from kuttaka_runtime.campaign import _model
        with mock.patch.dict(os.environ, {}, clear=True):
            c2, c1 = _model(self.args(), profile=C2_PROFILE), _model(self.args())
            c3 = _model(self.args(), profile=C3_PROFILE)
        self.assertTrue(c2.network_secret)
        self.assertEqual(c2.parse_mode, 'extract')
        self.assertFalse(c1.network_secret)
        self.assertEqual(c1.parse_mode, 'strict')
        self.assertTrue(c3.network_secret)
        self.assertEqual(c3.parse_mode, 'extract')
        with mock.patch.dict(os.environ, {'NVIDIA_API_KEY': 'x'}, clear=True):
            self.assertFalse(_model(self.args(), profile=C2_PROFILE).network_secret)

    def test_extract_mode_reads_the_action_inside_a_fence_like_c1(self):
        from kuttaka_runtime.models import parse_action
        reply = 'Here is my action:\n```json\n{"kind": "answer", "answer": {"artifact": "a"}}\n```'
        self.assertEqual(parse_action(reply, parse_mode='extract')['kind'], 'answer')
        self.assertEqual(parse_action(reply)['kind'], 'invalid')


if __name__ == '__main__':
    unittest.main()
