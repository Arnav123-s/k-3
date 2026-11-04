"""A second corpus of 31 correct, ordinary methods with correct hand checks and a requested input with its right
answer, written by the eleventh reviewer (physics and finance formulas with float constants, text normalisation,
small simulations, recursion with memo tables, dict-of-dict inputs). Data for tests/test_substitution_corpus.py."""


def c(i, e):
    return {'input': i, 'expected': e}


CORPUS2 = []


def add(name, source, checks, ask, answer, known=()):
    CORPUS2.append((name, source, checks, list(known), ask, answer))


# --- physics and finance formulas with float constants ---------------------------------------------------------
add('free fall distance (rounded to 3 places)', '''G = 9.81


def run(t):
    return round(0.5 * G * t * t, 3)
''', [c(1, 4.905), c(2, 19.62), c(3, 44.145)], 4, 78.48)

add('pendulum period', '''import math

G = 9.81


def run(length):
    return round(2 * math.pi * math.sqrt(length / G), 3)
''', [c(1.0, 2.006), c(9.81, 6.283), c(0.25, 1.003)], 4.0, 4.012)

add('compound interest', '''def run(p):
    return round(p["principal"] * (1 + p["rate"]) ** p["years"], 2)
''', [c({'principal': 1000, 'rate': 0.05, 'years': 10}, 1628.89), c({'principal': 500, 'rate': 0.1, 'years': 2}, 605.0),
      c({'principal': 200, 'rate': 0.0, 'years': 5}, 200.0)], {'principal': 100, 'rate': 0.1, 'years': 3}, 133.1)

add('monthly loan payment', '''def run(p):
    r = p["annual_rate"] / 100 / 12
    n = p["months"]
    if r == 0:
        return round(p["principal"] / n, 2)
    return round(p["principal"] * r / (1 - (1 + r) ** -n), 2)
''', [c({'principal': 1000, 'annual_rate': 12, 'months': 12}, 88.85),
      c({'principal': 1200, 'annual_rate': 0, 'months': 12}, 100.0),
      c({'principal': 10000, 'annual_rate': 6, 'months': 24}, 443.21)],
    {'principal': 5000, 'annual_rate': 0, 'months': 10}, 500.0)

add('kinetic energy', '''def run(p):
    m, v = p
    return 0.5 * m * v ** 2
''', [c([2, 3], 9.0), c([10, 4], 80.0), c([4, 10], 200.0)], [3, 4], 24.0)

add('celsius and fahrenheit (if-chain)', '''def run(p):
    value, unit = p["value"], p["unit"]
    if unit == "C":
        return round(value * 1.8 + 32, 2)
    if unit == "F":
        return round((value - 32) / 1.8, 2)
    raise ValueError(unit)
''', [c({'value': 100, 'unit': 'C'}, 212.0), c({'value': 32, 'unit': 'F'}, 0.0),
      c({'value': 212, 'unit': 'F'}, 100.0), c({'value': 37, 'unit': 'C'}, 98.6)], {'value': 50, 'unit': 'F'}, 10.0)

add('postage by weight tier', '''def run(grams):
    if grams <= 20:
        return 0.85
    if grams <= 100:
        return 1.70
    if grams <= 500:
        return 3.40
    return 6.80
''', [c(20, 0.85), c(80, 1.7), c(450, 3.4), c(900, 6.8)], 250, 3.4)

add('flat tax after deduction', '''DEDUCTION = 12000
RATE = 0.2


def run(income):
    return round(max(0, income - DEDUCTION) * RATE, 2)
''', [c(22000, 2000.0), c(50000, 7600.0), c(8000, 0.0)], 30000, 3600.0)

add('currency conversion (dict of dicts)', '''RATES = {"USD": {"EUR": 0.9, "JPY": 150.0}, "EUR": {"USD": 1.1}}


def run(p):
    return round(p["amount"] * RATES[p["from"]][p["to"]], 2)
''', [c({'amount': 100, 'from': 'USD', 'to': 'EUR'}, 90.0), c({'amount': 10, 'from': 'USD', 'to': 'JPY'}, 1500.0),
      c({'amount': 50, 'from': 'EUR', 'to': 'USD'}, 55.0)], {'amount': 20, 'from': 'EUR', 'to': 'USD'}, 22.0)

add('electricity bill with a standing charge', '''STANDING = 0.45   # per day
UNIT = 0.28       # per kWh


def run(p):
    return round(p["days"] * STANDING + p["kwh"] * UNIT, 2)
''', [c({'days': 30, 'kwh': 250}, 83.5), c({'days': 31, 'kwh': 0}, 13.95), c({'days': 10, 'kwh': 100}, 32.5)],
    {'days': 28, 'kwh': 300}, 96.6)

