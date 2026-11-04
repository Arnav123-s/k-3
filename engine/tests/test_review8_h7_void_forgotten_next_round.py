"""H7. A tie on the requested input is kept only for the job that raised it (_answer_disputed is reset at every
root solve, solve.py:1256 at 11e0ea9) and nothing of it is stored (the method is not refuted; no check is kept).
With --rounds, round 1 voids the disputed answer; if round 2's attempt writes only the tied method (no rival),
nothing re-raises the tie: differential disputes 40 (the method 800 vs the reference 820), the auditor errs with
the method as before, no check is added, and 800 is delivered as checked (right: 820). The void of round 1 is
undone by round 2 knowing less.

Control: round 1 alone (rounds=1) is void. Scripted models; local executor; every method, the reference and the
auditor are written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace, settings, solve_task
from tests.test_kuttaka_campaign import local_executor
from tests.test_review7_g2_stale_tie_voids_settled_answer import REFERENCE, RIVAL, TASK
from tests.test_solve_review_regressions import Model, check, store

W = ('def run(n):\n    if n > 0 and n % 20 == 0:\n        return (n // 2) * n\n'
     '    return n * (n + 1) // 2\n')     # a fast path that forgets the +1: 800 at 40


class Rounds(Model):
    """Round 1: R (fails check 0), then W. Later rounds: W at once."""
    def __init__(self):
        super().__init__(self.planned, None, audit=lambda c: {
            'expected': [n * n // 2 if n == 40 else n * (n + 1) // 2 for n in c['inputs']]})   # errs at 40 like W
        self.round = 0

    def planned(self, context):
        if 'previous_contract' not in context:
            self.round += 1
        return {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
                'reference': REFERENCE, 'small_inputs': [3, 4, 40]}

    def propose(self, context):
        if 'request' in context and 'history' in context:
            history = context['history']
            last = history[-1] if history else None
            if last and last['action'].get('tool') == 'remember_method' and isinstance(last['observation'], dict) \
                    and last['observation'].get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': last['observation']['artifact']}}
            stores = [h for h in history if h['action'].get('tool') == 'remember_method']
            plan = [RIVAL, W] if self.round == 1 else [W]
            if len(stores) < len(plan):
                return store(plan[len(stores)])
            return {'kind': 'unresolved', 'reason': 'scripted stop'}
        return super().propose(context)


def run(rounds):
    return solve_task(TASK, settings=settings(rounds=rounds, max_calls=20, max_executions=400),
                      workspace=Workspace(Path(tempfile.mkdtemp())), models=(Rounds(),) * 5, executor=local_executor)


class H7VoidForgottenNextRound(unittest.TestCase):
    def test_control_round_one_is_void(self):
        result = run(1)
        print('\nrounds=1: status', result['status'], '|', result.get('reason'))
        self.assertEqual(result['status'], 'incomplete')

    def test_round_two_delivers_the_answer_round_one_found_disputed(self):
        result = run(2)
        print('\nrounds=2: status', result['status'], '| answer', result.get('answer'), '(right: 820)',
              '\n  rounds:', [(h['round'], h['status'], (h.get('reason') or '')[:60]) for h in result['rounds']])
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 800),
                            'round 2 delivers the answer round 1 voided as disputed')


if __name__ == '__main__':
    unittest.main()
