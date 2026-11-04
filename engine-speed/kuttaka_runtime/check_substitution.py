"""The substitution test: a literal written for one input, found by running the method, not by reading it.

Whether a constant in a method is part of its rule or a copy of one check's answer cannot be decided from its
text, and the forms it can take are endless (four reviews found a class body, a `match` pattern, a call around a
conditional, a guard on a number's text, a table keyed by a tuple, ...: `check_memorization.py` reads the
common ones). It can be tested, the way the tradition tests a result by putting it back into the conditions
(S4): change the constant to a value nothing else uses, run the method again on the check inputs and on inputs
around them, and see where its answers change.

  - A constant equal to that check's answer whose change alters the answer on exactly one check's input, and on
    no other input tried, is a literal written for that input, however it is written (a named constant, a table
    filled by assignment, `n == 7 and 22 or ...`). So is a guard standing alone (`year == 1900`, `year = 2000`,
    `3 * n + (n == 7)`) that changes that input alone. A guard that is one member of a collection written out
    counts only when, changed, that input gets an answer no other input gives: a member of `m in (4, 6, 9, 11)`
    falls back to what the other months get, and a sibling put in its place gets what it gets (a value padded in
    beside a special case, `year in (1900, -1)`, shares nothing).
  - A constant of the rule changes the answer on many inputs (`3 * n`, `28` for February in every year), and
    a base case changes it on every input that recurses down to it (`if n == 3: return 4` in a staircase
    count): neither is a special case.
  - A finite map over the whole known domain (every input tried that has an answer is answered by its own
    constant, as in a rock-paper-scissors table) is read as logic when every check it answers rests on the
    reference or two derivations; otherwise it cannot be told from a table of the checks, and is flagged.

The inputs tried are the check inputs, the small inputs, and for each check in turn (so that a check late in a
long list has inputs of its own): inputs near it (pramana.near_inputs), its numbers doubled and tripled (a rule
such as `n % 15 == 0` shows on 30 and 45), its texts with their digits moved ("2024-03-01" -> "2025-03-01",
"4048-03-01"), and inputs made by putting the source's own constants in place of its numbers or text (so that a
membership `m in (4, 6, 9, 11)` is seen answering 6, 9 and 11 too). A check whose input holds only small values
(-1, 0, 1, 2, empty), or whose expected value is one, is an ordinary edge case and is not judged.

The constants tried are those equal to a value of a check (a leaf of its input or of its expected value, or its
text). The original is run twice; an input on which it does not repeat itself, or that times out, is not used,
and a timeout is no evidence of a change. Everything runs in one sandbox execution, with a time limit per call
(re-fired while a method swallows it, then the whole run ends) and for the whole (checked on every call); a
test cut short judges nothing.

What the test finds is a question about the method, not a verdict (solve.py, `_memorized`). It cannot tell a
correct table entry ("h": 3600, used by the one check that names it) from a copied wrong check (7 -> 22), and a
literal is a warning about the method around it more than about the check: a wrong method patches even a correct
check where its own rule fails (a leap-year patch for 2024 in a method that knows no leap years). So the blind
auditor is asked about the check's input (unless the user, the reference or two derivations support it) and about
a few inputs made from it that the method answers by its own rule (`around`, with the method's answers from its
first plain run). The method stands only if the auditor agrees with it everywhere it answered; disputed, it is set
aside, refuted if written in this job, and the dispute is kept for the task. It never clears what the text
reading found, and it gives no check any support. On the tenth and eleventh reviews' corpora of 74 correct,
ordinary methods it asks on 17, and all 74 are delivered (tests/test_substitution_corpus.py).

This module only prepares the run and reads its result. The method runs in the sandbox, never here.
"""
import ast
import json

from .check_memorization import _leaves, _nontrivial, _same
from .pramana import near_inputs

MAX_CONSTANTS = 24     # constants substituted in one test; more and the test judges nothing
MAX_PROBES = 48        # inputs tried
CONSTANT_PROBES = 12   # of which made from the source's own constants
PER_CALL = 0.5         # seconds per run of the method on one input
BUDGET = 12.0          # seconds for the whole test (the sandbox allows 20)

