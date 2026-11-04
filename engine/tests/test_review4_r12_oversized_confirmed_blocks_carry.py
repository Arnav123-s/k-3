"""R12. _extend_checks writes every confirmed check to confirmed.jsonl before the check book accepts it. A
confirmed value the check contract cannot hold (a 70000-letter string) is kept, and on the next round or
replan _carry_confirmed builds one CheckBook with all carried checks: it fails, and no confirmed check of
that task is carried again, the small valid one included. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store

PROMPT = 'Letters: run(n) returns a string of n letters a.'


def plan(context):
    return {'contract': 'run(n) returns "a" repeated n times', 'input': None,
            'checks': [check(0, ''), check(1, 'a'), check(3, 'aaa')],
            'reference': 'def reference(n):\n    return "a" * n\n', 'small_inputs': [6, 70000]}


def solve(ws, source):
    m = Metered(Model(plan, lambda r: store(source), audit=lambda c: {'expected': ['a' * n for n in c['inputs']]}),
                {'used': 0, 'limit': 40})
    return Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                  say=lambda _: None).solve(PROMPT)


class R12OversizedConfirmedBlocksCarry(unittest.TestCase):
    def test_the_valid_confirmed_check_is_never_carried_again(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        solve(ws, 'def run(n):\n    return "a" * min(n, 5)\n')          # round 1: refuted on 6 and 70000
        confirmed = {c['input'] for c in ws.confirmed(PROMPT)}
        # Fixed: a confirmed value the check contract cannot hold is no longer kept at all.
        self.assertIn(6, confirmed)
        self.assertNotIn(70000, confirmed)
        again = solve(ws, 'def run(n):\n    return "a" * n\n')            # round 2, same task
        kinds = [e['kind'] for e in Journal.read(Path(again['solve_journal']))]
        print('\nround 2 carried:', 'solve_confirmed_carried' in kinds, '| not carried:', 'solve_confirmed_not_carried' in kinds,
              '| check 6 present:', check(6, 'aaaaaa') in again['checks'])
        self.assertIn(check(6, 'aaaaaa'), again['checks'], 'the valid confirmed check was not carried')


if __name__ == '__main__':
    unittest.main()
