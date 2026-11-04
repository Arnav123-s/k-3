"""N10. rival(): where the reference survived settlement, it decides any input beyond the checks on which
it agrees with one side, and that input is taken out of what the blind auditor is asked, even when the
auditor is asked in the same round anyway (here: for a contested hand check). The reference is the
planner's "slow but obviously correct way, for small inputs"; the probes include the requested input,
which need not be small. A bounded reference that agrees with a rival on the large requested input
refutes the correct method (by id for every task, by source for this one) and confirms a wrong check for
the task, with no blind re-derivation of that input. Before the fix the auditor (correct here) was asked
on it, upheld the method, and the right answer was delivered. Scripted models; local executor; every
method, the reference and the auditor are written for this test."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import check
from tests.test_solve_pramana import Rewriting

TASK = 'Triangular number: run(n) returns 1 + 2 + ... + n.'
REFERENCE = ('def reference(n):\n    if n < 1:\n        raise ValueError("small positive n only")\n'
             '    total = 0\n    for k in range(1, min(n, 1000) + 1):\n        total += k\n    return total\n')
RIVAL = 'def run(n):\n    if n < 1:\n        return -1\n    return sum(range(1, min(n, 1000) + 1))\n'
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'


def solve():
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 5000,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
                      'reference': REFERENCE, 'small_inputs': [3, 4]}
    model = Rewriting(plan, RIVAL, RIGHT, audit=lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]})
    m = Metered(model, {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK), solver


class N10ReferenceVoiceOverrulesAuditor(unittest.TestCase):
    def test_the_right_method_is_refuted_on_the_reference_outside_its_domain(self):
        result, solver = solve()
        journal = Journal.read(Path(result['solve_journal']))
        rivals = [e['body'].get('outcome') for e in journal if e['kind'] == 'solve_rival']
        print('\nrival outcomes:', rivals, '| status:', result['status'], '| answer:', result.get('answer'),
              '\nrefuted:', [r['reason'][:40] for r in solver.ws._read(solver.ws.refuted_path)],
              '| confirmed:', json.dumps(solver.ws.confirmed(TASK)))
        self.assertNotIn(check(5000, 500500), solver.ws.confirmed(TASK), 'a wrong value is kept as settled for the task')
        self.assertEqual((result['status'], result.get('answer')), ('checked', 12502500))


if __name__ == '__main__':
    unittest.main()
