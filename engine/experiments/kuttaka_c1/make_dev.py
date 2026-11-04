"""Development stream (builder-authored, visible): 3 chains x 3 dependent tasks with holdout inputs.

Expected values come from the reference solutions below, which never enter a run; only
requests and visible checks do. This stream is for development; the sealed stream is
written by a separate evaluator session.
"""
import json
from collections import deque
from fractions import Fraction
from pathlib import Path


def egcd(p):
    a, b = p['a'], p['b']
    x0, y0, x1, y1 = 1, 0, 0, 1
    while b:
        q = a // b
        a, b, x0, x1, y0, y1 = b, a - q * b, x1, x0 - q * x1, y1, y0 - q * y1
    return {'g': a, 'x': x0, 'y': y0}


def dioph(p):
    r = egcd({'a': p['a'], 'b': p['b']})
    g = r['g']
    if p['c'] % g:
        return None
    step = p['b'] // g
    x = (r['x'] * (p['c'] // g)) % step
    return {'x': x, 'y': (p['c'] - p['a'] * x) // p['b']}


def kuttaka(p):
    n, m = 0, 1
    for r, mod in zip(p['remainders'], p['moduli']):
        # n + m*t = r (mod mod)
        s = dioph({'a': m, 'b': mod, 'c': r - n}) if True else None
        if s is None:
            return None
        n = n + m * s['x']
        m = m * mod // egcd({'a': m, 'b': mod})['g']
        n %= m
    return n


def frac(v):
    return [v.numerator, v.denominator]


def collide(p):
    m1, m2, u1, u2 = (Fraction(p[k]) for k in ('m1', 'm2', 'u1', 'u2'))
    v1 = ((m1 - m2) * u1 + 2 * m2 * u2) / (m1 + m2)
    v2 = ((m2 - m1) * u2 + 2 * m1 * u1) / (m1 + m2)
    return {'v1': frac(v1), 'v2': frac(v2)}


def energy_after(p):
    r = collide(p)
    v1, v2 = Fraction(*r['v1']), Fraction(*r['v2'])
    return frac(Fraction(p['m1']) * v1 * v1 / 2 + Fraction(p['m2']) * v2 * v2 / 2)


def momentum_transfer(p):
    r = collide(p)
    return frac(Fraction(p['m2']) * (Fraction(*r['v2']) - Fraction(p['u2'])))


def bfs(p):
    rows, cols, blocked = p['rows'], p['cols'], {tuple(b) for b in p['blocked']}
    s, g = tuple(p['start']), tuple(p['goal'])
    ok = lambda c: 0 <= c[0] < rows and 0 <= c[1] < cols and c not in blocked
    if not ok(s) or not ok(g):
        return None
    prev, q = {s: None}, deque([s])
    while q:
        c = q.popleft()
        if c == g:
            break
        for d in ((-1, 0), (0, 1), (1, 0), (0, -1)):
            n = (c[0] + d[0], c[1] + d[1])
            if ok(n) and n not in prev:
                prev[n] = c
                q.append(n)
    if g not in prev:
        return None
    path, c = [], g
    while c is not None:
        path.append(list(c))
        c = prev[c]
    return path[::-1]


def via(p):
    a = bfs(dict(p, goal=p['waypoint']))
    b = bfs(dict(p, start=p['waypoint']))
    return None if a is None or b is None else a + b[1:]


def length(p):
    path = bfs(p)
    return None if path is None else len(path) - 1


CHAINS = {
    'numbers': [
        ('egcd', egcd, 'Write run(p). p has positive integers a,b. Return {g,x,y} where g=gcd(a,b) and a*x+b*y=g, '
         'with x,y the coefficients produced by the standard iterative extended Euclidean algorithm '
         '(start x0=1,y0=0,x1=0,y1=1; at each step q=a//b).',
         [{'a': 240, 'b': 46}, {'a': 17, 'b': 5}, {'a': 9, 'b': 6}],
         [{'a': 99, 'b': 78}, {'a': 1071, 'b': 462}, {'a': 13, 'b': 1}, {'a': 1, 'b': 7}, {'a': 120, 'b': 23}]),
        ('dioph', dioph, 'Write run(p). For positive integers a,b and integer c, solve a*x+b*y=c. Return {x,y} with x '
         'the least nonnegative integer that admits an integer y, or null if no integer solution exists.',
         [{'a': 6, 'b': 9, 'c': 3}, {'a': 6, 'b': 9, 'c': 4}, {'a': 43, 'b': 97, 'c': 1}],
         [{'a': 35, 'b': 64, 'c': 13}, {'a': 12, 'b': 18, 'c': 30}, {'a': 7, 'b': 3, 'c': -5},
          {'a': 10, 'b': 4, 'c': 7}, {'a': 1, 'b': 1, 'c': 0}]),
        ('kuttaka', kuttaka, 'Write run(p) for the remainder problem (Aryabhata\'s kuttaka). p has equal-length lists '
         'remainders and moduli (moduli positive, not necessarily coprime). Return the least nonnegative integer N '
         'with N mod moduli[i] == remainders[i] mod moduli[i] for every i, or null if none exists.',
         [{'remainders': [2, 3], 'moduli': [3, 5]}, {'remainders': [1, 2], 'moduli': [4, 6]},
          {'remainders': [3, 5], 'moduli': [4, 6]}],
         [{'remainders': [2, 3, 2], 'moduli': [3, 5, 7]}, {'remainders': [0, 0], 'moduli': [6, 4]},
          {'remainders': [1, 1, 1], 'moduli': [2, 3, 4]}, {'remainders': [5, 7], 'moduli': [12, 18]},
          {'remainders': [10, 4, 12], 'moduli': [11, 12, 13]}]),
    ],
    'physics': [
        ('collide', collide, 'Write run(p) for a one-dimensional perfectly elastic collision. p gives positive integer '
         'masses m1,m2 and integer velocities u1,u2. Return {v1:[num,den],v2:[num,den]} as exact reduced fractions '
         'with positive denominators (the nontrivial branch of momentum and energy conservation).',
         [{'m1': 1, 'm2': 1, 'u1': -1, 'u2': 1}, {'m1': 1, 'm2': 3, 'u1': 4, 'u2': 0}, {'m1': 2, 'm2': 1, 'u1': 3, 'u2': -3}],
         [{'m1': 5, 'm2': 3, 'u1': 2, 'u2': -7}, {'m1': 1, 'm2': 9, 'u1': 10, 'u2': 0}, {'m1': 4, 'm2': 4, 'u1': 0, 'u2': -2},
          {'m1': 7, 'm2': 2, 'u1': -1, 'u2': 3}, {'m1': 3, 'm2': 11, 'u1': 6, 'u2': 1}]),
        ('energy', energy_after, 'Write run(p). Same inputs as a 1-D perfectly elastic collision (masses m1,m2, '
         'velocities u1,u2). Return the total kinetic energy after the collision, computed from the final '
         'velocities, as an exact reduced fraction [num,den] with positive denominator.',
         [{'m1': 1, 'm2': 1, 'u1': -1, 'u2': 1}, {'m1': 1, 'm2': 3, 'u1': 4, 'u2': 0}, {'m1': 2, 'm2': 3, 'u1': 1, 'u2': 0}],
         [{'m1': 5, 'm2': 3, 'u1': 2, 'u2': -7}, {'m1': 1, 'm2': 9, 'u1': 10, 'u2': 0}, {'m1': 2, 'm2': 5, 'u1': 3, 'u2': 1},
          {'m1': 7, 'm2': 2, 'u1': -1, 'u2': 3}, {'m1': 3, 'm2': 11, 'u1': 6, 'u2': 1}]),
        ('impulse', momentum_transfer, 'Write run(p). Same inputs as a 1-D perfectly elastic collision. Return the '
         'momentum gained by body 2, m2*(v2-u2), as an exact reduced fraction [num,den] with positive denominator.',
         [{'m1': 1, 'm2': 1, 'u1': -1, 'u2': 1}, {'m1': 1, 'm2': 3, 'u1': 4, 'u2': 0}, {'m1': 2, 'm2': 3, 'u1': 1, 'u2': 0}],
         [{'m1': 5, 'm2': 3, 'u1': 2, 'u2': -7}, {'m1': 1, 'm2': 9, 'u1': 10, 'u2': 0}, {'m1': 2, 'm2': 5, 'u1': 3, 'u2': 1},
          {'m1': 7, 'm2': 2, 'u1': -1, 'u2': 3}, {'m1': 3, 'm2': 11, 'u1': 6, 'u2': 1}]),
    ],
    'grid': [
        ('path', bfs, 'Write run(p) to plan a shortest path on a grid (simulation). p has rows,cols,start,goal,blocked; '
         'positions are [row,col] from zero. Moves: up,right,down,left, explored breadth-first in that order, first '
         'discovery wins. Return the path including both ends, or null if impossible or an endpoint is blocked/outside.',
         [{'rows': 2, 'cols': 2, 'start': [0, 0], 'goal': [1, 1], 'blocked': []},
          {'rows': 2, 'cols': 2, 'start': [0, 0], 'goal': [1, 1], 'blocked': [[0, 1], [1, 0]]},
          {'rows': 1, 'cols': 1, 'start': [0, 0], 'goal': [0, 0], 'blocked': []}],
         [{'rows': 3, 'cols': 3, 'start': [0, 0], 'goal': [2, 2], 'blocked': [[0, 1], [1, 1]]},
          {'rows': 4, 'cols': 5, 'start': [3, 0], 'goal': [0, 4], 'blocked': [[1, 1], [2, 1], [2, 2], [1, 3]]},
          {'rows': 3, 'cols': 3, 'start': [0, 0], 'goal': [2, 2], 'blocked': [[2, 2]]},
          {'rows': 5, 'cols': 5, 'start': [2, 2], 'goal': [0, 0], 'blocked': [[1, 1], [1, 2], [2, 1]]},
          {'rows': 2, 'cols': 6, 'start': [0, 0], 'goal': [0, 5], 'blocked': [[0, 2], [1, 4]]}]),
        ('via', via, 'Write run(p). Grid as in breadth-first path planning (rows,cols,start,goal,blocked; moves up,'
         'right,down,left in that order, first discovery wins), plus waypoint. Return the path from start to '
         'waypoint followed by the path from waypoint to goal (waypoint listed once), each leg being that '
         'breadth-first shortest path; null if either leg is impossible.',
         [{'rows': 2, 'cols': 2, 'start': [0, 0], 'goal': [0, 1], 'waypoint': [1, 1], 'blocked': []},
          {'rows': 2, 'cols': 2, 'start': [0, 0], 'goal': [1, 1], 'waypoint': [0, 1], 'blocked': [[0, 1]]},
          {'rows': 1, 'cols': 3, 'start': [0, 0], 'goal': [0, 0], 'waypoint': [0, 2], 'blocked': []}],
         [{'rows': 3, 'cols': 3, 'start': [0, 0], 'goal': [2, 2], 'waypoint': [2, 0], 'blocked': [[1, 1]]},
          {'rows': 4, 'cols': 4, 'start': [0, 0], 'goal': [3, 3], 'waypoint': [0, 3], 'blocked': [[1, 1], [2, 2]]},
          {'rows': 3, 'cols': 4, 'start': [2, 0], 'goal': [0, 0], 'waypoint': [2, 3], 'blocked': [[1, 0], [1, 1], [1, 2]]},
          {'rows': 3, 'cols': 3, 'start': [0, 0], 'goal': [2, 2], 'waypoint': [1, 1], 'blocked': [[0, 1], [1, 0]]},
          {'rows': 2, 'cols': 5, 'start': [1, 4], 'goal': [1, 0], 'waypoint': [0, 2], 'blocked': [[1, 2]]}]),
        ('steps', length, 'Write run(p). Grid as in breadth-first path planning (rows,cols,start,goal,blocked). '
         'Return the number of moves on a shortest path, or null if none exists or an endpoint is blocked/outside.',
         [{'rows': 2, 'cols': 2, 'start': [0, 0], 'goal': [1, 1], 'blocked': []},
          {'rows': 2, 'cols': 2, 'start': [0, 0], 'goal': [1, 1], 'blocked': [[0, 1], [1, 0]]},
          {'rows': 1, 'cols': 1, 'start': [0, 0], 'goal': [0, 0], 'blocked': []}],
         [{'rows': 3, 'cols': 3, 'start': [0, 0], 'goal': [2, 2], 'blocked': [[0, 1], [1, 1]]},
          {'rows': 4, 'cols': 5, 'start': [3, 0], 'goal': [0, 4], 'blocked': [[1, 1], [2, 1], [2, 2], [1, 3]]},
          {'rows': 3, 'cols': 3, 'start': [0, 0], 'goal': [2, 2], 'blocked': [[2, 2]]},
          {'rows': 5, 'cols': 5, 'start': [2, 2], 'goal': [0, 0], 'blocked': [[1, 1], [1, 2], [2, 1]]},
          {'rows': 6, 'cols': 6, 'start': [0, 0], 'goal': [5, 5], 'blocked': [[1, 0], [1, 1], [1, 2], [1, 3], [1, 4], [3, 1], [3, 2], [3, 3], [3, 4], [3, 5]]}]),
    ],
}


def main(out):
    out = Path(out)
    stream, holdout = [], {}
    for chain, tasks in CHAINS.items():
        for name, fn, request, visible, hidden in tasks:
            tid = f'{chain}-{name}'
            stream.append({'id': tid, 'chain': chain, 'request': request,
                           'checks': [{'input': x, 'expected': fn(x)} for x in visible]})
            holdout[tid] = [{'input': x, 'expected': fn(x)} for x in hidden]
    (out / 'dev_stream.json').write_text(json.dumps(stream, indent=1) + '\n')
    (out / 'dev_holdout.json').write_text(json.dumps(holdout, indent=1) + '\n')
    print(len(stream), 'tasks')


if __name__ == '__main__':
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
