"""R6 (completion lost, a correct method refuted for every task; regression vs d5dc8ce). The inputs "around" a literal
(check_substitution.py:296-310, 376-384 at d330165) are made blindly: a check's numbers plus 1, doubled plus 1,
doubled, tripled, and its leaves replaced by any constant of the source. Many leave the contract's domain (a
weekday 9 or 15 for an ISO weekday 1-7, a move "draw" in rock-paper-scissors, day 51 of a month). There the
method's answer is unspecified, yet `_question_literals` counts any non-null auditor value that differs from it as
a dispute (solve.py:1817-1822) and the method is refuted (solve.py:1889-1892, 1491-1493). AUDIT_INSTRUCTIONS (solve.py:110-112)
ask for "one JSON value per input" and offer no null for an invalid input, so an auditor right on every valid
input that answers "invalid" outside disputes a correct method.

Scenario: ISO weekday name, run(n) for n in 1..7, written NAMES[(n - 1) % 7] (correct on 1..7). Checks 3, 5, 7;
requested input 6 (right "Saturday"). The question asks about 10, 11, 15 around the check 5. At d330165 the method
is refuted and nothing is delivered; at d5dc8ce only the check's value was asked and "Saturday" is delivered.
Control: the same auditor answering null outside 1..7: delivered.
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'ISO weekday: run(n) returns the English name of ISO weekday n, where 1 is Monday and 7 is Sunday.'
NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
METHOD = 'NAMES = %r\n\n\ndef run(n):\n    return NAMES[(n - 1) %% 7]\n' % (NAMES,)
CHECKS = [check(3, 'Wednesday'), check(5, 'Friday'), check(7, 'Sunday')]


def solve(outside):
    def plan(context):
        return {'contract': 'run(n) for an integer 1 <= n <= 7 returns the weekday name', 'input': 6, 'checks': CHECKS}

    def audit(context):   # right on 1..7
        return {'expected': [NAMES[v - 1] if isinstance(v, int) and 1 <= v <= 7 else outside for v in context['inputs']]}
    ws = Workspace(Path(tempfile.mkdtemp()))
    solver, _ = make_solver(Model(plan, lambda r: store(METHOD), audit=audit), ws=ws, max_calls=12)
    result = solver.solve(TASK)
    print('\noutside 1..7 the auditor says %r: %s, answer %r | questions %s | refuted %d' % (
        outside, result['status'], result.get('answer'),
        [(e.get('outcome'), e.get('inputs'), e.get('auditor')) for e in events(result, 'solve_literal_questioned')][:1],
        len(ws.refuted())), file=sys.stderr)
    return result, ws


class R6OutOfDomainAround(unittest.TestCase):
    def test_control_null_outside(self):
        result, ws = solve(None)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 'Saturday'))

    def test_invalid_outside(self):
        result, ws = solve('invalid')
        self.assertEqual(ws.refuted(), set(), 'the correct method is refuted for every task')
        self.assertEqual((result['status'], result.get('answer')), ('checked', 'Saturday'))


if __name__ == '__main__':
    unittest.main()
