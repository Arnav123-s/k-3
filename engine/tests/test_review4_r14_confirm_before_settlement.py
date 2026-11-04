"""R14. The user confirms the checks (CLI, Solver.confirm) before _complete_given asks for a reference and
settles them again: a check the user approved is then revised, and the user never sees the checks the
method is written to. docs/ARCHITECTURE.md row 6 says the user sees the settled checks.
Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store
from tests.test_solve_pramana import DIGITS, DIGIT_REFERENCE, DIGIT_SUM_COUNT, HAND_CHECKS, Auditing


class R14ConfirmBeforeSettlement(unittest.TestCase):
    def test_the_checks_shown_for_confirmation_are_not_the_checks_used(self):
        def plan(context):
            out = {'contract': 'count k <= n with a prime digit sum', 'input': None, 'checks': HAND_CHECKS}
            if 'given_conditions_missing' in context:
                out['reference'] = DIGIT_REFERENCE
            return out
        shown = []
        model = Model(plan, lambda r: store(DIGIT_SUM_COUNT), audit=Auditing(DIGIT_SUM_COUNT))
        m = Metered(model, {'used': 0, 'limit': 30})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                        max_executions=256, say=lambda _: None, confirm=lambda spec: shown.append(spec['checks']) or True)
        result = solver.solve(DIGITS)
        print('\nconfirmed 99 ->', [c['expected'] for c in shown[0] if c['input'] == {'n': 99}],
              '| used 99 ->', [c['expected'] for c in result['checks'] if c['input'] == {'n': 99}])
        self.assertEqual(shown[-1], result['checks'], 'the user approved checks that were changed afterwards')


if __name__ == '__main__':
    unittest.main()
