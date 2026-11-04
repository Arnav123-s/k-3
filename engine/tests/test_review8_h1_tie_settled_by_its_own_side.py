"""H1. settled_since (solve.py:1564-1568 at 11e0ea9) treats any non-HAND check on the tied input as settling the
tie. rival() (solve.py:2051-2053, 2088, 2093) adds such a check, labelled TWO_DERIVATIONS, from the method and the
auditor alone whenever a further rival disagrees with both, even while the reference still contradicts the method
on that input. Scenario: one attempt writes R3, R, then W (W is delivered; R3 and R are its rivals). Against R
the requested input 40 ties 2-2 (W 800 + auditor 800 vs R 820 + reference 820); nothing is added, the loop goes
on to R3 (1600 at 40), the auditor again says 800, and the check 40 -> 800 is added "against the rival" with the
reference (820) against it. That check settles the tie, makes 40 a check input (so differential never compares
the reference there again), and 800 is delivered as checked. Right: T(40) = 820.

Control: the same script without R3 voids the answer (the tie stands), which is also what 9fc29a0 did in the
main scenario (a recorded tie on the requested input voided at depth 0 whatever followed).
Scripted models; local executor; every method, the reference and the auditor are written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 1 + 2 + ... + n.'
REFERENCE = ('def reference(n):\n    if n < 1:\n        raise ValueError("positive n only")\n'
             '    total = 0\n    for k in range(1, n + 1):\n        total += k\n    return total\n')
R = 'def run(n):\n    if n < 1:\n        return -1\n    return n * (n + 1) // 2\n'                        # right n >= 1
R3 = 'def run(n):\n    if n < 1:\n        return -1\n    return n * (n + 1) // 2 if n % 40 else n * n\n'  # 1600 at 40
W = ('def run(n):\n    if n < 0:\n        return -1\n    if n > 0 and n % 20 == 0:\n'
     '        return (n // 2) * n\n    return n * (n + 1) // 2\n')     # fast path forgets the +1: 800 at 40


class Actor(Model):
    def __init__(self, plan, audit, sources):
        super().__init__(plan, None, audit=audit)
        self.sources = sources

    def propose(self, context):
        if 'request' in context and 'history' in context:
            history = context['history']
            last = history[-1] if history else None
            if last and last['action'].get('tool') == 'remember_method' and isinstance(last['observation'], dict) \
                    and last['observation'].get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': last['observation']['artifact']}}
            stores = [h for h in history if h['action'].get('tool') == 'remember_method']
            if len(stores) < len(self.sources):
                return store(self.sources[len(stores)])
            return {'kind': 'unresolved', 'reason': 'scripted stop'}
        return super().propose(context)


def auditor(context):   # a blind re-derivation that makes the method's mistake at 40, every time it is asked
    return {'expected': [n * n // 2 if n == 40 else n * (n + 1) // 2 for n in context['inputs']]}


def solve(sources):
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
                      'reference': REFERENCE, 'small_inputs': [3, 4, 40]}
    m = Metered(Actor(plan, auditor, sources), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK), solver


class H1TieSettledByItsOwnSide(unittest.TestCase):
    def test_control_the_tie_alone_voids_the_answer(self):
        result, solver = solve([R, W])
        print('\ncontrol [R, W]: status', result['status'], '|', result.get('reason'),
              '| tie record:', getattr(solver, '_voices', None))
        self.assertTrue(getattr(solver, '_voices', None))
        self.assertNotEqual(result['status'], 'checked')

    def test_a_check_made_by_the_tied_side_settles_the_tie_and_delivers_the_wrong_answer(self):
        result, solver = solve([R3, R, W])
        rivals = [(e.get('rival', '')[:8], e.get('outcome')) for e in events(result, 'solve_rival')]
        print('\n[R3, R, W]: status', result['status'], '| answer', result.get('answer'), '(right: 820)',
              '\n  checks at the end:', result['checks'],
              '\n  tie record:', getattr(solver, '_voices', None),
              '\n  rival events:', rivals)
        self.assertTrue(getattr(solver, '_voices', None), 'control: the tie on 40 was recorded')
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 800),
                            'a tie settled only by its own side (method + auditor) delivers the answer the '
                            'reference contradicts')


if __name__ == '__main__':
    unittest.main()
