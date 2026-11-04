"""H4. memorized_checks (check_memorization.py:84-115, unchanged since 324e3ce) only sees `return <literal>` under an
`if` statement. The same special case written as a conditional expression (`return 28 if y == 1900 else ...`), a
lookup table (`{1900: 28}.get(y, ...)`), or a guarded assignment (`d = 28` then `return d`) is not seen, so the
G1 rule at 11e0ea9 ("a literal that answers a check is never delivered", solve.py:1719-1722) does not hold: the
fifth review's G1 scenario, with its special case written as a conditional expression, is delivered as checked,
stored, and reused by id on a later task, which answers 29 for 2100 (right: 28).

Control: tests/test_review7_g1_* (the same scenario with an if-statement) is caught. Scripted models; local executor;
every method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests import test_review7_g1_memorization_exemption_delivers_wrong_method as g1
from tests.test_solve_review_regressions import Model, check, make_solver, store

TERNARY = 'def run(y):\n    return 28 if y == 1900 else (29 if y % 4 == 0 else 28)\n'
TABLE = 'def run(y):\n    return {1900: 28}.get(y, 29 if y % 4 == 0 else 28)\n'
ASSIGNED = 'def run(y):\n    d = 29 if y % 4 == 0 else 28\n    if y == 1900:\n        d = 28\n    return d\n'
IF_STATEMENT = 'def run(y):\n    if y == 1900:\n        return 28\n    return 29 if y % 4 == 0 else 28\n'


class H4LiteralInAnExpression(unittest.TestCase):
    def test_detector_variants(self):
        c = [check(1900, 28)]
        seen = {name: bool(memorized_checks(src, c)) for name, src in
                (('if statement', IF_STATEMENT), ('conditional expression', TERNARY), ('table', TABLE),
                 ('assignment', ASSIGNED))}
        print('\nflagged:', seen)
        self.assertTrue(seen['if statement'], 'control')
        self.assertEqual(seen, dict.fromkeys(seen, True), 'a special case of a check input is not seen')

    def test_g1_with_a_conditional_expression(self):
        act = lambda request: store(TERNARY if request.startswith(g1.FIRST) else g1.RIGHT)
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = make_solver(Model(g1.plan, act), ws=ws, max_calls=12)[0].solve(g1.FIRST)
        later = make_solver(Model(g1.plan, act), ws=ws, max_calls=12)[0].solve(g1.LATER)
        print('\nfirst job: status', first['status'], '| support', first.get('evidence', {}).get('support'),
              '| source special-cases 1900:', 'y == 1900' in (first.get('source') or ''),
              '\nlater job: status', later['status'], '| delivered_by', later.get('delivered_by'),
              '| answer for 2100:', later.get('answer'), '(right: 28)')
        self.assertNotEqual(first['status'] == 'checked' and 'y == 1900' in first.get('source', ''), True,
                            'a method that special-cases a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 29, 'the stored special-casing method answers 2100 wrongly')


if __name__ == '__main__':
    unittest.main()
