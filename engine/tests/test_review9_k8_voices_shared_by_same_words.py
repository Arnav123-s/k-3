"""K8. Voices are kept per prompt (solve.py:1653-1656, `self._voices[prompt]`) and filtered by the requested input only
when recorded; _answer_gate (solve.py:1668-1669) reads every voice under the prompt, whatever input it was about.
Two pieces with the same words but their own checks and requested inputs (the cycle guard allows it: other checks)
share one list, so what was said about piece A's input is counted against piece B's answer on another input.

Scenario: the root asks [T(40), 1000 + T(40)] by a recipe over two pieces whose request is the same sentence;
the planner, seeing each piece's checks, plans A (input 40) and B (1000 + T, input 12). In A, differential testing
refutes W at 40 (the reference and the auditor say 820), and A's next method is right. B's method is right and
nothing disputes it, but its gate sees A's auditor voice (820, about 40) against its answer 1078 at 12: B is void
"disputed", and the root, which needs B, is not delivered.

Control: the same script with B's request worded differently delivers [820, 1820]. Scripted models; local
executor; every method, the reference and the auditor are written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

ROOT = 'Triangular pair: run(n) returns [T(n), T(n) + 1000].'
PIECE = 'Triangular piece: run(n) returns the shifted sum 1 + ... + n.'
REFERENCE = 'def reference(n):\n    return sum(range(n + 1)) + %d\n'
W_A = 'def run(n):\n    return (n // 2) * n if n > 0 and n % 20 == 0 else n * (n + 1) // 2\n'   # 800 at 40
RIGHT = 'def run(n):\n    return n * (n + 1) // 2 + %d\n'


def solve(b_request):
    pieces = {'a': {'id': 'a', 'request': PIECE, 'checks': [check(1, 1)]},
              'b': {'id': 'b', 'request': b_request, 'checks': [check(1, 1001)]}}

    def plan(context):
        if context['task'] == ROOT:
            return {'contract': 'run(n) returns [T(n), T(n) + 1000], T(n) = n*(n+1)/2', 'input': 40,
                    'checks': [check(1, [1, 1001]), check(2, [3, 1003]), check(5, [15, 1015])],
                    'decomposition': {'subtasks': [pieces['a'], pieces['b']], 'recipe': {'version': 1, 'output': {
                        'op': 'array', 'items': [{'op': 'call', 'method': 'a', 'arg': {'op': 'input'}},
                                                 {'op': 'call', 'method': 'b', 'arg': {'op': 'input'}}]}}}}
        examples = context.get('model_examples') or context.get('user_examples') or []
        shift = 1000 if any(e['expected'] == 1001 for e in examples) else 0
        requested = 12 if shift else 40
        return {'contract': 'run(n) returns %d + n*(n+1)/2 for n >= 0' % shift, 'input': requested,
                'checks': [check(n, shift + n * (n + 1) // 2) for n in (0, 1, 2, 5)],
                'reference': REFERENCE % shift, 'small_inputs': [3, 4, requested]}

    def act(request):
        if request.startswith(ROOT):
            return {'kind': 'unresolved', 'reason': 'the root is assembled from its pieces only'}
        if 'returns 1000 +' in request:
            return store(RIGHT % 1000)
        return store(RIGHT % 0 if '"input": 40' in request else W_A)

    def auditor(context):   # right everywhere, for the root and for each piece
        if context['task'] == ROOT:
            return {'expected': [[n * (n + 1) // 2, 1000 + n * (n + 1) // 2] for n in context['inputs']]}
        shift = 1000 if 'returns 1000 +' in context.get('contract', '') else 0
        return {'expected': [shift + n * (n + 1) // 2 for n in context['inputs']]}
    m = Metered(Model(plan, act, audit=auditor), {'used': 0, 'limit': 30})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=400, say=lambda _: None)
    return solver.solve(ROOT), solver


class K8VoicesSharedBySameWords(unittest.TestCase):
    def test_control_pieces_with_their_own_words_deliver(self):
        result, _ = solve(PIECE.replace('Triangular piece', 'Shifted triangular piece'))
        self.assertEqual((result['status'], result.get('answer')), ('checked', [820, 1820]))

    def test_a_voice_about_piece_a_input_voids_piece_b(self):
        result, solver = solve(PIECE)
        print('\nstatus', result['status'], '| answer', result.get('answer'), '(right: [820, 1820])',
              '\n  pieces:', [(p.get('status'), p.get('reason')) for p in result['subtasks']],
              '\n  voices under the piece words:', solver._voices.get(PIECE),
              '\n  gate records:', [(d.get('input'), d.get('answer'), d.get('voices')) for d in
                                   events(result, 'solve_answer_disputed')])
        self.assertEqual((result['status'], result.get('answer')), ('checked', [820, 1820]),
                         'a voice about another input, kept under the same words, voids a right piece')


if __name__ == '__main__':
    unittest.main()
