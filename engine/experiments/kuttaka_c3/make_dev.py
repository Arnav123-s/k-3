"""KUTTAKA-C3 development stream (builder-authored, visible): 2 deep chains, 9 tasks, with holdout.

Expected values come from the reference solutions below. They never enter a run: only requests
and visible checks do. The sealed stream is written by a separate evaluator session from
``FAMILY_SPEC.md``; this file is also the template the evaluator may copy for its generator.

Chains (family, roles):
  kal   (lang):     kal-lex base -> kal-rpn extend -> kal-eval extend -> kal-run compose
                    -> kal-script variant of kal-run
  meru  (calendar): meru-ord base -> meru-weekday extend -> meru-market compose
                    -> meru-text variant of meru-market

Every request is self-contained: it restates every rule it needs, so a solver with no memory has
all the information. Only the solution effort differs: with memory, earlier verified methods can be
called by id.
"""
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------- kal: a tiny integer language
LIMIT = 10 ** 18
PREC = {'^': 4, '~': 3, '*': 2, '/': 2, '%': 2, '+': 1, '-': 1}


def kal_lex(p):
    s, out, i = p['text'], [], 0
    while i < len(s):
        c = s[i]
        if c in ' \t':
            i += 1
        elif '0' <= c <= '9':
            j = i
            while j < len(s) and '0' <= s[j] <= '9':
                j += 1
            out.append(['num', s[i:j]])
            i = j
        elif 'a' <= c <= 'z':
            j = i + 1
            while j < len(s) and ('a' <= s[j] <= 'z' or '0' <= s[j] <= '9' or s[j] == '_'):
                j += 1
            out.append(['id', s[i:j]])
            i = j
        elif c in '+-*/%^()':
            out.append(['op', c])
            i += 1
        else:
            return None
    return out


def kal_rpn(p):
    tokens = kal_lex({'text': p['expr']})
    if tokens is None:
        return None
    out, stack, prev = [], [], None
    for kind, text in tokens:
        if kind in ('num', 'id'):
            out.append(text)
        elif text == '(':
            stack.append('(')
        elif text == ')':
            while stack and stack[-1] != '(':
                out.append(stack.pop())
            if not stack:
                return None
            stack.pop()
        elif text == '-' and (prev is None or (prev[0] == 'op' and prev[1] != ')')):
            stack.append('~')
        else:
            while (stack and stack[-1] != '(' and (PREC[stack[-1]] > PREC[text]
                                                   or (PREC[stack[-1]] == PREC[text] and text != '^'))):
                out.append(stack.pop())
            stack.append(text)
        prev = (kind, text)
    while stack:
        top = stack.pop()
        if top == '(':
            return None
        out.append(top)
    return out


def kal_eval(p):
    rpn = kal_rpn({'expr': p['expr']})
    if rpn is None:
        return None
    env, stack = p['env'], []
    for t in rpn:
        if '0' <= t[0] <= '9':
            v = int(t)
        elif 'a' <= t[0] <= 'z':
            if t not in env:
                return None
            v = env[t]
        elif t == '~':
            if not stack:
                return None
            v = -stack.pop()
        else:
            if len(stack) < 2:
                return None
            b, a = stack.pop(), stack.pop()
            if t == '+':
                v = a + b
            elif t == '-':
                v = a - b
            elif t == '*':
                v = a * b
            elif t in '/%':
                if b == 0:
                    return None
                q = abs(a) // abs(b)
                q = -q if (a < 0) != (b < 0) else q
                v = q if t == '/' else a - b * q
            else:
                if b < 0 or (abs(a) >= 2 and b > 63):
                    return None
                v = a ** b
        if abs(v) > LIMIT:
            return None
        stack.append(v)
    return stack[0] if len(stack) == 1 else None


