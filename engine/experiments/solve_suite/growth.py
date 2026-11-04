"""SOLVE-SUITE-2 "growth": does the same model get cheaper and make fewer mistakes as Kuttaka's memory grows?

Three families of dependent tasks; later tasks build on earlier ones, and every family ends with a
restated variant of an earlier task (different wording and I/O shape, same core). Run it with the
same runner as SOLVE-SUITE-1:

    python3 experiments/solve_suite/growth.py          # writes growth_tasks.json / growth_hidden.json
    python3 experiments/solve_suite/run.py --arm kuttaka --suite growth --out results/solve-suite/g-kuttaka
    python3 experiments/solve_suite/run.py --arm fresh   --suite growth --out results/solve-suite/g-fresh

The claim tested is the owner's: the first time may cost a lot; the next times should cost less.
Measured per task: model calls, tokens, hidden-case result, reuse by artifact id. Builder-authored
development material, not a sealed comparison.
"""

import json
from collections import deque
from math import gcd
from pathlib import Path

from tasks import G, _bfs, crt, egcd, evaluate


# ---------------- number theory family
def lcm_list(p):
    out = 1
    for v in p['values']:
        out = out * v // gcd(out, v)
    return out


def solve_congruence(p):
    # all x in [0, m) with a*x = b (mod m), ascending
    a, b, m = p['a'] % p['m'], p['b'] % p['m'], p['m']
    g = gcd(a, m)
    if b % g:
        return []
    _, inv, _ = egcd(a // g, m // g)
    x0 = (inv * (b // g)) % (m // g)
    return [x0 + k * (m // g) for k in range(g)]


def crt_count(p):
    x = crt({'congruences': p['congruences']})
    if x is None:
        return 0
    period = 1
    for _, n in p['congruences']:
        period = period * n // gcd(period, n)
    lo, hi = p['low'], p['high']
    first = x + ((lo - x + period - 1) // period) * period
    return 0 if first > hi else (hi - first) // period + 1


def remainder_puzzle(p):
    # "eggs in a basket": smallest n > 0 leaving remainder r_i when grouped in g_i
    x = crt({'congruences': [[r, g] for g, r in p['groups']]})
    if x is None:
        return None
    if x == 0:
        period = 1
        for g, _ in p['groups']:
            period = period * g // gcd(period, g)
        return period
    return x


# ---------------- grid robot family
def path_cells(p):
    grid, start, goal = p['grid'], tuple(p['start']), tuple(p['goal'])
    prev, queue = {start: None}, deque([start])
    while queue:
        cell = queue.popleft()
        if cell == goal:
            break
        r, c = cell
        for dr, dc in ((-1, 0), (0, 1), (1, 0), (0, -1)):   # up, right, down, left: first found wins
            n = (r + dr, c + dc)
            if 0 <= n[0] < len(grid) and 0 <= n[1] < len(grid[0]) and grid[n[0]][n[1]] == '.' and n not in prev:
                prev[n] = cell
                queue.append(n)
    if goal not in prev:
        return None
    path, cell = [], goal
    while cell is not None:
        path.append(list(cell))
        cell = prev[cell]
    return path[::-1]


def moves_string(p):
    path = path_cells(p)
    if path is None:
        return None
    names = {(-1, 0): 'U', (1, 0): 'D', (0, -1): 'L', (0, 1): 'R'}
    return ''.join(names[(b[0] - a[0], b[1] - a[1])] for a, b in zip(path, path[1:]))


def battery_reach(p):
    # fewest moves from start to goal when the battery holds at most `battery` moves and
    # recharges to full on any cell marked 'C' (start counts as full); -1 if impossible
    grid, cap = p['grid'], p['battery']
    start, goal = tuple(p['start']), tuple(p['goal'])
    best = {(start, cap): 0}
    queue = deque([(start, cap)])
    while queue:
        (r, c), charge = queue.popleft()
        d = best[((r, c), charge)]
        if (r, c) == goal:
            return d
        if charge == 0:
            continue
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < len(grid) and 0 <= nc < len(grid[0]) and grid[nr][nc] in '.C':
                nch = cap if grid[nr][nc] == 'C' else charge - 1
                if ((nr, nc), nch) not in best:
                    best[((nr, nc), nch)] = d + 1
                    queue.append(((nr, nc), nch))
    return -1


def grid_distance_variant(p):
    # restated variant of r4-grid: walls given as a set of blocked cells instead of strings
    rows, cols = p['rows'], p['cols']
    blocked = {tuple(b) for b in p['blocked']}
    grid = [''.join('#' if (r, c) in blocked else '.' for c in range(cols)) for r in range(rows)]
    return _bfs(grid, p['from']).get(tuple(p['to']), -1)


# ---------------- language family
def tokenize(p):
    out, s, i = [], p['expr'], 0
    while i < len(s):
        ch = s[i]
        if ch.isspace():
            i += 1
        elif ch.isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            out.append(['num', int(s[i:j])])
            i = j
        elif ch.isalpha():
            j = i
            while j < len(s) and (s[j].isalnum() or s[j] == '_'):
                j += 1
            out.append(['name', s[i:j]])
            i = j
        else:
            out.append(['op', ch])
            i += 1
    return out


def evaluate_with_vars(p):
    expr = p['expr']
    for name in sorted(p['vars'], key=len, reverse=True):
        expr = expr.replace(name, '(%d)' % p['vars'][name])
    return evaluate({'expr': expr})


def run_program(p):
    # lines "name = expression" executed in order; returns the final variables, sorted by name
    env = {}
    for line in p['lines']:
        name, expr = (part.strip() for part in line.split('=', 1))
        env[name] = evaluate_with_vars({'expr': expr, 'vars': env})
    return dict(sorted(env.items()))


def calculator_variant(p):
    # restated variant of c4-evaluate: a list of expressions, answer each
    return [evaluate({'expr': e}) for e in p['expressions']]


W = ['.....', '.###.', '...#.', '.#...', '.#.#.']
B = ['.....#.....', '.###.#.###.', '...C...#...', '####.#.#.##', '.....#..C..', '.###.####..', '...........']

TASKS = [
    ('n1-lcm', 'number', 1, lcm_list, 'run(input) receives {"values": [positive ints]} (at least one) and returns their least common multiple.',
     [{'values': [4, 6]}, {'values': [7]}, {'values': [2, 3, 4, 5, 6]}, {'values': [12, 18, 30]}, {'values': list(range(1, 21))}]),
    ('n2-congruence', 'number', 2, solve_congruence, 'run(input) receives {"a": int, "b": int, "m": int >= 1} and returns the sorted list of all x in 0..m-1 with a*x = b (mod m); empty if none.',
     [{'a': 3, 'b': 1, 'm': 7}, {'a': 4, 'b': 2, 'm': 6}, {'a': 6, 'b': 4, 'm': 9}, {'a': 0, 'b': 0, 'm': 3}, {'a': 14, 'b': 30, 'm': 100}, {'a': -5, 'b': 3, 'm': 12}]),
    ('n3-crt-count', 'number', 3, crt_count, 'run(input) receives {"congruences": [[r, m], ...], "low": int, "high": int} with moduli that need not be coprime, and returns how many integers x with low <= x <= high satisfy x = r (mod m) for every pair.',
     [{'congruences': [[2, 3], [3, 5]], 'low': 0, 'high': 100}, {'congruences': [[1, 4], [3, 6]], 'low': -50, 'high': 50}, {'congruences': [[0, 4], [2, 6]], 'low': 0, 'high': 1000}, {'congruences': [[5, 7]], 'low': 6, 'high': 4}, {'congruences': [[1, 2], [2, 3], [3, 5]], 'low': -1000, 'high': 1000}]),
    ('n4-eggs', 'number', 4, remainder_puzzle, 'A basket of eggs: grouped g at a time, r eggs are left over, for each [g, r] in {"groups": [[g, r], ...]}. run(input) returns the smallest positive number of eggs that fits every grouping, or null if none.',
     [{'groups': [[2, 1], [3, 1], [4, 1], [5, 1], [6, 1], [7, 0]]}, {'groups': [[3, 2], [5, 3], [7, 2]]}, {'groups': [[4, 0], [6, 0]]}, {'groups': [[4, 1], [6, 2]]}, {'groups': [[29, 15], [45, 11]]}]),
    ('r7-path', 'robot', 1, path_cells, 'run(input) receives {"grid": [str, ...], "start": [row, col], "goal": [row, col]} with "." free and "#" walls. Return a shortest path as a list of [row, col] cells from start to goal inclusive, found by breadth-first search trying neighbours in the order up, right, down, left (the first time a cell is reached fixes its predecessor); null if unreachable.',
     [{'grid': W, 'start': [0, 0], 'goal': [4, 4]}, {'grid': W, 'start': [2, 2], 'goal': [2, 2]}, {'grid': W, 'start': [4, 0], 'goal': [0, 4]}, {'grid': ['.#', '#.'], 'start': [0, 0], 'goal': [1, 1]}, {'grid': G, 'start': [0, 0], 'goal': [9, 9]}]),
    ('r8-moves', 'robot', 2, moves_string, 'Same robot and rules as a breadth-first shortest path that tries neighbours up, right, down, left: run(input) receives {"grid", "start", "goal"} and returns the path as a string of moves U, D, L, R (empty if start is goal), or null if unreachable.',
     [{'grid': W, 'start': [0, 0], 'goal': [4, 4]}, {'grid': W, 'start': [2, 2], 'goal': [2, 2]}, {'grid': W, 'start': [4, 0], 'goal': [0, 4]}, {'grid': G, 'start': [8, 0], 'goal': [4, 6]}]),
    ('r9-battery', 'robot', 3, battery_reach, 'run(input) receives {"grid": [str, ...], "start", "goal", "battery": int} where "." is free, "#" a wall and "C" a charger. Each move uses 1 charge; the robot starts full and entering a "C" cell refills it to "battery". It cannot move with 0 charge. Return the fewest moves to reach goal, or -1.',
     [{'grid': B, 'start': [0, 0], 'goal': [6, 10], 'battery': 30}, {'grid': B, 'start': [0, 0], 'goal': [6, 10], 'battery': 8}, {'grid': B, 'start': [0, 0], 'goal': [6, 10], 'battery': 4}, {'grid': B, 'start': [0, 0], 'goal': [2, 3], 'battery': 5}, {'grid': B, 'start': [6, 0], 'goal': [0, 10], 'battery': 9}]),
    ('r10-grid-again', 'robot', 4, grid_distance_variant, 'A warehouse floor has {"rows": int, "cols": int} cells and {"blocked": [[row, col], ...]} shelves. run(input) also receives "from" and "to" cells and returns the fewest up/down/left/right steps between them avoiding shelves, or -1.',
     [{'rows': 3, 'cols': 3, 'blocked': [[1, 1]], 'from': [0, 0], 'to': [2, 2]}, {'rows': 1, 'cols': 5, 'blocked': [[0, 2]], 'from': [0, 0], 'to': [0, 4]}, {'rows': 4, 'cols': 4, 'blocked': [], 'from': [3, 3], 'to': [0, 0]}, {'rows': 5, 'cols': 6, 'blocked': [[0, 1], [1, 1], [2, 1], [3, 3], [4, 3], [2, 4]], 'from': [0, 0], 'to': [4, 5]}]),
    ('l1-tokens', 'language', 1, tokenize, 'run(input) receives {"expr": str} and returns its tokens as a list of [kind, value]: ["num", int] for a run of digits, ["name", str] for a letter followed by letters, digits or "_", and ["op", char] for any other non-space character; spaces separate tokens and are dropped.',
     [{'expr': '1 + 2'}, {'expr': 'x1*(y_2-30)'}, {'expr': ''}, {'expr': '  12abc  '}, {'expr': 'a=b<=c'}]),
    ('l2-vars', 'language', 2, evaluate_with_vars, 'run(input) receives {"expr": str, "vars": {name: int}}: an integer expression with + - * /, parentheses, unary minus, spaces and variable names, and returns its int value (usual precedence, left associative, division truncating toward zero). Do not use eval.',
     [{'expr': 'x + 1', 'vars': {'x': 4}}, {'expr': 'a*b - c/2', 'vars': {'a': 3, 'b': 5, 'c': 7}}, {'expr': '-(rate) * 3', 'vars': {'rate': 2}}, {'expr': 'n / -4', 'vars': {'n': 9}}, {'expr': '(x+y)*(x-y)', 'vars': {'x': 10, 'y': 3}}]),
    ('l3-program', 'language', 3, run_program, 'run(input) receives {"lines": [str, ...]}, each "name = expression" in the integer expression language with + - * /, parentheses, unary minus and earlier names (division truncates toward zero). Execute the lines in order and return all variables as an object sorted by name. Do not use eval.',
     [{'lines': ['x = 2', 'y = x * 3', 'x = y - x']}, {'lines': ['a = 7', 'b = a / 2', 'c = -(a - b) * b']}, {'lines': []}, {'lines': ['n = 10', 'm = n * n - 1', 'k = m / 3 / 3']}]),
    ('l4-calc-again', 'language', 4, calculator_variant, 'A calculator: run(input) receives {"expressions": [str, ...]} of integer arithmetic with + - * /, parentheses and unary minus, division truncating toward zero, and returns the list of their values. Do not use eval.',
     [{'expressions': ['1+1', '2*(3+4)']}, {'expressions': []}, {'expressions': ['-7/2', '7/-2', '-(-7)/2']}, {'expressions': ['100-99*2/3', '((((5))))']}]),
]


def build(here=Path(__file__).parent):
    tasks = [{'id': t, 'domain': d, 'level': lv, 'prompt': prompt} for t, d, lv, _, prompt, _ in TASKS]
    hidden = {t: [{'input': i, 'expected': f(i)} for i in inputs] for t, _, _, f, _, inputs in TASKS}
    (here / 'growth_tasks.json').write_text(json.dumps(tasks, indent=1) + '\n')
    (here / 'growth_hidden.json').write_text(json.dumps(hidden, indent=1) + '\n')
    return tasks, hidden


if __name__ == '__main__':
    tasks, hidden = build()
    for t in tasks:
        print(t['id'], [c['expected'] for c in hidden[t['id']]][:3])