RUN = r'''import ast, hashlib, json, os, signal, time


class _Stop(BaseException):
    pass


_FIRED = [0]


def _alarm(signum, frame):
    # The timer fires again every 50 ms while a call runs on: a method that swallows the stop (`except
    # BaseException`) gets it again, and after a few the whole test ends at once (it judges nothing then).
    _FIRED[0] += 1
    if _FIRED[0] > 6:
        os._exit(3)
    raise _Stop()


def _mark(entry):
    if 'value' in entry:
        return 'v' + hashlib.sha256(json.dumps(entry['value'], sort_keys=True).encode()).hexdigest()[:16]
    return 'e' + entry['error']


def _substituted(source, where):
    if where is None:
        return compile(source, '<method>', 'exec')
    tree = ast.parse(source)
    line, column, sentinel = where
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.lineno == line and node.col_offset == column:
            node.value = sentinel
            return compile(tree, '<method>', 'exec')
    raise LookupError('constant not found')


def _timed(call):
    _FIRED[0] = 0
    signal.setitimer(signal.ITIMER_REAL, PER_CALL, 0.05)
    try:
        return call()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def run(payload):
    global PER_CALL
    sources, inputs, variants = payload['sources'], payload['inputs'], payload['variants']
    PER_CALL, deadline = payload['per_call'], time.monotonic() + payload['budget']
    signal.signal(signal.SIGALRM, _alarm)
    rows, values = [], []
    for variant in variants:
        if time.monotonic() > deadline:
            break
        target = variant[0] if variant else None
        loaded = {}

        def use(artifact, target=target, loaded=loaded):
            if artifact not in loaded:
                space = {'__name__': '__method__', 'use': use}
                where = variant[1:] if target == artifact else None
                exec(_substituted(sources[artifact], where), space)
                loaded[artifact] = space['run']
            return loaded[artifact]
        row = []
        try:
            space = {'__name__': '__kuttaka_substitution__', 'use': use}
            code = _substituted(sources[''], variant[1:] if target == '' else None)
            _timed(lambda: exec(code, space))
            method, failed = space['run'], None
        except _Stop:
            method, failed = None, 'timeout'
        except BaseException as error:
            method, failed = None, type(error).__name__
        timeouts = 0
        for n, value in enumerate(inputs):
            if time.monotonic() > deadline:
                return {'rows': rows, 'values': values}   # cut short: the test judges nothing
            if method is None:
                row.append('e' + failed)
                continue
            if (rows and rows[0][n] == 'etimeout') or timeouts >= 2:
                row.append('etimeout')   # not run: the original times out here, or this variant already did twice
                continue
            try:
                result = _timed(lambda: method(json.loads(json.dumps(value))))
                result = json.loads(json.dumps(result, allow_nan=False))
                row.append(_mark({'value': result}))
                if not rows:   # the method's own answers, from its first plain run, when small
                    text = json.dumps(result)
                    values.append(result if len(text) <= 2000 else None)
            except _Stop:
                row.append('etimeout')
                timeouts += 1
            except BaseException as error:
                row.append('e' + type(error).__name__)
            if not rows and len(values) < len(row):
                values.append(None)
        rows.append(row)
    return {'rows': rows, 'values': values}
'''


# bytes of the payload: kept at the old 32 KiB job size, which bounds the variants' sandbox time
MAX_PAYLOAD = 32768 - len(json.dumps({'source': RUN, 'input': None})) - 1024


def _key(value):
    return json.dumps(value, sort_keys=True)


def _sentinel(value):
    """A value of the same kind nothing else in a task is likely to hold."""
    if isinstance(value, int):
        return value + 104729 if value >= 0 else value - 104729
    if isinstance(value, float):
        return value * 1.5 + 0.7371
    return value + '⁣k'


def _docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def _constants(source):
    """(line, column, value, siblings) of every number or text constant outside docstrings and f-strings;
    siblings are the other values of the collection written out it is one item of (a tuple, list or set of two
    or more, or a key of a table of two or more), or None."""
    tree = ast.parse(source)
    skip = _docstrings(tree)
    groups = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            skip.update(id(part) for part in ast.walk(node))
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)) and len(node.elts) >= 2:
            items = node.elts
        elif isinstance(node, ast.Dict) and len(node.keys) >= 2:
            items = [k for k in node.keys if k is not None]
        else:
            continue
        values = [e.value for e in items if isinstance(e, ast.Constant)]
        for e in items:
            if isinstance(e, ast.Constant):
                groups[id(e)] = [v for v in values if v is not e.value]
    return [(node.lineno, node.col_offset, node.value, groups.get(id(node))) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and id(node) not in skip
            and isinstance(node.value, (int, float, str)) and not isinstance(node.value, bool)]


