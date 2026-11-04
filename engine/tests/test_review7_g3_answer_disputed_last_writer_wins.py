"""G3. Solver._answer_disputed is one slot for the whole job, written by every rival() call at any depth with that
spec's own requested input, last writer wins (solve.py:2036-2045 at 9fc29a0); the depth-0 void (solve.py:1554-1561)
compares only that last record's input. Whether a tie on the requested input voids the root then depends on the
order of unrelated subtasks: here piece A (planned with input 40, the root's requested input) ties 2-2 on 40 and
piece B (planned with input 12) ties 2-2 on 12. Solved in the order [B, A], the root is void (A's tie is the last
record); in the order [A, B], B's record replaces A's and the root delivers [800, 1800] for 40 (right: [820, 1820]),
the value A's tie disputed: F1's harm through a decomposition.

Scripted models; local executor; every method, the reference and the auditor are written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

ROOT = 'Triangular pair: run(n) returns [T(n), T(n) + 1000].'
A, B = 'Triangular piece A: run(n) returns 1 + ... + n.', 'Shifted triangular piece B: run(n) returns 1000 + 1 + ... + n.'
REFERENCE = ('def reference(n):\n    if n < 1:\n        raise ValueError("positive n only")\n'
             '    total = 0\n    for k in range(1, n + 1):\n        total += k\n    return total + %d\n')
RIVAL = 'def run(n):\n    if n < 1:\n        return -1\n    return n * (n + 1) // 2 + %d\n'       # right for n >= 1
WRONG_A = 'def run(n):\n    return n * (n + 1) // 2 if n <= 30 else n * n // 2\n'                 # wrong above 30
WRONG_B = 'def run(n):\n    return 1000 + (n * (n + 1) // 2 if n <= 11 else n * n // 2)\n'       # wrong above 11
SHIFT = {A: 0, B: 1000}


class Actor(Model):
    """Each piece: its rival first (fails check 0), then its wrong method (passes every check)."""
    def propose(self, context):
        if 'request' in context and 'history' in context:
            history = context['history']
            last = history[-1] if history else None
            if last and last['action'].get('tool') == 'remember_method' and isinstance(last['observation'], dict) \
                    and last['observation'].get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': last['observation']['artifact']}}
            stores = [h for h in history if h['action'].get('tool') == 'remember_method']
            piece = A if context['request'].startswith(A) else B if context['request'].startswith(B) else None
            if piece and len(stores) < 2:
                return store(RIVAL % SHIFT[piece] if not stores else {A: WRONG_A, B: WRONG_B}[piece])
            return {'kind': 'unresolved', 'reason': 'scripted stop'}
        return super().propose(context)


def auditor(context):   # a blind re-derivation that errs at 40 and 12 the way the methods do
    shift = SHIFT.get(context['task'], 0)
    return {'expected': [shift + (n * n // 2 if n in (40, 12) else n * (n + 1) // 2) for n in context['inputs']]}


def solve(order):
    pieces = {'a': {'id': 'a', 'request': A, 'checks': [check(1, 1)]},
              'b': {'id': 'b', 'request': B, 'checks': [check(1, 1001)]}}

    def plan(context):
        if context['task'] == ROOT:
            return {'contract': 'run(n) returns [T(n), T(n)], T(n) = n*(n+1)/2', 'input': 40,
                    'checks': [check(1, [1, 1001]), check(2, [3, 1003]), check(5, [15, 1015])],
                    'decomposition': {'subtasks': [pieces[k] for k in order], 'recipe': {'version': 1, 'output': {
                        'op': 'array', 'items': [{'op': 'call', 'method': 'a', 'arg': {'op': 'input'}},
                                                 {'op': 'call', 'method': 'b', 'arg': {'op': 'input'}}]}}}}
        requested, shift = (40, 0) if context['task'] == A else (12, 1000)
        return {'contract': 'run(n) returns %d + n*(n+1)/2 for n >= 0' % shift, 'input': requested,
                'checks': [check(n, shift + n * (n + 1) // 2) for n in (0, 1, 2, 5)],
                'reference': REFERENCE % shift, 'small_inputs': [3, 4, requested]}
    m = Metered(Actor(plan, None, audit=auditor), {'used': 0, 'limit': 30})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=400, say=lambda _: None)
    return solver.solve(ROOT), solver


class G3LastWriterWins(unittest.TestCase):
    def test_control_order_b_then_a_voids_the_root(self):
        result, _ = solve(['b', 'a'])
        print('\n[B, A]: status', result['status'], '| answer', result.get('answer'), '|', result.get('reason'),
              '| pieces delivered by:', [p.get('delivered_by') for p in result['subtasks']])
        self.assertNotEqual(result['status'], 'checked')

    def test_order_a_then_b_delivers_the_answer_a_tie_disputed(self):
        result, solver = solve(['a', 'b'])
        print('\n[A, B]: status', result['status'], '| answer', result.get('answer'), '(right: [820, 1820])',
              '| pieces delivered by:', [p.get('delivered_by') for p in result['subtasks']],
              '| last disputed record:', getattr(solver, '_voices', None))
        self.assertNotEqual(result['status'], 'checked', 'the answer A\'s tie disputed is delivered: [B, A] voids it')


if __name__ == '__main__':
    unittest.main()
