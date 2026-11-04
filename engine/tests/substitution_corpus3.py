"""A third corpus of 22 correct, ordinary methods with correct hand checks, a requested input with its right answer,
and the contract's valid domain where it has one, written by the twelfth reviewer. Data for
tests/test_substitution_corpus.py."""


def c(i, e):
    return {'input': i, 'expected': e}


CORPUS3 = []


def add(name, source, checks, ask, answer, known=(), valid=None):
    CORPUS3.append((name, source, checks, list(known), ask, answer, valid))


MOVES = ('rock', 'paper', 'scissors', 'lizard', 'spock')

add('litres per 100 km by vehicle class', '''KM_PER_LITRE = {"car": 15.0, "van": 10.0, "truck": 4.0, "bus": 3.0, "scooter": 40.0}


def run(kind):
    return 100 / KM_PER_LITRE[kind]
''', [c('van', 10.0), c('truck', 25.0), c('scooter', 2.5)], 'car', 100 / 15.0)

add('harmonic number with a memo table', '''KNOWN = {1: 1.0, 2: 1.5, 3: 1.8333333333333333, 4: 2.083333333333333}


def run(n):
    if n in KNOWN:
        return KNOWN[n]
    return sum(1 / k for k in range(1, n + 1))
''', [c(3, 1.8333333333333333), c(4, 2.083333333333333), c(6, sum(1 / k for k in range(1, 7))),
      c(10, sum(1 / k for k in range(1, 11)))], 8, sum(1 / k for k in range(1, 9)))

add('regular polygon by name (dict output)', '''SIDES = {"triangle": 3, "square": 4, "pentagon": 5, "hexagon": 6, "heptagon": 7, "octagon": 8}


def run(name):
    n = SIDES[name]
    return {"sides": n, "angle": 180 * (n - 2) / n}
''', [c('square', {'sides': 4, 'angle': 90.0}), c('hexagon', {'sides': 6, 'angle': 120.0}),
      c('octagon', {'sides': 8, 'angle': 135.0})], 'pentagon', {'sides': 5, 'angle': 108.0})

add('ordinal of a number', '''def run(n):
    if n % 100 in (11, 12, 13):
        return str(n) + "th"
    return str(n) + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
''', [c(11, '11th'), c(22, '22nd'), c(101, '101st'), c(113, '113th'), c(23, '23rd')], 112, '112th')

add('next UK traffic light phase', '''NEXT = {"green": "amber", "amber": "red", "red": "red and amber", "red and amber": "green"}


def run(phase):
    return NEXT[phase]
''', [c('green', 'amber'), c('amber', 'red'), c('red', 'red and amber')], 'red and amber', 'green',
    valid=lambda x: x in ('green', 'amber', 'red', 'red and amber'))

add('chess material of a list of pieces', '''VALUES = {"pawn": 1, "knight": 3, "bishop": 3, "rook": 5, "queen": 9}


def run(pieces):
    return sum(VALUES[p] for p in pieces)
''', [c(['queen'], 9), c(['rook', 'rook'], 10), c(['pawn', 'knight', 'bishop'], 7)], ['queen', 'rook', 'pawn'], 15)

add('ISO weekday name (1-7)', '''NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def run(n):
    return NAMES[(n - 1) % 7]
''', [c(3, 'Wednesday'), c(5, 'Friday'), c(7, 'Sunday')], 6, 'Saturday',
    valid=lambda x: isinstance(x, int) and 1 <= x <= 7)

add('rock paper scissors lizard spock', '''BEATS = {"rock": ("scissors", "lizard"), "paper": ("rock", "spock"), "scissors": ("paper", "lizard"),
         "lizard": ("spock", "paper"), "spock": ("scissors", "rock")}


def run(p):
    a, b = p
    if a == b:
        return "draw"
    return "first" if b in BEATS[a] else "second"
''', [c(['rock', 'lizard'], 'first'), c(['spock', 'paper'], 'second'), c(['lizard', 'lizard'], 'draw'),
      c(['scissors', 'spock'], 'second')], ['paper', 'spock'], 'first',
    valid=lambda x: isinstance(x, list) and len(x) == 2 and all(m in MOVES for m in x))

add('coins to cents', '''CENTS = {"penny": 1, "nickel": 5, "dime": 10, "quarter": 25}


def run(coins):
    return sum(CENTS[coin] for coin in coins)
''', [c(['quarter'], 25), c(['dime', 'dime', 'nickel'], 25), c(['penny', 'quarter', 'dime'], 36)],
    ['quarter', 'quarter', 'penny'], 51)

add('electricity bill with an allowance', '''def run(kwh):
    if kwh <= 50:
        return 4.0
    return round(4.0 + (kwh - 50) * 0.17, 2)
''', [c(30, 4.0), c(150, 21.0), c(250, 38.0)], 100, 12.5)

add('Morse code of a word', '''CODE = {"A": ".-", "B": "-...", "E": ".", "O": "---", "S": "...", "T": "-"}


def run(word):
    return " ".join(CODE[ch] for ch in word)
''', [c('SOS', '... --- ...'), c('BAT', '-... .- -'), c('EAST', '. .- ... -')], 'TOES', '- --- . ...')