def kal_run(p):
    env = dict(p['env'])
    for i, line in enumerate(p['lines']):
        if line.count('=') != 1:
            return {'error_line': i}
        left, right = line.split('=')
        tokens = kal_lex({'text': left})
        if tokens is None or len(tokens) != 1 or tokens[0][0] != 'id':
            return {'error_line': i}
        value = kal_eval({'expr': right, 'env': env})
        if value is None:
            return {'error_line': i}
        env[tokens[0][1]] = value
    return {'env': env}


def kal_script(p):
    env = {}
    for name, value in p['vars']:
        env[name] = value
    result = kal_run({'lines': p['script'].split(';'), 'env': env})
    if 'error_line' in result:
        return 'error on line %d' % (result['error_line'] + 1)
    return ['%s=%d' % (name, result['env'][name]) for name in sorted(result['env'])]


LEX = ('Kal tokens: an integer literal is a maximal run of decimal digits 0-9; an identifier is a '
       'lowercase letter a-z followed by a maximal run of lowercase letters, digits and underscores; '
       'the operators + - * / % ^ ( ) are one token each; spaces and tabs separate tokens and are '
       'otherwise ignored; any other character makes the text invalid.')
RPN = ('Conversion to postfix (shunting-yard): a "-" is unary minus when it is the first token or '
       'follows an operator other than ")"; otherwise it is binary. Precedence: ^ 4 '
       '(right-associative), unary minus 3, * / % 2 (left-associative), + - 1 (left-associative). '
       'Read the tokens left to right: append a number or identifier to the output; push a unary '
       'minus; push "("; on ")" pop operators to the output until "(" and discard that "(" (if there '
       'is none the expression is invalid); on a binary operator o first pop to the output every '
       'operator on top of the stack (stopping at "(") whose precedence is greater than that of o, or '
       'equal to it when o is left-associative, then push o. At the end pop every operator to the '
       'output; a remaining "(" makes the expression invalid. In the output a number keeps its digit '
       'text, an identifier its name, a binary operator its symbol, and unary minus is written "~".')
EVAL = ('Evaluate the postfix list with a stack of integers: a number pushes its value; an '
        'identifier pushes its value from the variables (a missing identifier is an error); "~" '
        'negates the top value; a binary operator pops b, then a, and pushes a op b, where + - * are '
        'exact, / is the integer quotient truncated toward zero, % is a - b*q with q that truncated '
        'quotient, / and % with b = 0 are errors, and ^ is a to the power b, an error when b < 0 '
        '(0^0 = 1). Pushing a value whose absolute value exceeds 10^18 is an error. Popping from an '
        'empty stack is an error, and the end must leave exactly one value.')
LINE = ('Each line must contain exactly one "="; the text left of it must be exactly one identifier '
        'token (spaces allowed around it) and the text right of it is an infix Kal expression.')

