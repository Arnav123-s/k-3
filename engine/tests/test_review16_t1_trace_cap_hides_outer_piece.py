"""T1 (wrong answer delivered as checked; regression vs e7cb51d). TRACE_RUN (kuttaka_runtime/solve.py:162-192) now
traces every depth (`base.__globals__['use'] = traced`, solve.py:186-187) into the same list `_CALLS`, capped at 200
entries (solve.py:179), and a call is appended only AFTER the piece returns (solve.py:178-181). A piece that makes
200 or more piece calls of its own fills the list with its inner calls before its own entry is written, so its
own answer on the input the parent handed it is never compared with its voices there (the H2 rule): the dispute
the piece gate exists for is lost. At e7cb51d only the parent's own calls were traced, and the piece's entry was
always the first written.

Scenario: root "sum of squares" (requested input: 250 ones, right 250), planned as one piece P1 ("sum of squares",
a reference, the requested list among its small inputs), and P1 planned as two pieces: square, then total by the
recipe total(map(square)). The model's total is wrong for lists longer than 200 (adds 1). P1's differential test
records the reference's voice 250 at the 250 ones; the auditor gives no usable reply, so P1 is delivered. At the
root, the piece gate's trace sees 200 square calls and stops recording: P1's 251 at the 250 ones is never compared
with the reference's 250 there, and 251 is delivered as checked.
  e7cb51d: the trace records P1's call (one entry): 251 against the voice 250, a tie: void.
Control: the same with 150 ones and total wrong above 100 (152 entries fit): void.
Scripted models; local executor; every method is written here. Run: python3 <this file> from the repo root."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

ROOT = 'Energy of a list: run(xs) returns the sum of the squares of the numbers in xs.'
P1 = 'Sum of squares: run(xs) returns x1*x1 + x2*x2 + ... for the list xs.'
SQ = 'Square: run(x) returns x*x.'
TOT = 'Total: run(xs) returns the sum of the list xs.'
REFERENCE = 'def reference(xs):\n    total = 0\n    for x in xs:\n        total += x * x\n    return total\n'
SQUARE = 'def run(x):\n    return x * x\n'
TOTAL = 'def run(xs):\n    return sum(xs) + (1 if len(xs) > %d else 0)\n'   # wrong for long lists


def solve(length, wrong_above):
    asked = [1] * length

    def plan(context):
        task = context['task']
        if task == ROOT:
            return {'contract': 'run(xs) returns the sum of x*x over the list xs', 'input': asked,
                    'checks': [check([1, 2], 5), check([3], 9), check([2, 2], 8)],
                    'decomposition': {'subtasks': [{'id': 'p1', 'request': P1, 'checks': [check([1, 2], 5)]}],
                                      'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'p1',
                                                                           'arg': {'op': 'input'}}}}}
        if task == P1:
            return {'contract': 'run(xs) returns the sum of x*x over the list xs', 'input': None,
                    'checks': [check([1, 2], 5), check([3], 9), check([], 0), check([2, 2, 2], 12)],
                    'reference': REFERENCE, 'small_inputs': [[1, 1], [4], asked],
                    'decomposition': {'subtasks': [{'id': 'sq', 'request': SQ, 'checks': [check(3, 9)]},
                                                   {'id': 'tot', 'request': TOT, 'checks': [check([1, 4], 5)]}],
                                      'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'tot', 'arg': {
                                          'op': 'map', 'method': 'sq', 'arg': {'op': 'input'}}}}}}
        if task == SQ:
            return {'contract': 'run(x) returns x*x', 'input': None,
                    'checks': [check(3, 9), check(0, 0), check(-2, 4), check(5, 25)]}
        return {'contract': 'run(xs) returns the sum of xs', 'input': None,
                'checks': [check([1, 4], 5), check([], 0), check([9], 9), check([1, 2, 3], 6)]}

    def act(request):
        if request.startswith(SQ):
            return store(SQUARE)
        if request.startswith(TOT):
            return store(TOTAL % wrong_above)
        return {'kind': 'unresolved', 'reason': 'not attempted directly'}

    def audit(context):   # right on the pieces' hand checks; no usable reply for the sum of squares
        if context['task'] == P1:
            return {}
        if context['task'] == SQ:
            return {'expected': [x * x for x in context['inputs']]}
        if context['task'] == TOT:
            return {'expected': [sum(x) for x in context['inputs']]}
        return {'expected': [sum(v * v for v in x) for x in context['inputs']]}
    solver, _ = make_solver(Model(plan, act, audit=audit), ws=Workspace(Path(tempfile.mkdtemp())), max_calls=60)
    result = solver.solve(ROOT)
    print('\n%d ones (total wrong above %d): %s, answer %r (right %d) | disputed by %s | pieces %s' % (
        length, wrong_above, result['status'], result.get('answer'), length,
        [e.get('by') for e in events(result, 'solve_answer_disputed')],
        [(p.get('task', '')[:12], p.get('status')) for p in result.get('subtasks', [])]), file=sys.stderr)
    return result


class T1TraceCapHidesTheOuterPiece(unittest.TestCase):
    def test_control_fewer_inner_calls(self):
        result = solve(150, 100)
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 151))
        self.assertIn('piece voices', [e.get('by') for e in events(result, 'solve_answer_disputed')])

    def test_inner_calls_fill_the_trace(self):
        result = solve(250, 200)
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 251),
                            'the piece\'s disputed answer is delivered: its own call was never recorded')


if __name__ == '__main__':
    unittest.main()
