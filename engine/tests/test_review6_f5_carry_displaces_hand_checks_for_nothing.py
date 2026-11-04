"""F5. _carry_confirmed (solve.py:697-707 at 324e3ce) removes hand-worked plan checks to make room for EVERY
confirmed check, then admits the confirmed checks one by one, taking only those the contract holds together.
The hand checks removed for confirmed checks that were then not taken are lost although their slots stay
free (and they would fit). Here: the plan's 12 checks n = 0..11, five confirmed checks of 55000+ letters (each
holds alone; four fit together). Five plan checks (7..11) are removed, four big checks are taken: 11 checks,
one slot free, checks 7..11 gone. A method wrong only at n = 10 is then delivered; at 2739320 the combined book
failed, the plan's checks stayed, and that method was not delivered. Scripted models; local executor; the
methods are written for this test."""
import unittest
import tempfile
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace, digest
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store

PROMPT = 'Letters: run(n) returns a string of n letters a.'
BIG = [55000, 55001, 55002, 55003, 55004]
WRONG = 'def run(n):\n    return "a" * (n - 1 if n == 10 else n)\n'


def plan(context):
    return {'contract': 'run(n) returns "a" repeated n times', 'input': None,
            'checks': [check(n, 'a' * n) for n in range(12)]}


class F5CarryDisplacesHandChecksForNothing(unittest.TestCase):
    def test_plan_checks_are_dropped_for_carried_checks_that_are_not_taken(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        given = digest({'examples': [], 'origin': 'user'})          # the identity _solve gives these words
        for n in BIG:
            ws.remember_confirmed(PROMPT, check(n, 'a' * n), given)
        m = Metered(Model(plan, lambda r: store(WRONG), audit=lambda c: {'expected': ['a' * n for n in c['inputs']]}),
                    {'used': 0, 'limit': 20})
        result = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                        say=lambda _: None).solve(PROMPT)
        carried = [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_confirmed_carried']
        inputs = sorted(c['input'] for c in result['checks'])
        print('\nchecks:', len(inputs), [i for i in inputs if i < 100], '| carried:', [len(c['carried']) for c in carried],
              '| status:', result['status'])
        self.assertTrue(len(inputs) == 12 or all(n in inputs for n in range(12)),
                        'plan checks removed to make room that was not used')
        self.assertNotEqual(result['status'], 'checked', 'the method wrong at n = 10 is delivered')


if __name__ == '__main__':
    unittest.main()
