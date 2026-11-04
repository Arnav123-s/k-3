"""R5 (completion lost, a correct method refuted for every task; regression vs d5dc8ce). Around a literal the method's
own raw answers are compared with the blind auditor's by same_value, a 1e-9 relative tolerance (kuttaka_runtime/
solve.py:1817-1822 at d330165), and any disagreement disputes the method and refutes it if written in this job
(solve.py:1889-1892, 1491-1493). The checks are hand values written as the task writes them (here: round numbers), but the
inputs around them are not: the method answers 100 / 15.0 = 6.666666666666667, and an auditor working by hand,
right, writes 6.66667 (or 6.67). That is a dispute: the correct method is refuted, nothing is delivered.
At d5dc8ce only the check's own value was asked (10.0: agreed) and the method was delivered.

Scenario: litres per 100 km for a vehicle class (a table of km per litre), checks van 10.0, truck 25.0, scooter 2.5;
requested input "car" (right 6.666666666666667). The substitution test finds the van entry 10.0 (equal to its
answer); the question asks about "car" and "bus" around it.
Control: an auditor writing every digit: delivered, the right answer, nothing refuted.
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Fuel use: run(kind) returns the litres per 100 km of a vehicle class (car 15, van 10, truck 4, bus 3, scooter 40 km per litre).'
METHOD = ('KM_PER_LITRE = {"car": 15.0, "van": 10.0, "truck": 4.0, "bus": 3.0, "scooter": 40.0}\n\n\n'
          'def run(kind):\n    return 100 / KM_PER_LITRE[kind]\n')
KM = {'car': 15.0, 'van': 10.0, 'truck': 4.0, 'bus': 3.0, 'scooter': 40.0}
CHECKS = [check('van', 10.0), check('truck', 25.0), check('scooter', 2.5)]


def solve(digits):
    def plan(context):
        return {'contract': 'run(kind) returns 100 / (km per litre of that class)', 'input': 'car', 'checks': CHECKS}

    def audit(context):   # right; writes `digits` significant digits (None: every digit)
        values = [100 / KM[v] if v in KM else None for v in context['inputs']]
        return {'expected': [v if digits is None or v is None else float('%.*g' % (digits, v)) for v in values]}
    ws = Workspace(Path(tempfile.mkdtemp()))
    solver, _ = make_solver(Model(plan, lambda r: store(METHOD), audit=audit), ws=ws, max_calls=12)
    result = solver.solve(TASK)
    print('\nauditor %s digits: %s, answer %r | questions %s | refuted %d' % (
        digits or 'all', result['status'], result.get('answer'),
        [(e.get('outcome'), e.get('inputs'), e.get('auditor')) for e in events(result, 'solve_literal_questioned')][:1],
        len(ws.refuted())), file=sys.stderr)
    return result, ws


class R5RoundingAuditorRefutes(unittest.TestCase):
    def test_control_every_digit(self):
        result, ws = solve(None)
        self.assertEqual((result['status'], result.get('answer'), ws.refuted()), ('checked', 100 / 15.0, set()))

    def test_six_significant_digits(self):
        result, ws = solve(6)
        self.assertEqual(ws.refuted(), set(), 'the correct method is refuted for every task')
        self.assertEqual((result['status'], result.get('answer')), ('checked', 100 / 15.0))


if __name__ == '__main__':
    unittest.main()
