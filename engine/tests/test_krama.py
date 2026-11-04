"""Krama: the model writes one step; Kuttaka takes every step after it (first-hand session k, 2026-11-02: the jugs task;
the move was checked as a piece, and the loop that compounds it was still written by the model).

Scenario: two jugs of a and b liters; the fewest moves until one holds d liters, or -1. The actor declines the whole
method; the splitter returns the step (start, moves, done, answer, exhausted).
  - a right step: checked, with no loop written by the model (the walk is Kuttaka's);
  - a wrong step (a pour that ignores the capacity): its failed checks come back with the path the walk took, and
    the step written after reading them is checked;
  - a simulation (one move per state, the Collatz walk) and a walk that comes back on itself (an impossible d).
Control: a splitter that returns pieces still splits into pieces. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = ('Two jugs hold a and b liters and start empty. A move fills a jug, empties a jug, or pours one jug into the '
        'other until the first is empty or the second is full. Return the fewest moves after which one jug holds '
        'exactly d liters, or -1 if that is impossible.')
REFERENCE = '''def reference(x):
    a, b, d = x['a'], x['b'], x['d']
    seen, frontier, moves = {(0, 0)}, [(0, 0)], 0
    while frontier:
        if any(d in state for state in frontier):
            return moves
        nxt = []
        for p, q in frontier:
            u, v = min(p, b - q), min(q, a - p)
            for s in ((a, q), (p, b), (0, q), (p, 0), (p - u, q + u), (p + v, q - v)):
                if s not in seen:
                    seen.add(s)
                    nxt.append(s)
        frontier, moves = nxt, moves + 1
    return -1
'''
STEP = '''def start(x):
    return [0, 0]


def moves(x, state):
    a, b = x['a'], x['b']
    p, q = state
    u, v = min(p, b - q), min(q, a - p)
    return [[a, q], [p, b], [0, q], [p, 0], [p - u, q + u], [p + v, q - v]]


def done(x, state):
    return x['d'] in state


def answer(x, state, moves):
    return moves


def exhausted(x):
    return -1
'''
WRONG = STEP.replace('u, v = min(p, b - q), min(q, a - p)', 'u, v = p, q')   # a pour that ignores the capacity
c = lambda a, b, d: {'a': a, 'b': b, 'd': d}
CHECKS = [check(c(3, 5, 0), 0), check(c(3, 5, 5), 1), check(c(3, 5, 2), 2), check(c(2, 4, 3), -1), check(c(3, 5, 4), 6)]


def solve(replies, task=TASK, checks=CHECKS, reference=REFERENCE, seen=None):
    plan = lambda context: {'contract': 'run({"a": a, "b": b, "d": d}) returns the fewest moves, or -1',
                            'input': None, 'reference': reference, 'checks': checks}
    act = lambda request: {'kind': 'unresolved', 'reason': 'the smaller step is one move'}
    queue = list(replies)

    def split(context):
        if seen is not None:
            seen.append(context)
        return queue.pop(0) if queue else {'subtasks': []}
    m = Metered(Model(plan, act, split=split, audit=lambda c: {}), {'used': 0, 'limit': 12})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(task)


class Krama(unittest.TestCase):
    def test_a_right_step_is_taken_step_after_step_and_checked(self):
        result = solve([{'steps': STEP}])
        self.assertEqual(result['status'], 'checked')
        self.assertIn('__kuttaka_krama__', result['source'], 'the loop was not the walk Kuttaka built')
        self.assertEqual([e['outcome'] for e in events(result, 'solve_krama')], ['checked'])

    def test_a_wrong_step_is_shown_its_path_and_taken_again(self):
        seen = []
        result = solve([{'steps': WRONG}, {'steps': STEP}], seen=seen)
        self.assertEqual(result['status'], 'checked')
        rejected = seen[1].get('previous_reply_rejected', '')
        self.assertIn('path', rejected)
        self.assertIn('"expected": 6', rejected)
        self.assertEqual([e['outcome'] for e in events(result, 'solve_krama')], ['incomplete', 'checked'])

    def test_a_simulation_and_a_walk_that_comes_back_on_itself(self):
        collatz = ('def start(n):\n    return n\n\n\ndef moves(n, m):\n    return [m // 2 if m % 2 == 0 else 3 * m + 1]\n\n\n'
                   'def done(n, m):\n    return m == 1\n\n\ndef answer(n, m, moves):\n    return moves\n')
        ref = ('def reference(n):\n    k = 0\n    while n != 1:\n        n = n // 2 if n % 2 == 0 else 3 * n + 1\n'
               '        k += 1\n    return k\n')
        result = solve([{'steps': collatz}], task='Count the Collatz steps from n down to 1.',
                       checks=[check(1, 0), check(2, 1), check(3, 7), check(6, 8), check(27, 111)], reference=ref)
        self.assertEqual(result['status'], 'checked')
        from kuttaka_runtime import solve_krama
        walk = solve_krama.assemble(STEP)
        space = {}
        exec(walk, space)
        self.assertEqual(space['run'](c(2, 4, 3)), -1)   # every reachable state seen once, then exhausted

    def test_control_pieces_still_split_into_pieces(self):
        piece = {'subtasks': [{'id': 'moves', 'request': 'run(x) returns the states one move away',
                               'checks': [check({'a': 3, 'b': 5, 'state': [0, 0]}, [[0, 5], [3, 0]])]}],
                 'recipe': None}
        result = solve([piece])
        self.assertEqual(events(result, 'solve_krama'), [])
        self.assertTrue(any('one move away' in (p.get('task') or '') for p in result.get('subtasks') or []))



# The runner's Docker gate at a0cbfc2 (2026-11-03): the wrong step's walk grows to the state limit on three checks,
# about 8 s in one sandbox job here and past the 20 s limit on a machine twice as slow; the job timed out and the
# feedback on every check was lost ("the walk did not finish on the checks"). The same loss, made certain here: on
# one check the walk never ends. Each check now has its own time, so the others still come back.
NEVER_ON_THREE = WRONG.replace("    p, q = state\n", "    p, q = state\n    if x['d'] == 3:\n        import time\n"
                                                       "        time.sleep(60)\n")
assert NEVER_ON_THREE != WRONG


class SlowWalk(unittest.TestCase):
    def test_a_walk_that_never_ends_on_one_check_leaves_the_feedback_on_the_others(self):
        seen = []
        result = solve([{'steps': NEVER_ON_THREE}, {'steps': STEP}], seen=seen)
        self.assertEqual(result['status'], 'checked')
        rejected = seen[1].get('previous_reply_rejected', '')
        self.assertIn('"raised": "RuntimeError"', rejected)   # the check before it came back
        self.assertIn('"timeout": true', rejected)            # the one that never ends is named, not lost
        self.assertIn('"expected": 6', rejected)

if __name__ == '__main__':
    unittest.main()
