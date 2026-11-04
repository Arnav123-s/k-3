"""Pramāṇa: how much a check, and so a checked method, can be trusted, and where more evidence comes from.

Nyāya counts testimony as knowledge only from a trustworthy speaker. A check whose expected value the
model worked out by hand is testimony from a speaker known to make arithmetic mistakes; a value an
executed reference computed, or a property confirmed by substitution, is stronger; a value the user
gave is a given condition, as in the kuṭṭaka, and is never revised. Each check carries one support
label, and a delivered method reports the weakest support among the checks it passed.

Pure helpers only: the Solver runs the sandbox and the models.
"""
import json

MISSING = object()   # no value: a run that failed, timed out or was not attempted

# Support labels, strongest first. 'hand' is the model's own worked value with nothing else behind it.
GIVEN = 'given'                                   # the user's example, or a configured host oracle
REFERENCE = 'reference'                           # the planner's slow reference computes the same value
PROPERTY = 'property'                             # the planner's verify(input, output) accepts it
TWO_DERIVATIONS = 'two derivations'               # two independent derivations agree (a revision or an added check)
HAND = 'hand'
ORDER = (GIVEN, TWO_DERIVATIONS, REFERENCE, PROPERTY, HAND)


def key(check):
    """Support is attached to an (input, expected) pair: a revised check is a different pair."""
    return json.dumps([check['input'], check['expected']], sort_keys=True, separators=(',', ':'))


def summarize(labels):
    """The method's support: given if every check is given; otherwise the weakest label among the checks."""
    labels = list(labels)
    if not labels:
        return HAND
    counts = {label: labels.count(label) for label in ORDER if label in labels}
    weakest = max(counts, key=ORDER.index)
    return weakest


VARIED_CHARS = ('A', 'Z', '0', '9', ' ', '_', '-', '.')


def _near(v):
    if isinstance(v, bool):
        yield not v
    elif isinstance(v, int):
        yield v + 1
        yield v - 1
    elif isinstance(v, float):
        yield v + 1.0
        yield v - 1.0
    elif isinstance(v, str):
        yield v + 'x'
    elif isinstance(v, list):
        for i, item in enumerate(v):
            for miss in _near(item):
                yield v[:i] + [miss] + v[i + 1:]
    elif isinstance(v, dict):
        for key in sorted(v):
            for miss in _near(v[key]):
                yield dict(v, **{key: miss})


def near_misses(value, limit=8):
    """Values of the same shape as `value` (same types, list lengths and object keys) that differ from it in exactly
    one leaf: bool flipped; integer and float +1 and -1; text + "x"; null has none; lists by index, objects by sorted
    key; the first `limit`. A property that accepts the value and every near miss of it cannot tell a right output
    from a wrong one of that shape, so its acceptance supports nothing (ERDŐS-979-d: a verify that tested only the
    keys gave the placeholder {"n": 0, ...} property support). Written as a Kuttaka task by the builder, as the model:
    docs/reviews/first_hand_2026-11-02e (checked; 24 inputs beyond the checks agreed with the reference)."""
    from itertools import islice
    return list(islice(_near(value), limit))


def varied_inputs(inputs, limit=16):
    """Inputs of the same form as the given ones with their texts and lists varied, made without a model (numbers are
    moved by near_inputs): a text with a letter's case swapped, a digit, a space or a mark put in, reversed, shortened,
    lengthened; a list reversed, sorted, rotated, shortened, with an element repeated; every field of an object in
    turn. A method that is right on the checks but handles only part of its input's form (only lower-case letters,
    only sorted lists) is met on these by the reference and the property. An input outside the contract costs
    nothing: a value on which a side fails to run is never evidence. Interleaved across the inputs given.

    Order (2026-11-01, from W4-j's h-c1-regex): a text lengthened by its own last character comes first (`a*`
    becomes `a**`), then a letter's case swapped, a digit inserted, shortened and reversed, and other
    characters inserted. A field takes up to eight variants. Keeping both a change within the existing
    alphabet and a digit early prevents the field and probe caps from dropping either class of challenge."""
    def texts(t):
        out = []
        if t:
            middle = len(t) // 2
            out += [t + t[-1], t[0].swapcase() + t[1:], t[:middle] + '0' + t[middle:], t[:-1], t[::-1]]
            out += [t[:middle] + c + t[middle:] for c in VARIED_CHARS if c != '0']
        else:
            out += ['a', 'A0']
        return [x for x in dict.fromkeys(out) if x != t]

    def lists(xs):
        out = []
        if xs:
            out += [list(reversed(xs)), xs[1:] + xs[:1], xs[:-1], xs + xs[:1]]
            try:
                out.append(sorted(xs))
            except TypeError:
                pass
        else:
            out.append([])
        return [x for x in out if x != xs]

    def variants(value, depth=0):
        if depth > 3:
            return []
        if isinstance(value, str):
            return texts(value)
        if isinstance(value, list):
            per_item = [lists(value)] + [[value[:i] + [v] + value[i + 1:] for v in variants(item, depth + 1)[:3]]
                                         for i, item in enumerate(value[:4])]
            return [row[n] for n in range(max(map(len, per_item), default=0)) for row in per_item if n < len(row)]
        if isinstance(value, dict):   # the fields take turns, so a cap on probes still reaches every field
            per_field = [[dict(value, **{k: v}) for v in variants(item, depth + 1)[:8]] for k, item in value.items()]
            return [row[n] for n in range(max(map(len, per_field), default=0)) for row in per_field if n < len(row)]
        return []

    per_input = [variants(value) for value in inputs]
    out = []
    for round_ in range(max((len(v) for v in per_input), default=0)):
        out.extend(v[round_] for v in per_input if round_ < len(v))
    return out[:limit * max(1, len(inputs))]


