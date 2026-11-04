"""Prastāra with bhāvanā: every well-formed small composition of stored methods, listed in a fixed order.

Piṅgala's prastāra lists every metre of a given length in a fixed order, with a way to find a
pattern's place in the list (uddiṣṭa) and the pattern at a place (naṣṭa). Here the patterns are
recipes over stored methods (`composition.py` compiles them). Brahmagupta's bhāvanā composes two
solutions into a third by a fixed law; the law here is the form of the data: a method may be
applied to a value only if the value has the form of the inputs the method was checked on, and a
method may be mapped over a list only if the list's elements have that form. Each method's input and
output forms come from the checks it passed (pramana.shape).

Typing makes the space small enough to list in full. Every recipe up to MAX_SIZE nodes whose form
is the task's output form is listed, by size, then by the methods' order, then by input field, each
with its place. `recipes` says whether the list is complete (every well-formed recipe up to
MAX_SIZE) or was cut at the limit. Nothing here calls a model or runs code; the Solver screens each
recipe on every check input and qualifies only one that matches every check.

This is a complete search over a small, stated space (rule S10 with S4 and S7 of
research/kuttaka_method/RECONSTRUCTION.md), not program synthesis: a task whose answer is not a
small composition of stored methods is not found here.
"""
import json

INPUT = {'op': 'input'}
MAX_SIZE = 5
TERM_LIMIT = 4000   # terms built, of any form, before the list is reported cut (a very wide input form)


def _form(shape):
    return json.dumps(shape, sort_keys=True)


def _elements(shape):
    """The element forms of a list form, or None when the form is not a list."""
    if isinstance(shape, list) and shape[:1] == ['list']:
        return [_form(item) for item in shape[1]]
    return None


def describe(expression, names):
    """A readable formula for a recipe expression: total(map(square, input.values))."""
    op = expression['op']
    if op == 'input':
        return 'input'
    if op == 'get':
        return f'{describe(expression["value"], names)}.{expression["key"]}'
    if op == 'call':
        return f'{names.get(expression["method"], expression["method"][:8])}({describe(expression["arg"], names)})'
    if op == 'map':
        return f'map({names.get(expression["method"], expression["method"][:8])}, {describe(expression["arg"], names)})'
    if op == 'array':
        return '[' + ', '.join(describe(item, names) for item in expression['items']) + ']'
    return op


def recipes(methods, in_shape, out_shape, limit, max_size=MAX_SIZE, tried=()):
    """(complete, [(place, formula, recipe)]) for every well-formed recipe up to `max_size` nodes.

    `methods` is a list of (artifact id, name, input shape, output shape) in recall order; the
    shapes are pramana.shape values. A single method in `tried` applied to the whole input is left
    out (reuse as it is has run it on these checks); every other one is listed. `complete` is False
    when the list was cut at `limit`.
    """
    names = {m: name for m, name, _, _ in methods}
    typed = [(m, _form(i), _form(o), o) for m, _, i, o in methods]
    want = _form(out_shape)
    out, seen = [], set()

    def uses_a_method(expression):
        return expression['op'] in ('call', 'map') or any(
            isinstance(v, dict) and 'op' in v and uses_a_method(v) for v in expression.values()) or any(
            uses_a_method(item) for item in expression.get('items', []))

    def offer(expression, form):
        """List one recipe if it has the task's output form and runs a stored method; False at the limit."""
        if form != want or expression == INPUT or not uses_a_method(expression):
            return True   # plumbing alone ([input, input], input.w) is not a composition of stored methods
        if expression['op'] == 'call' and expression['arg'] == INPUT and expression['method'] in tried:
            return True   # reuse as it is ran this method on the whole input, on these checks
        key = json.dumps(expression, sort_keys=True)
        if key in seen:
            return True
        if len(out) >= limit:
            return False
        seen.add(key)
        out.append((len(out), describe(expression, names), {'version': 1, 'output': expression}))
        return True

    terms = {1: [(INPUT, _form(in_shape), in_shape)]}
    for size in range(2, max_size + 1):
        level = []
        for expression, form, shape in terms[size - 1]:
            if isinstance(shape, dict):   # a field of the input, or of a result that has fields
                level += [({'op': 'get', 'value': expression, 'key': k}, _form(v), v) for k, v in sorted(shape.items())]
        for expression, form, shape in terms[size - 1]:
            elements = _elements(shape)
            for m, method_in, method_out, out_value in typed:
                if form == method_in:
                    level.append(({'op': 'call', 'method': m, 'arg': expression}, method_out, out_value))
                if elements is not None and elements in ([method_in], []):
                    mapped = ['list', [out_value]]
                    level.append(({'op': 'map', 'method': m, 'arg': expression}, _form(mapped), mapped))
        for left in range(1, size - 1):   # two results side by side: left + right + 1 = size
            right = size - 1 - left
            # Any two terms side by side (the input, a field, a call, a map; the same one twice), in both
            # orders: [f(x), g(x)] and [g(x), f(x)] differ.
            for a, fa, sa in terms[left]:
                for b, fb, sb in terms[right]:
                    pair = ['list', [json.loads(f) for f in sorted({fa, fb})]]
                    level.append(({'op': 'array', 'items': [a, b]}, _form(pair), pair))
        terms[size] = level
        for expression, form, _ in level:   # a level built is always offered in full
            if not offer(expression, form):
                return False, out
        if size < max_size and sum(len(t) for t in terms.values()) > TERM_LIMIT:
            return False, out   # the next level is not built: cut, and said so
    return True, out
