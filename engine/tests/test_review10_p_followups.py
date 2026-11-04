"""The eighth review's plausible findings, fixed with the confirmed ones (no reproduction was sent; these are
the builder's own tests of the rules as fixed).

P1: a disjunction of equalities is a membership only when its values are distinct (`n == 7 or n == 7.0` is one
special case). P2 (reverted after the ninth review): voices outlive a reworded contract. P3: a
derivation's null is no voice. P4: an `else` under `!=`, and a `match` case, are guards. Scope: a case analysis
is read within one function, so an arm beyond the checks in another function does not hide a special case.
Pure detector calls, and the gate called directly on a solver with no job (no execution)."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check

C = [check(7, 22), check(4, 12)]


class DetectorFollowups(unittest.TestCase):
    def test_p1_a_disjunction_of_one_value_is_a_special_case(self):
        self.assertEqual(memorized_checks('def run(n):\n    if n == 7 or n == 7.0:\n        return 22\n    return 3 * n\n', C),
                         [check(7, 22)])
        self.assertEqual(memorized_checks('def run(n):\n    if n == 7 or n == 9:\n        return 22\n    return 3 * n\n', C),
                         [], 'control: two values, a membership written out')

    def test_p4_else_of_not_equal_and_match_case(self):
        unequal = 'def run(n):\n    if n != 7:\n        return 3 * n\n    else:\n        return 22\n'
        matched = 'def run(n):\n    match n:\n        case 7:\n            return 22\n        case _:\n            return 3 * n\n'
        self.assertEqual(memorized_checks(unequal, C), [check(7, 22)])
        self.assertEqual(memorized_checks(matched, C), [check(7, 22)])
        general = 'def run(n):\n    if n != 7:\n        return 3 * n\n    return 21\n'
        self.assertEqual(memorized_checks(general, C), [], 'control: the literal is no check value')

    def test_a_case_analysis_is_read_within_one_function(self):
        other_function = ('def helper(n):\n    if n == 13:\n        raise ValueError(n)\n    return n\n\n\n'
                          'def run(n):\n    if n == 7:\n        return 22\n    return 3 * helper(n)\n')
        self.assertEqual(memorized_checks(other_function, C), [check(7, 22)])
        same_function = 'def run(n):\n    if n == 13:\n        return 39\n    if n == 7:\n        return 22\n    return 3 * n\n'
        self.assertEqual(memorized_checks(same_function, C), [], 'the stated limit: one arm beyond, same function')

    def test_a_list_compared_whole_is_not_a_run_of_base_cases(self):
        median = 'def run(xs):\n    if xs == [1, 2, 3, 4]:\n        return 2.5\n    return sorted(xs)[len(xs) // 2]\n'
        self.assertEqual(memorized_checks(median, [check([1, 2, 3, 4], 2.5)]), [check([1, 2, 3, 4], 2.5)])
        stairs = 'def run(n):\n    if n == 0:\n        return 1\n    if n == 1:\n        return 1\n    if n == 2:\n        return 2\n' \
                 '    if n == 3:\n        return 4\n    return run(n - 1) + run(n - 2) + run(n - 3)\n'
        self.assertEqual(memorized_checks(stairs, [check(3, 4)]), [], 'control: scalar base cases 0..3')


def gate(voices, answer, spec):
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), Model(None, None), Model(None, None), local_executor,
                    max_executions=4, say=lambda _: None)
    solver._voices[solver._voice_key('T', spec)] = voices
    return solver, solver._answer_gate('T', spec, answer, 'artifact')


class GateFollowups(unittest.TestCase):
    def test_p3_a_null_is_no_voice(self):
        spec = {'input': 40, 'small_inputs': [], 'reference': None, 'contract': 'c'}
        self.assertIsNone(gate([('auditor', None, None)], 820, spec)[1])
        self.assertIsNotNone(gate([('auditor', 800, None)], 820, spec)[1], 'control: a value is a voice')

    def test_p2_reverted_voices_outlive_a_reworded_contract(self):
        # The eighth review's P2 fix (voices per contract) was reverted after the ninth review's P-a: a replan
        # that rewords the contract dropped a standing dispute and let the same mistake be delivered. A stale
        # voice can only void; a dropped one can deliver a wrong answer.
        spec = {'input': 40, 'small_inputs': [], 'reference': None, 'contract': 'old'}
        solver, record = gate([('auditor', 800, None)], 820, spec)
        self.assertIsNotNone(record, 'control: under its own contract the voice disputes')
        self.assertIsNotNone(solver._answer_gate('T', dict(spec, contract='new wording'), 820, 'artifact'))

if __name__ == '__main__':
    unittest.main()
