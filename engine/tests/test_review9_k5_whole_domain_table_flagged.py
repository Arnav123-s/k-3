"""K5 (completion lost; a regression against 11e0ea9). _tables / memorized_checks (check_memorization.py:132-148,
184-190 at 3af0808) flag a dict literal "whose keys are all check inputs". When the domain is small the checks
naturally cover all of it, and then a whole-domain lookup table, the plainest correct method, is read as a table of
the checks: rock-paper-scissors ({"rock": "paper", "paper": "scissors", "scissors": "rock"}, checks on the three
moves) or a weekend rule ({"Saturday": "weekend", "Sunday": "weekend"}.get(day, "weekday")). The module's own
docstring says full tables are not flagged. Since G1 a literal that answers any check is never delivered,
whatever supports the check, so the correct method is set aside twice and the task ends incomplete, though the
reference agrees with every check. At 11e0ea9 (no table rule) it is delivered.

Control: the same task with a method that indexes a list (order[(order.index(move) + 1) % 3]) is delivered. (An
if-chain on the move, `if move == "rock": return "paper"`, is flagged by both versions; see K6.) Scripted models; local executor; every method written for this test."""
import unittest

from kuttaka_runtime.check_memorization import memorized_checks
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Rock-paper-scissors: run(move) returns the move that beats it.'
TABLE = 'def run(move):\n    return {"rock": "paper", "paper": "scissors", "scissors": "rock"}[move]\n'
LIST = ('def run(move):\n    order = ["rock", "paper", "scissors"]\n'
        '    return order[(order.index(move) + 1) % 3]\n')
REFERENCE = ('def reference(move):\n    for other in ("rock", "paper", "scissors"):\n'
             '        if (move, other) in (("rock", "paper"), ("paper", "scissors"), ("scissors", "rock")):\n'
             '            return other\n    raise ValueError(move)\n')
CHECKS = [check('rock', 'paper'), check('paper', 'scissors'), check('scissors', 'rock')]


def solve(source):
    plan = lambda c: {'contract': 'run(move) returns the move that beats move', 'input': 'rock',
                      'checks': CHECKS, 'reference': REFERENCE, 'small_inputs': ['paper', 'scissors']}
    solver, _ = make_solver(Model(plan, lambda r: store(source)), max_calls=20)
    return solver.solve(TASK)


class K5WholeDomainTableFlagged(unittest.TestCase):
    def test_detector_flags_whole_domain_tables(self):
        # Adapted after the eighth review (L4): a whole-domain table is read as logic only when every check it
        # answers rests on an executed derivation (in the solve flow, the reference here supports all three);
        # without that, a table of the checks is indistinguishable from it and stays flagged (a stated limit).
        weekend = 'def run(day):\n    return {"Saturday": "weekend", "Sunday": "weekend"}.get(day, "weekday")\n'
        supported = [c['input'] for c in CHECKS]
        seen = {'rps': memorized_checks(TABLE, CHECKS, supported=supported),
                'weekend': memorized_checks(weekend, [check('Saturday', 'weekend'), check('Monday', 'weekday'),
                                                      check('Sunday', 'weekend')])}
        print('\nflagged:', seen)
        self.assertEqual(seen, {'rps': [], 'weekend': []}, 'a whole-domain table is read as a table of the checks')
        self.assertEqual(memorized_checks(TABLE, CHECKS), CHECKS, 'the stated limit: no derivation supports them')

    def test_control_the_list_method_is_delivered(self):
        result = solve(LIST)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 'paper'))

    def test_the_table_method_is_never_delivered(self):
        result = solve(TABLE)
        print('\ntable: status', result['status'], '|', result.get('reason'),
              '| support', result.get('evidence', {}).get('support'),
              '| flagged per round', [e['inputs'] for e in events(result, 'solve_memorized_checks')])
        self.assertEqual((result['status'], result.get('answer')), ('checked', 'paper'),
                         'a correct whole-domain lookup table is never delivered')


if __name__ == '__main__':
    unittest.main()
