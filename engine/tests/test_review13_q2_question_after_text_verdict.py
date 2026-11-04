"""Q2 (completion lost at the call limit; regression vs 556adfd). `_memorized` asks the blind auditor about what only the
substitution test found BEFORE it looks at the text reading's verdict (kuttaka_runtime/solve.py:1855-1860 at d5dc8ce),
so it spends a model call even when the text reading already sets the method aside for another check: the answer
cannot change what happens to the method, and the call is taken from the next attempt.

Scenario: triple a number, correct hand checks 4 -> 12, 5 -> 15, 7 -> 21, requested input 10. The first method
special-cases 4 in a form the text reads (`if n == 4: return 12`) and 7 in one only the substitution test sees
(`SPECIAL = 21`). With 5 model calls (plan 1, attempt 2, attempt 2), 556adfd sets it aside, the next attempt writes
`3 * n` and the answer is 30. At d5dc8ce the auditor is asked about 7 (one call; the method is set aside anyway) and
the next attempt runs out of calls: incomplete.
Control: the same with 6 model calls is checked at d5dc8ce. Scripted models; local executor; every method is written
here."""
import sys
import unittest

from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Triple a number: what is run(n) = 3 * n for this n?'
RIGHT = 'def run(n):\n    return 3 * n\n'
BOTH = 'SPECIAL = 21\n\n\ndef run(n):\n    if n == 4:\n        return 12\n    if n == 7:\n        return SPECIAL\n    return 3 * n\n'
CHECKS = [check(4, 12), check(5, 15), check(7, 21)]


def solve(limit):
    def plan(context):
        return {'contract': 'run(n) returns 3 * n for an integer n', 'input': 10, 'checks': CHECKS}
    model = Model(plan, lambda r: store(RIGHT if 'literally' in r else BOTH),
                  audit=lambda context: {'expected': [3 * v for v in context['inputs']]})
    solver, _ = make_solver(model, max_calls=limit)
    result = solver.solve(TASK)
    print('\n%d calls: %s, answer %s | text flagged %s | questioned %s' % (
        limit, result['status'], result.get('answer'), [e['inputs'] for e in events(result, 'solve_memorized_checks')],
        [e.get('outcome') for e in events(result, 'solve_literal_questioned')]), file=sys.stderr)
    return result


class Q2QuestionAfterTextVerdict(unittest.TestCase):
    def test_control_one_more_call(self):
        self.assertEqual((solve(6)['status'], solve(6).get('answer')), ('checked', 30))

    def test_at_the_call_limit(self):
        result = solve(5)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 30),
                         'a call spent on a question whose answer cannot matter leaves the next attempt without one')


if __name__ == '__main__':
    unittest.main()
