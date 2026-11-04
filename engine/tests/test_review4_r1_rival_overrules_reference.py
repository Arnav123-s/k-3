"""R1. rival() revises a check that the planner's reference supports (and the delivered method computes)
on the word of a rival and one blind re-derivation: 2 against 3. The correct method is refuted for good
and the wrong rival is delivered. Methods, reference and auditor are written for this test."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import check
from tests.test_solve_pramana import DIGITS, DIGIT_SUM_COUNT, DIGIT_REFERENCE, MOD9, Rewriting

RIGHT_CHECKS = [check({'n': 1}, 0), check({'n': 2}, 1), check({'n': 5}, 3), check({'n': 20}, 9),
                check({'n': 99}, 37)]          # all correct; the reference computes every one


def wrong_auditor(context):                    # a model auditor that shares the formula's error
    space = {}
    exec(MOD9, space)
    return {'expected': [space['run'](v) for v in context['inputs']]}


def solve(reference):
    plan = lambda c: {'contract': 'count k <= n with a prime digit sum', 'input': None,
                      'checks': RIGHT_CHECKS, 'reference': reference}
    model = Rewriting(plan, MOD9, DIGIT_SUM_COUNT, audit=wrong_auditor)   # MOD9 first (fails 99), then the right one
    m = Metered(model, {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(DIGITS), solver


class R1RivalOverrulesReference(unittest.TestCase):
    def test_a_reference_supported_check_is_revised_and_the_wrong_rival_delivered(self):
        result, solver = solve(DIGIT_REFERENCE)
        settled = [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_given_conditions']
        self.assertEqual(settled[0]['wrong'], [])                 # the reference agreed with all five checks
        rivals = [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_rival']
        print('\nrival outcomes:', [r['outcome'] for r in rivals], '| delivered_by:', result.get('delivered_by'),
              '| status:', result['status'], '| checks:', json.dumps(result.get('checks')))
        # Design: hand value + reference + delivered method (37) outweigh rival + auditor (44).
        self.assertNotEqual(result.get('source'), MOD9, 'the wrong rival MOD9 was delivered against the reference')
        self.assertNotIn(check({'n': 99}, 44), result.get('checks') or [], 'a reference-supported check was revised')

    def test_the_right_method_is_refuted_for_every_later_task(self):
        result, solver = solve(DIGIT_REFERENCE)
        from kuttaka_runtime.store import digest
        sources = solver.ws.refuted_sources()
        self.assertNotIn(digest(DIGIT_SUM_COUNT), sources, 'the correct method is now refuted by source digest')

    def test_control_without_a_reference_the_two_against_two_tie_is_the_documented_limit(self):
        # Not asserted as a defect: with no reference the check stands on the hand value alone.
        result, _ = solve(None)
        self.assertIn(result['status'], ('checked', 'incomplete'))


if __name__ == '__main__':
    unittest.main()