KAL = [
    ('kal-lex', 'base', [], None, kal_lex,
     'Write run(p). p = {"text": string}. Split the text into Kal tokens. ' + LEX + ' Return the list '
     'of tokens in order, each as [kind, text] with kind "num", "id" or "op" and text the exact '
     'characters of the token, or null if the text is invalid.',
     [{'text': '12+x_1*(3-y)'}, {'text': 'a1b 007^2'}, {'text': '2x%y9'}, {'text': 'x = 1'}],
     [{'text': ''}, {'text': 'Ab'}, {'text': '_a'}, {'text': 'a__b9 3'}, {'text': '((1))/z'},
      {'text': '4 \t 5'}, {'text': '9.5'}]),
    ('kal-rpn', 'extend', ['kal-lex'], None, kal_rpn,
     'Write run(p). p = {"expr": string}, an infix Kal expression. Convert it to postfix. ' + LEX + ' '
     + RPN + ' Return the postfix output as a list of strings, or null if the text or the '
     'parentheses are invalid. Do no other validation.',
     [{'expr': '1+2*3'}, {'expr': '-2^2'}, {'expr': '2^3^2-(4-x)'}],
     [{'expr': '2*-3'}, {'expr': '-3*2'}, {'expr': '(1+2'}, {'expr': '1+2)'}, {'expr': 'a-b-c'},
      {'expr': '2^-1'}, {'expr': '--x_2'}, {'expr': '7%3/2'}, {'expr': '1 # 2'}]),
    ('kal-eval', 'extend', ['kal-rpn'], None, kal_eval,
     'Write run(p). p = {"expr": string, "env": object mapping identifiers to integers}. Evaluate the '
     'infix Kal expression with the variables in env. ' + LEX + ' ' + RPN + ' ' + EVAL + ' Return the '
     'integer value, or null if the text or the parentheses are invalid or any error occurs.',
     [{'expr': '7/-2', 'env': {}}, {'expr': 'x^2 - y%3', 'env': {'x': 4, 'y': -7}},
      {'expr': '(1+q)*2', 'env': {'x': 1}}],
     [{'expr': '-2^2', 'env': {}}, {'expr': '2^-1', 'env': {}}, {'expr': '10/0', 'env': {}},
      {'expr': '2^62', 'env': {}}, {'expr': '10^18', 'env': {}}, {'expr': '-7/2+7%-3', 'env': {}},
      {'expr': 'a b', 'env': {'a': 1, 'b': 2}}, {'expr': '1+', 'env': {}}, {'expr': '0^0', 'env': {}},
      {'expr': 'x_1*3', 'env': {'x_1': 5}}]),
    ('kal-run', 'compose', ['kal-lex', 'kal-eval'], None, kal_run,
     'Write run(p). p = {"lines": list of strings, "env": object mapping identifiers to integers}. '
     'Run a Kal program. ' + LINE + ' ' + LEX + ' ' + RPN + ' ' + EVAL + ' The lines run in order, '
     'starting from a copy of env: each expression is evaluated with the current variables and its '
     'value is assigned to the identifier. Return {"env": the final variables, including unchanged '
     'ones} if every line succeeds; otherwise return {"error_line": i} with i the 0-based index of '
     'the first line that is malformed or whose expression is invalid or has an error.',
     [{'lines': ['a = 3', 'b = a*a - 1', 'a = b/a'], 'env': {}},
      {'lines': ['x = 2^10', 'y = x % 7', 'z = y / 0'], 'env': {'w': 1}},
      {'lines': ['n = -n', '2 = n'], 'env': {'n': 5}}],
     [{'lines': [], 'env': {'k': 1}}, {'lines': ['a == 1'], 'env': {}}, {'lines': ['a b = 1'], 'env': {}},
      {'lines': [' t_2 = 7%-3 ', 'u = t_2^3'], 'env': {}}, {'lines': ['v = 1', 'w = q'], 'env': {}},
      {'lines': ['big = 10^9', 'big = big*big', 'big = big*10'], 'env': {}},
      {'lines': ['m = -2^2', 'm = m - -m'], 'env': {}}]),
    ('kal-script', 'variant', ['kal-run'], 'kal-run', kal_script,
     'Write run(p). p = {"script": string, "vars": list of [identifier, integer] pairs}. Run a Kal '
     'script and report its variables. The initial variables come from vars, a later pair replacing '
     'an earlier one with the same name. The script is split at every ";" into lines (an empty line '
     'is malformed). ' + LINE + ' ' + LEX + ' ' + RPN + ' ' + EVAL + ' The lines run in order: each '
     'expression is evaluated with the current variables and its value is assigned to the '
     'identifier. If every line succeeds, return a list of strings "name=value" (value in decimal) '
     'for all final variables, sorted by name in character-code order; otherwise return the string '
     '"error on line N" with N the 1-based number of the first line that is malformed or whose '
     'expression is invalid or has an error.',
     [{'script': 'a = 3; b = a*a - 1; a = b/a', 'vars': []},
      {'script': 'x = 2^10;y = x % 7;z = y/0', 'vars': [['w', 1]]},
      {'script': 'n = -n', 'vars': [['n', 5], ['n', 6]]}],
     [{'script': '', 'vars': [['k', 1]]}, {'script': 'a=1;', 'vars': []},
      {'script': 'p = 7/-2; q = p%2', 'vars': []}, {'script': 'z = 2^62', 'vars': []},
      {'script': 'b = a', 'vars': [['a', -4]]}, {'script': 'c1 = 0^0; c2 = -c1^2', 'vars': []}]),
]

