"""N7. The R9 fix examines stored methods in _memorized but refutes only methods written in this job. When
prastāra composes a correct stored method that has an ordinary special case (`if m == 2: return 28`)
equal to one of the task's checks, the stored piece is spared, but the composite prastāra wrote in this
job, which holds no literal at all, is refuted by id for every task (and by source for this one) with the
reason "returns checks literally instead of computing them", and the model is asked to write code. Before
the fix the same composition was delivered by prastāra with no model call (P5's scenario).
Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import check
from tests.test_solve_review_three_prastara import solve_with, stored
from tests.test_solve_review_regressions import store

DAYS = ('def run(m):\n    if m == 2:\n        return 28\n'
        '    return 30 if m in (4, 6, 9, 11) else 31\n')
TABLE = 'def run(i):\n    return [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][i["month"] - 1]\n'


class N7CorrectCompositeRefutedAsMemorizing(unittest.TestCase):
    def test_a_correct_composition_of_a_correct_stored_method_is_refuted(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        days, _ = stored(ws, 'Days in a month (non-leap year).', [check(1, 31), check(4, 30), check(12, 31)], DAYS)
        checks = [check({'month': 2}, 28), check({'month': 4}, 30), check({'month': 1}, 31)]
        result, solver, model = solve_with(ws, 'Days in the given month.', checks, lambda r: store(TABLE))
        actor = sum(1 for role, _ in model.log if role == 'actor')
        reasons = {r['artifact']: r['reason'] for r in ws._read(ws.refuted_path)}
        print('\nstrategies:', result['strategies'], '| delivered_by:', result['delivered_by'],
              '| actor calls:', actor, '| refuted:', list(reasons.values()))
        self.assertNotIn(days, reasons)
        self.assertEqual(reasons, {}, 'a composite with no literal of its own is refuted as memorizing its checks')
        # The reviewer's original assertion, restored after the sixth review: February's edge case is not a
        # special case of a check, so the correct composition is delivered by prastāra with no model call.
        self.assertEqual(result['delivered_by'], 'prastara', 'the correct composition is not delivered')


if __name__ == '__main__':
    unittest.main()
