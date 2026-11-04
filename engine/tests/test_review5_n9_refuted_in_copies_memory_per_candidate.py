"""N9 (performance). adopt and prastāra now call _refuted_in on every candidate; once anything in the workspace
is refuted, _refuted_in calls demo.load_method, which copies the whole memory directory and replays its
event log (re-reading every blob) for each candidate, before prastāra even reads the candidate's examples
from the memory it already holds open. Measured: about 0.36 s per call with 1500 stored methods, so the
recall of one prastāra pass (and adopt, and again after _complete_given, and in every subtask) costs whole
seconds of copying. Here: counts of load_method calls in one solve. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

import kuttaka_runtime.demo as demo
from kuttaka_runtime.solve import Workspace
from kuttaka_runtime.store import Journal
from tests.test_solve_review_regressions import check, store
from tests.test_solve_review_three_prastara import solve_with, stored


class N9RefutedInCopiesMemoryPerCandidate(unittest.TestCase):
    def test_memory_copies_per_solve_grow_with_the_library(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = None
        for k in range(1, 11):
            artifact, solver = stored(ws, 'Add %d to a number.' % k, [check(2, 2 + k), check(3, 3 + k)],
                                      'def run(x):\n    return x + %d\n' % k)
            first = first or (artifact, solver)
        artifact, solver = first
        solver._job = Journal(ws.root / 'refute.jsonl')
        try:
            solver._refute(artifact, 'shown wrong by a later task', 'Add 1 to a number.')   # one refutation anywhere
        except TypeError:   # cabfada: no task argument
            solver._refute(artifact, 'shown wrong by a later task')
        solver._job.close()
        solver._job = None
        calls = []
        original = demo.load_method
        demo.load_method = lambda memory_dir, a: calls.append(a) or original(memory_dir, a)
        try:
            result, _, _ = solve_with(ws, 'Cube a number.', [check(2, 8), check(3, 27)],
                                      lambda r: store('def run(x):\n    return x ** 3\n'))
        finally:
            demo.load_method = original
        print('\nstatus:', result['status'], '| load_method calls (each a full memory copy):', len(calls))
        self.assertEqual(result['status'], 'checked')
        self.assertLessEqual(len(calls), 3, 'one memory copy per recalled candidate')


if __name__ == '__main__':
    unittest.main()
