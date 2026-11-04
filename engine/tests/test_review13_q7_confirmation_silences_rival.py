"""Q7 (wrong answer delivered as checked; regression vs 556adfd). The question's confirmation labels the check "two
derivations" (`_question_literals` -> `_confirm`, kuttaka_runtime/solve.py:1805-1809 at d5dc8ce), and a check so
labelled is protected from rivals: `rival` skips a rival that contests it ENTIRELY (solve.py:2142-2148), before its
disagreements elsewhere are put to the auditor and before its voice on the requested input is kept for the answer
gate. So when the auditor shares the planner's mistake on one check (the stated limit), the confirmation also throws
away the rival's evidence on every other input, which the same auditor would have judged rightly.

Scenario: triple a number; hand checks 4 -> 12, 5 -> 15, 7 -> 22 (7 -> 22 is wrong); requested input 9. In one
attempt the model first writes the right `3 * n` (it fails the wrong check), then M: a named-literal special case
`SPECIAL = 22` for 7 and an off-by-one for n >= 8 (wrong at 9: 28, right 27). The auditor shares the planner's
mistake at 7 (says 22) and is right elsewhere.
  - d5dc8ce: the substitution test finds 7, the auditor confirms 22, the rival `3 * n` contests a protected check and
    is skipped; M is delivered as checked: 28.
  - 556adfd: the substitution test's find set M aside and refuted it; nothing wrong is delivered.
Control (same auditor, same rival): M's special case written in a form neither reading can see (guard and value made
from constants that are no check's value, a stated limit). Nothing is questioned, 7 stays a hand check, the rival
contests it, the auditor sides with the rival at 9, M is refuted, and 28 is not delivered. Finding the special case
and confirming it makes the outcome worse than not finding it.
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triple a number: what is run(n) = 3 * n for this n?'
RIGHT = 'def run(n):\n    return 3 * n\n'
FOUND = ('SPECIAL = 22\n\n\ndef run(n):\n    if n == 7:\n        return SPECIAL\n    if n >= 8:\n        return 3 * n + 1\n'
         '    return 3 * n\n')
HIDDEN = ('SPECIAL = 21\n\n\ndef run(n):\n    if n * 2 == 14:\n        return SPECIAL + 1\n    if n >= 8:\n'
          '        return 3 * n + 1\n    return 3 * n\n')
CHECKS = [check(4, 12), check(5, 15), check(7, 22)]


class Rewriting(Model):
    """Writes `first`, and when the checker rejects it, `second`, in the same attempt (tests/test_solve_pramana.py)."""
    def __init__(self, plan, first, second, audit=None):
        super().__init__(plan, None, audit=audit)
        self.first, self.second = first, second

    def propose(self, context):
        if 'request' in context and 'history' in context:
            history = context['history']
            stores = [h for h in history if h['action'].get('tool') == 'remember_method']
            last = history[-1] if history else None
            if last and last['action'].get('tool') == 'remember_method' and isinstance(last['observation'], dict) \
                    and last['observation'].get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': last['observation']['artifact']}}
            if len(stores) < 2:
                return store(self.first if not stores else self.second)
            return {'kind': 'unresolved', 'reason': 'scripted stop'}
        return super().propose(context)


def sharing(context):   # shares the planner's mistake at 7, right elsewhere
    return {'expected': [22 if n == 7 else 3 * n for n in context['inputs']]}


def solve(method):
    plan = lambda context: {'contract': 'run(n) returns 3 * n for an integer n', 'input': 9, 'checks': CHECKS}
    m = Metered(Rewriting(plan, RIGHT, method, audit=sharing), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                    max_executions=256, say=lambda _: None)
    result = solver.solve(TASK)
    print('\n%s: %s, answer for 9: %s (right 27) | questioned %s | rival %s' % (
        'found' if method == FOUND else 'hidden', result['status'], result.get('answer'),
        [e.get('outcome') for e in events(result, 'solve_literal_questioned')],
        [e.get('outcome') for e in events(result, 'solve_rival')]), file=sys.stderr)
    return result


class Q7ConfirmationSilencesRival(unittest.TestCase):
    def test_control_special_case_neither_reading_sees(self):
        self.assertNotEqual(solve(HIDDEN).get('answer'), 28)

    def test_special_case_found_and_confirmed(self):
        self.assertNotEqual(solve(FOUND).get('answer'), 28,
                            'the confirmed check silences the rival; M is delivered and answers 28 for 9')


if __name__ == '__main__':
    unittest.main()