# ---------------------------------------------------------------- meru: a fictional calendar
WEEK = ['Ash', 'Bel', 'Cor', 'Dun', 'Eld', 'Fen']


def meru_leap(y):
    return y % 5 == 0 and (y % 30 != 0 or y % 150 == 0)


def meru_length(y, m):
    return 30 if m <= 11 else (36 if meru_leap(y) else 35)


def meru_ord(p):
    y, m, d = p['y'], p['m'], p['d']
    if y < 1 or not 1 <= m <= 12 or not 1 <= d <= meru_length(y, m):
        return None
    n = y - 1
    return n * 365 + n // 5 - n // 30 + n // 150 + (m - 1) * 30 + d - 1


def meru_weekday(p):
    n = meru_ord(p)
    return None if n is None else WEEK[(n + 2) % 6]


def meru_market(p):
    y, m = p['y'], p['m']
    if y < 1 or not 1 <= m <= 12:
        return None
    last = meru_length(y, 12)
    return [d for d in range(1, meru_length(y, m) + 1)
            if not (m == 7 and d == 1) and not (m == 12 and d == last)
            and meru_weekday({'y': y, 'm': m, 'd': d}) in ('Bel', 'Dun')]


def meru_text(p):
    y, m = (int(part) for part in p['month'].split('-'))
    days = meru_market({'y': y, 'm': m})
    return 'invalid' if days is None else ','.join('%d-%d-%d' % (y, m, d) for d in days)


CAL = ('The Meru calendar: years are numbered from 1 and have 12 months. Months 1 to 11 have 30 days; '
       'month 12 has 35 days, or 36 in a leap year. A year is a leap year when it is divisible by 5, '
       'except that a year divisible by 30 is a leap year only when it is also divisible by 150. Day '
       'number 0 is year 1, month 1, day 1, and day numbers count consecutive days from there.')
WEEKDAYS = 'The Meru week has 6 days in the order Ash, Bel, Cor, Dun, Eld, Fen; day number 0 is a Cor.'
EXISTS = 'A date {y, m, d} exists when y >= 1, 1 <= m <= 12 and 1 <= d <= the length of month m in year y.'
MARKET = ('A market day is a day whose weekday is Bel or Dun, except the two festival days of every '
          'year, which are never market days: day 1 of month 7 and the last day of month 12.')

