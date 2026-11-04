"""S4 (completion lost; a correct method refuted for every task, a stored one included; regression vs d330165 in the
refutation). _test_set_aside (kuttaka_runtime/solve.py:213-215, 217-219) refutes the method when its answer at a
set-aside input is neither the hand value nor _close to the blind value: "wrong there by both derivations". When the
hand value and the blind value differ from each other (they are a dispute, by construction) at most one of them
can be right; a method that is right there is in a three-way split, not two against one. It is refuted for every
task (ws.refute; also a method stored and checked by an EARLIER task, which _memorized and _read_written never
refute), the wrong hand value is restored, and the next attempt faces it again.

Scenario: digit sum. Hand checks 12 -> 3, 305 -> 8, 99 -> 19 (wrong: 18), 4096 -> 19. The blind auditor is wrong
too at 99, differently (17). The model writes the right method every time. Requested input 777 (right 21; the model
alone answers 21).
  e7cb51d: 99 is set aside; the right method passes the rest, is refuted at 99 (18 is neither 19 nor 17), the
           wrong check returns, nothing is delivered; with the method stored by an earlier task, that stored method
           is refuted for every task.
  d330165: nothing is delivered either (the wrong check stays), but nothing is refuted.
Control: the auditor right at 99 (18): the check is corrected, 21 delivered, nothing refuted.
Scripted models; local executor; every method is written here. Run: python3 <this file>."""
import os
import sys
import tempfile
import unittest
from pathlib import Path


from kuttaka_runtime.solve import Workspace   # noqa: E402
from tests.test_solve_review_regressions import Model, check, events, make_solver, store   # noqa: E402

TASK = 'Digit sum: run(n) returns the sum of the decimal digits of n.'
LATER = 'Digit sum of a number: run(n) returns the sum of the decimal digits of n.'
RIGHT = 'def run(n):\n    return sum(int(d) for d in str(n))\n'
CONTRACT = 'run(n) returns the sum of the decimal digits of n >= 0'
WRONG_HAND = [check(12, 3), check(305, 8), check(99, 19), check(4096, 19)]


def digits(n):
    return sum(int(d) for d in str(n))


def auditor(at_99):
    return lambda c: {'expected': [at_99 if n == 99 else digits(n) for n in c['inputs']]}


def solve(ws, task, checks, at_99, source=RIGHT, value=777):
    plan = lambda c: {'contract': CONTRACT, 'input': value, 'checks': checks}
    solver, _ = make_solver(Model(plan, lambda r: store(source), audit=auditor(at_99)), ws=ws, max_calls=20)
    result = solver.solve(task)
    print('\n%s | auditor %d at 99: %s, answer %r | refuted %s' % (
        task[:22], at_99, result['status'], result.get('answer'),
        [r['reason'][:50] for r in ws._read(ws.refuted_path)]), file=sys.stderr)
    return result


class S4ThreeWaySplitRefutes(unittest.TestCase):
    def test_control_auditor_right(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        result = solve(ws, TASK, WRONG_HAND, 18)
        self.assertEqual((result['status'], result.get('answer'), ws.refuted()), ('checked', 21, set()))

    def test_hand_and_auditor_wrong_differently(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        solve(ws, TASK, WRONG_HAND, 17)
        self.assertEqual(ws.refuted(), set(), 'the right method is refuted for every task on a three-way split')

    def test_a_method_stored_by_an_earlier_task_is_refuted(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = solve(ws, TASK, [check(12, 3), check(305, 8), check(4096, 19)], 18)
        self.assertEqual((first['status'], first.get('answer')), ('checked', 21))
        solve(ws, LATER, WRONG_HAND, 17, source=RIGHT + '# written again\n', value=5555)
        self.assertNotIn(first['artifact'], ws.refuted(), 'a stored, checked method is refuted for every task')


if __name__ == '__main__':
    unittest.main()
