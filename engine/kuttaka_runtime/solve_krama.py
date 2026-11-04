"""Krama: the step after the step, taken by Kuttaka. The model writes one step; Kuttaka takes every step after it.

Many tasks are answered by a sequence of moves from a start: a simulation (one next state each time) or a search
for the fewest moves (several). Asked for the whole method, the model must write the loop, the memory of the states
already seen and the order of the search, as well as the move, in one thought (first-hand session k, 2026-11-02:
the move was checked as a piece, and the loop that compounds it was still the model's). Here the model writes only
the step, as Python source defining:

    start(input)               -> state     the first state (JSON)
    moves(input, state)        -> [states]  the states one move away (one, for a simulation)
    done(input, state)         -> bool      whether this state answers the task
    answer(input, state, moves)-> result    the answer from the first done state, reached in `moves` moves
    exhausted(input)  (optional) -> result  the answer when no reachable state is done (default null)

Kuttaka takes the moves breadth first, so the first done state is reached by the fewest moves; it remembers every
state it has seen (a state is never expanded twice, so a walk that comes back on itself ends), keeps for each state
the one it came from, and stops at a state limit. The assembled method is checked like any other. When it fails a
check, the path it took there (start, each state after it, the done state) is shown to the model, so the wrong step
can be found and taken again: the memory of the steps is outside the thinker.
"""

STATE_LIMIT = 200000
REQUIRED = ('start', 'moves', 'done', 'answer')

KRAMA_RUN = '''import json

_STEPS = %r
_STATE_LIMIT = %d
_steps = {'__name__': '__kuttaka_krama__'}
exec(compile(_STEPS, '<steps>', 'exec'), _steps)


def _key(state):
    return json.dumps(state, sort_keys=True, allow_nan=False)


def _walk(input):
    first = _steps['start'](input)
    came_from = {_key(first): None}   # every state seen, and the state it was reached from
    frontier, moves = [_key(first)], 0
    while frontier:
        for key in frontier:
            if _steps['done'](input, json.loads(key)):
                return key, moves, came_from
        following = []
        for key in frontier:
            for state in _steps['moves'](input, json.loads(key)):   # a copy: a move cannot change a kept state
                new = _key(state)
                if new not in came_from:
                    came_from[new] = key
                    following.append(new)
        if len(came_from) > _STATE_LIMIT:
            raise RuntimeError('krama: more states than the limit')
        frontier, moves = following, moves + 1
    return None, moves, came_from


def run(input):
    key, moves, _ = _walk(input)
    if key is None:
        return _steps['exhausted'](input) if 'exhausted' in _steps else None
    return _steps['answer'](input, json.loads(key), moves)


def path(input):
    """The states from the start to the first done state (at most the first and last 20), for the model to read."""
    key, moves, came_from = _walk(input)
    if key is None:
        return {'done': False, 'states_seen': len(came_from)}
    chain = []
    while key is not None:
        chain.append(json.loads(key))
        key = came_from[key]
    chain.reverse()
    shown = chain if len(chain) <= 40 else chain[:20] + ['...'] + chain[-20:]
    return {'done': True, 'moves': moves, 'path': shown}
'''

TRACE_RUN = KRAMA_RUN.replace('\ndef run(input):', '\ndef _run(input):') + '''
import signal
import time
# Each check has its own time, as in a probe batch (DIFFERENTIAL_RUN): a wrong step whose walk grows to the state
# limit on several checks used to spend the sandbox's whole time, and the feedback on every check was lost with it
# (the runner's Docker gate at a0cbfc2: about 8 s here, past 20 s on a machine twice as slow).
_BUDGET, _RESERVE = 15.0, 0.2


class _OutOfTime(BaseException):
    pass


def _alarm(signum, frame):
    raise _OutOfTime()


def _traced(value):
    """The result and the path from one walk (the path is read from the states it kept, not walked again)."""
    key, moves, came_from = _walk(value)
    if key is None:
        got = _steps['exhausted'](value) if 'exhausted' in _steps else None
        trace = {'done': False, 'states_seen': len(came_from)}
    else:
        got = _steps['answer'](value, json.loads(key), moves)
        chain = []
        while key is not None:
            chain.append(json.loads(key))
            key = came_from[key]
        chain.reverse()
        trace = {'done': True, 'moves': moves, 'path': chain if len(chain) <= 40 else chain[:20] + ['...'] + chain[-20:]}
    return json.loads(json.dumps(got, allow_nan=False)), trace


def run(inputs):
    out = []
    started = time.monotonic()
    try:
        signal.signal(signal.SIGALRM, _alarm)
        timed = True
    except Exception:
        timed = False
    for number, value in enumerate(inputs):
        left = _BUDGET - (time.monotonic() - started) - _RESERVE * (len(inputs) - number - 1)
        if timed and left <= 0:
            out.append({'timeout': True})
            continue
        entry = {'timeout': True}
        try:
            try:
                if timed:
                    signal.setitimer(signal.ITIMER_REAL, left, 0.05)
                got, trace = _traced(value)
                entry = {'got': got, 'trace': trace}
            except _OutOfTime:
                entry = {'timeout': True}
            except Exception as error:
                entry = {'raised': type(error).__name__}
            finally:
                if timed:
                    signal.setitimer(signal.ITIMER_REAL, 0)
        except _OutOfTime:
            pass   # a repeat of the stop, delivered while the cut walk's states were freed
        out.append(entry)
    return out
'''

def defines(source, name):
    """Whether `source` defines a top-level function `name` (read as data, never executed here)."""
    import ast
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    return any(isinstance(node, ast.FunctionDef) and node.name == name for node in tree.body)


def assemble(steps):
    """The walk's source, or None when the step lacks a required function."""
    if not isinstance(steps, str) or not all(defines(steps, name) for name in REQUIRED):
        return None
    return KRAMA_RUN % (steps, STATE_LIMIT)


def trace(steps):
    """A sandbox job's source that runs the walk on a list of inputs: each one's result and the path it took."""
    if assemble(steps) is None:
        return None
    return TRACE_RUN % (steps, STATE_LIMIT)
