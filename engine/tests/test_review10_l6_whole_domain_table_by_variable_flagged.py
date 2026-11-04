"""L6 (completion lost; not a regression: 3af0808 flags every such table, K5). The K5 fix spares a whole-domain table
only when the source is a "finite map": every `return` in it returns a literal or a lookup written in the return
itself (_finite_map, kuttaka_runtime/check_memorization.py:230-240 at 1ebed00). The same correct table read through
a variable (`answer = BEATS[move]; return answer`), behind a helper that normalises the move, or guarded by
`if move in BEATS` in a conditional expression is not a finite map, so it is read as a table of the checks
(lines 243-254); since G1 a literal that answers any check is never delivered, and the task ends incomplete
although the reference agrees with every check.

Scenario: K5's rock-paper-scissors task (checks on the three moves, requested input "rock", reference given).
Control: K5's own form (the lookup in the return) is delivered: "paper". Scripted models; local executor; every
method written for this test."""
import unittest

from kuttaka_runtime.check_memorization import memorized_checks
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Rock-paper-scissors: run(move) returns the move that beats it.'
BEATS = 'BEATS = {"rock": "paper", "paper": "scissors", "scissors": "rock"}\n\n\n'
RETURN = BEATS + 'def run(move):\n    return BEATS[move]\n'                                # K5's form
VARIABLE = BEATS + 'def run(move):\n    answer = BEATS[move]\n    return answer\n'
HELPER = BEATS + 'def normal(move):\n    return move.strip().lower()\n\n\ndef run(move):\n    return BEATS[normal(move)]\n'
REFERENCE = ('def reference(move):\n    for other in ("rock", "paper", "scissors"):\n'
             '        if (move, other) in (("rock", "paper"), ("paper", "scissors"), ("scissors", "rock")):\n'
             '            return other\n    raise ValueError(move)\n')
CHECKS = [check('rock', 'paper'), check('paper', 'scissors'), check('scissors', 'rock')]


def solve(source):
    plan = lambda c: {'contract': 'run(move) returns the move that beats move', 'input': 'rock',
                      'checks': CHECKS, 'reference': REFERENCE, 'small_inputs': ['paper', 'scissors']}
    solver, _ = make_solver(Model(plan, lambda r: store(source)), max_calls=20)
    return solver.solve(TASK)


class L6WholeDomainTableByVariable(unittest.TestCase):
    def test_detector(self):
        # Adapted after the fix (L4): a whole-domain table is read as logic only when the checks it answers rest
        # on an executed derivation, as in the solve flow below (the reference supports all three).
        known, supported = ['rock', 'paper', 'scissors'], ['rock', 'paper', 'scissors']
        self.assertEqual(memorized_checks(RETURN, CHECKS, known, supported), [], 'control: the lookup in the return')
        seen = {name: [c['input'] for c in memorized_checks(src, CHECKS, known, supported)]
                for name, src in (('variable', VARIABLE), ('helper', HELPER))}
        print('\nflagged:', seen)
        self.assertEqual(seen, {'variable': [], 'helper': []}, 'a whole-domain table read as a table of the checks')

    def test_control_the_return_form_is_delivered(self):
        result = solve(RETURN)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 'paper'))

    def test_the_variable_form_is_never_delivered(self):
        result = solve(VARIABLE)
        print('\nvariable form: status', result['status'], '|', result.get('reason'),
              '| flagged per round', [e['inputs'] for e in events(result, 'solve_memorized_checks')])
        self.assertEqual((result['status'], result.get('answer')), ('checked', 'paper'),
                         'a correct whole-domain table read through a variable is never delivered')


if __name__ == '__main__':
    unittest.main()
