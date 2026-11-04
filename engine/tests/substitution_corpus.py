"""A corpus of 43 correct, ordinary methods with correct hand checks, written by the tenth reviewer to measure
false positives of the memorization readings (kept as data for tests/test_substitution_corpus.py). Every method
is correct for its task and every check is correct; a flag on one is a false positive."""


def c(i, e):
    return {'input': i, 'expected': e}


MONTHS = ("['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', "
          "'October', 'November', 'December']")

CORPUS = []


def add(name, source, checks, known=()):
    CORPUS.append((name, source, checks, list(known)))


# 1. lookup tables of named constants
add('days in a month by name', '''DAYS = {"January": 31, "February": 28, "March": 31, "April": 30, "May": 31, "June": 30,
        "July": 31, "August": 31, "September": 30, "October": 31, "November": 30, "December": 31}


def run(name):
    return DAYS[name]
''', [c('February', 28), c('April', 30), c('July', 31)])

add('month name from number', 'NAMES = %s\n\n\ndef run(m):\n    return NAMES[m - 1]\n' % MONTHS,
    [c(1, 'January'), c(7, 'July'), c(12, 'December'), c(3, 'March')])

add('month number from name', 'NAMES = %s\n\n\ndef run(name):\n    return NAMES.index(name) + 1\n' % MONTHS,
    [c('March', 3), c('December', 12), c('January', 1), c('August', 8)])

add('element symbol to atomic number', '''NUMBERS = {"H": 1, "He": 2, "Li": 3, "C": 6, "N": 7, "O": 8, "Na": 11, "Fe": 26, "Cu": 29, "Ag": 47,
           "Au": 79, "U": 92}


def run(symbol):
    return NUMBERS[symbol]
''', [c('Fe', 26), c('Au', 79), c('O', 8), c('Na', 11)])

add('HTTP reason phrase', '''REASONS = {200: "OK", 201: "Created", 301: "Moved Permanently", 400: "Bad Request", 404: "Not Found",
           500: "Internal Server Error"}


def run(code):
    return REASONS.get(code, "Unknown")
''', [c(200, 'OK'), c(404, 'Not Found'), c(500, 'Internal Server Error'), c(999, 'Unknown')])

add('chinese zodiac animal', '''ANIMALS = ["Rat", "Ox", "Tiger", "Rabbit", "Dragon", "Snake", "Horse", "Goat", "Monkey", "Rooster", "Dog",
           "Pig"]


def run(year):
    return ANIMALS[(year - 4) % 12]
''', [c(2020, 'Rat'), c(2023, 'Rabbit'), c(1990, 'Horse'), c(2000, 'Dragon')])

add('weekday name of a date', '''import datetime

NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def run(p):
    return NAMES[datetime.date(p[0], p[1], p[2]).weekday()]
''', [c([2024, 1, 1], 'Monday'), c([2000, 1, 1], 'Saturday'), c([1969, 7, 20], 'Sunday'),
      c([2023, 12, 25], 'Monday')])

add('number to english words (0-999)', '''ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
        "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def run(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else "-" + ONES[n % 10])
    rest = n % 100
    return ONES[n // 100] + " hundred" + ("" if rest == 0 else " and " + run(rest))
''', [c(7, 'seven'), c(13, 'thirteen'), c(42, 'forty-two'), c(100, 'one hundred'), c(315, 'three hundred and fifteen')])

add('rock paper scissors winner', '''BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}


def run(p):
    a, b = p["a"], p["b"]
    if a == b:
        return "draw"
    return "first" if BEATS[a] == b else "second"
''', [c({'a': 'rock', 'b': 'scissors'}, 'first'), c({'a': 'paper', 'b': 'scissors'}, 'second'),
      c({'a': 'paper', 'b': 'paper'}, 'draw'), c({'a': 'scissors', 'b': 'paper'}, 'first')])

# 2. unit conversions
add('duration text to seconds', '''UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def run(text):
    return int(text[:-1]) * UNITS[text[-1]]
''', [c('1h', 3600), c('30m', 1800), c('45s', 45), c('2d', 172800)])

add('size text to bytes', '''UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}


def run(text):
    for unit in ("KB", "MB", "GB", "B"):
        if text.endswith(unit):
            return int(text[:-len(unit)]) * UNITS[unit]
    raise ValueError(text)
''', [c('1KB', 1024), c('3MB', 3145728), c('512B', 512), c('2GB', 2147483648)])

add('temperature conversion', '''def run(p):
    value, src, dst = p["value"], p["from"], p["to"]
    celsius = {"C": value, "F": (value - 32) * 5 / 9, "K": value - 273.15}[src]
    return round({"C": celsius, "F": celsius * 9 / 5 + 32, "K": celsius + 273.15}[dst], 2)
''', [c({'value': 100, 'from': 'C', 'to': 'F'}, 212.0), c({'value': 32, 'from': 'F', 'to': 'C'}, 0.0),
      c({'value': 0, 'from': 'C', 'to': 'K'}, 273.15), c({'value': 300, 'from': 'K', 'to': 'C'}, 26.85)])

