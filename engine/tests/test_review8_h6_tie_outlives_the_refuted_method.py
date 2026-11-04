"""H6. The tie record (solve.py:2065 at 11e0ea9) is kept per prompt with no link to the method it was raised
against, and the void at the end (solve.py:1564-1577) asks only whether a supported check on the input exists.
Scenario: the rival round ties 2-2 on the requested input 40 (method W 800 + an auditor that errs at 40 every time,
against rival R 820 + reference 820) and, in the same round, refutes W on 41 (R + auditor + reference). The next
attempt writes RIGHT, which passes every check and agrees with the reference on every probe, 40 included, so
nothing ever adds a check at 40: RIGHT's 820 (with R and the reference; W refuted) is void, "disputed, two
derivations each way".

Control: the same script with an auditor right at 40 (no tie) delivers 820. Scripted models; local executor;
every method, the reference and the auditor are written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_review7_g2_stale_tie_voids_settled_answer import REFERENCE, RIGHT, RIVAL, TASK, Actor
from tests.test_solve_review_regressions import check, events

from kuttaka_runtime.demo import load_method


def solve(errs_at_40):
    def auditor(context):
        return {'expected': [n * n // 2 if (errs_at_40 and n == 40) else n * (n + 1) // 2 for n in context['inputs']]}
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
                      'reference': REFERENCE, 'small_inputs': [3, 4, 40, 41]}
    m = Metered(Actor(plan, auditor), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK), solver


class H6TieOutlivesTheRefutedMethod(unittest.TestCase):
    def test_control_no_tie_delivers(self):
        result, _ = solve(errs_at_40=False)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 820))

    def test_the_tie_against_a_refuted_method_voids_the_right_answer(self):
        result, solver = solve(errs_at_40=True)
        print('\nstatus', result['status'], '|', result.get('reason'),
              '\n  checks at the end:', result['checks'],
              '\n  refuted:', [e['reason'][:40] for e in events(result, 'solve_refuted')],
              '\n  tie record:', getattr(solver, '_voices', None),
              '\n  voided artifact is RIGHT:', [load_method(solver.ws.memory, e['artifact'])[0] == RIGHT
                                                 for e in events(result, 'solve_answer_disputed')])
        self.assertEqual(result['status'], 'checked', 'a tie raised against a refuted method voids the right answer')


if __name__ == '__main__':
    unittest.main()
