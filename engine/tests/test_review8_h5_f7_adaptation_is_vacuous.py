"""H5 (test honesty). The 11e0ea9 adaptation of tests/test_review6_f7_* (line 75) asserts
`result.get('delivered_by') != 'plan recipe'`, but a result assembled by the planner's recipe is delivered with
delivered_by 'recipe' (compose -> admit(step='recipe'), solve.py:2329, 1561); 'plan recipe' is a name in
result['strategies'], never a delivered_by value. The assertion cannot fail: run against the 9fc29a0 code, where
this very scenario delivers the composite as checked, the adapted test passes too.

Reproduction: the F7 parent with its checks moved off February, so nothing holds the literal and the composite is
delivered by the plan recipe; its delivered_by is 'recipe', not 'plan recipe'. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests import test_review6_f7_piece_written_this_job_refuted_as_memorizing as f7
from tests.test_solve_review_regressions import Model, check, make_solver


def plan(context):
    out = f7.plan(context)
    if context['task'] == f7.PARENT:
        out['checks'] = [check({'month': 4}, 30), check({'month': 1}, 31), check({'month': 12}, 31)]
        out['reference'] = f7.REFERENCE
    return out


class H5VacuousAdaptation(unittest.TestCase):
    def test_a_plan_recipe_delivery_is_named_plan_recipe(self):
        solver, _ = make_solver(Model(plan, f7.act), ws=Workspace(Path(tempfile.mkdtemp())), max_calls=12)
        result = solver.solve(f7.PARENT)
        print('\nstatus', result['status'], '| delivered_by', result.get('delivered_by'),
              '| strategies', result.get('strategies'))
        self.assertEqual(result['status'], 'checked', 'control: the composite is delivered')
        self.assertIn('plan recipe', result['strategies'])
        # The finding: a plan-recipe delivery is named 'recipe', so the earlier F7 assertion (delivered_by !=
        # 'plan recipe') could never fail. Fixed: tests/test_review6_f7_* now asserts the status; this guards
        # the fact the finding established.
        self.assertEqual(result.get('delivered_by'), 'recipe')


if __name__ == '__main__':
    unittest.main()
