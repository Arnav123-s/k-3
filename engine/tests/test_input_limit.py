"""One sandbox input limit for every job (found from the hard runs' rows; no hidden case was opened).

Until 2026-11-01 a job without libraries was refused above 32 KiB before the method ran. The scorer's
jobs carry no libraries, so a hidden case with a large input failed for every method: on
h-c2-substrings and h-c3-maxflow four different methods each passed exactly the same number of cases.
No Docker is needed here: the process is replaced before it starts, and no model-written code runs.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime import execution
from kuttaka_runtime.execution import INPUT_LIMIT, LEGACY_INPUT_LIMIT, PythonExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments' / 'solve_suite'))


class Started(Exception):
    """Raised in place of starting the container: the job passed every check before it."""


def reaches_sandbox(job):
    with mock.patch('kuttaka_runtime.execution.subprocess.Popen', side_effect=Started):
        try:
            PythonExecutor()(job)
        except Started:
            return True
        except ValueError:
            return False
    return False


LARGE = {'source': 'def run(x):\n    return len(x["s"])\n', 'input': {'s': 'ab' * 50000}}   # 100000 letters


class OneLimit(unittest.TestCase):
    def test_a_large_input_without_libraries_reaches_the_sandbox(self):
        self.assertGreater(len(json.dumps(LARGE)), LEGACY_INPUT_LIMIT)
        self.assertTrue(reaches_sandbox(LARGE))

    def test_control_the_old_limit_refused_the_same_job(self):
        # The detector works: under the old size the same job never reaches the sandbox.
        with mock.patch.object(execution, 'INPUT_LIMIT', LEGACY_INPUT_LIMIT):
            self.assertFalse(reaches_sandbox(LARGE))

    def test_the_one_limit_still_holds(self):
        too_large = {'source': LARGE['source'], 'input': {'s': 'a' * (INPUT_LIMIT + 1)}}
        self.assertFalse(reaches_sandbox(too_large))

    def test_a_small_job_is_unchanged(self):
        self.assertTrue(reaches_sandbox({'source': 'def run(x):\n    return x\n', 'input': 3}))


class Rescore(unittest.TestCase):
    """The rescoring tool: same delivered sources, counts only, the as-run rows untouched."""

    def setUp(self):
        import rescore
        self.rescore = rescore
        self.dir = Path(tempfile.mkdtemp())
        rows = [
            {'task': 't-large', 'status': 'checked', 'hidden_passed': 1, 'hidden_cases': 2, 'solved': False,
             'source': LARGE['source'], 'reused': [], 'subtasks': 0},
            {'task': 't-small', 'status': 'checked', 'hidden_passed': 1, 'hidden_cases': 1, 'solved': True,
             'source': 'def run(x):\n    return x\n', 'reused': [], 'subtasks': 0},
            {'task': 't-none', 'status': 'incomplete', 'hidden_passed': 0, 'hidden_cases': 1, 'solved': False,
             'source': None, 'reused': [], 'subtasks': 0},
        ]
        (self.dir / 'rows.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        self.before = (self.dir / 'rows.jsonl').read_text()
        self.hidden = {'t-large': [{'input': {'s': 'ab'}, 'expected': 2}, {'input': LARGE['input'], 'expected': 100000}],
                       't-small': [{'input': 5, 'expected': 5}],
                       't-none': [{'input': 1, 'expected': 1}]}

    def scorer(self, limit):
        """A test-written stand-in for the sandbox: the size rule of `limit`, then the methods' known results."""
        def executor(job):
            if len(json.dumps(job, separators=(',', ':')).encode()) > limit:
                raise ValueError('too large')
            x = job['input']
            value = len(x['s']) if isinstance(x, dict) else x
            return {'exit_code': 0, 'output': json.dumps({'result': value})}
        return executor

    def test_the_large_case_passes_rescored_and_counts_only_are_reported(self):
        result = self.rescore.rescore(self.dir, self.hidden, self.scorer(INPUT_LIMIT))
        by = {e['task']: e for e in result['tasks']}
        self.assertEqual(by['t-large']['over_32k'], 1)
        self.assertEqual(by['t-large']['passed_rescored'], 2)
        self.assertTrue(by['t-large']['solved_rescored'])
        self.assertFalse(by['t-large']['solved_as_run'])
        self.assertNotIn('passed_rescored', by['t-none'])   # nothing delivered: nothing to score
        self.assertEqual(result['summary']['solved_as_run'], 1)
        self.assertEqual(result['summary']['solved_rescored'], 2)
        self.assertEqual(result['summary']['unsupported_as_run'], 1)
        self.assertEqual(result['summary']['unsupported_rescored'], 0)
        text = json.dumps(result)
        self.assertNotIn('ababab', text)            # no hidden input is written
        self.assertNotIn('"expected"', text)        # nor any expected value
        self.assertEqual((self.dir / 'rows.jsonl').read_text(), self.before)

    def test_control_under_the_old_limit_the_large_case_fails_as_it_did(self):
        result = self.rescore.rescore(self.dir, self.hidden, self.scorer(LEGACY_INPUT_LIMIT))
        by = {e['task']: e for e in result['tasks']}
        self.assertEqual(by['t-large']['passed_rescored'], 1)   # the as-run count is reproduced
        self.assertFalse(by['t-large']['solved_rescored'])

    def test_a_method_whose_stored_methods_cannot_be_found_is_not_rescored(self):
        rows = [{'task': 't-small', 'status': 'checked', 'hidden_passed': 1, 'hidden_cases': 1, 'solved': True,
                 'source': 'def run(x):\n    return use("f" * 64, x)\n', 'reused': ['f' * 64], 'subtasks': 0}]
        (self.dir / 'rows.jsonl').write_text(json.dumps(rows[0]) + '\n')
        result = self.rescore.rescore(self.dir, self.hidden, self.scorer(INPUT_LIMIT))
        self.assertIn('not rescored', result['tasks'][0]['rescored'])


if __name__ == '__main__':
    unittest.main()
