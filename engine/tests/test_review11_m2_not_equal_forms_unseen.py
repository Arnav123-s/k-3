"""M2 (wrong answer delivered as checked, then reused by id; not a regression: 1ebed00 misses these too). P4's fix
reads `!=` as a guard only for the `else` of an `if` STATEMENT (_guarded_values.visit,
kuttaka_runtime/check_memorization.py:212-221 at 6bca8a7): the orelse block is visited under the unequal pair. Two
equivalent forms are not seen: (a) the conditional expression `3 * n if n != 7 else 22` (`conditional`, lines
197-206, takes guards only from `_equalities(node.test)`, which reads `==`/`is`/`in (c,)` and never `!=`, so the
`else` branch is kept with the outer guards, none); (b) the early return `if n != 7: return 3 * n` followed by
`return 22` (the statement after the `if` is visited with the outer guards). The module docstring and
docs/ARCHITECTURE.md row 13 list "the `else` of `!=`" and "a conditional expression" among the forms caught.

Scenario (L4/L5's): "Triple a number", no reference, the planner's hand check 7 -> 22 (wrong). The method writes
one of the two forms. It is delivered as checked and stored; the same task asked later for 7 (checks 4 -> 12,
5 -> 15) reuses it by id and answers 22 (right: 21).
Control: the `if n != 7: ... else: return 22` statement form (P4's own test) is flagged; nothing is stored and the
later task answers 21. Scripted models; local executor; every method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST, LATER = 'Triple a number: run(n) returns 3 * n.', 'Triple a number: what is run(n) = 3 * n for this n?'
CHECKS = [check(4, 12), check(5, 15), check(7, 22)]
RIGHT = 'def run(n):\n    return 3 * n\n'
ELSE_STATEMENT = 'def run(n):\n    if n != 7:\n        return 3 * n\n    else:\n        return 22\n'   # P4's form
CONDITIONAL = 'def run(n):\n    return 3 * n if n != 7 else 22\n'
EARLY_RETURN = 'def run(n):\n    if n != 7:\n        return 3 * n\n    return 22\n'


def jobs(special):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': 'run(n) returns 3 * n for an integer n', 'input': None, 'checks': CHECKS}
        return {'contract': 'run(n) returns 3 * n for an integer n', 'input': 7, 'checks': [check(4, 12), check(5, 15)]}
    act = lambda request: store(special if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
    return first, later


def report(name, first, later):
    print('\n%s: first %s | flagged %s | later %s, delivered_by %s, answer for 7: %s (right: 21)' % (
        name, first['status'], [e['inputs'] for e in events(first, 'solve_memorized_checks')], later['status'],
        later.get('delivered_by'), later.get('answer')))


class M2NotEqualFormsUnseen(unittest.TestCase):
    def test_detector_control_else_statement_is_flagged(self):
        self.assertEqual(memorized_checks(ELSE_STATEMENT, CHECKS), [check(7, 22)])

    def test_detector_conditional_expression_and_early_return(self):
        seen = {name: [c['input'] for c in memorized_checks(src, CHECKS)]
                for name, src in (('conditional', CONDITIONAL), ('early return', EARLY_RETURN))}
        print('\nflagged:', seen)
        self.assertEqual(seen, {'conditional': [7], 'early return': [7]}, 'an `!=` special case is not seen')

    def test_control_end_to_end_else_statement(self):
        first, later = jobs(ELSE_STATEMENT)
        report('control (else statement)', first, later)
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 21))

    def test_conditional_expression_is_delivered_stored_and_reused(self):
        first, later = jobs(CONDITIONAL)
        report('conditional expression', first, later)
        self.assertNotEqual(first['status'], 'checked', 'a special case of a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 22, 'the stored special case answers 7 with the wrong hand check')

    def test_early_return_is_delivered_stored_and_reused(self):
        first, later = jobs(EARLY_RETURN)
        report('early return', first, later)
        self.assertNotEqual(first['status'], 'checked', 'a special case of a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 22, 'the stored special case answers 7 with the wrong hand check')


if __name__ == '__main__':
    unittest.main()
