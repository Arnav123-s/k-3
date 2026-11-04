"""Graded development suite for `kuttaka solve`: math, code and robot simulation, easy to hard.

Builder-authored (ENGINEERED evaluation material), so results on it are development evidence, not a
sealed comparison. The solver sees only each task's prompt. The hidden cases below are computed by
the reference functions here and are used only to score the delivered method afterwards.

    python3 experiments/solve_suite/tasks.py   # writes tasks.json (prompts) and hidden.json (cases)
"""

import heapq
import json
import math
from collections import deque
from itertools import permutations
from pathlib import Path


# ---------------------------------------------------------------- math / number theory
def gcd(p):
    a, b = abs(p['a']), abs(p['b'])
    while b:
        a, b = b, a % b
    return a


def egcd(a, b):
    if b == 0:
        return a, 1, 0
    g, x, y = egcd(b, a % b)
    return g, y, x - (a // b) * y


def modinv(p):
    a, m = p['a'], p['m']
    g, x, _ = egcd(a % m, m)
    return x % m if g == 1 else None


def kuttaka(p):
    # smallest x >= 0 with integer y such that a*x - b*y = c
    a, b, c = p['a'], p['b'], p['c']
    g, x0, y0 = egcd(a, b)          # a*x0 + b*y0 = g
    if c % g:
        return None
    step = b // g
    x = (x0 * (c // g)) % abs(step)
    y = (a * x - c) // b
    return [x, y]


def crt(p):
    x, m = 0, 1
    for r, n in p['congruences']:
        g, s, _ = egcd(m, n)
        if (r - x) % g:
            return None
        lcm = m // g * n
        x = (x + (r - x) // g * s % (n // g) * m) % lcm
        m = lcm
    return x


def pell(p):
    d = p['d']
    a0 = math.isqrt(d)
    m, q, a = 0, 1, a0
    h0, h1, k0, k1 = 1, a0, 0, 1
    while h1 * h1 - d * k1 * k1 != 1:
        m = a * q - m
        q = (d - m * m) // q
        a = (a0 + m) // q
        h0, h1 = h1, a * h1 + h0
        k0, k1 = k1, a * k1 + k0
    return [h1, k1]


def balanced_no_aba(p):
    # binary strings of length n, equal numbers of 0 and 1, never containing 010 or 101
    n = p['n']
    if n % 2:
        return 0
    from functools import lru_cache

    @lru_cache(None)
    def go(i, ones, last2):
        if i == n:
            return 1 if 2 * ones == n else 0
        total = 0
        for bit in '01':
            t = last2 + bit
            if len(t) == 3 and t in ('010', '101'):
                continue
            total += go(i + 1, ones + (bit == '1'), t[-2:])
        return total
    return go(0, 0, '')


# ---------------------------------------------------------------- coding
def reverse_words(p):
    return ' '.join(reversed(p['text'].split()))


def merge_intervals(p):
    out = []
    for s, e in sorted(p['intervals']):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def bracket_error(p):
    pairs, stack = {')': '(', ']': '[', '}': '{'}, []
    for i, ch in enumerate(p['text']):
        if ch in '([{':
            stack.append(i)
        elif ch in pairs:
            if not stack or p['text'][stack[-1]] != pairs[ch]:
                return i
            stack.pop()
    return stack[0] if stack else -1


def evaluate(p):
    s = p['expr'].replace(' ', '')
    pos = 0

    def expr():
        nonlocal pos
        v = term()
        while pos < len(s) and s[pos] in '+-':
            op = s[pos]; pos += 1
            v = v + term() if op == '+' else v - term()
        return v

    def term():
        nonlocal pos
        v = factor()
        while pos < len(s) and s[pos] in '*/':
            op = s[pos]; pos += 1
            r = factor()
            v = v * r if op == '*' else int(v / r) if (v < 0) != (r < 0) else v // r
        return v

    def factor():
        nonlocal pos
        if s[pos] == '-':
            pos += 1
            return -factor()
        if s[pos] == '(':
            pos += 1
            v = expr(); pos += 1
            return v
        start = pos
        while pos < len(s) and s[pos].isdigit():
            pos += 1
        return int(s[start:pos])
    return expr()


def stack_machine(p):
    prog = [line.split() for line in p['program']]
    labels = {ins[1]: i for i, ins in enumerate(prog) if ins[0] == 'label'}
    stack, out, pc, steps = [], [], 0, 0
    while pc < len(prog):
        steps += 1
        if steps > 1000:
            return {'output': out, 'halted': False}
        op, *arg = prog[pc]
        pc += 1
        if op == 'push':
            stack.append(int(arg[0]))
        elif op == 'pop':
            stack.pop()
        elif op == 'dup':
            stack.append(stack[-1])
        elif op == 'swap':
            stack[-1], stack[-2] = stack[-2], stack[-1]
        elif op in ('add', 'sub', 'mul'):
            b, a = stack.pop(), stack.pop()
            stack.append(a + b if op == 'add' else a - b if op == 'sub' else a * b)
        elif op == 'print':
            out.append(stack[-1])
        elif op == 'jz':
            if stack.pop() == 0:
                pc = labels[arg[0]]
        elif op == 'jmp':
            pc = labels[arg[0]]
    return {'output': out, 'halted': True}


def shortest_path(p):
    graph = {}
    for u, v, w in p['edges']:
        graph.setdefault(u, []).append((v, w))
        graph.setdefault(v, []).append((u, w))
    best = {p['start']: (0, [p['start']])}
    heap = [(0, [p['start']])]
    while heap:
        d, path = heapq.heappop(heap)
        u = path[-1]
        if (d, path) != best.get(u):
            continue
        for v, w in graph.get(u, []):
            cand = (d + w, path + [v])
            if v not in best or cand < best[v]:
                best[v] = cand
                heapq.heappush(heap, cand)
    if p['goal'] not in best:
        return None
    d, path = best[p['goal']]
    return {'distance': d, 'path': path}


# ---------------------------------------------------------------- robot simulation
def projectile(p):
    v, a = p['speed'], math.radians(p['angle_deg'])
    return round(v * v * math.sin(2 * a) / 9.81, 3)


def kinematics(p):
    x, v = 0.0, p['v0']
    for acc, dt in p['segments']:
        x += v * dt + 0.5 * acc * dt * dt
        v += acc * dt
    return [round(x, 3), round(v, 3)]


def odometry(p):
    x = y = th = 0.0
    L = p['wheelbase']
    for vl, vr, dt in p['commands']:
        v, w = (vl + vr) / 2, (vr - vl) / L
        if abs(w) < 1e-12:
            x += v * dt * math.cos(th)
            y += v * dt * math.sin(th)
        else:
            r = v / w
            x += r * (math.sin(th + w * dt) - math.sin(th))
            y -= r * (math.cos(th + w * dt) - math.cos(th))
            th += w * dt
    th = math.atan2(math.sin(th), math.cos(th))
    return [round(x, 4), round(y, 4), round(th, 4)]


def _bfs(grid, start):
    rows, cols = len(grid), len(grid[0])
    dist = {tuple(start): 0}
    queue = deque([tuple(start)])
    while queue:
        r, c = queue.popleft()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == '.' and (nr, nc) not in dist:
                dist[(nr, nc)] = dist[(r, c)] + 1
                queue.append((nr, nc))
    return dist


def grid_path(p):
    return _bfs(p['grid'], p['start']).get(tuple(p['goal']), -1)


def visit_all(p):
    points = [tuple(p['start'])] + [tuple(t) for t in p['targets']]
    dists = [_bfs(p['grid'], pt) for pt in points]
    best = None
    for order in permutations(range(1, len(points))):
        total, cur = 0, 0
        for nxt in order:
            d = dists[cur].get(points[nxt])
            if d is None:
                total = None
                break
            total, cur = total + d, nxt
        if total is not None and (best is None or total < best):
            best = total
    return -1 if best is None else best


def pid(p):
    x, v, prev_e, integral = p['x0'], 0.0, None, 0.0
    dt, target = p['dt'], p['target']
    settle = None
    for k in range(p['steps']):
        e = target - x
        integral += e * dt
        de = 0.0 if prev_e is None else (e - prev_e) / dt
        u = max(-p['u_max'], min(p['u_max'], p['kp'] * e + p['ki'] * integral + p['kd'] * de))
        v += u * dt
        x += v * dt
        prev_e = e
        if abs(target - x) <= 0.02 * abs(target - p['x0']):
            if settle is None:
                settle = k + 1
        else:
            settle = None
    return {'x': round(x, 4), 'settle_step': settle}


G = ['.....#....', '.###.#.##.', '.#...#..#.', '.#.###.#..', '...#...#.#',
     '.#.#.#.#..', '.#...#...#', '.####.###.', '......#...', '.####...#.']

TASKS = [
    # level 1
    ('m1-gcd', 'math', 1, gcd, 'run(input) receives {"a": int, "b": int} and returns their greatest common divisor as a non-negative int (gcd(0, 0) is 0).',
     [{'a': 1071, 'b': 462}, {'a': -48, 'b': 18}, {'a': 0, 'b': 7}, {'a': 0, 'b': 0}, {'a': 2**61 - 1, 'b': 2**31 - 1}, {'a': 123456789, 'b': 987654321}]),
    ('c1-words', 'code', 1, reverse_words, 'run(input) receives {"text": str} and returns the words in reverse order joined by single spaces, ignoring extra whitespace.',
     [{'text': 'the sky is blue'}, {'text': '  hello   world  '}, {'text': 'a'}, {'text': ''}, {'text': 'one\ttwo\nthree'}]),
    ('r1-projectile', 'robot', 1, projectile, 'run(input) receives {"speed": m/s, "angle_deg": degrees} for a projectile launched from flat ground with g = 9.81 m/s^2 and no air resistance, and returns its horizontal range in metres rounded to 3 decimals.',
     [{'speed': 10, 'angle_deg': 45}, {'speed': 25.5, 'angle_deg': 30}, {'speed': 3, 'angle_deg': 89}, {'speed': 100, 'angle_deg': 15}, {'speed': 0, 'angle_deg': 60}]),
    # level 2
    ('m2-modinv', 'math', 2, modinv, 'run(input) receives {"a": int, "m": int >= 2} and returns the inverse of a modulo m in the range 0..m-1, or null if it does not exist.',
     [{'a': 3, 'm': 11}, {'a': 10, 'm': 17}, {'a': 6, 'm': 9}, {'a': -3, 'm': 7}, {'a': 123456789, 'm': 1000000007}, {'a': 0, 'm': 5}]),
    ('c2-intervals', 'code', 2, merge_intervals, 'run(input) receives {"intervals": [[start, end], ...]} with integer start <= end, and returns the merged list of overlapping or touching intervals, sorted by start.',
     [{'intervals': [[1, 3], [2, 6], [8, 10], [15, 18]]}, {'intervals': [[1, 4], [4, 5]]}, {'intervals': []}, {'intervals': [[5, 7], [1, 2], [2, 3], [6, 9]]}, {'intervals': [[1, 10], [2, 3], [4, 5]]}]),
    ('r2-kinematics', 'robot', 2, kinematics, 'run(input) receives {"v0": initial velocity, "segments": [[acceleration, duration], ...]} for a cart on a straight line starting at position 0, each segment holding a constant acceleration, and returns [final position, final velocity], each rounded to 3 decimals.',
     [{'v0': 0, 'segments': [[2, 3]]}, {'v0': 5, 'segments': [[0, 2], [-1, 4]]}, {'v0': -1.5, 'segments': [[0.5, 1.2], [3, 0.7], [-2, 2]]}, {'v0': 0, 'segments': []}, {'v0': 10, 'segments': [[-9.81, 1], [-9.81, 1]]}]),
    # level 3
    ('m3-kuttaka', 'math', 3, kuttaka, 'Aryabhata\'s kuttaka: run(input) receives {"a": positive int, "b": positive int, "c": int} and returns [x, y], the integer solution of a*x - b*y = c with the smallest x >= 0, or null if there is none.',
     [{'a': 137, 'b': 60, 'c': 10}, {'a': 6, 'b': 4, 'c': 3}, {'a': 1, 'b': 1, 'c': 0}, {'a': 29, 'b': 45, 'c': 16}, {'a': 1000003, 'b': 999983, 'c': 7}, {'a': 12, 'b': 18, 'c': -6}]),
    ('c3-brackets', 'code', 3, bracket_error, 'run(input) receives {"text": str} and checks (), [] and {} nesting, ignoring other characters. Return -1 if balanced; otherwise the index of the first closing bracket that does not match, or if none, the index of the earliest opening bracket left unclosed.',
     [{'text': '([]{})'}, {'text': '(]'}, {'text': 'a(b[c]d'}, {'text': '}'}, {'text': '(()'}, {'text': 'f(x) = [y{z}]'}, {'text': '([)]'}]),
    ('r3-odometry', 'robot', 3, odometry, 'A differential-drive robot starts at x = 0, y = 0, heading 0 rad. run(input) receives {"wheelbase": metres, "commands": [[v_left, v_right, dt], ...]} with wheel speeds held constant for dt seconds each; integrate the motion exactly (straight line when the speeds are equal, circular arc otherwise) and return [x, y, heading] with heading normalised to (-pi, pi], each rounded to 4 decimals.',
     [{'wheelbase': 0.5, 'commands': [[1, 1, 2]]}, {'wheelbase': 0.5, 'commands': [[1, 1.5, 3.14159]]}, {'wheelbase': 0.3, 'commands': [[-0.2, 0.2, 2], [0.5, 0.5, 1]]}, {'wheelbase': 1, 'commands': [[0.3, 0.7, 1], [0.6, 0.2, 2], [1, 1, 0.5]]}, {'wheelbase': 0.4, 'commands': []}]),
    # level 4
    ('m4-crt', 'math', 4, crt, 'run(input) receives {"congruences": [[r, m], ...]} with moduli m >= 1 that need not be coprime, and returns the smallest non-negative x with x = r (mod m) for all of them, or null if they are inconsistent.',
     [{'congruences': [[2, 3], [3, 5], [2, 7]]}, {'congruences': [[1, 4], [3, 6]]}, {'congruences': [[0, 4], [2, 6]]}, {'congruences': [[5, 1]]}, {'congruences': [[123, 1000], [456, 1001], [789, 1003]]}, {'congruences': [[1, 2], [2, 4]]}]),
    ('c4-evaluate', 'code', 4, evaluate, 'run(input) receives {"expr": str}, an integer arithmetic expression with + - * /, parentheses, unary minus and spaces, and returns its int value with the usual precedence, left-to-right association, and division that truncates toward zero. Do not use eval.',
     [{'expr': '1 + 2 * 3'}, {'expr': '(1+2)*3'}, {'expr': '7 / -2'}, {'expr': '-(3 - 10) * 2'}, {'expr': '100 / 7 / 2'}, {'expr': '2*(3+(4-1)*2)-8/3'}, {'expr': '--5'}]),
    ('r4-grid', 'robot', 4, grid_path, 'run(input) receives {"grid": [str, ...], "start": [row, col], "goal": [row, col]} where "." is free and "#" is a wall; the robot moves one cell up, down, left or right. Return the fewest moves from start to goal, or -1 if unreachable.',
     [{'grid': G, 'start': [0, 0], 'goal': [9, 9]}, {'grid': G, 'start': [0, 0], 'goal': [0, 9]}, {'grid': G, 'start': [2, 2], 'goal': [2, 2]}, {'grid': ['.#.', '##.', '...'], 'start': [0, 0], 'goal': [2, 2]}, {'grid': G, 'start': [8, 0], 'goal': [4, 6]}]),
    # level 5
    ('m5-pell', 'math', 5, pell, 'run(input) receives {"d": positive int that is not a perfect square, at most 1000} and returns [x, y], the smallest positive integer solution of Pell\'s equation x^2 - d*y^2 = 1 (exact big integers).',
     [{'d': 2}, {'d': 7}, {'d': 61}, {'d': 109}, {'d': 661}, {'d': 991}]),
    ('c5-machine', 'code', 5, stack_machine, 'run(input) receives {"program": [str, ...]} for a stack machine with instructions: push N, pop, dup, swap, add, sub, mul (pop b then a, push a op b), print (append the top value to the output without popping), label NAME, jmp NAME, jz NAME (pop; jump if it was 0). Execute from the first line; label is a no-op. Stop when execution runs past the last line, returning {"output": [...], "halted": true}, or after 1000 executed instructions, returning {"output": [...], "halted": false}.',
     [{'program': ['push 2', 'push 3', 'add', 'print']}, {'program': ['push 5', 'label L', 'print', 'push 1', 'sub', 'dup', 'jz E', 'jmp L', 'label E']},
      {'program': ['label L', 'push 1', 'print', 'jmp L']}, {'program': ['push 3', 'push 4', 'swap', 'sub', 'print', 'push 6', 'mul', 'print']},
      {'program': ['push 1', 'push 10', 'label L', 'dup', 'jz E', 'swap', 'push 2', 'mul', 'print', 'swap', 'push 1', 'sub', 'jmp L', 'label E']}]),
    ('r5-visit', 'robot', 5, visit_all, 'run(input) receives {"grid": [str, ...], "start": [row, col], "targets": [[row, col], ...]} (at most 7 targets) with "." free and "#" walls and 4-directional moves. Return the fewest total moves for the robot to visit every target in any order (it need not return), or -1 if some target is unreachable.',
     [{'grid': G, 'start': [0, 0], 'targets': [[9, 9], [0, 9]]}, {'grid': G, 'start': [0, 0], 'targets': [[4, 6], [8, 9], [2, 3], [6, 2]]},
      {'grid': ['.#.', '##.', '...'], 'start': [0, 0], 'targets': [[2, 2]]}, {'grid': G, 'start': [8, 0], 'targets': [[0, 4], [9, 7], [5, 9], [2, 6], [6, 6], [3, 2], [0, 0]]}]),
    # level 6
    ('m6-strings', 'math', 6, balanced_no_aba, 'run(input) receives {"n": int, 0 <= n <= 60} and returns how many binary strings of length n have as many 0s as 1s and contain neither "010" nor "101" as a substring (the empty string counts once).',
     [{'n': 0}, {'n': 2}, {'n': 6}, {'n': 7}, {'n': 20}, {'n': 41}, {'n': 60}]),
    ('c6-dijkstra', 'code', 6, shortest_path, 'run(input) receives {"edges": [[u, v, w], ...], "start": str, "goal": str} for an undirected graph with string nodes and positive integer weights. Return {"distance": d, "path": [nodes from start to goal]} for the shortest route; among equally short routes choose the one whose node list is smallest in lexicographic (list) order. Return null if the goal is unreachable.',
     [{'edges': [['a', 'b', 1], ['b', 'c', 2], ['a', 'c', 4]], 'start': 'a', 'goal': 'c'}, {'edges': [['a', 'b', 1], ['a', 'c', 1], ['b', 'd', 1], ['c', 'd', 1]], 'start': 'a', 'goal': 'd'},
      {'edges': [['x', 'y', 3]], 'start': 'x', 'goal': 'z'}, {'edges': [['s', 'a', 2], ['s', 'b', 2], ['a', 't', 2], ['b', 't', 2], ['a', 'b', 1], ['s', 't', 5]], 'start': 's', 'goal': 't'},
      {'edges': [['n%d' % i, 'n%d' % (i + 1), (i * 7) % 5 + 1] for i in range(20)] + [['n0', 'n10', 20], ['n5', 'n15', 9]], 'start': 'n0', 'goal': 'n20'}]),
    ('r6-pid', 'robot', 6, pid, 'Simulate a PID controller moving a point mass along a line: run(input) receives {"x0", "target", "kp", "ki", "kd", "u_max", "dt", "steps"}. Start with velocity 0, integral 0 and no previous error. Each step: e = target - x; integral += e*dt; derivative = 0 on the first step, otherwise (e - previous e)/dt; u = kp*e + ki*integral + kd*derivative clipped to [-u_max, u_max]; v += u*dt; x += v*dt. Return {"x": final x rounded to 4 decimals, "settle_step": the 1-based step from which |target - x| stayed within 2% of |target - x0| through the last step, or null}.',
     [{'x0': 0, 'target': 1, 'kp': 4, 'ki': 0, 'kd': 3, 'u_max': 10, 'dt': 0.01, 'steps': 1000}, {'x0': 0, 'target': 1, 'kp': 10, 'ki': 0, 'kd': 0, 'u_max': 100, 'dt': 0.01, 'steps': 500},
      {'x0': 2, 'target': -1, 'kp': 3, 'ki': 0.5, 'kd': 2.5, 'u_max': 2, 'dt': 0.02, 'steps': 800}, {'x0': 0, 'target': 5, 'kp': 1, 'ki': 0, 'kd': 2, 'u_max': 1, 'dt': 0.05, 'steps': 400}]),
]


def build(here=Path(__file__).parent):
    tasks = [{'id': t, 'domain': d, 'level': lv, 'prompt': prompt} for t, d, lv, _, prompt, _ in TASKS]
    hidden = {t: [{'input': i, 'expected': f(i)} for i in inputs] for t, _, _, f, _, inputs in TASKS}
    (here / 'tasks.json').write_text(json.dumps(tasks, indent=1) + '\n')
    (here / 'hidden.json').write_text(json.dumps(hidden, indent=1) + '\n')
    return tasks, hidden


if __name__ == '__main__':
    tasks, hidden = build()
    for t in tasks:
        print(t['level'], t['id'], [c['expected'] for c in hidden[t['id']]][:3])