# --- text normalisation ----------------------------------------------------------------------------------------
add('collapse whitespace and lowercase', '''import re


def run(text):
    return re.sub(r"\\s+", " ", text).strip().lower()
''', [c('  Hello   World ', 'hello world'), c('A\tB\nC', 'a b c'), c('Same', 'same')], ' Big  Data ', 'big data')

add('slugify', '''import re


def run(text):
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-")
''', [c('Hello World!', 'hello-world'), c('  Kuttaka: the pulveriser ', 'kuttaka-the-pulveriser'),
      c('C3PO & R2D2', 'c3po-r2d2')], 'Ten Reviews, One Fix', 'ten-reviews-one-fix')

add('expand contractions', '''CONTRACTIONS = {"can't": "cannot", "won't": "will not", "it's": "it is", "i'm": "i am"}


def run(text):
    return " ".join(CONTRACTIONS.get(w, w) for w in text.lower().split())
''', [c("I can't go", 'i cannot go'), c("won't", 'will not'), c("It's fine", 'it is fine')], "i'm here", 'i am here')

add('title case with small words', '''SMALL = {"a", "an", "the", "of", "and", "in", "on"}


def run(text):
    words = text.lower().split()
    return " ".join(w if i and w in SMALL else w.capitalize() for i, w in enumerate(words))
''', [c('the lord of the rings', 'The Lord of the Rings'), c('a tale of two cities', 'A Tale of Two Cities'),
      c('war and peace', 'War and Peace')], 'the name of the rose', 'The Name of the Rose')

add('morse code', '''CODE = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....",
        "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.",
        "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
        "Y": "-.--", "Z": "--.."}


def run(text):
    return " ".join(CODE[ch] for ch in text.upper() if ch in CODE)
''', [c('SOS', '... --- ...'), c('E', '.'), c('hi', '.... ..')], 'TEA', '- . .-')

add('phone number normalisation', '''def run(text):
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits[:3] + "-" + digits[3:6] + "-" + digits[6:]
''', [c('(555) 123-4567', '555-123-4567'), c('+1 555 987 6543', '555-987-6543'), c('555.111.2222', '555-111-2222')],
    '1-800-555-0199', '800-555-0199')

add('strip accents', '''import unicodedata


def run(text):
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")
''', [c('café', 'cafe'), c('naïve', 'naive'), c('Ångström', 'Angstrom')], 'résumé', 'resume')

# --- small simulations -----------------------------------------------------------------------------------------
add('bank account transactions', '''def run(ops):
    balance = 0
    for kind, amount in ops:
        if kind == "deposit":
            balance += amount
        elif kind == "withdraw" and amount <= balance:
            balance -= amount
    return balance
''', [c([['deposit', 100], ['withdraw', 30]], 70), c([['withdraw', 50]], 0),
      c([['deposit', 20], ['deposit', 5], ['withdraw', 40]], 25)], [['deposit', 10], ['withdraw', 3]], 7)

add('robot on a grid', '''MOVES = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}


def run(path):
    x = y = 0
    for step in path:
        dx, dy = MOVES[step]
        x, y = x + dx, y + dy
    return [x, y]
''', [c('NNEESW', [1, 1]), c('EEEE', [4, 0]), c('NNNWWS', [-2, 2])], 'SSSE', [1, -3])

add('traffic light colour at time t', '''CYCLE = [("green", 30), ("yellow", 5), ("red", 25)]


def run(t):
    t %= sum(d for _, d in CYCLE)
    for colour, duration in CYCLE:
        if t < duration:
            return colour
        t -= duration
''', [c(10, 'green'), c(32, 'yellow'), c(40, 'red'), c(92, 'yellow')], 70, 'green')

add('elevator floors travelled', '''def run(p):
    floor, total = p["start"], 0
    for stop in p["requests"]:
        total += abs(stop - floor)
        floor = stop
    return total
''', [c({'start': 3, 'requests': [5, 1, 7]}, 12), c({'start': 10, 'requests': [10]}, 0),
      c({'start': 4, 'requests': [9, 6]}, 8)], {'start': 1, 'requests': [4, 2]}, 5)

add('queue at a counter (total wait)', '''def run(p):
    free, wait = 0, 0
    for arrive in p["arrivals"]:
        start = max(free, arrive)
        wait += start - arrive
        free = start + p["service"]
    return wait
''', [c({'arrivals': [0, 1, 2], 'service': 3}, 6), c({'arrivals': [0, 10], 'service': 4}, 0),
      c({'arrivals': [5, 5, 5], 'service': 2}, 6)], {'arrivals': [0, 0], 'service': 5}, 5)

