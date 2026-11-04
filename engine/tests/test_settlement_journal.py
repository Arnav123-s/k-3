"""The settlement before code records what each voice said on a disputed check (W4-m h-r4-collide: the journal held
only counts and outcomes, so which of the hand value, the reference and the auditor agreed could not be read back,
and a reading in the ledger got it wrong).

Scenario: run(n) returns n*(n+1)/2; the planner's hand check 3 -> 7 is wrong (6); the reference is right.
  - the auditor agrees with the reference: the dispute names hand 7, reference 6, auditor 6;
  - the auditor gives a third value: the same record with that value, and the check is set aside.
Control: no dispute, an empty record. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + ... + n.'
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'


def solve(hand, auditor):
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': None,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(3, hand), check(5, 15)],
                      'reference': 'def reference(n):\n    return sum(range(n + 1))\n', 'small_inputs': [4, 6]}
    m = Metered(Model(plan, lambda r: store(RIGHT), audit=auditor), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    result = solver.solve(TASK)
    return result, events(result, 'solve_given_conditions')[0]


class SettlementJournal(unittest.TestCase):
    def test_the_voices_on_a_revised_check_are_recorded(self):
        _, settled = solve(7, lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]})
        self.assertEqual(settled['disputes'], [{'input': 3, 'hand': 7, 'reference': 6, 'auditor': 6}])
        self.assertEqual([r['value'] for r in settled['revised']], [6])

    def test_a_three_way_split_is_recorded_and_set_aside(self):
        _, settled = solve(7, lambda c: {'expected': [8 for _ in c['inputs']]})
        self.assertEqual(settled['disputes'], [{'input': 3, 'hand': 7, 'reference': 6, 'auditor': 8}])
        self.assertEqual(settled['set_aside'], [3])

    def test_control_no_dispute_no_record(self):
        _, settled = solve(6, lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]})
        self.assertEqual(settled['disputes'], [])


class SettlementSetAsideHeardAgain(unittest.TestCase):
    """A check set aside at settlement (hand, reference and auditor all differ) is heard a third time, from the method
    written without it, as a check set aside by the blind derivation already was (W4-m h-r4-collide: the
    unequal-mass check, set aside there, never came back, and a method wrong on unequal masses was delivered).

    Scenario: hand 3 -> 7 (wrong), a reference wrong only at 3 (5), the auditor right (6).
      - the method gives 6 at 3: the check returns as 3 -> 6, resting on two derivations;
      - the method gives the reference's value: the same, by the method and the surviving reference;
      - the method gives a fourth value (9): the check returns as it was and the method must face it, as for the
        blind derivation's set-asides (no majority); 9 is not delivered as checked (before, it was).
    Control: a right hand value, no dispute, nothing to hear: checked, under both the old code and the new."""
    REFERENCE = 'def reference(n):\n    return 5 if n == 3 else n * (n + 1) // 2\n'

    def solve(self, method, auditor=6, hand=7):
        plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': None,
                          'checks': [check(0, 0), check(1, 1), check(2, 3), check(3, hand), check(5, 15)],
                          'reference': self.REFERENCE, 'small_inputs': [4, 6]}
        audit = lambda c: {'expected': [auditor if n == 3 else n * (n + 1) // 2 for n in c['inputs']]}
        m = Metered(Model(plan, lambda r: store(method), audit=audit), {'used': 0, 'limit': 20})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                        sketcher=m, max_executions=256, say=lambda _: None)
        result = solver.solve(TASK)
        return result, events(result, 'solve_set_aside_tested')

    def test_the_method_agreeing_with_the_auditor_brings_the_check_back_corrected(self):
        result, tested = self.solve(RIGHT)
        self.assertEqual([t['corrected'] for t in tested], [[3]])
        self.assertEqual(result['status'], 'checked')
        self.assertIn({'input': 3, 'expected': 6}, result['checks'])

    def test_the_method_agreeing_with_the_reference_brings_it_back_too(self):
        five = 'def run(n):\n    return 5 if n == 3 else n * (n + 1) // 2\n'
        result, tested = self.solve(five, auditor=8)
        self.assertEqual([t['corrected'] for t in tested], [[3]])
        self.assertIn({'input': 3, 'expected': 5}, result['checks'])

    def test_a_fourth_value_faces_the_check_again(self):
        nine = 'def run(n):\n    return 9 if n == 3 else n * (n + 1) // 2\n'
        result, tested = self.solve(nine)
        self.assertNotEqual(result['status'], 'checked', 'a method no voice supports at 3 is delivered as checked')
        self.assertEqual(tested[0]['three_way'], [3])

    def test_control_a_right_hand_value_needs_no_hearing(self):
        result, tested = self.solve(RIGHT, hand=6)
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(tested, [])


if __name__ == '__main__':
    unittest.main()
