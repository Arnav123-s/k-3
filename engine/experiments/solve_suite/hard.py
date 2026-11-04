"""SOLVE-SUITE-3 "hard": 12 tasks above the graded suite's level 6 (math, code, robot simulation).

Builder-authored (ENGINEERED evaluation material): development evidence, not a sealed comparison.
Written before any run on it. The solver sees only each prompt; hidden cases come from the reference
functions here and are used only to score the delivered method. Several hidden inputs are large, so a
correct but slow method fails the 20 s sandbox limit, as it would in use.

    python3 experiments/solve_suite/hard.py   # writes hard_tasks.json and hard_hidden.json
"""
import heapq
import json
import math
import random
import re
from collections import deque
from functools import lru_cache
from pathlib import Path


# ---------------------------------------------------------------- math
def _is_prime(n):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d, s = d // 2, s + 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def digit_prime_sum(p):
    digits = list(map(int, str(p['n'])))

    @lru_cache(None)
    def go(i, total, tight):
        if i == len(digits):
            return 1 if _is_prime(total) else 0
        top = digits[i] if tight else 9
        return sum(go(i + 1, total + d, tight and d == top) for d in range(top + 1))
    return go(0, 0, True)


def squarefree_count(p):
    n = p['n']
    r = math.isqrt(n)
    mu = [1] * (r + 1)
    is_comp = [False] * (r + 1)
    for i in range(2, r + 1):
        if not is_comp[i]:
            for j in range(i, r + 1, i):
                is_comp[j] = j != i or is_comp[j]
                mu[j] = -mu[j]
            for j in range(i * i, r + 1, i * i):
                mu[j] = 0
    return sum(mu[d] * (n // (d * d)) for d in range(1, r + 1))


def primitive_root(p):
    q = p['p']
    if q == 2:
        return 1
    m, factors, f = q - 1, set(), 2
    while f * f <= m:
        while m % f == 0:
            factors.add(f)
            m //= f
        f += 1
    if m > 1:
        factors.add(m)
    g = 2
    while any(pow(g, (q - 1) // f, q) == 1 for f in factors):
        g += 1
    return g


def sqrt_mod(p):
    a, q = p['a'] % p['p'], p['p']
    if a == 0:
        return 0
    if q == 2:
        return a
    if pow(a, (q - 1) // 2, q) != 1:
        return None
    s, e = q - 1, 0
    while s % 2 == 0:
        s, e = s // 2, e + 1
    z = 2
    while pow(z, (q - 1) // 2, q) != q - 1:
        z += 1
    x, b, g, r = pow(a, (s + 1) // 2, q), pow(a, s, q), pow(z, s, q), e
    while b != 1:
        t, m = b, 0
        while t != 1:
            t, m = t * t % q, m + 1
        gs = pow(g, 1 << (r - m - 1), q)
        x, g = x * gs % q, gs * gs % q
        b, r = b * g % q, m
    return min(x, q - x)


# ---------------------------------------------------------------- code
def regex_match(p):
    return re.fullmatch(p['pattern'], p['text']) is not None


def distinct_substrings(p):
    s = p['s']
    link, length, trans = [-1], [0], [{}]
    last = 0
    for ch in s:
        cur = len(length)
        length.append(length[last] + 1); link.append(-1); trans.append({})
        x = last
        while x != -1 and ch not in trans[x]:
            trans[x][ch] = cur
            x = link[x]
        if x == -1:
            link[cur] = 0
        else:
            y = trans[x][ch]
            if length[x] + 1 == length[y]:
                link[cur] = y
            else:
                clone = len(length)
                length.append(length[x] + 1); link.append(link[y]); trans.append(dict(trans[y]))
                while x != -1 and trans[x].get(ch) == y:
                    trans[x][ch] = clone
                    x = link[x]
                link[y] = link[cur] = clone
        last = cur
    return sum(length[v] - length[link[v]] for v in range(1, len(length)))


def max_flow(p):
    n, s, t = p['n'], p['source'], p['sink']
    graph = [[] for _ in range(n)]
    to, cap = [], []
    for u, v, c in p['edges']:
        graph[u].append(len(to)); to.append(v); cap.append(c)
        graph[v].append(len(to)); to.append(u); cap.append(0)
    flow = 0
    while True:
        level = [-1] * n
        level[s] = 0
        queue = deque([s])
        while queue:
            u = queue.popleft()
            for e in graph[u]:
                if cap[e] > 0 and level[to[e]] < 0:
                    level[to[e]] = level[u] + 1
                    queue.append(to[e])
        if level[t] < 0:
            return flow
        it = [0] * n

        def push(u, f):
            if u == t:
                return f
            while it[u] < len(graph[u]):
                e = graph[u][it[u]]
                v = to[e]
                if cap[e] > 0 and level[v] == level[u] + 1:
                    got = push(v, min(f, cap[e]))
                    if got:
                        cap[e] -= got
                        cap[e ^ 1] += got
                        return got
                it[u] += 1
            return 0
        while True:
            f = push(s, float('inf'))
            if not f:
                break
            flow += f


def eight_puzzle(p):
    start, goal = tuple(sum(p['board'], [])), (1, 2, 3, 4, 5, 6, 7, 8, 0)
    seen, queue = {start: 0}, deque([start])
    while queue:
        state = queue.popleft()
        if state == goal:
            return seen[state]
        z = state.index(0)
        r, c = divmod(z, 3)
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if 0 <= r + dr < 3 and 0 <= c + dc < 3:
                w = (r + dr) * 3 + c + dc
                nxt = list(state)
                nxt[z], nxt[w] = nxt[w], nxt[z]
                nxt = tuple(nxt)
                if nxt not in seen:
                    seen[nxt] = seen[state] + 1
                    queue.append(nxt)
    return -1


# ---------------------------------------------------------------- robot / physics
def drag_range(p):
    v0, k, dt, g = p['speed'], p['k'], p['dt'], 9.81
    a = math.radians(p['angle_deg'])
    state = (0.0, 0.0, v0 * math.cos(a), v0 * math.sin(a))

    def f(s):
        _, _, vx, vy = s
        sp = math.hypot(vx, vy)
        return (vx, vy, -k * sp * vx, -g - k * sp * vy)
    while True:
        k1 = f(state)
        k2 = f(tuple(x + dt / 2 * d for x, d in zip(state, k1)))
        k3 = f(tuple(x + dt / 2 * d for x, d in zip(state, k2)))
        k4 = f(tuple(x + dt * d for x, d in zip(state, k3)))
        nxt = tuple(x + dt / 6 * (a1 + 2 * a2 + 2 * a3 + a4) for x, a1, a2, a3, a4 in zip(state, k1, k2, k3, k4))
        if nxt[1] < 0:
            x0, y0, x1, y1 = state[0], state[1], nxt[0], nxt[1]
            return round(x0 + (x1 - x0) * y0 / (y0 - y1), 3)
        state = nxt


def turning_robot(p):
    grid, (sr, sc), (gr, gc) = p['grid'], p['start'], p['goal']
    dirs = [(0, 1), (1, 0), (0, -1), (-1, 0)]  # east, south, west, north
    rows, cols = len(grid), len(grid[0])
    dist = {(sr, sc, 0): 0}
    heap = [(0, sr, sc, 0)]
    while heap:
        d, r, c, h = heapq.heappop(heap)
        if (r, c) == (gr, gc):
            return d
        if d > dist[(r, c, h)]:
            continue
        moves = [(r, c, (h + 1) % 4, p['turn_cost']), (r, c, (h + 3) % 4, p['turn_cost'])]
        nr, nc = r + dirs[h][0], c + dirs[h][1]
        if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == '.':
            moves.append((nr, nc, h, 1))
        for nr, nc, nh, w in moves:
            if d + w < dist.get((nr, nc, nh), float('inf')):
                dist[(nr, nc, nh)] = d + w
                heapq.heappush(heap, (d + w, nr, nc, nh))
    return -1


def two_link_ik(p):
    l1, l2, x, y = p['l1'], p['l2'], p['x'], p['y']
    d2 = x * x + y * y
    c2 = (d2 - l1 * l1 - l2 * l2) / (2 * l1 * l2)
    if c2 > 1 + 1e-12 or c2 < -1 - 1e-12:
        return None
    t2 = math.acos(max(-1.0, min(1.0, c2)))
    t1 = math.atan2(y, x) - math.atan2(l2 * math.sin(t2), l1 + l2 * math.cos(t2))
    t1 = math.atan2(math.sin(t1), math.cos(t1))
    if t1 <= -math.pi:
        t1 += 2 * math.pi
    return [round(t1, 4), round(t2, 4)]


def elastic_line(p):
    xs, vs, ms, length, t_end = list(map(float, p['x'])), list(map(float, p['v'])), p['m'], p['length'], p['t']
    n, t = len(xs), 0.0
    while True:
        best, what = t_end - t, None
        for i in range(n):
            if i == 0 and vs[0] < 0:
                dt = -xs[0] / vs[0]
                if dt < best:
                    best, what = dt, ('wall', 0)
            if i == n - 1 and vs[i] > 0:
                dt = (length - xs[i]) / vs[i]
                if dt < best:
                    best, what = dt, ('wall', i)
            if i + 1 < n and vs[i] > vs[i + 1]:
                dt = (xs[i + 1] - xs[i]) / (vs[i] - vs[i + 1])
                if dt < best:
                    best, what = dt, ('pair', i)
        xs = [x + v * best for x, v in zip(xs, vs)]
        t += best
        if what is None:
            return [round(x, 4) for x in xs]
        kind, i = what
        if kind == 'wall':
            vs[i] = -vs[i]
        else:
            m1, m2, u1, u2 = ms[i], ms[i + 1], vs[i], vs[i + 1]
            vs[i] = ((m1 - m2) * u1 + 2 * m2 * u2) / (m1 + m2)
            vs[i + 1] = ((m2 - m1) * u2 + 2 * m1 * u1) / (m1 + m2)


# ---------------------------------------------------------------- inputs
rng = random.Random(20261030)


def _prime_near(n):
    while not _is_prime(n):
        n += 1
    return n


def _puzzle(moves):
    board = [1, 2, 3, 4, 5, 6, 7, 8, 0]
    for _ in range(moves):
        z = board.index(0)
        r, c = divmod(z, 3)
        options = [(r + dr) * 3 + c + dc for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)) if 0 <= r + dr < 3 and 0 <= c + dc < 3]
        w = rng.choice(options)
        board[z], board[w] = board[w], board[z]
    return [board[0:3], board[3:6], board[6:9]]


def _flow_graph(n, m):
    edges = [[i, i + 1, rng.randint(1, 50)] for i in range(n - 1)]
    edges += [[rng.randrange(n), rng.randrange(n), rng.randint(1, 100)] for _ in range(m)]
    return {'n': n, 'source': 0, 'sink': n - 1, 'edges': [e for e in edges if e[0] != e[1]]}


def _maze(rows, cols, walls):
    g = [['.'] * cols for _ in range(rows)]
    for _ in range(walls):
        g[rng.randrange(rows)][rng.randrange(cols)] = '#'
    g[0][0] = g[rows - 1][cols - 1] = '.'
    return [''.join(r) for r in g]


SPIRAL = ['..........', '.########.', '.#......#.', '.#.####.#.', '.#.#..#.#.', '.#.#.##.#.', '.#.#....#.', '.#.######.', '.#........', '.#########']
PRIMES_1MOD8 = [_prime_near(2 ** 40 * 3 + 1), 998244353, _prime_near(10 ** 18)]
TASKS = [
    ('h-m1-digits', 'math', 7, digit_prime_sum, 'run(input) receives {"n": int, 1 <= n <= 10^18} and returns how many integers k with 1 <= k <= n have a digit sum that is a prime number.',
     [{'n': 1}, {'n': 100}, {'n': 12345}, {'n': 10 ** 9}, {'n': 987654321987654321}, {'n': 10 ** 18}]),
    ('h-c1-regex', 'code', 7, regex_match, 'run(input) receives {"pattern": str, "text": str}. The pattern uses only lowercase letters, "." (any one character), the postfix operators "*", "+" and "?", alternation "|" and parentheses for grouping, with the usual meanings and precedence. Return true if the whole text matches the pattern, otherwise false. Do not use the re module.',
     [{'pattern': 'a(b|c)*d', 'text': 'abcbcd'}, {'pattern': 'a(b|c)*d', 'text': 'abcbca'}, {'pattern': '(ab|a)(bc|c)?', 'text': 'abc'}, {'pattern': 'x?y+z*', 'text': 'yyy'},
      {'pattern': '((a|b)*c)+', 'text': 'abcbacc'}, {'pattern': '.*a.', 'text': 'bbbab'}, {'pattern': '(a|)b', 'text': 'b'}, {'pattern': 'a*a*a*a*a*b', 'text': 'a' * 25},
      {'pattern': '(h(e|a)llo|hi)+ world?', 'text': 'hellohallohi worl'}]),
    ('h-r1-drag', 'robot', 7, drag_range, 'A projectile with quadratic air drag: acceleration = (-k*|v|*vx, -9.81 - k*|v|*vy). run(input) receives {"speed", "angle_deg", "k", "dt"}; start at (0, 0). Integrate with the classical 4th-order Runge-Kutta method with fixed step dt until the first step whose new y is below 0, then interpolate linearly in y between the last two states to find the landing x. Return that x rounded to 3 decimals.',
     [{'speed': 20, 'angle_deg': 45, 'k': 0.0, 'dt': 0.01}, {'speed': 50, 'angle_deg': 30, 'k': 0.01, 'dt': 0.001}, {'speed': 100, 'angle_deg': 60, 'k': 0.005, 'dt': 0.002},
      {'speed': 5, 'angle_deg': 80, 'k': 0.2, 'dt': 0.0005}, {'speed': 300, 'angle_deg': 10, 'k': 0.001, 'dt': 0.001}]),
    ('h-m2-squarefree', 'math', 8, squarefree_count, 'run(input) receives {"n": int, 1 <= n <= 10^12} and returns how many integers k with 1 <= k <= n are squarefree (not divisible by any square greater than 1).',
     [{'n': 1}, {'n': 10}, {'n': 1000}, {'n': 10 ** 6}, {'n': 123456789012}, {'n': 10 ** 12}]),
    ('h-m3-primroot', 'math', 8, primitive_root, 'run(input) receives {"p": a prime, 2 <= p <= 10^12} and returns the smallest positive primitive root modulo p.',
     [{'p': 2}, {'p': 7}, {'p': 41}, {'p': 998244353}, {'p': _prime_near(10 ** 12 - 1000)}, {'p': 999999999989}, {'p': _prime_near(2 ** 39)}]),
    ('h-c2-substrings', 'code', 8, distinct_substrings, 'run(input) receives {"s": str} with up to 100000 lowercase letters and returns the number of distinct non-empty substrings of s.',
     [{'s': 'abab'}, {'s': 'aaaa'}, {'s': ''}, {'s': 'banana'}, {'s': ''.join(rng.choice('ab') for _ in range(2000))}, {'s': ''.join(rng.choice('abcdefgh') for _ in range(100000))}, {'s': 'ab' * 40000}]),
    ('h-r2-turning', 'robot', 8, turning_robot, 'run(input) receives {"grid": [str, ...], "start": [row, col], "goal": [row, col], "turn_cost": int >= 1} with "." free and "#" walls. The robot starts facing east (increasing column). It can move one cell forward in the direction it faces (cost 1) or turn 90 degrees left or right in place (cost turn_cost). Return the minimum total cost to reach the goal cell in any heading, or -1 if it cannot.',
     [{'grid': SPIRAL, 'start': [0, 0], 'goal': [4, 4], 'turn_cost': 1}, {'grid': SPIRAL, 'start': [0, 0], 'goal': [4, 4], 'turn_cost': 5}, {'grid': ['..', '..'], 'start': [0, 0], 'goal': [0, 0], 'turn_cost': 3},
      {'grid': ['.#', '#.'], 'start': [0, 0], 'goal': [1, 1], 'turn_cost': 1}, {'grid': _maze(60, 60, 900), 'start': [0, 0], 'goal': [59, 59], 'turn_cost': 2}, {'grid': SPIRAL, 'start': [9, 0], 'goal': [8, 9], 'turn_cost': 2}]),
    ('h-m4-sqrtmod', 'math', 9, sqrt_mod, 'run(input) receives {"a": int, "p": prime up to about 10^18} and returns the smallest x with 0 <= x < p and x*x = a (mod p), or null if there is none.',
     [{'a': 2, 'p': 7}, {'a': 3, 'p': 7}, {'a': 0, 'p': 13}, {'a': 10, 'p': 13}] + [{'a': rng.randrange(1, q), 'p': q} for q in PRIMES_1MOD8 for _ in range(2)] + [{'a': 5, 'p': 2}]),
    ('h-c3-maxflow', 'code', 9, max_flow, 'run(input) receives {"n": nodes 0..n-1, "source": int, "sink": int, "edges": [[u, v, capacity], ...]} for a directed graph (parallel edges allowed) and returns the maximum flow value from source to sink.',
     [{'n': 2, 'source': 0, 'sink': 1, 'edges': [[0, 1, 5], [0, 1, 3]]}, {'n': 4, 'source': 0, 'sink': 3, 'edges': [[0, 1, 3], [0, 2, 2], [1, 2, 5], [1, 3, 2], [2, 3, 3]]},
      {'n': 3, 'source': 0, 'sink': 2, 'edges': [[1, 2, 4]]}, _flow_graph(200, 3000), _flow_graph(500, 8000)]),
    ('h-c4-puzzle', 'code', 9, eight_puzzle, 'run(input) receives {"board": 3x3 list of lists with the numbers 0..8, 0 being the blank}. A move slides a tile next to the blank into it. Return the fewest moves to reach [[1,2,3],[4,5,6],[7,8,0]], or -1 if impossible.',
     [{'board': [[1, 2, 3], [4, 5, 6], [7, 8, 0]]}, {'board': [[1, 2, 3], [4, 5, 6], [0, 7, 8]]}, {'board': [[2, 1, 3], [4, 5, 6], [7, 8, 0]]}, {'board': _puzzle(30)}, {'board': _puzzle(200)}, {'board': [[8, 6, 7], [2, 5, 4], [3, 0, 1]]}]),
    ('h-r3-arm', 'robot', 9, two_link_ik, 'A planar two-link arm has its base at the origin: the elbow is at l1*(cos t1, sin t1) and the hand at elbow + l2*(cos(t1+t2), sin(t1+t2)). run(input) receives {"l1", "l2", "x", "y"} and returns [t1, t2] in radians that put the hand at (x, y), choosing the solution with 0 <= t2 <= pi and t1 normalised to (-pi, pi], each rounded to 4 decimals; return null if (x, y) is out of reach.',
     [{'l1': 1, 'l2': 1, 'x': 1, 'y': 1}, {'l1': 2, 'l2': 1, 'x': 3, 'y': 0}, {'l1': 1, 'l2': 1, 'x': 3, 'y': 0}, {'l1': 1.5, 'l2': 0.7, 'x': -0.4, 'y': -1.2},
      {'l1': 1, 'l2': 2, 'x': 0.5, 'y': 0.2}, {'l1': 0.8, 'l2': 0.8, 'x': -1.0, 'y': 0.0001}, {'l1': 1, 'l2': 2, 'x': 0.2, 'y': 0.1}]),
    ('h-r4-collide', 'robot', 9, elastic_line, 'Particles move on the segment [0, length] of a line with constant velocities between collisions, in increasing order of position. Collisions between neighbours are perfectly elastic (masses given), and the end particles bounce off the walls at 0 and length by reversing velocity. run(input) receives {"x": [positions], "v": [velocities], "m": [masses], "length": float, "t": float} and returns the positions at time t, in the same order, each rounded to 4 decimals.',
     [{'x': [1.0], 'v': [1.0], 'm': [1.0], 'length': 10.0, 't': 25.0}, {'x': [2.0, 5.0], 'v': [1.0, -1.0], 'm': [1.0, 1.0], 'length': 10.0, 't': 3.0},
      {'x': [1.0, 3.0, 6.0], 'v': [2.0, 0.0, -1.5], 'm': [1.0, 3.0, 0.5], 'length': 8.0, 't': 7.3}, {'x': [0.5, 1.7, 2.9, 4.4, 6.1], 'v': [0.3, -1.1, 2.2, -0.4, 0.9], 'm': [2.0, 1.0, 0.5, 4.0, 1.5], 'length': 7.0, 't': 19.7}]),
]


def build(here=Path(__file__).parent):
    tasks = [{'id': t, 'domain': d, 'level': lv, 'prompt': prompt} for t, d, lv, _, prompt, _ in TASKS]
    hidden = {t: [{'input': i, 'expected': f(i)} for i in inputs] for t, _, _, f, _, inputs in TASKS}
    (here / 'hard_tasks.json').write_text(json.dumps(tasks, indent=1) + '\n')
    (here / 'hard_hidden.json').write_text(json.dumps(hidden) + '\n')
    return tasks, hidden


if __name__ == '__main__':
    import time
    for t, _, lv, f, _, inputs in TASKS:
        started = time.time()
        values = [f(i) for i in inputs]
        print(lv, t, f'{time.time() - started:.1f}s', [v if not isinstance(v, (str,)) else v for v in values][:6])
    build()