def near_inputs(inputs, limit=8):
    """Inputs near the given ones, made without a model: each number in turn moved up a little.

    Positive integers become x+1 and 2x+1, other numbers x+1. Nothing becomes negative or shrinks,
    so sizes and ranges the checks respect are rarely left. An invalid input costs nothing: a value
    on which a side fails to run is never evidence."""
    out = []

    def leaves(value, path=()):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            yield path, value
        elif isinstance(value, dict):
            for name, item in value.items():
                yield from leaves(item, path + (name,))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from leaves(item, path + (index,))

    def replaced(value, path, new):
        if not path:
            return new
        head, rest = path[0], path[1:]
        if isinstance(value, dict):
            return {k: (replaced(v, rest, new) if k == head else v) for k, v in value.items()}
        return [replaced(v, rest, new) if i == head else v for i, v in enumerate(value)]

    per_input = []
    for value in inputs:
        near = []
        for path, number in list(leaves(value))[:limit]:
            moves = [number + 1, 2 * number + 1] if isinstance(number, int) and number > 0 else [number + 1]
            near.extend(replaced(value, path, move) for move in moves)
        per_input.append(near)
    # Interleaved, so that a cap on probes still reaches every check, not only the first few.
    for round_ in range(max((len(n) for n in per_input), default=0)):
        out.extend(near[round_] for near in per_input if round_ < len(near))
    return out


def shape(value):
    """The form of a JSON input, without its values: what paribhāṣā matches a stored method on."""
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, (int, float)):
        return 'num'
    if isinstance(value, str):
        return 'str'
    if value is None:
        return 'null'
    if isinstance(value, list):
        kinds = sorted({json.dumps(shape(v), sort_keys=True) for v in value})
        return ['list', [json.loads(k) for k in kinds]]
    if isinstance(value, dict):
        return {name: shape(item) for name, item in sorted(value.items())}
    return 'other'


def unify(a, b):
    """The one form two shapes share, or None. An empty list says nothing about its elements, so it
    unifies with any list; lists of different element forms unify to a list of both; objects with the
    same fields unify field by field."""
    if a == b:
        return a
    if isinstance(a, list) and isinstance(b, list) and a[:1] == b[:1] == ['list']:
        if not a[1]:
            return b
        if not b[1]:
            return a
        kinds = sorted({json.dumps(k, sort_keys=True) for k in a[1] + b[1]})
        return ['list', [json.loads(k) for k in kinds]]
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        out = {k: unify(a[k], b[k]) for k in a}
        return None if any(v is None for v in out.values()) else out
    return None


def _common(values):
    """The form every value shares (unified), else the form the most share (ties: first seen)."""
    shapes = [shape(v) for v in values]
    if not shapes:
        return None
    acc = shapes[0]
    for item in shapes[1:]:
        acc = unify(acc, item)
        if acc is None:
            texts = [json.dumps(x, sort_keys=True) for x in shapes]
            return max(dict.fromkeys(texts), key=texts.count)
    return json.dumps(acc, sort_keys=True)


def output_form(checks):
    """One signature for what a task returns, as `form` is for what it takes."""
    return _common([c['expected'] for c in checks])


def form(checks):
    """One signature for a task's inputs: the form all its check inputs share (pramana.unify)."""
    return _common([c['input'] for c in checks])


def part_forms(form_text):
    """The forms of the parts of an input of this form: each field, and the elements of each list.

    A method that takes a part of the input is a candidate piece of a composition (solve_prastara)."""
    if form_text is None:
        return []
    out = []

    def walk(value, depth):
        if depth > 2:
            return
        if isinstance(value, dict):
            for item in value.values():
                out.append(item)
                walk(item, depth + 1)
        elif isinstance(value, list) and value[:1] == ['list']:
            for item in value[1]:
                out.append(item)
                walk(item, depth + 1)

    walk(json.loads(form_text), 0)
    return list(dict.fromkeys(json.dumps(v, sort_keys=True) for v in out))
