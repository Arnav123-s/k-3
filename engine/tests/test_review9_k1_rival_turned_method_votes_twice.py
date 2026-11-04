"""K1. _answer_gate (solve.py:1681-1686 at 3af0808) counts the delivered method once ("the method counting one")
and then counts every recorded 'rival' voice whose source is not refuted. When rival() refutes the method and
admits the rival in its place (solve.py:2146-2148), the rival's own voice on the requested input, recorded at
solve.py:2098-2101 with source=rival, is still in the list: the delivered method is counted twice.

Scenario: T(n) = n(n+1)/2, requested input 40 (not a declared small input); the planner's hand check 0 -> 1 is
wrong (the reference, written for 1 <= n <= 30, cannot run there). One attempt writes B (right at 0, 900 at 40)
then A (fits the wrong check, 800 at 40). The rival round corrects check 0 by B + auditor, refutes A, admits B.
On 40 the record is: B says 900, the auditor (right everywhere) says 820. The stated rule (the method counts
one; another value with as many voices voids the answer) gives 1 against 1: disputed. The gate counts B twice
(2 against 1) and delivers 900 as checked. Right: 820.

Control: the gate itself, called on the same record with the delivered method's own rival voice left out,
returns the dispute. Scripted models; local executor; every method, the reference and the auditor are written
for this test."""
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace, canonical
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + 2 + ... + n.'
REFERENCE = ('def reference(n):\n    if n < 1 or n > 30:\n        raise ValueError("written for 1..30")\n'
             '    return sum(range(1, n + 1))\n')
B = ('def run(n):\n    if n < 0:\n        return -1\n    if n > 0 and n % 20 == 0:\n'
     '        return n * n // 2 + 100\n    return n * (n + 1) // 2\n')            # right at 0; 900 at 40
A = ('def run(n):\n    if n < 0:\n        return -1\n    if n == 0:\n        return 1\n'
     '    if n % 20 == 0:\n        return (n // 2) * n\n    return n * (n + 1) // 2\n')   # fits 0 -> 1; 800 at 40


class Actor(Model):
    """Stores its sources in order (automatic submission rejects each that fails a check), as in H1."""
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


def auditor(context):   # right everywhere
    return {'expected': [n * (n + 1) // 2 for n in context['inputs']]}


def solve():
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                      'checks': [check(0, 1), check(1, 1), check(2, 3), check(5, 15)],
                      'reference': REFERENCE, 'small_inputs': [3, 4, 7]}
    m = Metered(Actor(plan, auditor, [B, A]), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK), solver


class K1RivalTurnedMethodVotesTwice(unittest.TestCase):
    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_the_admitted_rival_is_counted_twice_and_a_disputed_answer_is_delivered(self):
        result, solver = solve()
        spec = {'input': 40, 'small_inputs': [3, 4, 7], 'reference': REFERENCE,
                'contract': 'run(n) returns n*(n+1)/2 for n >= 0'}
        key = solver._voice_key(TASK, spec)   # adapted: voices are kept per task and input (K8)
        voices = solver._voices.get(key, [])
        rivals = [(e.get('outcome'), e.get('corrected')) for e in events(result, 'solve_rival')]
        print('\nstatus', result['status'], '| answer', result.get('answer'), '(right: 820)',
              '| delivered_by', result.get('delivered_by'),
              '\n  rival events:', rivals,
              '\n  voices on 40:', [(k, v, (s or '')[:8]) for k, v, s in voices],
              '\n  delivered artifact:', (result.get('artifact') or '')[:8])
        # Adapted: since the fix the answer is void and the result names no artifact; the method the gate judged
        # is named in its record.
        judged = result.get('artifact') or (events(result, 'solve_answer_disputed') or [{}])[-1].get('artifact')
        own = [v for v in voices if v[0] == 'rival' and v[2] == judged]
        self.assertTrue(own, 'precondition: the delivered method is the rival whose voice was recorded')
        # Control: the same record without the delivered method's own voice is a 1-1 dispute for the gate.
        solver._voices[key] = [v for v in voices if v not in own]
        self.assertIsNotNone(solver._answer_gate(TASK, spec, result.get('answer'), result.get('artifact')),
                             'control: the gate voids 900 when the method is counted once')
        solver._voices[key] = voices
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 900),
                            'the delivered method votes for itself twice (as the method and as a rival)')


if __name__ == '__main__':
    unittest.main()
