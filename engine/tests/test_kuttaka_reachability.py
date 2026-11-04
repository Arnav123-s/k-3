import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime.campaign import Ceiling, TransportOutage, run_campaign
from kuttaka_runtime.models import connect_target, tcp_reachable
from kuttaka_runtime.runtime import Budget
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import STREAM, Scripted, local_executor


def flaky(down):
    """Reports the network as down ``down`` times, then up."""
    state = {'left': down}

    def reach():
        if state['left']:
            state['left'] -= 1
            return False
        return True
    return reach


class ReachabilityTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def run_arm(self, name, model):
        return run_campaign(STREAM, self.dir / name, 'KUTTAKA', model, budget=Budget(6, 12),
                            executor=local_executor)

    def test_waiting_out_a_refusing_network_spends_no_call_and_leaves_journals_unchanged(self):
        plain = Ceiling(Scripted(), 50)
        rows = self.run_arm('plain', plain)
        sleeps = []
        waited = Ceiling(Scripted(), 50, reach=flaky(3), reach_wait=60, sleep=sleeps.append)
        again = self.run_arm('waited', waited)
        self.assertEqual(sleeps, [2, 4, 8])
        self.assertEqual(waited.unreachable_checks, 3)
        self.assertEqual(waited.used, plain.used)
        self.assertEqual([r['journal_sha256'] for r in again], [r['journal_sha256'] for r in rows])

    def test_a_network_that_stays_down_stops_the_arm_before_any_attempt(self):
        model = Ceiling(Scripted(), 50, reach=lambda: False, reach_wait=10, sleep=lambda s: None)
        with self.assertRaises(TransportOutage):
            self.run_arm('down', model)
        self.assertEqual(model.used, 0)
        events = Journal.read(self.dir / 'down' / 'runs' / 't1' / 'events.jsonl')
        self.assertNotIn('model_attempt', [e['kind'] for e in events])

    def test_reach_and_wait_must_be_given_together(self):
        with self.assertRaises(ValueError):
            Ceiling(Scripted(), 5, reach=lambda: True)
        with self.assertRaises(ValueError):
            Ceiling(Scripted(), 5, reach_wait=30)


class ProbeTests(unittest.TestCase):
    def test_probe_sees_an_open_and_a_closed_port(self):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        port = server.getsockname()[1]
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(tcp_reachable(f'http://127.0.0.1:{port}/v1'))
            server.close()
            self.assertFalse(tcp_reachable(f'http://127.0.0.1:{port}/v1', timeout=1))

    def test_probe_targets_the_proxy_when_one_is_configured(self):
        with mock.patch.dict(os.environ, {'https_proxy': 'http://proxy.internal:3128'}, clear=True):
            self.assertEqual(connect_target('https://integrate.api.nvidia.com/v1'), ('proxy.internal', 3128))
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(connect_target('https://integrate.api.nvidia.com/v1'),
                             ('integrate.api.nvidia.com', 443))


if __name__ == '__main__':
    unittest.main()
