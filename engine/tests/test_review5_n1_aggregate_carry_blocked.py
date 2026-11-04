"""N1. R12 is fixed only for a single oversized value. _remember_confirmed and _carry_confirmed test each
confirmed check alone against the contract (holds()), but _carry_confirmed still builds ONE CheckBook from
all of them, and the CheckBook also limits the whole proposal list (256 KiB) and the whole record set
(10000 nodes). Several confirmed checks, each within the limits, together exceed them: the combined book
fails and no confirmed check of the task is carried, the small valid one included (R12's failure mode).
Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store

PROMPT = 'Letters: run(n) returns a string of n letters a.'
BIG = [55000, 55001, 55002, 55003, 55004]   # each 'a' * n is < 65536 chars and < 256 KiB: holds alone


def plan(context):
    return {'contract': 'run(n) returns "a" repeated n times', 'input': None,
            'checks': [check(0, ''), check(1, 'a'), check(3, 'aaa')],
            'reference': 'def reference(n):\n    return "a" * n\n', 'small_inputs': [6] + BIG}


def solve(ws, source):
    m = Metered(Model(plan, lambda r: store(source), audit=lambda c: {'expected': ['a' * n for n in c['inputs']]}),
                {'used': 0, 'limit': 40})
    return Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                  say=lambda _: None).solve(PROMPT)


class N1AggregateLimitBlocksEveryCarry(unittest.TestCase):
    def test_the_small_confirmed_check_is_not_carried_when_the_others_together_are_too_big(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = solve(ws, 'def run(n):\n    return "a" * min(n, 5)\n')     # round 1: refuted on all six probes
        confirmed = {c['input'] for c in ws.confirmed(PROMPT)}
        print('\nround 1:', first['status'], '| confirmed kept:', sorted(confirmed))
        self.assertIn(6, confirmed)
        self.assertTrue(set(BIG) <= confirmed, 'each big check holds alone, so each is kept')
        again = solve(ws, 'def run(n):\n    return "a" * n\n')            # round 2, same task
        body = [e['body'] for e in Journal.read(Path(again['solve_journal'])) if e['kind'] == 'solve_confirmed_not_carried']
        print('round 2 not carried:', body[:1], '| check 6 present:', check(6, 'aaaaaa') in again['checks'])
        self.assertIn(check(6, 'aaaaaa'), again['checks'],
                      'the valid confirmed check was not carried: the carried checks are not carried one by one')


if __name__ == '__main__':
    unittest.main()
