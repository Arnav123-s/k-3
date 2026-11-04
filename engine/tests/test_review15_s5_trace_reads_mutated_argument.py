"""S5 (completion lost: a right answer voided; regression vs d330165). TRACE_RUN (kuttaka_runtime/solve.py:162-183)
records a piece's argument AFTER the piece returns (`json.dumps(arg)` follows `method(arg)`). A piece that works on
its input in place (sorting, popping, swapping: common in model code) is recorded at the input as it LEFT the
piece, and _piece_gate (solve.py:1902-1936) compares the piece's answer with the piece's voices at that other input.

Scenario: parent "inversions of a list", requested input [3, 1, 2] (right 2), planned as one piece "count
inversions" (no input of its own; a right reference and small inputs [1, 2, 3], [2, 3, 1]). The model's piece is
right and counts by insertion sort in place. The differential test records the reference's voice 0 at [1, 2, 3].
The parent's trace records the call as ([1, 2, 3] -> 2): 2 against the voice 0 there, a tie, the right answer void.
Control: the same piece copying its input first (xs = list(xs)): delivered, 2.
Scripted models; local executor; every method is written here. Run: python3 <this file>."""
import os
import sys
import tempfile
import unittest
from pathlib import Path


from kuttaka_runtime.solve import Workspace   # noqa: E402
from tests.test_solve_review_regressions import Model, check, make_solver, store   # noqa: E402

PARENT = 'Disorder of a list: run(xs) returns the number of inversions of the list xs.'
PIECE = 'Count inversions: run(xs) returns the number of pairs i < j with xs[i] > xs[j].'
REFERENCE = ('def reference(xs):\n'
             '    return sum(1 for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[i] > xs[j])\n')
IN_PLACE = ('def run(xs):\n    count = 0\n    for i in range(1, len(xs)):\n        j = i\n'
            '        while j > 0 and xs[j - 1] > xs[j]:\n            xs[j - 1], xs[j] = xs[j], xs[j - 1]\n'
            '            count += 1\n            j -= 1\n    return count\n')
COPYING = IN_PLACE.replace('    count = 0\n', '    xs = list(xs)\n    count = 0\n', 1)


def inversions(xs):
    return sum(1 for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[i] > xs[j])


def solve(piece_source):
    def plan(context):
        if context['task'] == PARENT:
            return {'contract': 'run(xs) returns the inversion count of xs', 'input': [3, 1, 2],
                    'checks': [check([2, 1], 1), check([3, 2, 1], 3)],
                    'decomposition': {'subtasks': [{'id': 'inv', 'request': PIECE,
                                                    'checks': [check([2, 1], 1), check([1, 3, 2], 1)]}],
                                      'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'inv',
                                                                           'arg': {'op': 'input'}}}}}
        return {'contract': 'run(xs) returns the number of inversions of xs', 'input': None,
                'checks': [check([2, 1], 1), check([1, 3, 2], 1), check([4, 3, 2, 1], 6)],
                'reference': REFERENCE, 'small_inputs': [[1, 2, 3], [2, 3, 1]]}

    def act(request):
        if request.startswith(PIECE):
            return store(piece_source)
        return {'kind': 'unresolved', 'reason': 'not attempted directly'}
    audit = lambda c: {'expected': [inversions(x) for x in c['inputs']]}   # right
    solver, _ = make_solver(Model(plan, act, audit=audit), ws=Workspace(Path(tempfile.mkdtemp())), max_calls=40)
    result = solver.solve(PARENT)
    print('\n%s: %s, answer %r (right 2) | %s' % ('in place' if piece_source == IN_PLACE else 'copying',
          result['status'], result.get('answer'), result.get('disputed_answer')), file=sys.stderr)
    return result


class S5TraceReadsTheMutatedArgument(unittest.TestCase):
    def test_control_piece_copies_its_input(self):
        result = solve(COPYING)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 2))

    def test_piece_sorting_in_place(self):
        result = solve(IN_PLACE)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 2),
                         'a right answer is voided: the trace compares the piece with voices at the input it left')


if __name__ == '__main__':
    unittest.main()