def _check_values(checks):
    values = []
    for check in checks:
        for leaf in _leaves(check['input']) + _leaves(check['expected']):
            if _nontrivial(leaf):
                values.append(leaf)
                if isinstance(leaf, (int, float)):
                    values.append(str(leaf))
                elif isinstance(leaf, str):
                    try:
                        values.append(int(leaf))
                    except ValueError:
                        pass
    return values


def _paths(value, path=()):
    """Paths to the non-trivial numbers and texts inside an input."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float, str)):
        if _nontrivial(value) or isinstance(value, (int, float)):
            yield path, value
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from _paths(item, path + (name,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _paths(item, path + (index,))


def _text_near(text):
    """Texts near one that holds numbers: each run of digits moved up by one and doubled, its width kept
    ("2024-03-01" -> "2025-03-01", "4048-03-01", "2024-04-01", ...), so a date, a time or a size has
    neighbours as a number does."""
    import re
    out = []
    for match in re.finditer(r'\d+', text):
        digits = match.group()
        for moved in (int(digits) + 1, int(digits) * 2):
            new = str(moved).zfill(len(digits))
            out.append(text[:match.start()] + new + text[match.end():])
    return [t for t in dict.fromkeys(out) if t != text]


def _replaced(value, path, new):
    if not path:
        return new
    head, rest = path[0], path[1:]
    if isinstance(value, dict):
        return {k: (_replaced(v, rest, new) if k == head else v) for k, v in value.items()}
    return [_replaced(v, rest, new) if i == head else v for i, v in enumerate(value)]


def prepare(sources, checks, known=()):
    """The payload of one test, or None when there is nothing to test or it cannot be held.

    `sources` maps '' to the method's source and each stored method it runs to its own; `known` names the
    small inputs. Returns {'payload', 'constants': [(source key, line, column, value)], 'inputs'}."""
    try:
        constants = {key: _constants(text) for key, text in sources.items()}
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    wanted = _check_values(checks)
    def matches(value, w):
        if isinstance(value, str) or isinstance(w, str):
            return isinstance(value, str) and isinstance(w, str) and value == w
        return _same(value, w)
    targets = [(key, line, column, value) for key, found in constants.items() for line, column, value, _ in found
               if _nontrivial(value) and any(matches(value, w) for w in wanted)]
    group = {(key, line, column): g for key, found in constants.items() for line, column, _, g in found}
    if not targets or len(targets) > MAX_CONSTANTS:
        return None
    inputs, origin, seen = [], [], set()

    def add(value, source=None):
        key = _key(value)
        if key not in seen and len(inputs) < MAX_PROBES:
            seen.add(key)
            inputs.append(value)
            origin.append(source)
        return key
    for i, check in enumerate(checks):
        add(check['input'], i)
    for value in known:
        add(value)
    # A member of a collection is tested with its siblings put in its place: a real membership's members share
    # an answer (`m in (4, 6, 9, 11)`: 6 gets what 4 gets); a value padded in beside a special case does not.
    siblings = []
    for key, line, column, value in targets:
        found = []
        for i, check in enumerate(checks):
            for path, leaf in _paths(check['input']):
                if matches(value, leaf):
                    for other in (group[(key, line, column)] or [])[:4]:
                        if isinstance(other, (int, float, str)) and not isinstance(other, bool):
                            found.append(add(_replaced(check['input'], path, other), i))
        siblings.append(found)
    pool = [value for found in constants.values() for _, _, value, _ in found if _nontrivial(value)]
    own = []
    for i, check in enumerate(checks):   # each check's own inputs: near, made, scaled, texts near, in turn
        made = [_replaced(check['input'], path, value) for path, leaf in _paths(check['input'])
                for value in pool if type(value) is type(leaf) and not _same(value, leaf)]
        near = near_inputs([check['input']])
        scaled = [_replaced(check['input'], path, leaf * k) for k in (2, 3) for path, leaf in _paths(check['input'])
                  if isinstance(leaf, int) and not isinstance(leaf, bool) and leaf > 2]   # a rule shows on multiples
        texts = [_replaced(check['input'], path, t) for path, leaf in _paths(check['input'])
                 if isinstance(leaf, str) for t in _text_near(leaf)]
        kinds = [near, made, scaled, texts]
        own.append([v for row in range(max(map(len, kinds), default=0)) for v in (k[row] for k in kinds if row < len(k))])
    # Every check gets its share in turn (a check late in a long list is not left without inputs of its own).
    mixed = [(row[n], i) for n in range(max(map(len, own), default=0)) for i, row in enumerate(own) if n < len(row)]
    for value, i in mixed:
        if len(inputs) >= MAX_PROBES:
            break
        add(value, i)
    variants = [[], []] + [[key, line, column, _sentinel(value)] for key, line, column, value in targets]
    payload = {'sources': sources, 'inputs': inputs, 'variants': variants, 'per_call': PER_CALL, 'budget': BUDGET}
    try:
        size = len(json.dumps(payload, allow_nan=False))
    except (TypeError, ValueError):
        return None
    if size > MAX_PAYLOAD:
        return None
    return {'payload': payload, 'constants': targets, 'inputs': inputs, 'origin': origin,
            'siblings': [[k for k in found if k in seen] for found in siblings]}


def read(prepared, result, checks, supported=()):
    """What the test shows: {'judged': [check inputs judged], 'special': [(check, source key)], 'around':
    {check input key: [(input, the method's answer)]}}, or None when the run was cut short or malformed (nothing
    is judged). `around` holds, for each check, inputs made from it on which the method answered, with its
    answers: what a question about the method around a literal asks about."""
    constants, inputs = prepared['constants'], prepared['inputs']
    rows = result.get('rows') if isinstance(result, dict) else None
    values = result.get('values') if isinstance(result, dict) else None
    if (not isinstance(rows, list) or len(rows) != len(constants) + 2
            or any(not isinstance(r, list) or len(r) != len(inputs) or any(not isinstance(x, str) for x in r)
                   for r in rows)):
        return None
    if not isinstance(values, list) or len(values) != len(inputs):
        values = [None] * len(inputs)
    first, again = rows[0], rows[1]
    stable = {i for i in range(len(inputs)) if first[i] == again[i] and first[i] != 'etimeout'}
    # A timeout is no evidence; a variant with any timeout where the original answered is incomplete and gives no
    # evidence at all (its missing answers could make a change look like one input's alone).
    complete = [all(row[i] != 'etimeout' for i in stable) for row in rows[2:]]
    changed = [{i for i in stable if row[i] != first[i]} if ok else set() for row, ok in zip(rows[2:], complete)]
    index = {_key(v): i for i, v in enumerate(inputs)}
    siblings = prepared.get('siblings') or [[] for _ in constants]
    judged, special = [], []
    targets = []
    for check in checks:
        i = index.get(_key(check['input']))
        if (i is None or i not in stable or not _nontrivial(check['expected'])
                or not any(_nontrivial(leaf) for leaf in _leaves(check['input']))
                or len(stable - {i}) < 2):
            continue
        judged.append(check['input'])
        others = {first[k] for k in stable if k != i}
        # A constant that is this check's answer (a value of its expected) and changes this input alone is a
        # literal written for it; so is a guard standing alone (`year == 1900`, `year = 2000`) that changes it
        # alone. A guard that is one member of a collection counts unless the collection is a real membership:
        # changed, this input gets an answer other inputs give, and a sibling put in its place gets the same
        # answer this input gets (`m in (4, 6, 9, 11)`; not `year in (1900, -1)`, where -1 shares nothing).
        answers = [leaf for leaf in _leaves(check['expected']) if _nontrivial(leaf)]

        def membership(j):
            return rows[2 + j][i] in others and any(index.get(k) in stable and first[index[k]] == first[i]
                                                    for k in siblings[j])
        hit = [constants[j][0] for j, moved in enumerate(changed)
               if moved == {i} and (any(_same(constants[j][3], a) and type(constants[j][3]) is not str
                                        or constants[j][3] == a for a in answers)
                                    or not (siblings[j] and membership(j)))]
        if hit:
            targets.append((check, i, hit))
    answered = {i for i in stable if first[i].startswith('v')}
    alone = {i for moved in changed if len(moved) == 1 for i in moved}
    whole = (len(answered) >= 2 and answered <= alone
             and answered <= {index.get(_key(c['input'])) for c in checks}
             and all(any(_same(check['input'], s) for s in supported) for check, _, _ in targets))
    if not whole:
        for check, _, hit in targets:
            special.extend((check, key) for key in dict.fromkeys(hit))
    origin = prepared.get('origin') or [None] * len(inputs)
    check_keys = {_key(c['input']) for c in checks}
    around = {}
    for check in checks:
        i = index.get(_key(check['input']))
        around[_key(check['input'])] = [
            (inputs[k], values[k]) for k in range(len(inputs))
            if origin[k] == checks.index(check) and k in stable and first[k].startswith('v')
            and values[k] is not None and _key(inputs[k]) not in check_keys]
    return {'judged': judged, 'special': special, 'around': around}