add('length conversion', '''TO_METRES = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "in": 0.0254, "ft": 0.3048, "mi": 1609.344}


def run(p):
    return round(p["value"] * TO_METRES[p["from"]] / TO_METRES[p["to"]], 6)
''', [c({'value': 1, 'from': 'km', 'to': 'm'}, 1000.0), c({'value': 12, 'from': 'in', 'to': 'ft'}, 1.0),
      c({'value': 1, 'from': 'mi', 'to': 'km'}, 1.609344), c({'value': 250, 'from': 'cm', 'to': 'm'}, 2.5)])

add('clock time to minutes', '''def run(text):
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)
''', [c('01:00', 60), c('00:45', 45), c('00:05', 5)])

add('minutes to clock time', '''def run(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)
''', [c(60, '01:00'), c(75, '01:15'), c(1439, '23:59'), c(5, '00:05')])

# 3. piecewise functions with thresholds
add('shipping cost by weight', '''def run(weight):
    if weight <= 1:
        return 5.0
    if weight <= 5:
        return 10.0
    if weight <= 20:
        return 25.0
    return 50.0
''', [c(0.5, 5.0), c(3, 10.0), c(10, 25.0), c(30, 50.0)])

add('BMI category', '''def run(p):
    bmi = p["weight"] / p["height"] ** 2
    if bmi < 18.5:
        return "underweight"
    if bmi < 25:
        return "normal"
    if bmi < 30:
        return "overweight"
    return "obese"
''', [c({'weight': 50, 'height': 1.8}, 'underweight'), c({'weight': 70, 'height': 1.75}, 'normal'),
      c({'weight': 85, 'height': 1.75}, 'overweight'), c({'weight': 110, 'height': 1.7}, 'obese')])

add('income tax with brackets', '''BRACKETS = [(10000, 0.0), (40000, 0.2), (float("inf"), 0.4)]


def run(income):
    tax, low = 0.0, 0
    for high, rate in BRACKETS:
        if income > low:
            tax += (min(income, high) - low) * rate
        low = high
    return round(tax, 2)
''', [c(5000, 0.0), c(20000, 2000.0), c(50000, 10000.0), c(10000, 0.0)])

add('wind speed to Beaufort description', '''def run(kmh):
    if kmh < 1:
        return "calm"
    if kmh < 20:
        return "breeze"
    if kmh < 62:
        return "strong wind"
    if kmh < 118:
        return "gale"
    return "hurricane"
''', [c(0.5, 'calm'), c(15, 'breeze'), c(40, 'strong wind'), c(90, 'gale'), c(150, 'hurricane')])

add('FizzBuzz word', '''def run(n):
    if n % 15 == 0:
        return "FizzBuzz"
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    return str(n)
''', [c(15, 'FizzBuzz'), c(9, 'Fizz'), c(10, 'Buzz'), c(7, '7')])

add('triangle kind', '''def run(sides):
    a, b, c = sorted(sides)
    if a + b <= c:
        return "invalid"
    if a == c:
        return "equilateral"
    if a == b or b == c:
        return "isosceles"
    return "scalene"
''', [c([3, 4, 5], 'scalene'), c([5, 5, 8], 'isosceles'), c([7, 7, 7], 'equilateral'), c([1, 2, 10], 'invalid')])

# 4. date and calendar arithmetic
add('days in a month (year, month)', '''def run(p):
    year, month = p
    if month == 2:
        return 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31
''', [c([2023, 2], 28), c([2024, 2], 29), c([1900, 2], 28), c([2000, 2], 29), c([2023, 4], 30), c([2023, 7], 31)])

add('day of the year', '''DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def run(p):
    year, month, day = p["year"], p["month"], p["day"]
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return sum(DAYS[:month - 1]) + day + (1 if leap and month > 2 else 0)
''', [c({'year': 2023, 'month': 1, 'day': 15}, 15), c({'year': 2024, 'month': 3, 'day': 1}, 61),
      c({'year': 2023, 'month': 12, 'day': 31}, 365), c({'year': 1900, 'month': 3, 'day': 1}, 60)])

add('days in a year', '''def run(year):
    return 366 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 365
''', [c(2024, 366), c(2023, 365), c(1900, 365), c(2000, 366)])

add('days between two dates', '''import datetime


def run(p):
    a = datetime.date(*p[0])
    b = datetime.date(*p[1])
    return abs((b - a).days)
''', [c([[2024, 1, 1], [2024, 12, 31]], 365), c([[2023, 1, 1], [2023, 3, 1]], 59),
      c([[2000, 2, 28], [2000, 3, 1]], 2), c([[2020, 5, 17], [2020, 5, 17]], 0)])

# 5. geometry / kinematics with floats
add('projectile range', '''import math

G = 9.81


def run(p):
    return round(p["v"] ** 2 * math.sin(math.radians(2 * p["angle"])) / G, 2)
''', [c({'v': 20, 'angle': 45}, 40.77), c({'v': 10, 'angle': 30}, 8.83), c({'v': 9.81, 'angle': 45}, 9.81)])

add('free fall time', '''import math


def run(h):
    return round(math.sqrt(2 * h / 9.8), 3)
''', [c(4.9, 1.0), c(19.6, 2.0), c(44.1, 3.0), c(9.8, 1.414)])

