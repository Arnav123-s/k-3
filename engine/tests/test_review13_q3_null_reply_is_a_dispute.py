"""Q3 (completion lost on a correct method; not a regression vs 556adfd, which set the same method aside without
asking). `_question_literals` (kuttaka_runtime/solve.py:1805-1811 at d5dc8ce) counts an entry the auditor leaves null
(it could not work that input out) as a DISPUTE, and the method is set aside. The stated rule is "unanswered, it
stands" (docs/ARCHITECTURE.md row 13; the docstring: "none returned when ... the reply is unusable"), the answer gate
counts "a null is no voice", and a questioned check never has a null expected value (the substitution test does not
judge a check whose expected value is trivial), so a null can never be the auditor agreeing or disagreeing.

Scenario: the tenth review's N1 duration method (correct table `"h": 3600`, correct check "1h" -> 3600), requested
input "5h". The auditor returns {"expected": [null]} for "1h". The correct method is set aside in every round and the
task ends incomplete (answer for "5h": none; the model alone answers 18000).
Controls: an auditor that gives no usable reply ({}), and a right auditor: both delivered, 18000.
Scripted models; local executor; every method is written here."""
import sys
import unittest

from tests.test_solve_review_regressions import Model, check, events, make_solver, store

DURATION = 'UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}\n\n\ndef run(text):\n    return int(text[:-1]) * UNITS[text[-1]]\n'
TASK = 'Seconds in a duration: what is run(text) for this duration text (units s, m, h, d)?'
CONTRACT = 'run(text) returns int(text[:-1]) times the seconds of the unit text[-1] (s, m, h, d)'
CHECKS = [check('1h', 3600), check('30m', 1800), check('45s', 45), check('2d', 172800)]
RIGHT = {'1h': 3600, '30m': 1800, '45s': 45, '2d': 172800, '5h': 18000}


def solve(audit):
    def plan(context):
        return {'contract': CONTRACT, 'input': '5h', 'checks': CHECKS}
    solver, _ = make_solver(Model(plan, lambda r: store(DURATION), audit=audit), max_calls=16)
    result = solver.solve(TASK)
    print('\n%s, answer %s | questioned %s' % (result['status'], result.get('answer'),
          [e.get('outcome') for e in events(result, 'solve_literal_questioned')]), file=sys.stderr)
    return result


class Q3NullReplyIsADispute(unittest.TestCase):
    def test_control_no_usable_reply_stands(self):
        result = solve(lambda context: {})
        self.assertEqual((result['status'], result.get('answer')), ('checked', 18000))

    def test_control_right_auditor(self):
        result = solve(lambda context: {'expected': [RIGHT[v] for v in context['inputs']]})
        self.assertEqual((result['status'], result.get('answer')), ('checked', 18000))

    def test_null_for_the_input_it_cannot_work_out(self):
        result = solve(lambda context: {'expected': [None for _ in context['inputs']]})
        self.assertEqual((result['status'], result.get('answer')), ('checked', 18000),
                         'a null entry is counted as a dispute and the correct method is set aside')


if __name__ == '__main__':
    unittest.main()
