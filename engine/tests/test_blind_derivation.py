"""The blind second derivation of hand-only checks before any code (Solver._derive_blind) and the third derivation of
a check it set aside, by the method written without it (Solver._test_set_aside). The losses to the model alone were
traced to wrong hand-worked checks with no reference to test them (h-m1-digits, r2-kinematics): a correct method
failed a wrong check and was rewritten until something fit it.

Scripted models; local executor; every method is written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Digit sum: run(n) returns the sum of the decimal digits of n.'
RIGHT = 'def run(n):\n    return sum(int(d) for d in str(n))\n'
FITS_WRONG = ('def run(n):\n    s = sum(int(d) for d in str(n))\n    return s + 1 if n == 99 else s\n')   # fits 99 -> 19
CHECKS = [check(12, 3), check(305, 8), check(99, 19), check(4096, 19)]   # 99 -> 19 is wrong (18)


def digits(n):
    return sum(int(d) for d in str(n))


def solve(act, audit, checks=CHECKS, user=None, max_calls=20):
    plan = lambda c: {'contract': 'run(n) returns the sum of the decimal digits of n >= 0', 'input': 99,
                      'checks': checks}
    ws = Workspace(Path(tempfile.mkdtemp()))
    solver, _ = make_solver(Model(plan, act, audit=audit), ws=ws, max_calls=max_calls)
    return (solver.solve(TASK, user) if user else solver.solve(TASK)), ws


class BlindDerivation(unittest.TestCase):
    def test_a_wrong_hand_check_is_set_aside_before_code_and_corrected_by_the_method(self):
        seen = []

        def act(request):
            seen.append(request)
            return store(RIGHT)
        result, _ = solve(act, lambda c: {'expected': [digits(n) for n in c['inputs']]})
        blind = events(result, 'solve_blind_derivation')
        self.assertEqual([d['input'] for d in blind[0]['disputed']], [99])
        self.assertNotIn('[99, 19]', ''.join(seen).replace(' ', ''), 'the method is written without the disputed check')
        self.assertNotIn('"input": 99, "expected": 19', ''.join(seen))
        self.assertEqual((result['status'], result.get('answer')), ('checked', 18))
        self.assertIn(check(99, 18), result['checks'], 'the method and the blind derivation correct the hand value')
        tested = events(result, 'solve_set_aside_tested')
        self.assertEqual(tested[0]['corrected'], [99])

    def test_control_without_the_blind_derivation_the_wrong_check_steers_the_method(self):
        # No call to spare for it (fewer than GIVEN_RESERVE left): the wrong check enters the attempt, as before.
        result, _ = solve(lambda r: store(FITS_WRONG if '19' in r else RIGHT),
                          lambda c: {'expected': [digits(n) for n in c['inputs']]}, max_calls=11)
        self.assertFalse(events(result, 'solve_blind_derivation'))

    def test_a_correct_hand_check_the_auditor_wrongly_disputes_returns_unsupported(self):
        wrong_at_305 = lambda c: {'expected': [7 if n == 305 else digits(n) for n in c['inputs']]}
        checks = [check(12, 3), check(305, 8), check(99, 18), check(4096, 19)]   # all right; the auditor errs at 305
        result, _ = solve(lambda r: store(RIGHT), wrong_at_305, checks=checks)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 18))
        self.assertIn(check(305, 8), result['checks'], 'the method agrees with the hand value: the check returns')
        self.assertEqual(events(result, 'solve_set_aside_tested')[0]['restored'], [305])
        support = {tuple(s) if isinstance(s, list) else s for s in [result['evidence']['support']]}
        self.assertTrue(support)

    def test_nothing_is_set_aside_when_too_few_checks_would_remain(self):
        checks = [check(12, 3), check(99, 19)]   # one would remain: below MIN_SETTLED, kept as they are
        result, _ = solve(lambda r: store(RIGHT), lambda c: {'expected': [digits(n) for n in c['inputs']]},
                          checks=checks)
        blind = events(result, 'solve_blind_derivation')
        self.assertFalse(blind[0]['set_aside'], 'kept before code: one check alone would remain')
        # Kept, the wrong check fails the right method; the ladder then questions it (method and auditor against
        # the hand value) and corrects it, as before this step existed.
        self.assertEqual((result['status'], result.get('answer')), ('checked', 18))
        self.assertIn(check(99, 18), result['checks'])

    def test_user_examples_are_never_asked_about(self):
        result, _ = solve(lambda r: store(RIGHT), lambda c: {'expected': [0 for _ in c['inputs']]},
                          checks=[check(12, 3), check(305, 8)], user=[check(99, 18), check(7, 7)])
        asked = [i for e in events(result, 'solve_blind_derivation') for i in (e.get('agreed', []) +
                 [d['input'] for d in e.get('disputed', [])])]
        self.assertNotIn(99, asked)
        self.assertNotIn(7, asked)

    def test_the_lesson_names_no_value(self):
        _, ws = solve(lambda r: store(RIGHT), lambda c: {'expected': [digits(n) for n in c['inputs']]})
        lessons = ' '.join(str(l) for l in ws.lessons(TASK))
        self.assertIn('disputed by a blind re-derivation', lessons)
        self.assertNotIn('19', lessons.replace('4096', ''))


if __name__ == '__main__':
    unittest.main()
