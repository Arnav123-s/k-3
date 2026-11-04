"""N2. rival() now ignores any rival that contests a check whose support label is not HAND (solve.py
rival: `supported`). _settle_given labels every check a property accepts PROPERTY, and tests whether the
property tells values apart only for checks the reference disputes (`weak`). A property that accepts any
value ("the count is a non-negative int") thus labels every hand value PROPERTY, and the rival can no
longer contest a wrong hand value: the formula that fits the wrong check is delivered, the right method
is set aside. The project's own rule is that a property that accepts both values is no witness.
Control: the same run without the property delivers the right method (RivalTests h-m1-digits).
Scripted models; local executor; the auditor is written for this test."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import check
from tests.test_solve_pramana import DIGITS, DIGIT_SUM_COUNT, MOD9, HAND_CHECKS, Auditing, Rewriting

WEAK = 'def verify(input, output):\n    return isinstance(output, int) and output >= 0\n'


def solve(verify):
    plan = lambda c: dict({'contract': 'count k <= n with a prime digit sum', 'input': None,
                           'checks': HAND_CHECKS}, **({'verify': verify} if verify else {}))
    model = Rewriting(plan, DIGIT_SUM_COUNT, MOD9, audit=Auditing(DIGIT_SUM_COUNT))  # right first, fails 99 -> 44
    m = Metered(model, {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(DIGITS)


class N2WeakPropertyShieldsWrongHandCheck(unittest.TestCase):
    def test_control_without_the_property_the_right_method_is_delivered(self):
        result = solve(None)
        self.assertEqual((result['status'], result.get('source')), ('checked', DIGIT_SUM_COUNT))

    def test_a_property_that_accepts_anything_makes_the_wrong_check_uncontestable(self):
        result = solve(WEAK)
        journal = [e for e in Journal.read(Path(result['solve_journal']))]
        rivals = [e['body'].get('outcome') for e in journal if e['kind'] == 'solve_rival']
        given = [e['body'] for e in journal if e['kind'] == 'solve_given_conditions']
        print('\nsupport after settlement:', given[0]['support'] if given else None,
              '\nrival outcomes:', rivals, '| status:', result['status'],
              '| delivered MOD9:', result.get('source') == MOD9, '| checks:', json.dumps(result.get('checks')))
        self.assertNotEqual(result.get('source'), MOD9,
                            'the formula fitted to the wrong hand value 44 was delivered; the right rival was ignored')


if __name__ == '__main__':
    unittest.main()
