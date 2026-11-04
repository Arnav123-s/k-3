"""H2. The G3 fix keys a tie by the prompt of the solve level that raised it and voids that level's result only
when the tie is on that level's own requested input (solve.py:2056-2066, 1564-1577 at 11e0ea9). A piece of the
planner's decomposition is planned from its own request and checks (the root's requested input is not passed
down), so its plan normally names no input; its rival round then records nothing for a 2-2 tie on 40, the root's
requested input, the piece is delivered, and the root, assembled by the recipe with no rival round of its own,
delivers [800, 1800] for 40 (right: [820, 1820]), the value both pieces' ties disputed.

Control: the G3 test's own scenario (pieces planned with input 40 and 12) voids the root. This test is G3's
script with one change: the pieces' plans name no requested input.
Scripted models; local executor; every method, the reference and the auditor are written for this test."""
import unittest

from tests import test_review7_g3_answer_disputed_last_writer_wins as g3
from tests.test_solve_review_regressions import events


class H2PieceTieWithoutPieceInput(unittest.TestCase):
    def solve(self, piece_input):
        original = g3.solve
        # g3.solve builds its planner inside; rebuild it with the pieces' requested input replaced.
        import tempfile
        from pathlib import Path
        from kuttaka_runtime.solve import Metered, Solver, Workspace
        from tests.test_kuttaka_campaign import local_executor
        check = g3.check
        pieces = {'a': {'id': 'a', 'request': g3.A, 'checks': [check(1, 1)]},
                  'b': {'id': 'b', 'request': g3.B, 'checks': [check(1, 1001)]}}

        def plan(context):
            if context['task'] == g3.ROOT:
                return {'contract': 'run(n) returns [T(n), T(n)], T(n) = n*(n+1)/2', 'input': 40,
                        'checks': [check(1, [1, 1001]), check(2, [3, 1003]), check(5, [15, 1015])],
                        'decomposition': {'subtasks': [pieces['a'], pieces['b']], 'recipe': {'version': 1, 'output': {
                            'op': 'array', 'items': [{'op': 'call', 'method': 'a', 'arg': {'op': 'input'}},
                                                     {'op': 'call', 'method': 'b', 'arg': {'op': 'input'}}]}}}}
            requested, shift = (40, 0) if context['task'] == g3.A else (12, 1000)
            return {'contract': 'run(n) returns %d + n*(n+1)/2 for n >= 0' % shift,
                    'input': requested if piece_input else None,
                    'checks': [check(n, shift + n * (n + 1) // 2) for n in (0, 1, 2, 5)],
                    'reference': g3.REFERENCE % shift, 'small_inputs': [3, 4, 40, 12]}
        m = Metered(g3.Actor(plan, None, audit=g3.auditor), {'used': 0, 'limit': 30})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                        sketcher=m, max_executions=400, say=lambda _: None)
        return solver.solve(g3.ROOT), solver

    def test_control_pieces_planned_with_their_input_void_the_root(self):
        result, solver = self.solve(piece_input=True)
        print('\npieces with input: status', result['status'], '| answer', result.get('answer'),
              '| tie records:', getattr(solver, '_voices', None))
        self.assertNotEqual(result['status'], 'checked')

    def test_pieces_planned_without_an_input_deliver_the_disputed_answer(self):
        # Closed (was an open limit, an expected failure, since the sixth review): pieces record their voices on
        # every input, and the parent's piece gate traces what it hands each piece and reads the piece's voices there.
        result, solver = self.solve(piece_input=False)
        ties = [e for e in events(result, 'solve_rival')]
        print('\npieces without input: status', result['status'], '| answer', result.get('answer'),
              '(right: [820, 1820]) | delivered_by', result.get('delivered_by'),
              '| tie records:', getattr(solver, '_voices', None),
              '\n  piece rival outcomes:', [e.get('outcome') for e in ties])
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', [800, 1800]),
                            'the root delivers the answer its pieces tied on, 2-2, at the requested input')


if __name__ == '__main__':
    unittest.main()