add('western zodiac sign of [month, day]', '''LAST = [19, 18, 20, 19, 20, 20, 22, 22, 22, 22, 21, 21]
SIGNS = ["Capricorn", "Aquarius", "Pisces", "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra",
         "Scorpio", "Sagittarius"]


def run(p):
    month, day = p
    i = month - 1
    return SIGNS[i] if day <= LAST[i] else SIGNS[(i + 1) % 12]
''', [c([3, 25], 'Aries'), c([7, 4], 'Cancer'), c([12, 25], 'Capricorn'), c([8, 30], 'Virgo')], [5, 10], 'Taurus',
    valid=lambda x: (isinstance(x, list) and len(x) == 2 and 1 <= x[0] <= 12
                     and 1 <= x[1] <= [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][x[0] - 1]))

add('compass point of a bearing', '''POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def run(degrees):
    return POINTS[int((degrees % 360) / 45 + 0.5) % 8]
''', [c(90, 'E'), c(225, 'SW'), c(350, 'N'), c(30, 'NE')], 300, 'NW')

add('Fibonacci with a memo table', '''MEMO = {0: 0, 1: 1, 2: 1, 3: 2, 4: 3, 5: 5}


def run(n):
    if n in MEMO:
        return MEMO[n]
    a, b = 3, 5
    for _ in range(n - 5):
        a, b = b, a + b
    return b
''', [c(5, 5), c(10, 55), c(4, 3), c(7, 13)], 12, 144)

add('sales tax percent by state', '''RATES = {"CA": 0.0725, "TX": 0.0625, "NY": 0.04, "OR": 0.0}


def run(state):
    return round(RATES[state] * 100, 4)
''', [c('CA', 7.25), c('TX', 6.25), c('OR', 0.0)], 'NY', 4.0)

add('SI prefix text to a number', '''PREFIX = {"k": 1e3, "M": 1e6, "G": 1e9, "m": 1e-3, "u": 1e-6}


def run(text):
    if text[-1] in PREFIX:
        return float(text[:-1]) * PREFIX[text[-1]]
    return float(text)
''', [c('5k', 5000.0), c('2M', 2000000.0), c('250m', 0.25), c('42', 42.0)], '3G', 3e9)

add('average of the scores, absent ignored', '''def run(scores):
    present = [s for s in scores if s != "absent"]
    return sum(present) / len(present) if present else None
''', [c([80, 90, 'absent'], 85.0), c([70, 'absent', 75, 80], 75.0), c([60, 61], 60.5)], [10, 20, 'absent', 25],
    55 / 3)

add('screen resolution name', '''NAMES = {"1280x720": "HD", "1920x1080": "Full HD", "2560x1440": "QHD", "3840x2160": "4K UHD"}


def run(p):
    return NAMES.get("%dx%d" % (p["width"], p["height"]), "other")
''', [c({'width': 1920, 'height': 1080}, 'Full HD'), c({'width': 3840, 'height': 2160}, '4K UHD'),
      c({'width': 1024, 'height': 768}, 'other')], {'width': 2560, 'height': 1440}, 'QHD',
    valid=lambda x: isinstance(x, dict) and x.get('width', 0) > 0 and x.get('height', 0) > 0)

add('speed in metres per second for a named animal', '''TOP_KMH = {"cheetah": 112, "horse": 88, "human": 45, "snail": 0.05}


def run(animal):
    return TOP_KMH[animal] / 3.6
''', [c('human', 12.5), c('horse', 88 / 3.6), c('cheetah', 112 / 3.6)], 'snail', 0.05 / 3.6)

add('blood pressure category', '''def run(p):
    systolic, diastolic = p
    if systolic >= 180 or diastolic >= 120:
        return "crisis"
    if systolic >= 140 or diastolic >= 90:
        return "high 2"
    if systolic >= 130 or diastolic >= 80:
        return "high 1"
    if systolic >= 120:
        return "elevated"
    return "normal"
''', [c([115, 75], 'normal'), c([125, 78], 'elevated'), c([135, 85], 'high 1'), c([150, 95], 'high 2'),
      c([185, 100], 'crisis')], [128, 70], 'elevated')

add('word count by kind of word', '''VOWELS = "aeiou"


def run(text):
    words = text.lower().split()
    starts = sum(1 for w in words if w[0] in VOWELS)
    return {"words": len(words), "vowel_start": starts, "consonant_start": len(words) - starts}
''', [c('An apple a day', {'words': 4, 'vowel_start': 3, 'consonant_start': 1}),
      c('keeps the doctor away', {'words': 4, 'vowel_start': 1, 'consonant_start': 3})], 'Eat our oats now',
    {'words': 4, 'vowel_start': 3, 'consonant_start': 1})

add('days until the weekend', '''LEFT = {"Monday": 5, "Tuesday": 4, "Wednesday": 3, "Thursday": 2, "Friday": 1, "Saturday": 0, "Sunday": 0}


def run(day):
    return LEFT[day]
''', [c('Monday', 5), c('Wednesday', 3), c('Saturday', 0)], 'Tuesday', 4,
    valid=lambda x: x in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'))
