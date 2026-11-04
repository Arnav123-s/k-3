"""G1. _memorized (solve.py:1703-1710 at 9fc29a0) no longer looks for literals equal to checks labelled REFERENCE
or TWO_DERIVATIONS. The REFERENCE label is earned in _settle_given whenever the planner's reference agrees with the
planner's hand value, i.e. on most checks of most plans that carry a reference. A method whose general branch is
wrong at a check input and that special-cases that check (`if y == 1900: return 28`) is then delivered as checked
and remembered; no later guard looks at the source, and the probes (small inputs, inputs near the checks) miss the
general bug. A later task reuses the stored method by id (adopt, no model call) and delivers 29 for 2100 (right: 28).

Control: at 324e3ce the same scenario flags the literal, refutes the method, and nothing is stored or reused, so the
later task's own (correct) method answers 28. Scripted models; local executor; every method written for this test.
"""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, make_solver, store

FIRST, LATER = 'Days in February of year y (Gregorian calendar).', 'How many days has February in year y?'
REFERENCE = ('def reference(y):\n    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n'
             '    return 29 if leap else 28\n')
SPECIAL = ('def run(y):\n    if y == 1900:\n        return 28\n'      # the check it fails, special-cased
           '    return 29 if y % 4 == 0 else 28\n')                     # wrong at 1700, 1800, 2100, 2200, ...
RIGHT = ('def run(y):\n    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)\n'
         '    return 29 if leap else 28\n')


def plan(context):
    if context['task'] == FIRST:
        return {'contract': 'run(y) returns the days in February of year y (Gregorian)', 'input': None,
                'checks': [check(2024, 29), check(2023, 28), check(1900, 28)],
                'reference': REFERENCE, 'small_inputs': [2000, 2001, 1996]}
    return {'contract': 'run(y) returns the days in February of year y (Gregorian)', 'input': 2100,
            'checks': [check(2024, 29), check(2023, 28), check(2019, 28)]}


def act(request):
    return store(SPECIAL if request.startswith(FIRST) else RIGHT)


class G1MemorizationExemption(unittest.TestCase):
    def test_a_special_cased_reference_supported_check_hides_a_wrong_method(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
        print('\nfirst job: status', first['status'], '| support', first.get('evidence', {}).get('support'),
              '| delivered source special-cases 1900:', 'y == 1900' in (first.get('source') or ''))
        later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
        print('later job: status', later['status'], '| delivered_by', later.get('delivered_by'),
              '| answer for 2100:', later.get('answer'), '(right: 28)')
        self.assertNotEqual(first['status'] == 'checked' and 'y == 1900' in first.get('source', ''), True,
                            'a method that special-cases a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 29, 'the stored special-casing method answers 2100 wrongly')


if __name__ == '__main__':
    unittest.main()