add('circle area', '''import math


def run(r):
    return round(math.pi * r * r, 4)
''', [c(1, 3.1416), c(2, 12.5664), c(3.5, 38.4845), c(10, 314.1593)])

add('polygon area (shoelace)', '''def run(points):
    total = 0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2
''', [c([[0, 0], [4, 0], [4, 3]], 6.0), c([[0, 0], [2, 0], [2, 2], [0, 2]], 4.0),
      c([[1, 1], [5, 1], [5, 4], [1, 4]], 12.0)])

# 6. combinatorics with base cases
add('staircase with 1, 2 or 3 steps', '''def run(n):
    ways = [1, 1, 2]
    for i in range(3, n + 1):
        ways.append(ways[-1] + ways[-2] + ways[-3])
    return ways[n]
''', [c(3, 4), c(4, 7), c(5, 13), c(10, 274)])

add('binomial coefficient by recursion', '''from functools import lru_cache


@lru_cache(maxsize=None)
def run_(n, k):
    if k == 0 or k == n:
        return 1
    return run_(n - 1, k - 1) + run_(n - 1, k)


def run(p):
    return run_(p[0], p[1])
''', [c([5, 2], 10), c([6, 3], 20), c([10, 1], 10), c([4, 4], 1)])

add('derangements', '''def run(n):
    if n == 0:
        return 1
    if n == 1:
        return 0
    a, b = 1, 0
    for i in range(2, n + 1):
        a, b = b, (i - 1) * (a + b)
    return b
''', [c(3, 2), c(4, 9), c(5, 44), c(6, 265)])

add('catalan numbers', '''def run(n):
    c = 1
    for i in range(n):
        c = c * 2 * (2 * i + 1) // (i + 2)
    return c
''', [c(3, 5), c(4, 14), c(5, 42), c(10, 16796)])

# 7. string processing and parsing
add('roman numeral to integer', '''VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def run(text):
    total = 0
    for i, ch in enumerate(text):
        v = VALUES[ch]
        if i + 1 < len(text) and VALUES[text[i + 1]] > v:
            total -= v
        else:
            total += v
    return total
''', [c('III', 3), c('IV', 4), c('IX', 9), c('LVIII', 58), c('MCMXCIV', 1994)])

add('integer to roman numeral', '''TABLE = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
         (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def run(n):
    out = ""
    for value, symbol in TABLE:
        while n >= value:
            out += symbol
            n -= value
    return out
''', [c(3, 'III'), c(4, 'IV'), c(9, 'IX'), c(58, 'LVIII'), c(40, 'XL')])

add('vowel count', '''def run(text):
    return sum(ch in "aeiouAEIOU" for ch in text)
''', [c('hello world', 3), c('aeiou', 5), c('rhythm', 0), c('Programming', 3)])

add('caesar shift', '''def run(p):
    out = []
    for ch in p["text"]:
        if ch.isalpha():
            base = ord("a") if ch.islower() else ord("A")
            out.append(chr((ord(ch) - base + p["shift"]) % 26 + base))
        else:
            out.append(ch)
    return "".join(out)
''', [c({'text': 'abc', 'shift': 3}, 'def'), c({'text': 'xyz', 'shift': 3}, 'abc'),
      c({'text': 'Hello, World', 'shift': 13}, 'Uryyb, Jbeyq')])

add('parse key=value pairs', '''def run(text):
    out = {}
    for part in text.split(";"):
        if part:
            key, value = part.split("=", 1)
            out[key.strip()] = value.strip()
    return out
''', [c('a=1;b=2', {'a': '1', 'b': '2'}), c('name = Ada ; lang=py', {'name': 'Ada', 'lang': 'py'}), c('', {})])

add('ordinal suffix', '''def run(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return str(n) + suffix
''', [c(3, '3rd'), c(11, '11th'), c(22, '22nd'), c(101, '101st'), c(112, '112th')])

add('word frequency (top word)', '''def run(text):
    counts = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    return max(sorted(counts), key=lambda w: counts[w])
''', [c('the cat and the hat', 'the'), c('b a b a c', 'a'), c('one two three two', 'two')])

add('compass direction from degrees', '''DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def run(deg):
    return DIRS[round(deg / 45) % 8]
''', [c(135, 'SE'), c(300, 'NW'), c(225, 'SW'), c(10, 'N')])

add('planet weight', '''RATIO = {"Mercury": 0.38, "Venus": 0.91, "Earth": 1.0, "Mars": 0.38, "Jupiter": 2.34, "Saturn": 1.06}


def run(p):
    return round(p["weight"] * RATIO[p["planet"]], 2)
''', [c({'weight': 100, 'planet': 'Jupiter'}, 234.0), c({'weight': 80, 'planet': 'Mars'}, 30.4),
      c({'weight': 50, 'planet': 'Earth'}, 50.0)])

add('grade from score', '''def run(score):
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 50:
        return "pass"
    return "fail"
''', [c(95, 'excellent'), c(80, 'good'), c(60, 'pass'), c(30, 'fail')])
