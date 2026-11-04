"""N6. R14's fix asks the user again after the late settlement (_solve_planned: `spec['check_scope'] !=
scope ... self.confirm(spec)`), but nothing shows the user the changed checks first: the checks are
printed (say) only once, before the first question, and the CLI's confirm (solve._confirm) prints only
"Use these checks? [Y/n]". On the CLI the user approves a revised check (n=99 -> 37) never shown;
the only line between the two questions is "1 hand-worked value(s) corrected". The R14 test passes because
its confirm callable reads spec itself. Scripted models; local executor; same scenario as the R14 test."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store
from tests.test_solve_pramana import DIGITS, DIGIT_REFERENCE, DIGIT_SUM_COUNT, HAND_CHECKS, Auditing


class N6ReconfirmWithoutShowing(unittest.TestCase):
    def test_the_user_is_asked_to_approve_checks_that_were_never_printed(self):
        def plan(context):
            out = {'contract': 'count k <= n with a prime digit sum', 'input': None, 'checks': HAND_CHECKS}
            if 'given_conditions_missing' in context:
                out['reference'] = DIGIT_REFERENCE
            return out
        printed, asked = [], []

        def cli_confirm(spec):   # what the CLI user has seen when solve._confirm asks "Use these checks?"
            asked.append((list(printed), spec['checks']))
            return True
        model = Model(plan, lambda r: store(DIGIT_SUM_COUNT), audit=Auditing(DIGIT_SUM_COUNT))
        m = Metered(model, {'used': 0, 'limit': 30})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                        max_executions=256, say=printed.append, confirm=cli_confirm)
        solver.solve(DIGITS)
        self.assertEqual(len(asked), 2)
        seen, checks = asked[-1]
        print('\nprinted before the second question:', seen)
        revised = json.dumps(check({'n': 99}, 37))[:-1]          # '{"input": {"n": 99}, "expected": 37'
        self.assertIn(check({'n': 99}, 37), checks)
        self.assertTrue(any(revised in line for line in seen),
                        'the user is asked to approve a revised check (99 -> 37) that was never shown')


if __name__ == '__main__':
    unittest.main()
