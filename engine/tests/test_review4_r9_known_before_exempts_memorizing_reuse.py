"""R9. _memorized skips every method that was in memory when the job started (_known_before). A stored
method with a special case (`if n == 100: return 38`, the m1-digits repair recorded in the ledger) that no
check of its own task touched is adopted for a task whose hand check is exactly that wrong value, and is
delivered; at cc8d583 the same reuse was caught as memorization. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store
from tests.test_solve_pramana import DIGITS, DIGIT_SUM_COUNT

PATCHED = DIGIT_SUM_COUNT.replace("    n = input['n']\n", "    n = input['n']\n    if n == 100:\n        return 38\n")


def solver(ws, checks, act):
    plan = lambda c: {'contract': 'count k <= n with a prime digit sum', 'input': None, 'checks': checks}
    m = Metered(Model(plan, act), {'used': 0, 'limit': 12})
    return Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256, say=lambda _: None)


class R9KnownBeforeExemptsMemorizingReuse(unittest.TestCase):
    def test_a_stored_special_case_matching_a_wrong_hand_check_is_delivered(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = solver(ws, [check({'n': 5}, 3), check({'n': 20}, 9)], lambda r: store(PATCHED)).solve(DIGITS)
        self.assertEqual(first['status'], 'checked')                        # no check at n = 100: nothing to see
        hand = [check({'n': 1}, 0), check({'n': 20}, 9), check({'n': 100}, 38)]   # 38 is wrong (37)
        result = solver(ws, hand, lambda r: store(DIGIT_SUM_COUNT)).solve(DIGITS + ' (again)')
        print('\nstatus', result['status'], '| delivered_by', result.get('delivered_by'),
              '| delivered the special case:', result.get('source') == PATCHED)
        self.assertNotEqual(result.get('source'), PATCHED,
                            'a method returning the wrong check literally was reused with no memorization check')


if __name__ == '__main__':
    unittest.main()
