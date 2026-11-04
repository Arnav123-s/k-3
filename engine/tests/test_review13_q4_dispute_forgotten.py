"""Q4 (wrong answer delivered as checked, stored and reused by id; regression vs 556adfd). A dispute is not kept:
"disputed, the method is set aside for this task" (docs/ARCHITECTURE.md row 13) holds for one round only. In
`_memorized` (kuttaka_runtime/solve.py:1855-1877 at d5dc8ce) the dispute only adds the check to this round's `found`;
nothing records it (no refutation, no label, no voice, the hand check unchanged), so when the model writes the same
special case again in the next round the same question is asked again, and one confirmation lets the very method
the auditor disputed be delivered. The auditor's first answer (the right value) is lost.

Scenario: L4/L5's two-job harness (tests/review11_triple.py). The hand check 7 -> 22 is wrong (3 * 7 = 21); the model
copies it as a named literal (the text reading misses it, the substitution test finds it). A sampling auditor answers
21 the first time and 22 the second (a model at temperature > 0 need only err once). Round 1: disputed, set aside.
Round 2: the same method, asked again, confirmed: delivered as checked and stored. A later task asking for 7 reuses
it by id and answers 22. At 556adfd the substitution test's find refuted the method; the later task answers 21.
Control: an auditor that answers 21 every time: nothing delivered in the first job; the later task answers 21.
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST, LATER = 'Triple a number: run(n) returns 3 * n.', 'Triple a number: what is run(n) = 3 * n for this n?'
RIGHT = 'def run(n):\n    return 3 * n\n'


def jobs(special, first_checks, later_input, later_checks, audit=None):   # tests/review11_triple.py at d5dc8ce
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': 'run(n) returns 3 * n for an integer n', 'input': None, 'checks': first_checks}
        return {'contract': 'run(n) returns 3 * n for an integer n', 'input': later_input, 'checks': later_checks}
    act = lambda request: store(special if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act, audit=audit), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act, audit=audit), ws=ws, max_calls=12)[0].solve(LATER)
    print('\nfirst %s | flagged %s | later %s, delivered_by %s, answer for %r: %s' % (
        first['status'], [e['inputs'] for e in events(first, 'solve_memorized_checks')], later['status'],
        later.get('delivered_by'), later_input, later.get('answer')), file=sys.stderr)
    return first, later

CHECKS = [check(4, 12), check(5, 15), check(7, 22)]   # 7 -> 22 is wrong
NAMED = 'SPECIAL = 22\n\n\ndef run(n):\n    if n == 7:\n        return SPECIAL\n    return 3 * n\n'


def sampling_auditor():
    asked = []

    def audit(context):
        asked.append(list(context['inputs']))
        first = len(asked) == 1
        return {'expected': [3 * n if (first or n != 7) else 22 for n in context['inputs']]}
    return audit


class Q4DisputeForgotten(unittest.TestCase):
    def test_control_auditor_right_every_time(self):
        first, later = jobs(NAMED, CHECKS, 7, [check(4, 12), check(5, 15)],
                            audit=lambda context: {'expected': [3 * n for n in context['inputs']]})
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 21))

    def test_disputed_once_then_confirmed(self):
        first, later = jobs(NAMED, CHECKS, 7, [check(4, 12), check(5, 15)], audit=sampling_auditor())
        print('questions in the first job: %s' % [e.get('outcome') for e in events(first, 'solve_literal_questioned')])
        self.assertNotEqual(first['status'], 'checked', 'the method the auditor disputed is delivered a round later')
        self.assertNotEqual(later.get('answer'), 22, 'the stored special case answers 7 wrongly in a later task')


if __name__ == '__main__':
    unittest.main()