MERU = [
    ('meru-ord', 'base', [], None, meru_ord,
     'Write run(p). p = {"y", "m", "d"}: integer year, month and day. ' + CAL + ' ' + EXISTS
     + ' Return the day number of the date, or null if the date does not exist.',
     [{'y': 1, 'm': 1, 'd': 1}, {'y': 5, 'm': 12, 'd': 36}, {'y': 30, 'm': 12, 'd': 36},
      {'y': 150, 'm': 12, 'd': 36}],
     [{'y': 6, 'm': 1, 'd': 1}, {'y': 60, 'm': 12, 'd': 36}, {'y': 300, 'm': 12, 'd': 36},
      {'y': 0, 'm': 1, 'd': 1}, {'y': 1999, 'm': 7, 'd': 30}, {'y': 12, 'm': 11, 'd': 31},
      {'y': 12, 'm': 13, 'd': 1}, {'y': 31, 'm': 1, 'd': 1}]),
    ('meru-weekday', 'extend', ['meru-ord'], None, meru_weekday,
     'Write run(p). p = {"y", "m", "d"}: integer year, month and day. ' + CAL + ' ' + WEEKDAYS + ' '
     + EXISTS + ' Return the weekday name of the date as a string, or null if the date does not exist.',
     [{'y': 1, 'm': 1, 'd': 1}, {'y': 5, 'm': 12, 'd': 36}, {'y': 2, 'm': 2, 'd': 31}],
     [{'y': 30, 'm': 12, 'd': 35}, {'y': 30, 'm': 12, 'd': 36}, {'y': 150, 'm': 12, 'd': 36},
      {'y': 151, 'm': 1, 'd': 1}, {'y': 7, 'm': 0, 'd': 3}, {'y': 1000, 'm': 6, 'd': 15},
      {'y': 61, 'm': 1, 'd': 1}]),
    ('meru-market', 'compose', ['meru-ord', 'meru-weekday'], None, meru_market,
     'Write run(p). p = {"y", "m"}: integer year and month. ' + CAL + ' ' + WEEKDAYS + ' ' + MARKET
     + ' Return the list of the day-of-month numbers of the market days in month m of year y, in '
     'increasing order, or null if y < 1 or m is not 1 to 12.',
     [{'y': 1, 'm': 1}, {'y': 7, 'm': 12}, {'y': 2, 'm': 7}],
     [{'y': 30, 'm': 12}, {'y': 150, 'm': 12}, {'y': 300, 'm': 7}, {'y': 7, 'm': 13}, {'y': 0, 'm': 1},
      {'y': 1234, 'm': 5}, {'y': 31, 'm': 7}, {'y': 15, 'm': 12}]),
    ('meru-text', 'variant', ['meru-market'], 'meru-market', meru_text,
     'Write run(p). p = {"month": "Y-M"}, a string of two non-negative decimal integers joined by '
     '"-": a year Y and a month M. ' + CAL + ' ' + WEEKDAYS + ' ' + MARKET + ' Return one string '
     'that lists the market days of month M of year Y in increasing order, each written "Y-M-D" in '
     'decimal without padding, joined by ","; return the string "invalid" if Y < 1 or M is not 1 '
     'to 12.',
     [{'month': '2-7'}, {'month': '4-12'}, {'month': '3-13'}],
     [{'month': '30-12'}, {'month': '150-12'}, {'month': '0-4'}, {'month': '300-7'},
      {'month': '61-2'}, {'month': '1234-5'}, {'month': '36-7'}]),
]

CHAINS = [('kal', 'lang', KAL), ('meru', 'calendar', MERU)]


def build():
    stream, holdout, references = [], {}, {}
    for chain, family, tasks in CHAINS:
        for level, (task_id, role, needs, target, fn, request, visible, hidden) in enumerate(tasks, 1):
            task = {'id': task_id, 'chain': chain, 'family': family, 'level': level, 'role': role,
                    'needs': needs, 'request': request,
                    'checks': [{'input': x, 'expected': fn(x)} for x in visible]}
            if target is not None:
                task['target'] = target
            stream.append(task)
            holdout[task_id] = [{'input': x, 'expected': fn(x)} for x in hidden]
            references[task_id] = fn
    return stream, holdout, references


def discriminative(stream, references):
    """No earlier reference in the chain passes a later task's visible checks (generation rule G6)."""
    problems = []
    for index, task in enumerate(stream):
        for earlier in stream[:index]:
            if earlier['chain'] != task['chain']:
                continue
            passes = True
            for check in task['checks']:
                try:
                    passes = passes and references[earlier['id']](check['input']) == check['expected']
                except Exception:
                    passes = False
            if passes:
                problems.append(f'{earlier["id"]} passes the visible checks of {task["id"]}')
    return problems


def main(out):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from kuttaka_runtime.c3 import lint_stream
    stream, holdout, references = build()
    problems = discriminative(stream, references) + lint_stream(stream, holdout, sealed=False)
    if problems:
        raise SystemExit('\n'.join(problems))
    out = Path(out)
    (out / 'dev_stream.json').write_text(json.dumps(stream, indent=1) + '\n')
    (out / 'dev_holdout.json').write_text(json.dumps(holdout, indent=1) + '\n')
    print(len(stream), 'tasks;', sum(len(v) for v in holdout.values()), 'holdout cases')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
