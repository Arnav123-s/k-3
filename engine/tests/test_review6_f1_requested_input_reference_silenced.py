"""F1. rival() (solve.py:1974-1976 at 324e3ce) never asks the reference about the requested input, even when
the planner lists it among the small inputs the reference is written for. On the requested input the method
and a blind auditor that errs the same way then outvote the rival and the reference (two against two, which
the fix's own rule says decides nothing): a wrong value is added as a TWO_DERIVATIONS check and the wrong answer
is delivered as checked. At 2739320 the reference sided with the rival, the wrong method was refuted and no
answer was delivered (incomplete; a later blind audit also settles 800 there, a pre-existing path, so only the
delivery is asserted). Scripted models; local executor; every method, the reference and the
auditor are written for this test."""
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
REFERENCE = ('def reference(n):\n    if n < 1:\n        raise ValueError("positive n only")\n'
             '    total = 0\n    for k in range(1, n + 1):\n        total += k\n    return total\n')
RIVAL = 'def run(n):\n    if n < 1:\n        return -1\n    return n * (n + 1) // 2\n'           # right for n >= 1
WRONG = 'def run(n):\n    return n * (n + 1) // 2 if n <= 30 else n * n // 2\n'          # wrong above 30


def auditor(context):   # a blind re-derivation that errs the way the method does above 30
    return {'expected': [n * (n + 1) // 2 if n <= 30 else n * n // 2 for n in context['inputs']]}


def solve():
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
                      'reference': REFERENCE, 'small_inputs': [3, 4, 40]}     # 40 is declared small
    model = Rewriting(plan, RIVAL, WRONG, audit=auditor)
    m = Metered(model, {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK), solver


class F1RequestedInputReferenceSilenced(unittest.TestCase):
    def test_a_wrong_answer_is_delivered_on_two_against_two(self):
        result, solver = solve()
        journal = Journal.read(Path(result['solve_journal']))
        rivals = [(e['body'].get('outcome'), e['body'].get('rival_contradicted')) for e in journal
                  if e['kind'] == 'solve_rival' and e['body'].get('outcome')]
        print('\nrival outcomes:', rivals, '| status:', result['status'], '| answer:', result.get('answer'),
              '(right: 820) | confirmed for the task:', json.dumps(solver.ws.confirmed(TASK)))
        self.assertNotEqual(result.get('answer'), 800, 'the wrong answer is delivered')


if __name__ == '__main__':
    unittest.main()