# --- recursion with memo tables --------------------------------------------------------------------------------
add('coin change ways (memo)', '''from functools import lru_cache

COINS = (1, 5, 10, 25)


@lru_cache(maxsize=None)
def ways(n, k):
    if n == 0:
        return 1
    if n < 0 or k == len(COINS):
        return 0
    return ways(n - COINS[k], k) + ways(n, k + 1)


def run(n):
    return ways(n, 0)
''', [c(10, 4), c(25, 13), c(11, 4)], 30, 18)

add('tribonacci with a memo table', '''MEMO = {0: 0, 1: 0, 2: 1}


def run(n):
    if n not in MEMO:
        MEMO[n] = run(n - 1) + run(n - 2) + run(n - 3)
    return MEMO[n]
''', [c(4, 2), c(7, 13), c(10, 81)], 12, 274)

add('edit distance (memo)', '''from functools import lru_cache


def run(p):
    a, b = p

    @lru_cache(maxsize=None)
    def d(i, j):
        if i == 0:
            return j
        if j == 0:
            return i
        return min(d(i - 1, j) + 1, d(i, j - 1) + 1, d(i - 1, j - 1) + (a[i - 1] != b[j - 1]))
    return d(len(a), len(b))
''', [c(['kitten', 'sitting'], 3), c(['flaw', 'lawn'], 2), c(['abc', 'abc'], 0)], ['sunday', 'saturday'], 3)

add('collatz steps (memo)', '''MEMO = {1: 0}


def run(n):
    path = []
    while n not in MEMO:
        path.append(n)
        n = n // 2 if n % 2 == 0 else 3 * n + 1
    steps = MEMO[n]
    for m in reversed(path):
        steps += 1
        MEMO[m] = steps
    return MEMO[path[0]] if path else steps
''', [c(6, 8), c(7, 16), c(27, 111)], 9, 19)

add('integer partitions (memo)', '''from functools import lru_cache


@lru_cache(maxsize=None)
def p(n, k):
    if n == 0:
        return 1
    if k == 0 or n < 0:
        return 0
    return p(n - k, k) + p(n, k - 1)


def run(n):
    return p(n, n)
''', [c(4, 5), c(5, 7), c(10, 42)], 7, 15)

# --- dict-of-dict inputs ---------------------------------------------------------------------------------------
add('top student by average (dict of dicts)', '''def run(grades):
    return max(sorted(grades), key=lambda s: sum(grades[s].values()) / len(grades[s]))
''', [c({'alice': {'math': 90, 'art': 80}, 'bob': {'math': 70, 'art': 95}}, 'alice'),
      c({'carol': {'math': 60}, 'dan': {'math': 61}}, 'dan'),
      c({'eve': {'x': 50, 'y': 50}, 'fay': {'x': 40, 'y': 70}}, 'fay')],
    {'gil': {'a': 10, 'b': 30}, 'hal': {'a': 25, 'b': 20}}, 'hal')

add('inventory value (dict of dicts)', '''def run(stock):
    return round(sum(item["qty"] * item["price"] for item in stock.values()), 2)
''', [c({'apple': {'qty': 3, 'price': 0.5}, 'pear': {'qty': 2, 'price': 0.75}}, 3.0),
      c({'pen': {'qty': 10, 'price': 1.2}}, 12.0),
      c({'a': {'qty': 0, 'price': 9.99}, 'b': {'qty': 4, 'price': 2.5}}, 10.0)],
    {'cup': {'qty': 6, 'price': 1.5}}, 9.0)

add('shortest path (dict of dicts)', '''import heapq


def run(p):
    graph, start, end = p["graph"], p["start"], p["end"]
    best, heap = {start: 0}, [(0, start)]
    while heap:
        d, node = heapq.heappop(heap)
        if node == end:
            return d
        if d > best.get(node, float("inf")):
            continue
        for nxt, w in graph.get(node, {}).items():
            if d + w < best.get(nxt, float("inf")):
                best[nxt] = d + w
                heapq.heappush(heap, (d + w, nxt))
    return -1
''', [c({'graph': {'A': {'B': 1, 'C': 4}, 'B': {'C': 2}, 'C': {}}, 'start': 'A', 'end': 'C'}, 3),
      c({'graph': {'A': {'B': 5}, 'B': {}}, 'start': 'A', 'end': 'B'}, 5),
      c({'graph': {'A': {'B': 1}, 'B': {}, 'C': {}}, 'start': 'A', 'end': 'C'}, -1)],
    {'graph': {'S': {'T': 7, 'U': 2}, 'U': {'T': 3}}, 'start': 'S', 'end': 'T'}, 5)

add('org chart depth (dict of dicts)', '''def run(tree):
    if not tree:
        return 0
    return 1 + max(run(child) for child in tree.values())
''', [c({'ceo': {'cto': {'dev': {}}, 'cfo': {}}}, 3), c({'solo': {}}, 1), c({'a': {'b': {'c': {'d': {}}}}}, 4)],
    {'x': {'y': {}, 'z': {}}}, 2)
