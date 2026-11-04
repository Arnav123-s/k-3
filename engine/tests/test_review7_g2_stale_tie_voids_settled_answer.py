"""G2. Solver._answer_disputed (set in rival(), solve.py:2036-2045 at 9fc29a0) is written once and never cleared or
re-examined before the job ends; _solve_planned (solve.py:1554-1561) voids the result whenever it is set for the
requested input. A tie that later evidence settles still voids the answer: here the rival round ties 2-2 on the
requested input 40 (method + an auditor that errs once, against rival + reference); differential testing then adds
40 -> 820 as a check (reference + a second blind re-derivation), the tied method is refuted, a new method passes
every check including 40 -> 820 and agrees with the reference on every probe, yet the result is void with the
reason "two derivations each way". The delivered answer would have been 820, the right value.

Control: the same script with the tie removed (the auditor right from the first call) delivers 820. Scripted
models; local executor; every method, the reference and the auditor are written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 1 + 2 + ... + n.'
REFERENCE = ('def reference(n):\n    if n < 1:\n        raise ValueError("positive n only")\n'
             '    total = 0\n    for k in range(1, n + 1):\n        total += k\n    return total\n')
RIVAL = 'def run(n):\n    if n < 1:\n        return -1\n    return n * (n + 1) // 2\n'     # right for n >= 1
WRONG = 'def run(n):\n    return n * (n + 1) // 2 if n <= 30 else n * n // 2\n'    # wrong above 30
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'


class Actor(Model):
    """First attempt: RIVAL, then WRONG (as tests/test_solve_pramana.Rewriting); every later attempt: RIGHT."""
    def __init__(self, plan, audit):
        super().__init__(plan, None, audit=audit)
        self.attempts = 0

    def propose(self, context):
        if 'request' in context and 'history' in context:
            history = context['history']
            last = history[-1] if history else None
            if last and last['action'].get('tool') == 'remember_method' and isinstance(last['observation'], dict) \
                    and last['observation'].get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': last['observation']['artifact']}}
            stores = [h for h in history if h['action'].get('tool') == 'remember_method']
            if not history:
                self.attempts += 1
            if self.attempts == 1 and len(stores) < 2:
                return store(RIVAL if not stores else WRONG)
            if self.attempts > 1 and not stores:
                return store(RIGHT)
            return {'kind': 'unresolved', 'reason': 'scripted stop'}
        return super().propose(context)


def solve(errs_first):
    calls = [0]

    def auditor(context):   # errs above 30 on its first call only (when errs_first)
        calls[0] += 1
        wrong = errs_first and calls[0] == 1
        return {'expected': [n * n // 2 if (wrong and n > 30) else n * (n + 1) // 2 for n in context['inputs']]}
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
                      'reference': REFERENCE, 'small_inputs': [3, 4, 40]}
    m = Metered(Actor(plan, auditor), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK)


class G2StaleTie(unittest.TestCase):
    def test_control_without_the_tie_the_right_answer_is_delivered(self):
        result = solve(errs_first=False)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 820))

    def test_a_tie_settled_by_a_later_check_still_voids_the_right_answer(self):
        result = solve(errs_first=True)
        disputed = events(result, 'solve_answer_disputed')
        diff = [e for e in events(result, 'solve_differential') if e.get('outcome') == 'disagree']
        print('\nstatus:', result['status'], '| reason:', result.get('reason'),
              '\n  checks at the end:', result['checks'],
              '\n  differential confirmed:', [e.get('confirmed') for e in diff],
              '\n  void best attempt:', (result.get('void') or {}).get('best_attempt'),
              '\n  disputed event:', disputed)
        self.assertIn(check(40, 820), result['checks'], 'control: the requested input is settled by a check')
        self.assertEqual(result['status'], 'checked', 'a settled right answer is voided by a stale tie')


if __name__ == '__main__':
    unittest.main()
