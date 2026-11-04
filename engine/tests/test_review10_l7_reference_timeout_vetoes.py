"""L7 (completion lost; a regression against 3af0808, by the gate docstring's "a sandbox failure" rule). _answer_gate
(kuttaka_runtime/solve.py:1685-1690 at 1ebed00) voids the answer when the planner's reference "could not be run" on a
requested input declared small: any sandbox result that is not a one-element list, which includes a timeout. The
reference runs there under the check executor (self._sandbox -> self.executor, 20 s in solve_task), not the deploy
executor the method's own final run gets (deploy, line 1240; solve_task builds it with --run-timeout). A slow
reference, which is what the planner is asked for ("a slow but obviously correct way"), that cannot finish on the
input the planner called small now vetoes a right answer nobody disputes, while a reference that RAISES there "says
nothing" (lines 1696-1698) and the answer is delivered. ARCHITECTURE row 15 names only "no execution left".

Scenario: T(n) = n(n+1)/2, requested input 40, declared small; RIGHT is right everywhere; no voice disputes it. The
executor stand-in answers the reference's jobs that hold 40 as the Docker executor answers a timeout ({'status':
'timeout'}); everything else runs in the local executor. At 1ebed00: void ("the reference could not be run"). At
3af0808: 820 delivered.

Controls: the same script with a reference that finishes delivers 820; with a reference that raises on 40, 820
is delivered too. Scripted models; local executor; every method and the reference are written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + ... + n.'
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'
REFERENCE = 'def reference(n):\n    return sum(range(n + 1))\n'
RAISES = 'def reference(n):\n    if n >= 40:\n        raise RecursionError("too deep")\n    return sum(range(n + 1))\n'


def executor(slow):
    def run(job):
        if slow and 'def reference' in job.get('source', '') and isinstance(job.get('input'), list) \
                and 40 in job['input']:
            return {'status': 'timeout', 'exit_code': None, 'output': '', 'diagnostics': ''}   # as PythonExecutor
        return local_executor(job)
    return run


def solve(reference, slow):
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                      'checks': [check(1, 1), check(2, 3), check(5, 15)],
                      'reference': reference, 'small_inputs': [3, 4, 40]}
    m = Metered(Model(plan, lambda r: store(RIGHT)), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, executor(slow), splitter=m, auditor=m, sketcher=m,
                    max_executions=256, say=lambda _: None)
    return solver.solve(TASK)


class L7ReferenceTimeoutVetoes(unittest.TestCase):
    def test_control_a_reference_that_finishes(self):
        result = solve(REFERENCE, slow=False)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 820))

    def test_control_a_reference_that_raises_says_nothing(self):
        result = solve(RAISES, slow=False)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 820))

    def test_a_reference_that_times_out_vetoes_a_right_answer(self):
        result = solve(REFERENCE, slow=True)
        print('\nslow reference: status', result['status'], '| answer', result.get('answer'), '|', result.get('reason'),
              '| disputed by', [e.get('by') for e in events(result, 'solve_answer_disputed')])
        self.assertEqual((result['status'], result.get('answer')), ('checked', 820),
                         'a reference too slow for the check executor voids an undisputed right answer')


if __name__ == '__main__':
    unittest.main()
