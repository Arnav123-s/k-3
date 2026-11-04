"""The Erdős #979 runner stops on a checked answer, not on a checked status (ERDŐS-979-c and -d: the solver reported
checked with no answer, and the runner stopped after that round with answer.json null). Scripted solve_task; no
model, no Docker.
  - checked with no answer, then checked with an answer: two rounds, the answer is written;
  - checked with no answer in every round: every round runs and answer.json is null.
Control: a checked answer in round 1 stops at once, as before."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RUN = Path(__file__).resolve().parents[1] / 'experiments' / 'erdos979' / 'run.py'


def load():
    spec = importlib.util.spec_from_file_location('erdos979_run', RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(results, rounds=3):
    module = load()
    out = Path(tempfile.mkdtemp()) / 'out'
    replies = iter(results)
    calls = []

    def solve_task(prompt, settings, workspace, say):
        calls.append(prompt)
        return dict(next(replies), cost={'model_calls': 1})
    argv = ['run.py', '--arm', 'kuttaka', '--out', str(out), '--rounds', str(rounds)]
    with mock.patch.object(sys, 'argv', argv), mock.patch.object(module, 'preflight', lambda: {'ready': True}), \
            mock.patch.object(module, 'settings', lambda **k: k), mock.patch.object(module, 'solve_task', solve_task):
        module.main()
    return len(calls), json.loads((out / 'answer.json').read_text())


WITNESS = {'n': 64, 'representations': [[2, 2, 2, 2]]}


class ErdosRunnerStopsOnAnAnswer(unittest.TestCase):
    def test_a_checked_status_with_no_answer_does_not_stop_the_rounds(self):
        self.assertEqual(run([{'status': 'checked', 'answer': None}, {'status': 'checked', 'answer': WITNESS}]),
                         (2, WITNESS))

    def test_no_answer_in_any_round_runs_every_round_and_writes_null(self):
        self.assertEqual(run([{'status': 'checked', 'answer': None}] * 3), (3, None))

    def test_control_a_checked_answer_stops_at_once(self):
        self.assertEqual(run([{'status': 'checked', 'answer': WITNESS}]), (1, WITNESS))


if __name__ == '__main__':
    unittest.main()
