"""Vallī: the kuṭṭaka's own reduction, as a mechanism for any task that reduces to smaller instances of itself.

In the kuṭṭaka, reciprocal division turns the problem into one of the same form with smaller
numbers, the chain of quotients (vallī) is kept, the bottom is solved where little is unknown, and
the answer is rebuilt up the chain by a fixed rule; the division must end, because the remainders
shrink. Here the model writes only the reduction step, as Python source defining:

    measure(input) -> int >= 0      the size that must shrink
    small(input)   -> bool          the bottom: inputs small enough to compute directly
    reduce(input)  -> [inputs]      smaller inputs of the same form
    combine(input, answers) -> ...  the answer for input from the answers for reduce(input), in order
    base(input)    (optional)       the bottom's answer, when no reference is given

Kuttaka assembles the recursion. It checks at run time that every reduced input has a strictly
smaller measure (so the recursion is well founded and ends), keeps every answer it computed (the
chain, so shared smaller inputs are computed once), and computes the bottom with the planner's
reference, exact on small inputs by its own contract, rather than with code the model wrote for
it. Each answer in the chain is handed out as a copy, so a combine that changes an answer in place
cannot change another's; an input that is not small must reduce to at least one smaller input, so
every chain ends at the bottom. The assembled method is then checked like any other.
"""

VALLI_RUN = '''import copy
import json
import pickle
import sys

_REDUCTION = %r
_BOTTOM = %r
_BOTTOM_NAME = %r
_CALL_LIMIT = %d

sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
_steps = {'__name__': '__kuttaka_valli__'}
exec(compile(_REDUCTION, '<reduction>', 'exec'), _steps)
_bottom = _steps
if _BOTTOM:
    _bottom = {'__name__': '__kuttaka_valli_bottom__'}
    exec(compile(_BOTTOM, '<bottom>', 'exec'), _bottom)
_chain = {}
_calls = [0]


def _freeze(answer):
    try:
        return ('pickle', pickle.dumps(answer, protocol=pickle.HIGHEST_PROTOCOL))   # exact, every type, fast
    except Exception:
        return ('copy', copy.deepcopy(answer))


def _thaw(frozen):
    kind, value = frozen
    return pickle.loads(value) if kind == 'pickle' else copy.deepcopy(value)


def run(input):
    key = json.dumps(input, sort_keys=True)
    if key in _chain:
        return _thaw(_chain[key])   # each caller gets its own copy: combine may change it in place
    _calls[0] += 1
    if _calls[0] > _CALL_LIMIT:
        raise RecursionError('valli: the reduction exceeded its call limit')
    if _steps['small'](input):
        answer = _bottom[_BOTTOM_NAME](input)
    else:
        size = _steps['measure'](input)
        if type(size) is not int or size < 0:
            raise ValueError('valli: measure must be a non-negative integer')
        parts = list(_steps['reduce'](input))
        if not parts:
            raise ValueError('valli: an input that is not small must reduce to at least one smaller input')
        for part in parts:
            smaller = _steps['measure'](part)
            if type(smaller) is not int or not 0 <= smaller < size:
                raise ValueError('valli: a reduced input is not smaller than its input')
        answer = _steps['combine'](input, [run(part) for part in parts])
    _chain[key] = _freeze(answer)   # a snapshot: what combine later does to `answer` cannot reach it
    return answer
'''

CALL_LIMIT = 200000
REQUIRED = ('measure', 'small', 'reduce', 'combine')


def defines(source, name):
    """Whether `source` defines a top-level function `name` (read as data, never executed here)."""
    import ast
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    return any(isinstance(node, ast.FunctionDef) and node.name == name for node in tree.body)


def assemble(reduction, reference=None):
    """The recursion's source, or None when the reduction lacks a required step or the bottom has no method."""
    if not isinstance(reduction, str) or not all(defines(reduction, name) for name in REQUIRED):
        return None
    if reference and defines(reference, 'reference'):
        return VALLI_RUN % (reduction, reference, 'reference', CALL_LIMIT)
    if defines(reduction, 'base'):
        return VALLI_RUN % (reduction, '', 'base', CALL_LIMIT)
    return None


STEP_RUN = '''import copy
import json

_steps = {'__name__': '__kuttaka_valli_step__'}
exec(compile(%r, '<reduction>', 'exec'), _steps)
_ref = {'__name__': '__kuttaka_valli_reference__'}
exec(compile(%r, '<reference>', 'exec'), _ref)


def _json(value):
    return json.loads(json.dumps(value, allow_nan=False))


def run(inputs):
    out = []
    for value in inputs:
        try:
            parts = list(_steps['reduce'](copy.deepcopy(value)))
            size = _steps['measure'](value)
            smaller = [_steps['measure'](part) for part in parts]
        except Exception as error:
            out.append({'skip': 'reduce or measure raised ' + type(error).__name__})
            continue
        if not parts or type(size) is not int or not all(type(m) is int and 0 <= m < size for m in smaller):
            out.append({'skip': 'no strictly smaller parts'})
            continue
        try:
            answers = [_ref['reference'](copy.deepcopy(part)) for part in parts]
            direct = _ref['reference'](copy.deepcopy(value))
        except Exception as error:
            out.append({'skip': 'the reference raised ' + type(error).__name__})
            continue
        try:
            combined = _steps['combine'](copy.deepcopy(value), copy.deepcopy(answers))
        except Exception as error:
            out.append({'parts': _json(parts), 'answers': _json(answers), 'direct': _json(direct),
                        'raised': type(error).__name__})
            continue
        out.append({'parts': _json(parts), 'answers': _json(answers), 'direct': _json(direct),
                    'combined': _json(combined)})
    return out
'''


def step_check(reduction, reference):
    """The source of a sandbox job that tests the reduction step itself, the kuṭṭaka's invariant at each division:
    on an input where reduce gives strictly smaller parts, combine(input, the reference's answers for the parts)
    must equal the reference's answer for the input. The recursion applies the step only above the bottom, so on
    checks that all sit at the bottom a wrong step passes every check; this test reaches it on the same small
    inputs. None without a reference (a model-written base is not a second voice)."""
    if not (isinstance(reduction, str) and isinstance(reference, str) and defines(reference, 'reference')
            and all(defines(reduction, name) for name in REQUIRED)):
        return None
    return STEP_RUN % (reduction, reference)
