"""One prompt in, a checked result out, with any local or hosted model.

    python -m kuttaka_runtime.solve "What is the 20th number that is both a square and a triangular number?"
    python -m kuttaka_runtime.solve stats

The model is never trained or changed, and it is never skipped: it understands the prompt and
writes or adapts the code. Kuttaka gives it the right material at each step and checks the result:
  1. the model turns the prompt into a contract for run(input) plus a few worked checks;
  2. Kuttaka offers the checked methods it already has for similar tasks; the model reuses them
     or writes new code (write, run the checks, read the failures, correct);
  3. if the result is wrong or only half right, Kuttaka retains admitted attempts on fixed checks, and
     the model splits the task around what already works, as the kuttaka reduces a problem
     to smaller ones (valli: keep the solved part, solve only the rest). Breakdowns that worked
     are kept and offered on similar tasks; if the model gives none, Kuttaka proposes the closest
     one. Subtasks that still fail are split again, up to three levels;
  4. checked methods, breakdowns and lessons from failures stay in a local workspace for later reuse;
     whether that reduces cost on related tasks remains an empirical question.

Verification is stated, never assumed: a result is "checked" only against the checks shown with
it. Checks the model proposed can themselves be wrong; give your own with --example, or confirm
them when asked. Whether mistakes and cost fall over time is measured by `stats`, not claimed.
"""

import argparse
import http.client
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid

from .artifacts import ArtifactMemory
from .campaign import _account_attempts
from .check_contracts import MAX_MODEL_CHECKS, MAX_PROPOSAL_ITEMS, CheckBook, CheckConflict
from . import check_substitution
from .check_memorization import judge as judge_memorized, memorized_checks, memorized_in_recipe
from .composition import compile_recipe, inspect_recipe
from .execution import PythonExecutor, preflight
from . import pramana
from .models import INSTRUCTIONS_C3, JsonModel
from .programs import C3_FIND_LIMIT, ProgramTask, ProgramTools
from .runtime import Budget, TransportFailure, investigate
from .store import Journal, canonical, digest
from .solve_progress import EvidenceFrontier, boundary_restatement_key, candidate_fingerprint, subproblem_key
from .solve_compose import compositions
from . import solve_krama, solve_prastara, solve_valli
from .solve_sketches import SketchCatalogue

PLAN_INSTRUCTIONS = '''You turn a user's task into a contract for a Python function run(input).
Return exactly one JSON object, without prose or Markdown fences:
{"contract": string, "checks": [{"input": JSON, "expected": JSON}, ...], "input": JSON or null,
 "verify": string or null, "reference": string or null, "small_inputs": [JSON, ...] or null,
 "large_inputs": string or null, "relations": string or null, "decomposition": object or null}
contract: one or two sentences saying what run(input) receives and returns. When the task states
the input or output format, keep it exactly: if it says run returns a number or a list, the
expected values are that bare number or list, never wrapped in an object. When the task allows several
right answers, the contract chooses one and says which (for example the smallest, or the first in order),
so that every check has one expected value.
checks: 3 to 6 small cases whose expected results you worked out by hand and are sure of,
including one edge case, and with at least two different expected results (if every small case of
the task has the same answer, generalize a parameter of the task, such as a smaller count or size,
so that small cases have different known answers). Keep each small enough to work out in a few steps: a case that needs long
counting or arithmetic belongs in small_inputs, where the reference computes it. If user_examples
are given, include them unchanged.
input: the input the user wants answered now, or null if the user wants the program itself. When
the task asks for one answer and run takes a parameter you introduced, input is the value of that
parameter that answers the task.
verify: when checking an answer is easier than computing it (substitute it back, test a property),
Python source defining verify(input, output) that returns True exactly when output is correct;
otherwise null. It must not recompute the answer by the same method.
reference: when a slow but obviously correct way exists (brute force, direct simulation, straight
from the definition; on small inputs one almost always does), Python source defining
reference(input) that computes the same result as run for small inputs; otherwise null. Keep it as
simple as possible: it is a second opinion, not the solution. It is run on your checks before any
method is written; where it and a check disagree, a blind re-derivation decides.
small_inputs: when reference is given, 5 to 20 small inputs, including edge cases, on which
reference finishes quickly; otherwise null.
large_inputs: when the task states sizes or bounds, one Python expression, given as a JSON string,
that builds a list of 1 to 3 valid inputs at the top of them, for example "[{'n': 10**18}]" or
"[{'s': 'ab' * 50000}]"; when it bounds the result too, include an input whose result is near that bound;
no expected values. The system evaluates it in the sandbox; the method must
finish each input within the time limit. Otherwise null.
relations: when the result changes in a known way under a known change of the input (the order of a list
does not matter, doubling every number doubles the result, a mirror image has the same answer), Python
source defining moves(input) -> a list of changed inputs, and agree(input, output, moved, moved_output)
-> True exactly when the two results stand in that relation. No expected values: it relates two runs.
It must not compute the result itself. Otherwise null.
decomposition: optional, only when a small explicit assembly of independent functions is useful.
Use {"subtasks": [{"id": "name", "request": string, "checks": [...]}, ...], "recipe": recipe}.
Use at most 4 subtasks, each with a unique lowercase id and its own input/output checks. An empty
subtask list can assemble available_methods by their exact artifact IDs. A recipe is data:
{"version": 1, "output": expression}. Expressions: {"op":"input"}, {"op":"literal","value":JSON},
{"op":"call","method":"subtask_id or artifact_id","arg":expression},
{"op":"map","method":"subtask_id or artifact_id","arg":expression} (a list),
{"op":"array","items":[expressions]}, {"op":"object","fields":{"key":expression}},
{"op":"get","value":expression,"key":"field or nonnegative integer index"}.
For sum of squares, call total on map square over input. No Python fragments or inferred adapters.
The system checks the composed result against the original checks; component checks are not a proof.
Avoid the mistakes listed in lessons. If previous_contract is given, it failed: write a clearer one.'''

# Asked of the planner only with Solver(conditions=True), in its context, so PLAN_INSTRUCTIONS stays as registered.
# Every condition a task states must be used (THE_ORIGINALS.md: Mīmāṃsāsūtra 1.2.1-7, Brahmasūtra 1.1.4, Nyāyasūtra
# 1.1.37 and 5.2.12, TaittU 3.1-6): each with the output that meets the others and breaks it, the tempting wrong answer.
CONDITIONS_NOTE = ('Also return "conditions": a list of 1 to 6 objects {"condition": string, "input": JSON, '
                   '"violating": JSON}, one for each condition the task states about the result (what it must be, '
                   'contain, satisfy or avoid). For each, give a valid input and an output for it that meets every '
                   'other stated condition but breaks this one: the tempting wrong answer for that condition. They are '
                   'not checks and no expected value is given for them. The system tests that verify rejects each '
                   'violating output, and runs the method and the reference on these inputs. Also return '
                   '"derivation": a short text, at most 1500 characters, that derives the rule of the result from the '
                   'task\'s conditions: why an output that follows the rule meets each of them. It is kept with the '
                   'method, marked unverified: nothing checks it.')
# The written derivation (upapatti) of the rule, kept with the method (bīja); the planner's words, never a check.
DERIVATION_UNVERIFIED = "unverified: the planner's words; no code checks them"

SPLIT_INSTRUCTIONS = '''A task could not be solved in one step. Split it into 2 to 4 smaller tasks.
Return exactly one JSON object, without prose or Markdown fences:
{"subtasks": [{"id": string, "request": string, "checks": [{"input": JSON, "expected": JSON}, ...]}, ...],
 "recipe": object or null}
Each request describes one self-contained function run(input), in the order they should be built;
the original task must become easy once they exist. Give each 2 to 5 checks worked out by hand.
The current checks and input describe the parent's interface and requested answer. Preserve their
input/output formats; model-written checks remain provisional, and every piece must be checked.
If partial_attempt is given, use its source and failed_checks (actual values or errors) to isolate
the missing operation, even when passed_checks is empty. Keep useful partial work, but do not treat
passing a few checks as proof that a piece is correct. Make the failing part a smaller subtask.
If previous_reply_rejected is given, correct that error before proposing the breakdown again.
available_methods lists stored pieces by exact artifact id. They passed their own finite checks;
use their ids where useful, but the assembled parent must still pass its own checks and gates.
If similar_breakdowns are given, they split similar tasks in ways that worked; follow them where they fit.
When the pieces combine mechanically, include a recipe: {"version":1,"output":expression}.
Expressions are input; call/map with method (subtask id or stored artifact id) and arg expression;
array with items; object with fields; get with value expression and key; literal with value.
Each expression has an "op" field. Example: {"op":"call","method":"total","arg":
{"op":"map","method":"square","arg":{"op":"input"}}}. Use unique lowercase subtask ids.
If the assembly needs logic this recipe language cannot express, omit recipe; do not invent operations.
When the task reduces to smaller instances of itself (as division reduces the kuttaka), you may
instead return {"reduction": source}: Python source defining measure(input) -> non-negative integer,
small(input) -> bool (true when the input is small enough to compute directly), reduce(input) -> a
list of inputs of the same form, each with a smaller measure, and combine(input, answers) -> the
result for input from the results for reduce(input), in order. Define base(input) for small inputs
only if no reference exists. Kuttaka builds the recursion, checks that every reduced input is
smaller, and computes small inputs with the slow reference.
When the answer is reached by a sequence of moves from a start (a simulation, or the fewest moves to a
goal), you may instead return {"steps": source}: Python source defining start(input) -> the first state
(JSON), moves(input, state) -> the list of states one move away (one state for a simulation),
done(input, state) -> bool, answer(input, state, moves) -> the result from the first done state, reached
in that many moves, and optionally exhausted(input) -> the result when no reachable state is done.
Write only the step: Kuttaka takes the moves breadth first (the first done state is reached by the fewest
moves), remembers every state it has seen, and shows you the path it took if a check fails.'''

AUDIT_INSTRUCTIONS = '''Work out by hand, step by step, the exact result of run(input) for each input
below, from the task and contract alone. Write null for an input the contract does not define, and for one
whose exact result you cannot work out by hand with certainty (never guess a value). Return
exactly one JSON object, without prose or Markdown fences: {"expected": [one JSON value per input, in
the same order]}.'''

MAX_DEPTH = 3
MATI_INSTRUCTIONS = '''A method has the right shape but fails some checks. Rewrite it with up to 3 holes:
write HOLE0, HOLE1, HOLE2 in the source where a constant, offset, bound or small choice is uncertain,
and list the candidate values for each hole (at most 40 each), most likely first. Kuttaka enumerates
fillings within the shared execution budget and keeps the best: one that passes all checks (ranked by
agreement with the reference, if any), else the one passing most. Return exactly one JSON object, without prose or
Markdown fences: {"source": string, "holes": {"HOLE0": [JSON values], ...}}.'''

MAX_STALLS = 2   # full passes through the strategy ladder without progress before stopping
TRUNCATION_LIMIT = 2   # replies in a row cut at the token limit before an attempt ends (the ladder moves on)
# The actor's instructions: the C3 text (kept byte-identical for C1-C3), and how the solver's host steps work.
SOLVE_ACTOR_INSTRUCTIONS = INSTRUCTIONS_C3 + '''
In this solver the host has already looked for reusable methods: the history begins with its
find_methods result (by host), and find_methods is not offered. When nothing listed fits, your
first action is remember_method with a general run(input). After each remember_method the host
validates the method on every check and submits it when all pass; otherwise the failed checks,
with what the method returned, are in the next history entry: correct the method and store it again.'''
# Told to the actor with remember_method when retrieval and validation are host work (Solver.host_protocol).
HOST_PROTOCOL_NOTE = ('\n\nThe host has already searched the stored methods for this task (the first history '
                      'entry, by host); there is no find_methods call. After remember_method the host validates '
                      'the method on every check at once and submits it when all pass; if a check fails, the '
                      'failures are in the next history entry. Your next action is normally a corrected '
                      'remember_method, not validate_method.')
MATI_COMPARE = 4   # fillings that pass every check, ranked against the reference
MATI_EXTRA = 32    # candidates tried after the first filling that passes, to find others to rank


VERIFY_RUN = '''import json
%s


def run(pairs):
    out = []
    for item in pairs:
        try:
            verdict = verify(item[0], item[1])
            out.append(verdict if type(verdict) is bool else None)
        except Exception:
            out.append(None)
    return out
'''

DIFFERENTIAL_RUN = '''import json
import signal
import time
_SOURCE = %r
# Each value has its own time (first-hand session f, 2026-11-02): one input that never finishes used to end the
# whole sandbox run, and every other value in its batch was lost with it. A value cut short says nothing.
_BUDGET, _RESERVE = 15.0, 0.2   # seconds for the batch (the sandbox allows 20); kept for each later value


class _OutOfTime(BaseException):
    pass


def _alarm(signum, frame):
    raise _OutOfTime()


def run(inputs):
    out = []
    started = time.monotonic()
    try:
        signal.signal(signal.SIGALRM, _alarm)
        timed = True
    except Exception:
        timed = False
    for number, value in enumerate(inputs):
        space = {'__name__': '__kuttaka_differential__'}
        if 'use' in globals():
            space['use'] = globals()['use']
        left = _BUDGET - (time.monotonic() - started) - _RESERVE * (len(inputs) - number - 1)
        if timed and left <= 0:
            out.append({'timeout': True, 'seconds': 0.0})
            continue
        entry = {'timeout': True, 'seconds': round(left, 3)}
        try:
            try:
                if timed:
                    # The stop repeats every 50 ms, so a method that swallows one is stopped again.
                    signal.setitimer(signal.ITIMER_REAL, left, 0.05)
                exec(compile(_SOURCE, '<differential>', 'exec'), space)
                result = space[%r](value)
                if timed:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                entry = {'value': json.loads(json.dumps(result, allow_nan=False))}
            except _OutOfTime:
                entry = {'timeout': True, 'seconds': round(left, 3)}
            except Exception as error:
                entry = {'error': type(error).__name__}
            finally:
                if timed:
                    signal.setitimer(signal.ITIMER_REAL, 0)
        except _OutOfTime:
            pass   # a repeat of the stop, delivered while the cut value's memory was freed: the value is cut
        out.append(entry)
    return out
'''
DIFFERENTIAL_HEAD = DIFFERENTIAL_RUN[:DIFFERENTIAL_RUN.index('%r')]
PROBE_BUDGET = 15.0   # DIFFERENTIAL_RUN's _BUDGET: the time a value run alone has
assert '_BUDGET, _RESERVE = %r, 0.2' % PROBE_BUDGET in DIFFERENTIAL_RUN


def _nested_inputs(checks):
    """A check input whose text holds a bracket pair, wrapped NEST_DEPTH levels deep in that bracket: [] or [input].

    Made by a rule, not by the planner, so a method that recurses once per level meets the depth its contract allows
    even when the plan names no sizes. Only running out (time, memory, recursion) counts on it in the scale test."""
    for check in checks:
        value = check['input']
        fields = [None] if isinstance(value, str) else [k for k, v in value.items() if isinstance(v, str)] \
            if isinstance(value, dict) else []
        for field in fields:
            text = value if field is None else value[field]
            for left, right in ('()', '[]', '{}'):
                # The line that holds the pair is wrapped, not the whole text: a stdin-shaped text (a count line,
                # then the data) stays in its shape. A text of one line is wrapped whole.
                lines = text.split('\n')
                at = next((i for i, line in enumerate(lines) if left in line and right in line), None)
                if at is not None:
                    lines[at] = left * NEST_DEPTH + lines[at] + right * NEST_DEPTH
                    deep = '\n'.join(lines)
                    return [deep] if field is None else [dict(value, **{field: deep})]
    return []


def _sequence_field(checks):
    """'' when every check takes a non-negative integer, the field name when every check takes {field: such an
    integer} with that one field, else None; and only when every expected value is an integer (a sequence)."""
    if not checks or not all(type(c['expected']) is int for c in checks):
        return None
    inputs = [c['input'] for c in checks]
    if all(type(v) is int and v >= 0 for v in inputs):
        return ''
    if all(isinstance(v, dict) and len(v) == 1 for v in inputs):
        names = {next(iter(v)) for v in inputs}
        if len(names) == 1:
            name = names.pop()
            if all(type(v[name]) is int and v[name] >= 0 for v in inputs):
                return name
    return None


def _probe_method(source, libraries):
    """The digest of the code (source and libraries) a DIFFERENTIAL_RUN job runs when it calls run(...), as the
    scale test and the final run would run it; None for a job that calls another function (a reference)."""
    import ast
    try:
        line = next(l for l in source.split('\n') if l.startswith('_SOURCE = '))
        method = ast.literal_eval(line[len('_SOURCE = '):])
    except (StopIteration, ValueError, SyntaxError):
        return None
    if "result = space['run'](value)" not in source:
        return None
    return digest({'source': method, 'libraries': libraries or {}})
TRACE_RUN = '''import json
_SOURCE = %r
_CALLS = []


def run(value):
    base = globals().get('use')

    def traced(artifact):
        method = base(artifact)

        def call(arg):
            entry = None
            if len(_CALLS) < 400:   # recorded before the call: an outer piece comes before its own sub-calls
                try:
                    entry = [artifact, json.loads(json.dumps(arg)), None]   # the input before it may change
                    _CALLS.append(entry)
                except Exception:
                    entry = None
            result = method(arg)
            if entry is not None:
                try:
                    entry[2] = {'value': json.loads(json.dumps(result, allow_nan=False))}
                except Exception:
                    pass
            return result
        return call
    if base is not None:   # pieces that run pieces load them through the same traced use: every depth is seen
        base.__globals__['use'] = traced
    space = {'__name__': '__kuttaka_trace__', 'use': traced}
    exec(compile(_SOURCE, '<trace>', 'exec'), space)
    result = space['run'](value)
    return {'result': json.loads(json.dumps(result, allow_nan=False)), 'calls': _CALLS}
'''
MAX_SMALL_INPUTS = 20
DIFFERENTIAL_ROUNDS = 2
AGAINST_ROUNDS = 1     # a method two derivations each reject on an input is asked for again once
ANSWER_ROUNDS = 1      # a checked method that returns null on the requested input is asked for again once
MEMORIZATION_ROUNDS = 2
MIN_SETTLED = 2    # checks that must survive settlement before the model writes code
SETTLE_RESERVE = 4  # executions left below which the checks are not tested before code
GIVEN_RESERVE = 12  # model calls left below which missing given conditions are not asked for
MAX_RIVALS = 3     # other methods written for the same checks, compared with the one delivered
MAX_PROBES = 16    # inputs beyond the checks on which a method and its rival are compared
NEAR_MISSES = 4    # near misses of each check's value on which a property must reject one to support it
MAX_CONDITIONS = 6   # stated conditions of a task, each with its tempting wrong output (Solver(conditions=True))
MAX_CONDITION_TEXT = 300
MAX_DERIVATION_TEXT = 1500
MAX_CONDITION_JSON = 4000   # characters of a condition's input or violating output, as canonical JSON
VARIED_BATCHES = 2  # sandbox batches of MAX_PROBES for the inputs varied in form
RIVAL_ROUNDS = 2
SCALE_ROUNDS = 2       # a checked method that times out at the top of the stated range is asked for again
RAISED_ROUNDS = 1      # a checked method that raises on an input the plan declares valid is asked for again
MAX_LARGE_INPUTS = 3
NEST_DEPTH = 300       # levels a check's bracketed text is wrapped in for the scale test (W4-r h-c1-regex: the
                       # delivered method raised RecursionError at 300 nested groups, an input its contract allows)
DEGENERATE_CHECKS = ('every check expects the same result, so a method returning that constant would pass them all '
                     'and the checks pin nothing: give checks with different expected results; if every small case '
                     'of the task has the same answer, generalize a parameter (a smaller count or size) so that '
                     'small cases have different known answers, and set input to the value that answers the task')
NO_CHECKS = ('checks must be nonempty when no supplied examples exist: give 3 to 6 small cases with known answers; if '
             'the task asks for one answer, generalize a parameter of it (a smaller count or size) into an argument of '
             'run, so that small instances can be checked, and set input to the value that answers the task')
CHECKS_ON_THE_ANSWER = ('every check has input null, so run takes no argument, its one check is a guess at the answer, '
                        'and a method returning that guess would pass: give checks on smaller instances of the task '
                        'whose answers are known or can be worked out (generalize a parameter, a smaller count or '
                        'size, into an argument of run), and set input to the value that answers the task')
INVALID_PLAN_REPLY = ('the previous reply was not valid JSON, so none of it could be used: return exactly one JSON '
                      'object, with code and Python expressions only inside JSON strings (as reference, verify '
                      'and large_inputs are); if unsure, set large_inputs to null')
MAX_LARGE_EXPRESSION = 2000   # characters of the planner's large_inputs expression
LARGE_INPUT_RUN = '''def run(index):
    values = list(%s)
    return values[index] if index < len(values) else None
'''
PROPERTY_ROUNDS = 2
RELATION_ROUNDS = 1    # a checked method that breaks a relation the plan states is asked for again once
RELATION_MOVES = 2     # moved inputs taken from each input a relation is tested on
RELATION_RUN = '''import json
_SOURCE = %r
_space = {'__name__': '__kuttaka_relation__'}
exec(compile(_SOURCE, '<relation>', 'exec'), _space)


def run(job):
    out = []
    if job['do'] == 'moves':
        for value in job['inputs']:
            try:
                moved = list(_space['moves'](value))[:%d]
                out.append({'moved': json.loads(json.dumps(moved, allow_nan=False))})
            except Exception as error:
                out.append({'error': type(error).__name__})
        return out
    for x, y, m, my in job['pairs']:
        try:
            out.append({'agree': _space['agree'](x, y, m, my) is True})
        except Exception as error:
            out.append({'error': type(error).__name__})
    return out
'''
PRASTARA_METHODS = 8   # stored methods the prastāra composes (typing keeps the space small)
PRASTARA_LIMIT = 48    # well-formed recipes listed and screened, in prastāra order, before code
ROLE_METHODS = 8       # stored methods whose fields the task's fields may fill (recall by role)
ROLE_SCREEN_VALUES = 48   # values in one role-screening sandbox run (each has its own time)
KNOWN_MIN_SUPPORT = 2  # supported checks a stored method must give before it speaks as knowledge
ROLE_PER_KNOWN = 6     # role assignments tried per stored method when looking for what is known
WITNESS_TERMS = 16     # consecutive small values a checked method is run on before a published table is searched
WITNESS_PROBES = 4     # larger indices of the table the method is compared on (the requested input among them)
WITNESS_ROUNDS = 1     # a method a published table contradicts is asked for again
PARIKSHA_KNOWN = 3     # stored methods recognized as this task's function that a checked method is set against
RESUME_CANDIDATES = 4  # exact-scope partials to recheck per task in a later round
PIECE_MIN_CALLS = 4    # plan, find, write, submit: keep tight mechanical compositions possible


def _like(hand, value):
    """`value` written as the hand value was: an integral float becomes an int where the hand value had
    an int (37.0 -> 37), so a check revised from a float reference still matches an int method exactly."""
    if isinstance(hand, bool) or isinstance(value, bool):
        return value
    if isinstance(hand, int) and isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(hand, float) and isinstance(value, int):
        try:
            return float(value)
        except OverflowError:   # an int beyond float range stays exact
            return value
    if isinstance(hand, list) and isinstance(value, list) and len(hand) == len(value):
        return [_like(h, v) for h, v in zip(hand, value)]
    if isinstance(hand, dict) and isinstance(value, dict) and hand.keys() == value.keys():
        return {k: _like(hand[k], value[k]) for k in value}
    return value


def _kind(value):
    """The JSON kinds of a value, int and float kept apart (an exact check compares them exactly)."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, float, str)):
        return type(value).__name__
    if isinstance(value, list):
        return ['list'] + [_kind(v) for v in value]
    if isinstance(value, dict):
        return {k: _kind(v) for k, v in sorted(value.items())}
    return 'other'


def _settled_value(hand, audited, computed):
    """The value a check is revised to when a blind re-derivation and a computed value agree: the
    re-derivation's own when it is written as the hand value was (an exact integer where a float
    reference rounded one), else the computed value in the hand value's form."""
    if _kind(audited) == _kind(hand):
        return audited
    return _like(hand, computed)


def same_value(a, b):
    """Equal JSON values, with numbers equal within 1e-9 relative (as the suites score)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int) and isinstance(b, int):
        return a == b   # exact; also avoids float overflow on very large integers
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
        except OverflowError:
            return False
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_value(a[k], b[k]) for k in a)
    return a == b


STOPWORDS = frozenset('a an and are as at be by for from given in input int into is it its list of on '
                      'or receives return returns run str that the their then this to with'.split())


class CallsExhausted(RuntimeError):
    """The run's hard model-call limit is spent."""


class Metered:
    """Counts every model call against one hard limit shared by all steps of a run."""

    def __init__(self, model, counter):
        self.model, self.counter = model, counter

    def propose(self, context):
        if self.counter['used'] >= self.counter['limit']:
            raise CallsExhausted('model-call limit reached')
        self.counter['used'] += 1
        self.last_usage = {}
        try:
            reply = self.model.propose(context)
        except http.client.HTTPException:
            # A response cut off mid-read (IncompleteRead) is a network failure, not a model failure.
            raise TransportFailure('response_read_error') from None
        self.last_usage = dict(getattr(self.model, 'last_usage', {}) or {})
        self.counter['replied'] = self.counter.get('replied', 0) + 1
        return reply

    def __getattr__(self, name):
        return getattr(self.model, name)


class ProgressActor:
    """Guide construction after retrieval finds no direct fit, and stop repeats.

    The actual model reply is journaled before a host-generated unresolved action
    replaces it. This is a control intervention, not another model conclusion.
    It does not memoize observations or assume arbitrary tools are pure.
    """

    def __init__(self, model, journal, stored, host_validates=False):
        self.model, self.journal, self.stored = model, journal, stored
        self.host_validates = host_validates
        self.stall = None
        self.construction_guided = False

    def __getattr__(self, name):
        return getattr(self.model, name)

    def observe_retrieval(self, request, observation):
        """Label advice after an empty or explicitly non-fitting lookup.

        The tool wrapper calls this only after find_methods returns successfully.
        Keeping the advice in the observation makes the next model context and
        its journal agree; it neither changes the request nor spends another call.
        """
        if (self.construction_guided or not isinstance(observation, dict)
                or 'status' in observation):
            return observation
        methods = observation.get('methods')
        empty = isinstance(methods, list) and not methods and observation.get('retrieval') == 'empty'
        no_direct_fit = (isinstance(methods, list) and bool(methods)
                         and observation.get('retrieval') == 'ranked'
                         and all(isinstance(entry, dict) and entry.get('fits_this_task') is False
                                 for entry in methods))
        if not (empty or no_direct_fit):
            return observation
        reason = ('Every returned method explicitly failed to fit the current public checks as-is. '
                  'The listed methods may still supply useful pieces for an adapter or composition. '
                  'This does not establish that the whole library lacks a solution.'
                  if no_direct_fit else
                  'This query returned no relevant checked method. This does not establish that '
                  'the whole library is empty or that the task is impossible.')
        construction = ('Construct an adapter, compose useful listed methods, or write the missing logic. '
                        if no_direct_fit else '')
        guidance = {
            'attribution': 'engineered solver guidance',
            'reason': reason,
            'instruction': construction + 'Move from retrieval to construction instead of repeating this lookup. '
                           'Construct a general Python procedure for the current task contract and valid inputs, '
                           'not a table of the visible answers. Use an experiment with tool remember_method: '
                           'input needs name, description, and source defining run(input); uses is optional '
                           'and must list any stored method IDs called through use("ID"). Include predictions '
                           'as usual. '
                           + ('The host then validates it on every check at once and submits it when all pass; '
                              'do not call validate_method for it. ' if self.host_validates else
                              'Then validate or submit the returned artifact through the normal checker; '
                              'storing a method alone is not success. ')
                           + 'If the whole procedure remains too difficult, '
                           'return unresolved and identify a smaller missing step for decomposition.'}
        self.construction_guided = True
        self.journal.append('solve_retrieval_construction_guidance', {
            'tool_input': request, 'tool_observation': observation, 'host_guidance': guidance})
        return {**observation, 'suggested_next_step': guidance}

    def propose(self, context):
        action = self.model.propose(context)
        reason = None
        if isinstance(action, dict) and action.get('kind') == 'experiment':
            body = action.get('input')
            if action.get('tool') == 'remember_method' and isinstance(body, dict):
                if isinstance(body.get('source'), str) and isinstance(body.get('uses', []), list):
                    try:
                        fp = _execution_fingerprint(body['source'], body.get('uses', []))
                    except (ValueError, TypeError):
                        pass  # The normal tool/schema validator reports malformed input.
                    else:
                        if fp in self.stored:
                            reason = 'same admitted candidate under unchanged checks; change strategy'
            history = context.get('history', [])
            if action.get('tool') == 'find_methods' and history:
                previous = history[-1].get('action', {})
                if (previous.get('kind') == 'experiment' and previous.get('tool') == 'find_methods'
                        and canonical(previous.get('input')) == canonical(body)):
                    reason = 'consecutive identical retrieval without new evidence; change strategy'
        if reason:
            self.stall = reason
            replacement = {'kind': 'unresolved', 'reason': reason}
            self.journal.append('solve_stall_intervention', {
                'model_action': action, 'host_action': replacement, 'reason': reason})
            return replacement
        return action


UNDEFINED = frozenset({'invalid', 'undefined', 'not defined', 'n/a', 'na', 'none', 'null', 'error', 'nan',
                       'out of range', 'out of domain', 'invalid input', 'not applicable', 'unknown', '?', '-'})


def _heard(recorded, answer):
    """The voices to count: one that agrees with the answer counts once however often it was heard (one derivation,
    asked twice, is not two), one that dissents counts each time (the gate leans to void: thirteenth review, S10)."""
    out, seen = [], set()
    for kind, value, source in recorded:
        if same_value(value, answer):
            key = (kind, canonical(value), source)
            if key in seen:
                continue
            seen.add(key)
        out.append((kind, value, source))
    return out


def _close(said, answer):
    """Agreement of a hand derivation with a method's answer: equal; or, where a value is a fraction, equal within a
    hand's rounding (0.5% relative, or 1e-6 absolute); element by element for lists and objects. Whole numbers are
    compared exactly: an off-by-one on 275 is a disagreement, not rounding (thirteenth review, S1, S2)."""
    if same_value(said, answer):
        return True
    if (isinstance(said, (int, float)) and isinstance(answer, (int, float))
            and not isinstance(said, bool) and not isinstance(answer, bool)):
        try:
            whole = lambda v: isinstance(v, int) or float(v).is_integer()   # noqa: E731
            if (isinstance(said, int) or isinstance(answer, int)) and whole(said) and whole(answer):
                return False   # a count (an int, or an int written 275.0): exactly (T3: two floats are measures)
            return abs(said - answer) <= max(1e-6, 5e-3 * max(abs(said), abs(answer)))
        except (OverflowError, ValueError):   # too large for a float: exactly (T5)
            return False
    if isinstance(said, list) and isinstance(answer, list):
        return len(said) == len(answer) and all(_close(a, b) for a, b in zip(said, answer))
    if isinstance(said, dict) and isinstance(answer, dict):
        return said.keys() == answer.keys() and all(_close(said[k], answer[k]) for k in said)
    return False


def _finite(value):
    """A model's reply with every NaN or infinity made null (no answer): JSON parsed from a model may hold them,
    and the journal, the checks and the comparisons hold only finite values (twelfth review, R3)."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


def _known_inputs(spec):
    """The inputs a task knows of beyond its checks: the requested input and the small inputs."""
    return [v for v in [spec.get('input'), *(spec.get('small_inputs') or [])] if v is not None]


def _execution_fingerprint(source, dependencies):
    # Python can inspect source and locations. AST similarity is useful research
    # metadata, but is insufficient authority to skip execution of different text.
    if not all(isinstance(dep, str) for dep in dependencies):
        raise ValueError('dependencies must be IDs')
    return digest({'source': source, 'dependencies': sorted(set(dependencies))})


def _words(text):
    return set(re.findall(r'[a-z0-9]+', text.lower())) - STOPWORDS


def _similar(a, b):
    a, b = _words(a), _words(b)
    return len(a & b) / len(a | b) if a and b else 0.0


class Workspace:
    """Local, persistent: checked methods, lessons from failures, and a cost history."""

    def __init__(self, root):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory = self.root / 'memory'
        self.runs = self.root / 'runs'
        self.lessons_path = self.root / 'lessons.jsonl'
        self.history_path = self.root / 'history.jsonl'
        self.solved_path = self.root / 'solved.jsonl'
        self.splits_path = self.root / 'splits.jsonl'
        self.refuted_path = self.root / 'refuted.jsonl'
        self.confirmed_path = self.root / 'confirmed.jsonl'
        self.property_disputes_path = self.root / 'property_disputes.jsonl'
        self.unfinished_path = self.root / 'unfinished.jsonl'

    def remember_unfinished(self, method, value, seconds):
        """A checked method's final run on an input that did not finish within `seconds`. `method` is the digest
        of its code (source and libraries): the same code is stored under another artifact id by a later round."""
        self._append(self.unfinished_path, {'time': _now(), 'method': method, 'input': canonical(value),
                                            'seconds': seconds})

    def unfinished(self, method, value):
        """The longest limit within which this code's final run on this input did not finish, or None."""
        key = canonical(value)
        limits = [r['seconds'] for r in self._read(self.unfinished_path)
                  if r.get('method') == method and r['input'] == key and isinstance(r.get('seconds'), (int, float))]
        return max(limits) if limits else None

    def _read(self, path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def _append(self, path, record):
        with path.open('a') as handle:
            handle.write(canonical(record) + '\n')

    def lessons(self, prompt, limit=3, form=None):
        """Mistakes on tasks worded alike, then (when `form` is given) on tasks whose inputs had the same
        form and whose wording is at least related: a task left void teaches the next task of its kind."""
        rows = self._read(self.lessons_path)
        scored = sorted(((_similar(prompt, l['task']), l) for l in rows), key=lambda pair: -pair[0])
        out = [{'task': l['task'], 'mistake': l['mistake']} for s, l in scored[:limit] if s >= 0.2]
        if form is not None:
            # The same input form is a weak sign of the same kind of problem (many tasks take one number),
            # and a lesson enters the prompt unverified: related wording is required too.
            for l in reversed(rows):
                if len(out) >= limit:
                    break
                entry = {'task': l['task'], 'mistake': l['mistake']}
                if l.get('form') == form and _similar(prompt, l['task']) >= 0.1 and entry not in out:
                    out.append(entry)
        return out[:limit]

    def learn(self, task, mistake, form=None):
        row = {'time': _now(), 'task': task, 'mistake': mistake[:500]}
        if form is not None:
            row['form'] = form
        self._append(self.lessons_path, row)

    def _nearest(self, path, prompt, threshold, limit):
        scored = sorted(((_similar(prompt, r['task']), r) for r in self._read(path)), key=lambda p: -p[0])
        return [dict(r, similarity=round(s, 2)) for s, r in scored[:limit] if s >= threshold]

    def remember_solved(self, task, contract, artifact, form=None, evidence=None, supersedes=(), conditions=(),
                        derivation=None):
        row = {'time': _now(), 'task': task, 'contract': contract, 'artifact': artifact}
        if form is not None:
            row['form'] = form   # the shape of the inputs it was checked on (pramana.form)
        if evidence:
            row['evidence'] = evidence   # its pramāṇa: support of its checks, what it held on beyond them
        if supersedes:
            row['supersedes'] = sorted(set(supersedes))   # methods shown wrong on the way to this one
        if conditions:
            # The analysis it was made from (bīja): the task's stated conditions, each with its tempting wrong output,
            # what the property said of it and what the method gave there. The model's reading, labelled by its tests.
            row['conditions'] = list(conditions)
        if derivation:
            # Its written derivation: kept beside the tested analysis, and labelled as what it is.
            row['derivation'] = {'text': derivation, 'status': DERIVATION_UNVERIFIED}
        self._append(self.solved_path, row)

    def refute(self, artifact, reason, source_digest=None, task=None):
        row = {'time': _now(), 'artifact': artifact, 'reason': reason[:300]}
        if source_digest and task:
            # The same source stored again under a new id is still wrong for this task (not for others:
            # the same text can be right under another contract).
            row['source'], row['task'] = source_digest, task
        self._append(self.refuted_path, row)

    def refuted(self):
        return {r['artifact'] for r in self._read(self.refuted_path)}

    def refuted_sources(self, task=pramana.MISSING):
        return {r['source'] for r in self._read(self.refuted_path)
                if r.get('source') and (task is pramana.MISSING or r.get('task') == task)}

    def remember_confirmed(self, task, check, given=None):
        """A check two derivations agreed on: it stays settled for this task, across replans and rounds.

        `given` identifies the task's given conditions (the user's examples): a task worded the same
        with other examples is another task, and receives none of these checks."""
        self._append(self.confirmed_path, {'time': _now(), 'task': task, 'given': given, 'input': check['input'],
                                           'expected': check['expected']})

    def confirmed(self, task, given=pramana.MISSING):
        latest = {}
        for r in self._read(self.confirmed_path):
            if r['task'] == task and (given is pramana.MISSING or r.get('given') == given):
                latest[canonical(r['input'])] = {'input': r['input'], 'expected': r['expected']}
        return list(latest.values())

    def remember_property_dispute(self, task, given, dispute, evidence):
        """Persist uncertainty separately from confirmed checks, including across process restarts."""
        record = {'task': task, 'given': given, 'dispute': dispute, 'property_evidence': evidence}
        if record not in self._read(self.property_disputes_path):
            self._append(self.property_disputes_path, record)

    def property_disputes(self, task, given):
        return [row for row in self._read(self.property_disputes_path)
                if row.get('task') == task and row.get('given') == given]

    def helpful_methods(self, prompt, limit=4, form=None):
        """Checked methods written for similar tasks, offered to the model before it starts.

        Paribhāṣā: a method applies by what it takes, not by how its task was worded. Methods for tasks
        worded alike come first; then, when `form` is given, methods checked on inputs of the same form
        (pramana.form), however their tasks were worded. A method later shown wrong (differential
        testing, or returning checks literally) is not offered."""
        seen, out = self.refuted(), []
        for r in self._nearest(self.solved_path, prompt, 0.25, 3 * limit):
            if r['artifact'] not in seen:
                seen.add(r['artifact'])
                out.append(r)
        if form is not None and len(out) < limit:
            alike = sorted((r for r in self._read(self.solved_path) if r.get('form') == form),
                           key=lambda r: -_similar(prompt, r['task']))
            for r in alike:
                if r['artifact'] not in seen:
                    seen.add(r['artifact'])
                    out.append(dict(r, similarity=round(_similar(prompt, r['task']), 2), by='form'))
        return out[:limit]

    def methods_taking(self, forms, limit=4):
        """Checked methods whose inputs have one of these forms, most recent first (not refuted)."""
        refuted, out = self.refuted(), []
        for r in reversed(self._read(self.solved_path)):
            if r.get('form') in forms and r['artifact'] not in refuted and r['artifact'] not in out:
                out.append(r['artifact'])
        return out[:limit]

    def remember_split(self, task, subtasks, recipe=None):
        row = {'time': _now(), 'task': task, 'subtasks': subtasks}
        if recipe is not None:
            # Canonical storage sorts object keys. Preserve expression evaluation
            # order inside an exact JSON string, as the compiler manifest does.
            row['recipe_json'] = json.dumps(recipe, ensure_ascii=True, separators=(',', ':'))
        self._append(self.splits_path, row)

    def similar_splits(self, prompt, threshold=0.3, limit=2):
        return self._nearest(self.splits_path, prompt, threshold, limit)

    def record(self, entry):
        self._append(self.history_path, dict(entry, time=_now()))

    def history(self):
        return self._read(self.history_path)


def _reference_fields(reply):
    """The planner's optional slow reference and its small inputs, or nothing usable.

    A reference without small inputs is still kept: it is run on the check inputs before code."""
    reference, small = reply.get('reference'), reply.get('small_inputs')
    if not isinstance(reference, str) or not reference.strip():
        return {'reference': None, 'small_inputs': []}
    small = small if isinstance(small, list) else []
    unique = []
    for value in small:
        if canonical(value) not in {canonical(u) for u in unique}:
            unique.append(value)
    return {'reference': reference, 'small_inputs': unique[:MAX_SMALL_INPUTS]}


def _conditions(reply, checks):
    """The planner's stated conditions of the task, each with an input and the output it calls the tempting wrong
    answer for that condition, as [{'condition', 'input', 'violating'}], and the reasons any were dropped.

    Only read when the solver asks for them (`Solver(conditions=True)`). An item is dropped when it is not that
    shape, or when its violating output is a check's expected value for the same input: the plan cannot hold an
    output both right and wrong (the planner's own two readings disagree, and neither is the task's word)."""
    raw = reply.get('conditions')
    if not isinstance(raw, list):
        return [], (['conditions is not a list'] if raw is not None else [])
    expected = {canonical(c['input']): c['expected'] for c in checks}
    kept, dropped = [], []
    for index, item in enumerate(raw):
        if len(kept) >= MAX_CONDITIONS:
            dropped.append(f'conditions[{index}]: more than {MAX_CONDITIONS}')
            continue
        if not (isinstance(item, dict) and set(item) == {'condition', 'input', 'violating'}
                and isinstance(item['condition'], str) and item['condition'].strip()
                and len(item['condition']) <= MAX_CONDITION_TEXT):
            dropped.append(f'conditions[{index}]: not {{condition, input, violating}} with a short text')
            continue
        try:
            sizes = len(canonical(item['input'])), len(canonical(item['violating']))
        except (TypeError, ValueError):
            dropped.append(f'conditions[{index}]: not JSON')
            continue
        if max(sizes) > MAX_CONDITION_JSON:
            # Kept with the method and in every journal: a condition is a small case, like a check.
            dropped.append(f'conditions[{index}]: longer than {MAX_CONDITION_JSON} characters as JSON')
            continue
        key = canonical(item['input'])
        if key in expected and same_value(expected[key], item['violating']):
            dropped.append(f'conditions[{index}]: its violating output is the expected value of a check')
            continue
        kept.append({'condition': item['condition'].strip(), 'input': item['input'], 'violating': item['violating']})
    return kept, dropped


def _condition_outcome(entry, violating):
    """What the checked method did on a stated condition's input, from one DIFFERENTIAL_RUN entry: said as it was."""
    if isinstance(entry, dict) and 'value' in entry:
        return ('gives the violating output' if same_value(entry['value'], violating)
                else 'differs from the violating output')
    if isinstance(entry, dict) and entry.get('error'):
        return 'raises %s' % str(entry['error'])[:80]
    if isinstance(entry, dict) and entry.get('timeout'):
        return 'did not finish'
    return 'not run'


def _derivation(reply):
    """The planner's written derivation of the rule, and the reason it was dropped, if it was. Only read when the
    solver asks for it (`Solver(conditions=True)`); a text, stripped, at most MAX_DERIVATION_TEXT characters."""
    raw = reply.get('derivation')
    if raw is None:
        return None, None
    if not isinstance(raw, str) or not raw.strip():
        return None, 'derivation is not a text'
    if len(raw.strip()) > MAX_DERIVATION_TEXT:
        return None, f'derivation is longer than {MAX_DERIVATION_TEXT} characters'
    return raw.strip(), None


def _large_inputs(reply):
    """The planner's expression for inputs at the top of the stated range (the scale test), or None.

    An expression, not the inputs: asked for the inputs themselves, the planner wrote Python inside its JSON
    ({"s": "a" * 100000}) and the whole plan failed to parse (W4-l at 901439f, h-c2-substrings)."""
    value = reply.get('large_inputs')
    return value.strip() if isinstance(value, str) and value.strip() and len(value) <= MAX_LARGE_EXPRESSION else None


def _admit_one_by_one(prompt, spec, supplied, proposed, extra, displaceable=()):
    """A check book with as many of `extra` as fit, added one at a time in order (the contract limits the
    checks together as well as each alone), the ones taken, and the checks of `proposed` displaced.

    A check in `displaceable` (hand-worked ones, weakest first to go: last first) is removed only when its
    removal is what lets one of `extra` in: no check is dropped for room that is not then used. Returns
    (None, [], []) when the base itself fails."""
    base = list(proposed)

    def book(checks):
        return CheckBook(prompt, spec['contract'], supplied, base + checks, supplied_origin=spec['examples_origin'],
                         model_limit=min(MAX_MODEL_CHECKS, len(base) + len(checks)))
    best = None
    if supplied or base:   # an empty base is no book yet; it is built up from `extra`
        try:
            best = book([])
        except ValueError:
            return None, [], []
    taken, displaced = [], []
    candidates = [c for c in reversed(list(displaceable))]

    def holds(book_, check):
        return any(canonical(r['input']) == canonical(check['input'])
                   and canonical(r['expected']) == canonical(check['expected']) for r in book_.records)
    for check in extra:
        try:
            if len(base) + len(taken) >= MAX_MODEL_CHECKS:
                raise ValueError('full')
            trial = book(taken + [check])
            if not holds(trial, check):
                raise ValueError('omitted by the check book')   # e.g. its total record limit
            best = trial
            taken.append(check)
            continue
        except ValueError:
            pass
        for weak in [c for c in candidates if c in base][:1]:
            base.remove(weak)
            try:
                trial = book(taken + [check])
                if not holds(trial, check):
                    raise ValueError('omitted by the check book')
            except ValueError:
                base.append(weak)   # its removal did not help: it stays
                continue
            best = trial
            taken.append(check)
            displaced.append(weak)
            candidates.remove(weak)
    if best is None and not taken:
        return None, [], []
    return best, taken, displaced


def _probe_batches(spec):
    """Inputs beyond the checks on which a method is compared: the planner's small inputs, inputs near
    the checks (pramana.near_inputs: numbers moved), inputs varied from them (pramana.varied_inputs: texts,
    lists and fields varied, so a method handling only part of its input's form is met), and the input the
    user asked about, each batch in its own sandbox run (the requested input may be large). Check inputs and
    repeats are left out."""
    seen = {canonical(c['input']) for c in spec['checks']}
    batches = []
    requested = [spec['input']] if spec.get('input') is not None else []
    checks = [c['input'] for c in spec['checks']]
    # A stated condition's input comes first (Solver(conditions=True)): it is where the tempting wrong answer lies, so
    # the method meets the reference and the property there (BAU 3.9.28: "do not say 'from seed'").
    stated = [c['input'] for c in spec.get('conditions') or []]
    # The varied inputs take two batches (2026-11-01): with nine checks, one batch of MAX_PROBES held only the
    # first kind of variant of each field; two hold a text lengthened and a case swapped for every field.
    for values, parts in ((stated, 1), (spec.get('small_inputs') or [], 1), (pramana.near_inputs(checks), 1),
                          (pramana.varied_inputs(checks), VARIED_BATCHES), (requested, 1)):
        batch = []
        for value in values:
            if len(batch) >= parts * MAX_PROBES:
                break   # Only scheduled probes are seen: a capped value may be the requested input later.
            if canonical(value) not in seen:
                seen.add(canonical(value))
                batch.append(value)
        for part in range(parts):
            if batch[part * MAX_PROBES:(part + 1) * MAX_PROBES]:
                batches.append(batch[part * MAX_PROBES:(part + 1) * MAX_PROBES])
    return batches


def _requested_first(disputes, spec):
    """Keep the requested input inside an audit cap, without changing the order of other disputes."""
    asked = spec.get('input')
    if asked is None:
        return disputes
    key = canonical(asked)
    return sorted(disputes, key=lambda dispute: canonical(dispute[0]) != key)


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _checks(value):
    if (not isinstance(value, list) or not value
            or any(not isinstance(c, dict) or set(c) != {'input', 'expected'} for c in value)):
        return None
    return value


def _decomposition(value):
    """Validate an explicit assembly before spending calls on its subtasks."""
    if type(value) is not dict or set(value) != {'subtasks', 'recipe'}:
        raise ValueError('decomposition requires subtasks and recipe')
    subtasks = value['subtasks']
    if type(subtasks) is not list or len(subtasks) > 4:
        raise ValueError('decomposition permits at most four subtasks')
    names, parts = set(), []
    for sub in subtasks:
        if type(sub) is not dict or set(sub) != {'id', 'request', 'checks'}:
            raise ValueError('each subtask requires id, request and checks')
        name = sub['id']
        if type(name) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', name) or name in names:
            raise ValueError('subtask ids must be distinct lowercase identifiers')
        # Same bounded JSON and consistency rules as later planning, with model provenance.
        book = CheckBook(sub['request'], sub['request'], sub['checks'], [], supplied_origin='model')
        parts.append({'id': name, 'request': sub['request'], 'checks': book.checks})
        names.add(name)
    metadata = inspect_recipe(value['recipe'])
    symbols = {ref for ref in metadata['methods'] if not re.fullmatch(r'[0-9a-f]{64}', ref)}
    if symbols != names:
        raise ValueError('recipe symbols must reference exactly the declared subtasks')
    return {'subtasks': parts, 'recipe': json.loads(json.dumps(value['recipe'], allow_nan=False))}


class Solver:
    # The blind second derivation of hand-only checks before code (_derive_blind). Tests of the mechanisms after
    # code that need a wrong hand check to reach the attempt turn it off, each with a note.
    derive_blind = True
    # Retrieval before the first call and validation after each stored method are host steps, not model calls
    # (runtime.investigate's prelude and auto_validate). Tests of the model-driven protocol turn it off.
    host_protocol = True

    def __init__(self, workspace, planner, actor, executor, *, splitter=None, auditor=None, sketcher=None, attempt_calls=8,
                 tool_calls=48, max_executions=512, confirm=None, say=print,
                 check_oracle=None, oracle_id=None, deploy_executor=None, resume_candidates=None, resume_repairs=None,
                 resume_contract=None, witness=None, conditions=False):
        self.ws, self.planner, self.actor, self.executor = workspace, planner, actor, executor
        # The planner is asked for the task's conditions, each with its tempting wrong output (CONDITIONS_NOTE). Off by
        # default: it changes what the model is asked, so it waits for a pre-registered run on a real model.
        self.conditions = bool(conditions)
        # Others' published work, asked about a checked method (kuttaka_runtime/witness.py). Off unless given: it
        # is knowledge from outside both arms of a "model + Kuttaka against the model alone" comparison.
        self.witness, self._testimony = witness, {}
        # The checked method's final run on the requested input may need more time than a check.
        self.deploy_executor = deploy_executor or executor
        self.splitter = splitter or planner
        self.auditor = auditor or planner
        self.sketcher = sketcher or planner
        self.attempt_calls, self.tool_calls, self.max_executions = attempt_calls, tool_calls, max_executions
        self.confirm, self.say = confirm, say
        self.usage = {'input_tokens': 0, 'output_tokens': 0, 'unknown_usage_calls': 0}
        self._checkbooks = {}
        self._frontiers = {}
        self._partials = {}
        self._active = set()
        self._split_outcomes = {}  # Exact parent check scope -> bounded scheduling diagnostics, never qualifications.
        self._active_boundary_restatements = set()
        self._job = None
        if check_oracle is not None and (not callable(check_oracle) or not isinstance(oracle_id, str)
                                         or not oracle_id.strip()):
            raise ValueError('a host check oracle requires a callable and a stable oracle_id')
        self.check_oracle, self.oracle_id = check_oracle, oracle_id
        self._resume_contract_checkpoint = self._contract_copy(resume_contract)
        self._continuity_spec, self._continuity_binding = None, None
        self._oracle_supported = set()
        if type(max_executions) is not int or max_executions < 0:
            raise ValueError('max_executions must be a nonnegative job-wide limit')
        self._execution_used = 0
        self._sketch_states = {}
        self._mechanical = {}
        self._support = {}    # pramāṇa of each check, by (input, expected); see pramana.py
        self._tested = {}     # artifact -> inputs beyond the checks it was tested on and held, by kind
        self._refuted_here = []
        self._known_before = set()
        self._given = {}      # prompt -> identity of its given conditions (see _given_id)
        self._adopt_tried = {}   # check scope -> methods reuse as it is ran on the whole input
        self._voices = {}   # (prompt, given conditions, input) -> what derivations said there (see _answer_gate)
        self._disputed = {}   # (prompt, given conditions) -> sources whose literal the auditor disputed
        self._piece_of = {}   # method delivered for a piece in this job -> the piece's prompt (see _piece_gate)
        self._valli_bottom = set()   # vallī methods whose bottom is the planner's reference
        self._reference_of = {}      # method -> digest of the reference it was delivered under (see _piece_gate)
        self._unsettled = {}   # (method, 'reference' or 'rival') -> a disagreement that nothing settled
        self._against_both = {}   # method -> inputs where the reference and the auditor each reject its value
        self._slow_probes, self._probe_values, self._finished_runs = set(), {}, set()
        self._raised = {}   # method -> inputs the plan declares valid on which it raised (see _raised_note)
        # Carry identities only. A fresh round must execute them again; no old pass counts, voices or support
        # become evidence here. A different immutable check scope cannot borrow these partials.
        self._resume_candidates = {
            scope: list(dict.fromkeys(a for a in candidates if isinstance(a, str)
                                     and re.fullmatch(r'[0-9a-f]{64}', a)))[:RESUME_CANDIDATES]
            for scope, candidates in (resume_candidates or {}).items()
            if isinstance(scope, str) and re.fullmatch(r'[0-9a-f]{64}', scope) and isinstance(candidates, list)
        } if isinstance(resume_candidates, dict) else {}
        # A stalled repair changes scheduling, never verification. Exact scope and executable text/dependencies
        # prevent a hint from suppressing a useful first repair of a changed task or candidate.
        self._stalled_repairs = {
            (scope, fingerprint)
            for scope, fingerprints in (resume_repairs or {}).items()
            if isinstance(scope, str) and re.fullmatch(r'[0-9a-f]{64}', scope) and isinstance(fingerprints, list)
            for fingerprint in fingerprints
            if isinstance(fingerprint, str) and re.fullmatch(r'[0-9a-f]{64}', fingerprint)
        } if isinstance(resume_repairs, dict) else set()

    def _count(self, step):
        self._mechanical[step] = self._mechanical.get(step, 0) + 1

    def _executions_left(self):
        return max(0, self.max_executions - self._execution_used)

    def _solve_piece(self, sub, parent_spec, depth, remaining_pieces):
        """Lease a fair share to one child, restoring the parent's absolute ceilings in every exit path.

        Unspent allowance remains available to later siblings. Reserve a parent attempt when there is
        room beyond a minimal attempt per child, and parent qualification/gate executions when possible.
        This bounds a struggling subtree without increasing the job's shared limits.
        """
        counter = self.actor.counter
        call_limit, execution_limit = counter['limit'], self.max_executions
        remaining_calls = max(0, call_limit - counter['used'])
        call_reserve = min(self.attempt_calls, max(0, remaining_calls - PIECE_MIN_CALLS * remaining_pieces))
        calls = (remaining_calls - call_reserve) // remaining_pieces
        if remaining_calls < PIECE_MIN_CALLS * remaining_pieces:
            # Below the minimum for every sibling, retain a chance to finish one useful piece rather than
            # allocating several unusably short attempts. This still obeys the shared ceiling.
            calls = min(PIECE_MIN_CALLS, remaining_calls)
        executions_left = self._executions_left()
        execution_reserve = min(len(parent_spec['checks']) + 4,
                                max(0, executions_left - remaining_pieces * max(1, len(sub['checks']))))
        executions = (executions_left - execution_reserve) // remaining_pieces
        self._event('solve_piece_budget', {'request': sub['request'], 'depth': depth + 1,
                    'remaining_pieces': remaining_pieces, 'calls': calls, 'executions': executions,
                    'parent_call_reserve': call_reserve, 'parent_execution_reserve': execution_reserve})
        if not calls or not executions:
            return {'status': 'incomplete', 'task': sub['request'], 'artifact': None, 'reused': [], 'subtasks': [],
                    'reason': 'insufficient remaining budget for this piece and its siblings'}
        counter['limit'] = counter['used'] + calls
        self.max_executions = self._execution_used + executions
        try:
            result = self.solve(sub['request'], sub['checks'], depth + 1, examples_origin='model')
            if result.get('status') != 'checked' and (not self._calls_left() or not self._executions_left()):
                result['budget_boundary'] = 'piece allocation exhausted; parent and sibling resources retained'
                self._event('solve_piece_budget_boundary', {'request': sub['request'],
                            'reason': result['budget_boundary']})
            return result
        finally:
            counter['limit'], self.max_executions = call_limit, execution_limit

    @staticmethod
    def _contract_copy(value):
        """One small JSON checkpoint; neither arbitrary objects nor proof state travel between rounds."""
        if type(value) is not dict:
            return None
        try:
            encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
            return json.loads(encoded) if len(encoded.encode('utf-8')) <= 65536 else None
        except (ValueError, TypeError, RecursionError):
            return None

    def _contract_binding(self, prompt, examples, origin):
        return digest({'request': prompt, 'examples': examples, 'origin': origin,
                       'oracle': {'enabled': self.check_oracle is not None, 'id': self.oracle_id}})

    def _read_retained_contract(self, prompt, examples, origin):
        saved = self._resume_contract_checkpoint
        if not saved:
            return None
        try:
            if (set(saved) != {'version', 'binding', 'check_scope', 'records', 'spec', 'identity'}
                    or type(saved['version']) is not int or saved['version'] != 1
                    or saved['binding'] != self._contract_binding(prompt, examples, origin)
                    or saved['identity'] != digest({k: v for k, v in saved.items() if k != 'identity'})):
                return None
            spec, records = saved['spec'], saved['records']
            if (set(spec) != {'contract', 'input', 'reference', 'verify', 'small_inputs', 'decomposition', 'given_seen'}
                    or type(spec['given_seen']) is not bool or type(spec['small_inputs']) is not list
                    or any(spec[k] is not None and type(spec[k]) is not str for k in ('reference', 'verify'))):
                return None
            supplied = [{'input': r['input'], 'expected': r['expected']} for r in records if r['source'] == 'supplied']
            proposed = [{'input': r['input'], 'expected': r['expected']} for r in records if r['source'] == 'model_proposal']
            book = CheckBook(prompt, spec['contract'], supplied, proposed, supplied_origin=origin)
            if book.records != records or book.scope_id != saved['check_scope']:
                return None
            if origin == 'user':
                anchors = CheckBook(prompt, spec['contract'], examples, []).checks if examples else []
                if supplied != anchors:
                    return None  # serialized records cannot invent, change or remove user anchors
            elif any(canonical(r['input']) not in {canonical(c['input']) for c in examples} for r in supplied):
                return None
            if spec['decomposition'] is not None:
                _decomposition(spec['decomposition'])
            restored = json.loads(canonical(spec))
            restored.update(checks=book.checks, check_scope=book.scope_id, examples_origin=origin,
                            checks_by=book.checks_by + ' (retained provisional contract)')
            return restored, book
        except (KeyError, ValueError, TypeError, RecursionError):
            return None

    def resume_contract(self):
        """Export the latest usable root contract as provisional data, never cached validation.

        Ephemeral disputes/voices cannot safely be reconstructed by this small
        checkpoint, so such roots decline continuity rather than lose obligations.
        """
        spec, binding = self._continuity_spec, self._continuity_binding
        if spec is None or binding is None:
            return None
        prompt, examples, origin = binding
        if (spec.get('disputed_hand') or spec.get('property_dispute')
                or any(key[0] == prompt for key in self._voices)
                or any(key[0] == prompt for key in self._disputed)):
            return None
        book = self._checkbooks.get(spec.get('check_scope'))
        if (book is None or book.contract != spec.get('contract') or book.checks != spec.get('checks')
                or book._request != prompt):
            return None
        fields = {key: spec.get(key) for key in ('contract', 'input', 'reference', 'verify', 'decomposition')}
        fields.update(small_inputs=spec.get('small_inputs') or [], given_seen=bool(spec.get('given_seen')))
        saved = {'version': 1, 'binding': self._contract_binding(prompt, examples, origin),
                 'check_scope': book.scope_id, 'records': book.records, 'spec': fields}
        saved = self._contract_copy(saved)
        if saved is None:
            return None
        saved['identity'] = digest(saved)
        saved = self._contract_copy(saved)
        # Use the import validator too, so exports always reconstruct the exact scope/provenance.
        prior, self._resume_contract_checkpoint = self._resume_contract_checkpoint, saved
        try:
            return saved if self._read_retained_contract(prompt, examples, origin) is not None else None
        finally:
            self._resume_contract_checkpoint = prior

    def resume_candidates(self):
        """Completed partial identities for another round, never their old qualification or pass counts."""
        candidates = {scope: list(ids) for scope, ids in self._resume_candidates.items()}
        for scope, frontier in self._frontiers.items():
            completed = [row['artifact_id'] for row in sorted(
                frontier.frontier, key=lambda row: (-row['passed_count'], row['artifact_id']))
                if (scope, row['artifact_id']) in self._partials]
            candidates[scope] = list(dict.fromkeys(completed + candidates.get(scope, [])))[:RESUME_CANDIDATES]
        return candidates

    def resume_repairs(self):
        """JSON-safe scheduling hints for later rounds; these carry no pass counts or truth claims."""
        hints = {}
        for scope, fingerprint in sorted(self._stalled_repairs):
            hints.setdefault(scope, []).append(fingerprint)
        return hints

    def _resume_partial(self, prompt, request, spec):
        """Recheck exact-scope partials before spending another round rediscovering their source."""
        candidates = self._resume_candidates.pop(spec['check_scope'], [])
        best, pending = None, []
        book = self._checkbooks[spec['check_scope']]
        for index, artifact in enumerate(candidates):
            if self._executions_left() < len(spec['checks']):
                pending.extend(candidates[index:])
                break
            try:
                with ArtifactMemory(self.ws.memory) as memory:
                    payload = memory.get(artifact)
                    if payload.get('kind') != 'method' or self._refuted_in(artifact, prompt, memory):
                        continue
                    source, dependencies = payload['body']['source'], payload['dependencies']
                outcome = self.admit(request, spec['checks'], None, 'resumed partial', stored=artifact,
                                     check_scope=spec['check_scope'], producer='partial revalidation',
                                     reason='earlier partial rechecked on the current checks', step='resume')
            except (KeyError, ValueError, TypeError, OSError, PermissionError):
                self._event('solve_partial_resume_skipped', {'artifact': artifact, 'reason': 'artifact unavailable'})
                continue
            self._event('solve_partial_resumed', {'artifact': artifact, 'check_scope': spec['check_scope'],
                        'status': outcome['status'], 'run': outcome['run']})
            if outcome['status'] == 'checked':
                return outcome
            receipts = [event['body'] for event in Journal.read(Path(outcome['run']) / 'events.jsonl')
                        if event['kind'] == 'method_check_receipt' and event['body']['artifact'] == artifact]
            if (len(receipts) != len(spec['checks'])
                    or {r['check_index'] for r in receipts} != set(range(len(spec['checks'])))
                    or any(r['status'] not in ('passed', 'counterexample') for r in receipts)):
                pending.append(artifact)  # retain the identity, but no pass evidence, after an infrastructure stop
                continue  # missing execution is not partial success
            receipts.sort(key=lambda r: r['check_index'])
            passed, failed, passed_ids = [], [], []
            for check, record, receipt in zip(spec['checks'], book.records, receipts):
                if receipt['status'] == 'passed':
                    passed.append(check)
                    passed_ids.append(record['id'])
                else:
                    detail = dict(check, execution_status='completed' if 'actual' in receipt else 'candidate_error')
                    if 'actual' in receipt:
                        detail['got'] = receipt['actual']
                    failed.append(detail)
            partial = {'artifact': artifact, 'source': source, 'uses': dependencies,
                       'passed_checks': passed, 'failed_checks': failed, 'check_scope': spec['check_scope']}
            self._partials[(spec['check_scope'], artifact)] = partial
            frontier = self._frontiers.setdefault(spec['check_scope'], EvidenceFrontier(r['id'] for r in book.records))
            frontier.observe(_execution_fingerprint(source, dependencies), artifact, passed_ids, completed=True)
            outcome['partial'] = partial
            if best is None or len(passed) > len(best['partial']['passed_checks']):
                best = outcome
        if pending:
            self._resume_candidates[spec['check_scope']] = pending
        return best

    def _execute(self, job, executor=None):
        if not self._executions_left():
            raise RuntimeError('shared execution limit reached')
        self._execution_used += 1  # Failed/interrupted executions remain spent.
        number = self._execution_used
        self._event('solve_execution_intent', {'number': number, 'job_digest': digest(job)})
        try:
            result = (executor or self.executor)(job)
        except Exception as error:
            self._event('solve_execution_error', {'number': number, 'type': type(error).__name__})
            raise
        self._event('solve_execution_finished', {'number': number,
                    'status': result.get('status') if isinstance(result, dict) else 'invalid_response'})
        return result

    def _event(self, kind, body):
        if self._job is not None:
            return self._job.append(kind, body)

    def _snapshot(self, spec):
        book = self._checkbooks[spec['check_scope']]
        return {'scope': book.scope_id, 'contract': spec['contract'], 'checks': book.checks,
                'records': book.records, 'conflicts': book.conflicts, 'revisions': book.revisions,
                'admission': book.metadata, 'support': 'finite_checks_only',
                'model_verifier': spec.get('verify'),
                'oracle_id': self.oracle_id,
                'oracle_supported_check_ids': [r['id'] for r in book.records
                                               if (book.scope_id, r['id']) in self._oracle_supported]}

    def _register_revision(self, book, old_scope):
        self._checkbooks.pop(old_scope, None)
        self._checkbooks[book.scope_id] = book
        self._carry_oracle_support(old_scope, book)
        self._event('solve_check_revised', {'scope_before': old_scope,
                    'scope_after': book.scope_id, 'revision': book.revisions[-1]})

    def _carry_oracle_support(self, old_scope, book):
        supported = {check_id for scope, check_id in self._oracle_supported if scope == old_scope}
        # Support follows unchanged input/expected records through an unrelated
        # revision; a changed check ID must earn new support.
        self._oracle_supported.update((book.scope_id, r['id']) for r in book.records
                                      if r['id'] in supported)

    def _support_label(self, book, record):
        """The pramāṇa of one active check (see pramana.py), within its contract."""
        if record['origin'] == 'user' or (book.scope_id, record['id']) in self._oracle_supported:
            return pramana.GIVEN
        return self._support.get((book.contract, pramana.key(record)), pramana.HAND)

    def _set_support(self, book, check, label):
        self._support[(book.contract, pramana.key(check))] = label

    def _given_id(self, prompt):
        """The identity of a task's given conditions: its user (or parent) examples, as planned."""
        return self._given.get(prompt)

    def _remember_confirmed(self, prompt, spec, check):
        """Keep a confirmed check for this task, if the check contract can hold it (one that cannot would
        stop every later carry of this task's checks)."""
        try:
            CheckBook(prompt, spec['contract'], [], [check])
        except ValueError:
            return False
        self.ws.remember_confirmed(prompt, check, self._given_id(prompt))
        return True

    def _confirm(self, prompt, book, check):
        """A check two derivations agree on: labelled so, and kept for this task across replans and rounds."""
        self._set_support(book, check, pramana.TWO_DERIVATIONS)
        self.ws.remember_confirmed(prompt, check, self._given_id(prompt))

    def _carry_confirmed(self, prompt, spec):
        """Paramparā: what two derivations settled for this task stays settled when it is planned again.

        Confirmed checks of the same input form replace the plan's own checks on the same inputs and are
        added to the others, in the slots the check contract has free; a user's check on the same input
        stands."""
        form = pramana.form(spec['checks'])
        confirmed = self.ws.confirmed(prompt, self._given_id(prompt))

        def holds(check):
            try:
                CheckBook(prompt, spec['contract'], [], [check])
                return True
            except ValueError:
                return False
        carry = [c for c in confirmed if pramana.form([c]) == form and holds(c)]
        book = self._checkbooks[spec['check_scope']]
        current = {canonical(r['input']): r for r in book.records}
        carry = [c for c in carry if not (canonical(c['input']) in current
                                          and (current[canonical(c['input'])]['origin'] == 'user'
                                               or canonical(current[canonical(c['input'])]['expected'])
                                               == canonical(c['expected'])))]
        if not carry:
            for c in confirmed:   # already present: keep its label
                if canonical(c['input']) in current and canonical(current[canonical(c['input'])]['expected']) == canonical(c['expected']):
                    self._set_support(book, c, pramana.TWO_DERIVATIONS)
            return 0
        replaced = {canonical(c['input']) for c in carry}
        keep = [r for r in book.records if canonical(r['input']) not in replaced]
        supplied = [{'input': r['input'], 'expected': r['expected']} for r in keep if r['source'] == 'supplied']
        proposed = [{'input': r['input'], 'expected': r['expected']} for r in keep if r['source'] != 'supplied']
        # Carried checks fill free slots only: the plan's own checks stay. (A counterexample confirmed against
        # the current method may displace a hand check, in _extend_checks; one from an earlier round may not.)
        new, taken, _ = _admit_one_by_one(prompt, spec, supplied, proposed, carry)
        if new is None or not taken:
            self._event('solve_confirmed_not_carried', {'check_scope': spec['check_scope'], 'count': len(carry)})
            return 0
        carry = taken
        self._checkbooks[new.scope_id] = new
        self._carry_oracle_support(book.scope_id, new)
        for c in confirmed:
            if any(canonical(c['input']) == canonical(r['input']) and canonical(c['expected']) == canonical(r['expected'])
                   for r in new.records):
                self._set_support(new, c, pramana.TWO_DERIVATIONS)
        spec['checks'], spec['check_scope'] = new.checks, new.scope_id
        spec['checks_by'] += f' (+{len(carry)} carried, settled earlier for this task)'
        self._event('solve_confirmed_carried', {'check_scope': new.scope_id, 'carried': carry})
        return len(carry)

    def _withdraw_support(self, contract, label):
        """A source shown wrong supports nothing: its labels under this contract become hand values."""
        for key in [k for k, v in self._support.items() if k[0] == contract and v == label]:
            del self._support[key]

    def _complete_given(self, prompt, spec):
        """The given conditions must exist before they can be tested. When the plan has neither a
        reference nor a verify property, and the user gave no examples (which are given conditions
        themselves), the planner is asked once more for them; only those two fields of its reply are
        used, so the contract and checks stay as planned."""
        book = self._checkbooks[spec['check_scope']]
        left = self.actor.counter['limit'] - self.actor.counter['used']
        if (spec.get('reference') or spec.get('verify') or spec.get('given_seen') or left < GIVEN_RESERVE
                or any(r['origin'] == 'user' for r in book.records)):
            return False   # given already (even if since set aside as wrong), or the method needs the calls
        spec['given_seen'] = True
        reply = self._ask(self.planner, {'task': prompt, 'user_examples': [], 'lessons': [],
                                         'previous_contract': spec['contract'],
                                         'given_conditions_missing': (
                                             'Your plan for this contract had no reference and no verify. Return the '
                                             'plan again with a reference (a slow but obviously correct way, for small '
                                             'inputs) or a verify property; for small inputs one almost always exists.')})
        found = _reference_fields(reply)
        verify = reply.get('verify') if isinstance(reply.get('verify'), str) and reply['verify'].strip() else None
        if found['reference']:
            spec['reference'], spec['small_inputs'] = found['reference'], found['small_inputs']
        if verify:
            spec['verify'] = verify
        self._event('solve_given_conditions_requested', {'check_scope': spec['check_scope'],
                    'reference': bool(found['reference']), 'verify': bool(verify)})
        return bool(found['reference'] or verify)

    def _derive_blind(self, prompt, spec):
        """A second derivation of every check that rests on the hand value alone, before any code (one call).

        The losses to the model alone were traced to wrong hand-worked checks (h-m1-digits, r2-kinematics): a
        correct method fails a wrong check and is rewritten until something fits it, or copies it. Settlement
        (`_settle_given`) tests the checks when the planner gave a reference or a property; without either, a hand
        check entered the attempt untested. So the blind auditor re-derives each such check from the contract
        alone. Agreed, nothing changes (one model's two derivations may share a mistake; the check stays open to a
        rival's contest). Disputed, it is unsettled testimony and is set aside, unless fewer than MIN_SETTLED checks
        would remain and no user check stands; then nothing is set aside (this step never voids a task) and the
        dispute is recorded. A null is no answer. A user's check and
        one an oracle supports are given conditions and are never asked about. Returns True when the checks changed."""
        book = self._checkbooks[spec['check_scope']]
        hand = [r for r in book.records if r['origin'] != 'user' and self._support_label(book, r) == pramana.HAND]
        left = self.actor.counter['limit'] - self.actor.counter['used']
        if not self.derive_blind or not hand or spec.get('blind_derived') == book.scope_id or left < GIVEN_RESERVE:
            return False
        spec['blind_derived'] = book.scope_id
        reply = self._ask(self.auditor, {'task': prompt, 'contract': spec['contract'],
                                         'inputs': [r['input'] for r in hand]})
        values = reply.get('expected') if isinstance(reply, dict) else None
        if not isinstance(values, list) or len(values) != len(hand):
            self._event('solve_blind_derivation', {'check_scope': book.scope_id, 'outcome': 'no_reply'})
            return False
        agreed, disputed = [], []
        for record, value in zip(hand, values):
            if value is None or value is pramana.MISSING:
                continue
            # exactly, as the checker compares: a rounded hand value is disputed and later corrected by the method
            # (T2); a right hand value an auditor rounded returns when the method gives it (_test_set_aside)
            (agreed if same_value(value, record['expected']) else disputed).append((record, value))
        # Agreement changes nothing: the planner and the auditor are one model, and a mistake they share must stay
        # open to a rival's contest (a two-derivation label would shield it: the eleventh review's Q7).
        keys = {canonical(r['input']) for r, _ in disputed}
        keep = [r for r in book.records if canonical(r['input']) not in keys]
        set_aside = bool(disputed) and (len(keep) >= MIN_SETTLED or any(r['origin'] == 'user' for r in keep))
        changed = False
        if set_aside:
            supplied = [{'input': r['input'], 'expected': r['expected']} for r in keep if r['source'] == 'supplied']
            proposed = [{'input': r['input'], 'expected': r['expected']} for r in keep if r['source'] != 'supplied']
            try:
                new = CheckBook(prompt, spec['contract'], supplied, proposed, supplied_origin=spec['examples_origin'],
                                model_limit=min(MAX_MODEL_CHECKS, len(proposed)))
            except ValueError:
                set_aside = False
            else:
                self._checkbooks[new.scope_id] = new
                self._carry_oracle_support(book.scope_id, new)
                spec['checks'], spec['check_scope'] = new.checks, new.scope_id
                spec['blind_derived'] = new.scope_id
                spec['checks_by'] += f' ({len(disputed)} set aside: a blind re-derivation disputes them)'
                # Kept to be tested against the method written without them (_test_set_aside).
                spec['disputed_hand'] = (spec.get('disputed_hand') or []) + [
                    {'input': r['input'], 'hand': r['expected'], 'blind': v} for r, v in disputed]
                changed = True
        for record, value in disputed:   # no values in the lesson: a later writer must not copy either one
            self.ws.learn(prompt, f'the hand-worked value of check {json.dumps(record["input"])[:80]} was disputed by a '
                                  f'blind re-derivation: work it out with care', form=pramana.form(spec['checks']))
        self._count('blind_agreed' if not disputed else 'blind_disputed')
        self._event('solve_blind_derivation', {'check_scope': book.scope_id, 'agreed': [r['input'] for r, _ in agreed],
                    'disputed': [{'input': r['input'], 'hand_value': r['expected'], 'blind_value': v} for r, v in disputed],
                    'set_aside': set_aside, 'outcome': 'disputed' if disputed else 'agreed'})
        if disputed:
            self.say(f'checks derived again before code: {len(disputed)} hand value(s) disputed'
                     + (', set aside' if set_aside else ', kept (too few would remain)'))
        return changed

    def _test_set_aside(self, prompt, spec, outcome):
        """A third derivation for each hand check a blind re-derivation disputed and set aside: the method that
        passed the other checks, written without seeing it, is run on its input (one sandbox run).
          - its answer is the blind value: two derivations against the hand value; the check returns corrected,
            resting on two derivations;
          - its answer is the hand value: the check returns as it was, resting on the hand value alone (the
            method may share the planner's mistake, so its agreement is no derivation): the memorization
            guards see it again, and a method that copies it is still refuted (the second review's 7 -> 22);
          - a third value, or it raises there, or it cannot be run: the method is supported there by nothing but
            itself; the check returns as it was and the method must face it in a new attempt, as before the check
            was set aside. Nothing is refuted for every task: with three values there is no majority (thirteenth
            review: S4, a right method beside two wrong derivations; S2, a wrong method beside a right hand value;
            S9, a method that raises or is not run).
        Returns True when a new attempt must face a returned check."""
        pending = spec.get('disputed_hand') or []
        if not pending:
            return False
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, outcome['artifact'])
        if self._executions_left() <= 3:
            return False   # no execution to spare for a new attempt: the method is kept, the checks stay aside (T4)
        got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), [p['input'] for p in pending], libraries)
        if not (isinstance(got, list) and len(got) == len(pending)):
            got = [None] * len(pending)   # not run: the checks return, and the method faces them (S9)
        restored, corrected, left, unrun, three_way = [], [], [], [], []
        for item, answer in zip(pending, got):
            value = answer.get('value', pramana.MISSING) if isinstance(answer, dict) else pramana.MISSING
            if value is pramana.MISSING:
                restored.append({'input': item['input'], 'expected': item['hand']})
                unrun.append(item['input'])
            elif same_value(value, item['hand']):
                restored.append({'input': item['input'], 'expected': item['hand']})
            elif item['blind'] is not pramana.MISSING and _close(item['blind'], value):
                corrected.append({'input': item['input'], 'expected': _like(item['hand'], value)})
            elif 'reference' in item and _close(item['reference'], value):   # a settlement's set-aside check
                corrected.append({'input': item['input'], 'expected': _like(item['hand'], value)})
            else:   # three values: no majority; the check returns and the method faces it (S2, S4)
                restored.append({'input': item['input'], 'expected': item['hand']})
                three_way.append(item['input'])
        spec['disputed_hand'] = left
        if not (restored or corrected):
            return False
        book = self._checkbooks[spec['check_scope']]
        supplied = [{'input': r['input'], 'expected': r['expected']} for r in book.records if r['source'] == 'supplied']
        proposed = [{'input': r['input'], 'expected': r['expected']} for r in book.records if r['source'] != 'supplied']
        new, fitting, _ = _admit_one_by_one(prompt, spec, supplied, proposed, corrected + restored, displaceable=[])
        if new is not None:
            new.carry_revisions(book.revisions)
        self._event('solve_set_aside_tested', {'artifact': outcome['artifact'], 'restored': [c['input'] for c in restored],
                    'corrected': [c['input'] for c in corrected], 'not_run': unrun,
                    'three_way': three_way, 'admitted': len(fitting or [])})
        if new is None or not fitting:
            return False
        self._checkbooks[new.scope_id] = new
        self._carry_oracle_support(book.scope_id, new)
        for check in fitting:
            if check in corrected:
                self._set_support(new, check, pramana.TWO_DERIVATIONS)
                self._remember_confirmed(prompt, spec, check)
        spec['checks'], spec['check_scope'] = new.checks, new.scope_id
        spec['checks_by'] += f' (+{len(fitting)} set aside before code, tested against the method)'
        facing = unrun + three_way
        return bool(facing) and any(c['input'] in facing for c in fitting)

    def _established(self, prompt, spec, book, records, computed):
        """What is already known about these check inputs: ({id(record): value}, [methods]).

        A method stored and checked by an earlier task (not in this solve) is recognized as this task's function
        when, as it is or with this task's fields put in its roles (solve_prastara.roles), it gives every value this
        task already has support for: the user's examples and the checks the reference agrees with, at least
        KNOWN_MIN_SUPPORT of them. Its values on the other checks are then knowledge: established before, by other
        derivations and gates, not by this task's hand. Where recognized methods differ on a check, none speaks
        there. Screened in one sandbox run per ROLE_SCREEN_VALUES values; no model call."""
        form, out_form = pramana.form(spec['checks']), pramana.output_form(spec['checks'])
        if form is None or out_form is None or not self._known_before:
            return {}, []
        task_in, task_out = json.loads(form), json.loads(out_form)
        supported = [n for n, (r, v) in enumerate(zip(records, computed))
                     if self._support_label(book, r) == pramana.GIVEN
                     or (v is not pramana.MISSING and same_value(v, r['expected']))]
        if len(supported) < KNOWN_MIN_SUPPORT:
            return {}, []
        methods = self._recalled_methods(prompt, form, ROLE_METHODS,
                                         keep=lambda m: m[0] in self._known_before)
        candidates = []
        for artifact, name, method_in, method_out in methods:
            if solve_prastara._fits(task_out, method_out) and method_in == task_in:
                candidates.append((artifact, None))
            candidates += [(artifact, a) for _, a, _, _ in solve_prastara.roles(
                [(artifact, name, method_in, method_out)], task_in, task_out, limit=ROLE_PER_KNOWN)[1]]
        if not candidates:
            return {}, []
        from .demo import load_method
        libraries = {}
        for artifact in dict.fromkeys(a for a, _ in candidates):
            source, inherited = load_method(self.ws.memory, artifact)
            libraries.update(inherited)
            libraries[artifact] = source
        inputs = [r['input'] for r in records]
        per_run = max(1, ROLE_SCREEN_VALUES // len(inputs))
        recognized = []
        for start in range(0, len(candidates), per_run):
            chunk = candidates[start:start + per_run]
            if self._executions_left() <= SETTLE_RESERVE + len(records):
                break
            got = self._sandbox_job(DIFFERENTIAL_RUN % (solve_prastara.ROLE_SCREEN % ([list(c) for c in chunk],), 'screen'),
                                    [[n, v] for n in range(len(chunk)) for v in inputs], libraries)
            if not (isinstance(got, list) and len(got) == len(chunk) * len(inputs)):
                continue
            for n, candidate in enumerate(chunk):
                rows = got[n * len(inputs):(n + 1) * len(inputs)]
                if all(isinstance(g, dict) and 'value' in g for g in rows) and all(
                        same_value(rows[i]['value'], records[i]['expected']) for i in supported):
                    recognized.append((candidate, [g['value'] for g in rows]))
        known = {}
        for i, record in enumerate(records):
            if i in supported or not recognized:
                continue
            said = [values[i] for _, values in recognized]
            if all(same_value(v, said[0]) for v in said):
                known[id(record)] = said[0]
        by = [{'method': a, 'assignment': asg} for (a, asg), _ in recognized]
        if recognized:
            self._event('solve_established', {'check_scope': spec['check_scope'], 'recognized': by,
                                              'speaks_on': [r['input'] for r in records if id(r) in known]})
        return known, by

    def _settle_given(self, prompt, spec):
        """Pramāṇa before any code: test the checks themselves, before the model writes a method.

        The planner works its checks out by hand and can be wrong; a correct method then fails a
        wrong check and is rewritten until something fits it (h-m1-digits, r2-kinematics). So the
        planner's slow reference runs on every check input, and its verify property on every check,
        in the sandbox. Agreement becomes the check's support. A check written by the model that
        one of them contradicts is settled two against one:
          - the reference and the property agree with each other against the check: it is revised;
          - the check has the other source on its side: the contradicting source is wrong here and
            is not used again for this task. A property votes only when it tells the two values
            apart: one that accepts both the check's value and the reference's is no witness;
          - otherwise the auditor re-derives the value blindly (one call for every such check): if it
            agrees with the reference, or gives a value the property accepts, the check is revised;
            if it agrees with the check, the reference or property is dropped; if nothing agrees,
            the check is unsettled testimony and is set aside (its place kept in the journal).
        A user's check is a given condition, as in the kuṭṭaka: never revised, and a reference or
        property that contradicts one is dropped. Settlement spends only executions beyond those one
        qualification of a method needs, and calls the auditor only when a call is left for the
        method. Returns a void record when fewer than MIN_SETTLED checks would remain and no user
        check stands, else None."""
        reference, prop = spec.get('reference'), spec.get('verify')
        if reference or prop:
            spec['given_seen'] = True    # never asked for again, even if shown wrong below
        book = self._checkbooks[spec['check_scope']]
        records = book.records
        reserve = max(SETTLE_RESERVE, len(records) + 1)   # one qualification of a method is kept back
        spare = lambda: self._executions_left() > reserve  # noqa: E731
        if not (reference or prop) or not spare():
            return None
        computed = [pramana.MISSING] * len(records)
        if reference:
            out = self._sandbox(DIFFERENTIAL_RUN % (reference, 'reference'), [r['input'] for r in records])
            if isinstance(out, list) and len(out) == len(records):
                computed = [o['value'] if isinstance(o, dict) and 'value' in o else pramana.MISSING for o in out]
        known, known_by = self._established(prompt, spec, book, records, computed) if spare() else ({}, [])
        if known_by:
            spec['established_by'] = known_by   # set against the method written for this task (pariksha)
        verdicts = [None] * len(records)
        if prop and spare():
            verdicts = self.verify(spec, [(r['input'], r['expected']) for r in records]) or verdicts
        blind = set()   # checks where the property accepts every near miss too: its acceptance is no witness
        if prop and spare() and any(v is True for v in verdicts):
            probes = [(id(r), r['input'], miss) for r, v in zip(records, verdicts) if v is True
                      for miss in pramana.near_misses(r['expected'], NEAR_MISSES)]
            told = self.verify(spec, [(i, miss) for _, i, miss in probes]) if probes else None
            if told:
                rejected = {origin for (origin, _, _), verdict in zip(probes, told) if verdict is False}
                blind = {origin for origin, _, _ in probes} - rejected
        # The two errors of a judge (Nyāyasūtra 5.2.22-23): a near miss tests that the property rejects a wrong value of
        # the right shape; a stated condition's tempting wrong output tests that it uses that condition. A property
        # that accepts an output the plan says breaks a condition witnesses for no check (THE_ORIGINALS.md, real gap 1).
        blind_to = []
        if prop and spec.get('conditions'):
            if spare():
                blind_to = self._condition_test(spec)
            else:   # said, not skipped: the property then stays a witness untested on the stated conditions
                self._event('solve_condition_test', {'check_scope': spec['check_scope'], 'stated': len(spec['conditions']),
                                                     'outcome': 'not run: no execution left', 'property_rejects': 0,
                                                     'property_accepts': 0, 'blind_to': []})
        if blind_to:
            blind = {id(r) for r in records}
        evidence = {'check_scope': book.scope_id, 'checks': len(records), 'established_by': known_by,
                    'reference_values': sum(v is not pramana.MISSING for v in computed),
                    'property_verdicts': sum(v is not None for v in verdicts), 'property_blind': len(blind)}
        if spec.get('conditions'):
            evidence['conditions'] = [{'condition': c['condition'], 'tested': c.get('tested', 'not tested')}
                                      for c in spec['conditions']]
        wrong, audit, discriminate, rejected_check, weak = set(), [], [], {}, set(blind)
        known_revise = []
        rows = list(zip(records, computed, verdicts))
        # Given conditions first: a source that contradicts one is wrong before anything else is weighed.
        rows.sort(key=lambda row: self._support_label(book, row[0]) != pramana.GIVEN)
        for record, value, verdict in rows:
            against = value is not pramana.MISSING and not same_value(value, record['expected'])
            rejected = verdict is False
            rejected_check[id(record)] = rejected
            if not (against or rejected):
                continue
            if self._support_label(book, record) == pramana.GIVEN:
                wrong.update(['reference'] if against else [])
                wrong.update(['property'] if rejected else [])
            elif against and id(record) in known and same_value(known[id(record)], value):
                # What is known speaks: a method checked in an earlier task, recognized as this task's function,
                # agrees with the reference against the hand value. Two derivations made apart outweigh a hand
                # value and a re-derivation by the same model (W4-m collide: those two outvoted a right reference).
                known_revise.append((record, _like(record['expected'], value), 'reference and established knowledge'))
            elif against and verdict is True:
                discriminate.append((record, value))   # does the property also accept the reference's value?
            elif rejected and value is not pramana.MISSING and not against:
                wrong.add('property')                  # the check and the reference agree
            else:
                audit.append((record, value if against else pramana.MISSING))
        if discriminate:
            told = (self.verify(spec, [(r['input'], v) for r, v in discriminate])
                    if 'property' not in wrong and spare() else None)
            for (record, value), verdict in zip(discriminate, told or [None] * len(discriminate)):
                if verdict is False:
                    wrong.add('reference')             # the property tells them apart and sides with the check
                else:
                    weak.add(id(record))               # it cannot tell them apart: no witness here
                    audit.append((record, value))
        # A dispute raised only by a source already shown wrong needs no settling.
        audit = [(r, v if 'reference' not in wrong else pramana.MISSING) for r, v in audit
                 if (v is not pramana.MISSING and 'reference' not in wrong)
                 or (rejected_check[id(r)] and 'property' not in wrong)]
        revise = list(known_revise)   # (record, value, by)
        if audit and prop and 'property' not in wrong and spare():
            # The reference and the property agreeing with each other against a hand value: two derivations.
            pending = [(r, v) for r, v in audit if v is not pramana.MISSING and id(r) not in weak]
            accepted = self.verify(spec, [(r['input'], v) for r, v in pending]) if pending else None
            for (record, value), verdict in zip(pending, accepted or []):
                if verdict is True:
                    revise.append((record, _like(record['expected'], value), 'reference and property'))
            audit = [(r, v) for r, v in audit if not any(r is x for x, _, _ in revise)]
        unsettled, auditor_said = [], {}

        def fits(record, value):   # a value the check contract can hold (size, depth, JSON)
            try:
                CheckBook(prompt, spec['contract'], [], [{'input': record['input'], 'expected': value}])
                return True
            except ValueError:
                return False
        calls_left = self.actor.counter['limit'] - self.actor.counter['used']
        if audit and calls_left >= 2:   # one for the re-derivation, at least one left for the method
            reply = self._ask(self.auditor, {'task': prompt, 'contract': spec['contract'],
                                             'inputs': [r['input'] for r, _ in audit]})
            values = reply.get('expected')
            if isinstance(values, list) and len(values) == len(audit):
                needs_property = [(r, a) for (r, v), a in zip(audit, values) if v is pramana.MISSING]
                accepted = (self.verify(spec, [(r['input'], a) for r, a in needs_property])
                            if needs_property and prop and 'property' not in wrong and spare() else None)
                accepts = {id(r): verdict for (r, _), verdict in zip(needs_property, accepted or [])}
                auditor_said = {id(r): a for (r, _), a in zip(audit, values)}
                for (record, value), audited in zip(audit, values):
                    if same_value(audited, record['expected']):
                        wrong.update(['reference'] if value is not pramana.MISSING else [])
                        wrong.update(['property'] if rejected_check.get(id(record)) else [])
                    elif value is not pramana.MISSING and same_value(audited, value):
                        revise.append((record, _settled_value(record['expected'], audited, value),
                                       'reference and blind re-derivation'))
                    elif value is pramana.MISSING and accepts.get(id(record)) is True:
                        revise.append((record, _like(record['expected'], audited), 'property and blind re-derivation'))
                    else:
                        unsettled.append(record)
            else:
                evidence['audit'] = 'no usable reply'
        elif audit:
            evidence['audit'] = 'no model call to spare'
        # A source shown wrong anywhere supports nothing: its revisions are withdrawn.
        revise = [(r, v, why) for r, v, why in revise
                  if not ('reference' in why and 'reference' in wrong) and not ('property' in why and 'property' in wrong)]
        # A check known wrong whose right value the contract cannot hold is set aside, not kept.
        unsettled += [r for r, v, _ in revise if not fits(r, v)]
        revise = [(r, v, why) for r, v, why in revise if fits(r, v)]
        if 'property' in wrong:
            # Kept as evidence: the result reports why the property was overruled before code.
            spec['property_overruled'] = {
                'source_digest': digest(prop), 'support': 'model_authored_heuristic',
                'verdicts': list(verdicts),
                'resolution': ("verifier overruled: it rejects the user's own examples, which stand"
                               if any(rejected_check.get(id(r)) and self._support_label(book, r) == pramana.GIVEN
                                      for r in records)
                               else 'verifier overruled before code: the reference or a blind re-derivation '
                                    'agrees with the checks it rejects')}
        for source, field in (('reference', 'reference'), ('property', 'verify')):
            if source in wrong:
                spec[field] = None
        if spec.get('verify'):
            # Verdicts already given on these exact pairs are not run again at delivery.
            spec['property_cache'] = {pramana.key(r): v for r, v in zip(records, verdicts) if v is not None}
        # Set aside first, from the checks as planned; then revise on the book that remains, so the
        # revisions stay in its record.
        void = None
        if unsettled:
            keys = {canonical(r['input']) for r in unsettled}
            keep = [r for r in book.records if canonical(r['input']) not in keys]
            if len(keep) < MIN_SETTLED and not any(r['origin'] == 'user' for r in keep):
                void = {'reason': 'the checks cannot be settled: the hand value, the reference, the property and '
                                  'a blind re-derivation do not agree', 'unsettled': [r['input'] for r in unsettled]}
            else:
                supplied = [{'input': r['input'], 'expected': r['expected']} for r in keep if r['source'] == 'supplied']
                proposed = [{'input': r['input'], 'expected': r['expected']} for r in keep if r['source'] != 'supplied']
                try:
                    new = CheckBook(prompt, spec['contract'], supplied, proposed,
                                    supplied_origin=spec['examples_origin'],
                                    model_limit=min(MAX_MODEL_CHECKS, len(proposed)))
                except ValueError as error:
                    evidence['set_aside_rejected'] = str(error)
                    unsettled = []
                else:
                    self._checkbooks[new.scope_id] = new
                    self._carry_oracle_support(book.scope_id, new)
                    book = new
                    # Kept to be heard a third time, from the method written without them (_test_set_aside), as
                    # the blind derivation's are: set aside here, a check was never heard again (W4-m collide).
                    computed_at = {id(r): v for r, v, _ in rows}
                    spec['disputed_hand'] = (spec.get('disputed_hand') or []) + [
                        dict({'input': r['input'], 'hand': r['expected'],
                              'blind': auditor_said.get(id(r), pramana.MISSING)},
                             **({'reference': computed_at[id(r)]} if 'reference' not in wrong
                                and computed_at.get(id(r), pramana.MISSING) is not pramana.MISSING else {}))
                        for r in unsettled]
        applied = []
        if void is None:
            for record, value, why in revise:
                old_scope = book.scope_id
                try:
                    changed = book.revise(record['input'], value, support='two_derivations_agree',
                                          evidence={'kind': 'settled_before_code', 'by': why,
                                                    'hand_value': record['expected'], 'auditor': self.auditor.identity})
                except (ValueError, CheckConflict) as error:   # e.g. a value beyond the check contract's limits
                    evidence.setdefault('revisions_rejected', []).append({'input': record['input'], 'reason': str(error)})
                    continue
                if changed:
                    self._register_revision(book, old_scope)
                    self._confirm(prompt, book, {'input': record['input'], 'expected': value})
                    applied.append((record, value, why))
        # Support from the sources that survived, for the checks as they now stand.
        survivors = {'reference': 'reference' not in wrong and reference,
                     'property': 'property' not in wrong and prop}
        by_input = {canonical(r['input']): (v, d, id(r)) for r, v, d in zip(records, computed, verdicts)}
        for record in book.records:
            if self._support_label(book, record) != pramana.HAND:
                continue
            value, verdict, origin = by_input.get(canonical(record['input']), (pramana.MISSING, None, None))
            if survivors['reference'] and value is not pramana.MISSING and same_value(value, record['expected']):
                self._set_support(book, record, pramana.REFERENCE)
            elif survivors['property'] and verdict is True and origin not in weak:
                self._set_support(book, record, pramana.PROPERTY)
        spec['checks'], spec['check_scope'] = book.checks, book.scope_id
        if void is None and (applied or unsettled):
            spec['checks_by'] += f' ({len(applied)} revised, {len(unsettled)} set aside before code)'
        # Every disputed check with what each voice said, so a settlement can be read from the journal alone.
        evidence['disputes'] = [
            dict({'input': r['input'], 'hand': r['expected']},
                 **({'reference': v} if v is not pramana.MISSING else {}),
                 **({'property': d} if d is not None else {}),
                 **({'auditor': auditor_said[id(r)]} if id(r) in auditor_said else {}))
            for r, v, d in rows
            if (v is not pramana.MISSING and not same_value(v, r["expected"])) or d is False][:32]
        self._event('solve_given_conditions', dict(evidence, wrong=sorted(wrong),
                    revised=[{'input': r['input'], 'hand_value': r['expected'], 'value': v, 'by': why}
                             for r, v, why in applied],
                    set_aside=[r['input'] for r in unsettled] if void is None else [], void=void,
                    support={pramana.key(r): self._support_label(book, r) for r in book.records}))
        if wrong:
            self.say(f'checks settled before code: the {" and ".join(sorted(wrong))} contradicted a check and is set aside')
        if applied:
            self.say(f'checks settled before code: {len(applied)} hand-worked value(s) corrected')
        return void

    def _resolve_checks(self, prompt, spec):
        """Ask an optional host oracle before exposing any candidate implementation.

        oracle(prompt, input) returns {'expected': JSON} or None (unsupported).
        This callback is trusted host configuration, not model-authored code.
        An unavailable configured oracle stops this task, preserving its evidence.
        Model checks can change even when the candidate would disagree; user
        anchors can only be reported as conflicting, never overwritten.
        """
        if self.check_oracle is None:
            return
        book, supported = self._checkbooks[spec['check_scope']], []
        for check in book.records:
            try:
                answer = self.check_oracle(prompt, json.loads(canonical(check['input'])))
                if answer is not None and (type(answer) is not dict or set(answer) != {'expected'}):
                    raise ValueError('oracle must return expected or None')
                if answer is not None:
                    # Validate oracle output through the same bounded JSON contract.
                    CheckBook(prompt, spec['contract'], [],
                              [{'input': check['input'], 'expected': answer['expected']}])
            except Exception as error:
                self._event('solve_oracle_unavailable', {'oracle': self.oracle_id,
                            'input': check['input'], 'error_type': type(error).__name__})
                raise CheckConflict('configured check oracle unavailable; verification remains incomplete') from error
            self._event('solve_oracle_observation', {'oracle': self.oracle_id,
                        'request': prompt, 'input': check['input'], 'answer': answer})
            if answer is None:
                continue
            if canonical(answer['expected']) != canonical(check['expected']):
                if check['origin'] == 'user':
                    raise CheckConflict('host oracle disagrees with a user anchor; resolve the contract')
                old_scope = book.scope_id
                book.revise(check['input'], answer['expected'], support='host_oracle_supported',
                            evidence={'kind': 'host_check_oracle', 'oracle': self.oracle_id,
                                      'expected': answer['expected']})
                self._register_revision(book, old_scope)
            supported.append(canonical(check['input']))
        spec['checks'], spec['check_scope'] = book.checks, book.scope_id
        for row in book.records:
            if canonical(row['input']) in supported:
                self._oracle_supported.add((book.scope_id, row['id']))
        if supported:
            spec['checks_by'] += f' ({len(supported)} host-oracle supported)'

    def _ask(self, model, context, retries=2):
        # Account each attempt once. Recursive finally blocks used to count the
        # last successful reply again for each preceding transport failure.
        for retry in range(retries + 1):
            failed = False
            try:
                reply = model.propose(context)
            except CallsExhausted:
                raise
            except TransportFailure:
                failed, reply = True, {}
            usage = getattr(model, 'last_usage', {}) or {}
            for key in ('input_tokens', 'output_tokens'):
                if type(usage.get(key)) is int:
                    self.usage[key] += usage[key]
            if not all(type(usage.get(k)) is int for k in ('input_tokens', 'output_tokens')):
                self.usage['unknown_usage_calls'] += 1
            if not failed or retry == retries or not self._calls_left():
                return _finite(reply) if isinstance(reply, dict) else {}

    def plan(self, prompt, examples, previous=None, *, examples_origin='user', retained=None):
        context = {'task': prompt, 'user_examples': examples, 'lessons': self.ws.lessons(prompt)}
        available = self.ws.helpful_methods(prompt)
        if available:
            context['available_methods'] = [{'artifact': m['artifact'], 'task': m['task'],
                                              'contract': m['contract']} for m in available]
            if self.conditions:
                # The analysis a stored method was made from (bīja), shown with it: the conditions it was checked
                # against and whether a property tested each (THE_ORIGINALS.md, the real gap 2).
                for shown, m in zip(context['available_methods'], available):
                    if m.get('conditions'):
                        shown['conditions'] = [{'condition': c['condition'], 'tested': c.get('tested', 'not tested')}
                                               for c in m['conditions']]
                    if m.get('derivation'):
                        shown['derivation'] = m['derivation']
        if self.conditions:
            context['state_conditions'] = CONDITIONS_NOTE
        if previous:
            context['previous_contract'] = previous
        if retained is not None and previous is None:
            spec, book = retained
            context['retained_contract'] = {'contract': spec['contract'], 'check_scope': book.scope_id,
                                            'check_count': len(book.checks), 'status': 'provisional_previous_round'}
            context['retained_contract_notice'] = ('A prior round produced this usable provisional contract. '
                'It is not a truth certificate. Return a valid plan; ordinary checks and settlement run again.')
        if examples_origin == 'model':
            context['model_examples'] = context.pop('user_examples')
        # Reject contradictory/oversized supplied constraints before spending a call.
        if examples:
            CheckBook(prompt, prompt, examples, [], supplied_origin=examples_origin)
        replied_plans = 0
        for attempt in range(1, 3):
            counter = getattr(self.planner, 'counter', {})
            replied_before = counter.get('replied', 0)
            reply = self._ask(self.planner, context)
            received = counter.get('replied', 0) > replied_before
            replied_plans += received
            contract, checks = reply.get('contract'), reply.get('checks')
            try:
                if reply.get('kind') == 'invalid' and set(reply) <= {'kind', 'raw_reply'}:
                    # The model port's wrapper for a reply that is not JSON: say so, not that a field is missing
                    # (W4-l: Python written inside the JSON was reported as a missing contract).
                    raise ValueError(INVALID_PLAN_REPLY)
                if not isinstance(contract, str):
                    raise ValueError('contract must be a nonempty string')
                if not contract.strip():
                    raise ValueError('contract must not be empty or blank')
                if not isinstance(checks, list):
                    raise ValueError('checks must be a list of input/expected objects')
                if len(checks) > MAX_PROPOSAL_ITEMS:
                    raise ValueError(f'checks must contain at most {MAX_PROPOSAL_ITEMS} proposals')
                for index, check in enumerate(checks):
                    if not isinstance(check, dict) or set(check) != {'input', 'expected'}:
                        raise ValueError(f'checks[{index}] must contain exactly input and expected')
                if not checks and not examples:
                    raise ValueError(NO_CHECKS)
                if (not examples and len(checks) >= 2
                        and len({canonical(check['expected']) for check in checks}) == 1):
                    # A constant passes every such check: they pin nothing (ERDŐS-979-c, all null).
                    raise ValueError(DEGENERATE_CHECKS)
                if not examples and all(check['input'] is None for check in checks):
                    # A zero-argument contract: run(None) has one value, so its only check is the planner's guess at
                    # the answer, which a method returning that guess passes, and no smaller instance exists to
                    # check (ERDŐS-979-d round 2: one check, input null, expecting {"n": 0, ...}).
                    raise ValueError(CHECKS_ON_THE_ANSWER)
                book = CheckBook(prompt, contract.strip(), examples, checks,
                                 supplied_origin=examples_origin)
            except ValueError as error:
                reason = str(error)[:1000]
                preview = json.dumps(reply, ensure_ascii=False, default=str)[:2000]
                self._event('solve_plan_rejected', {'attempt': attempt, 'reply_received': received,
                            'reply_preview': preview, 'reason': reason})
                context['previous_reply_rejected'] = reason
                continue
            self._checkbooks[book.scope_id] = book
            decomposition = None
            if reply.get('decomposition') is not None:
                try:
                    decomposition = _decomposition(reply['decomposition'])
                except ValueError as error:
                    self._event('solve_decomposition_rejected', {'reason': str(error)})
            planned = {'contract': contract.strip(), 'checks': book.checks,
                       'input': reply.get('input'), 'checks_by': book.checks_by,
                       'check_scope': book.scope_id, 'examples_origin': examples_origin,
                       'verify': reply.get('verify') if isinstance(reply.get('verify'), str) else None,
                       'decomposition': decomposition, 'large_inputs': _large_inputs(reply),
                       'relations': (reply['relations'] if isinstance(reply.get('relations'), str)
                                     and reply['relations'].strip() and len(reply['relations']) <= 20000 else None),
                       **_reference_fields(reply)}
            if self.conditions:
                stated, dropped = _conditions(reply, book.checks)
                if stated:
                    planned['conditions'] = stated
                derivation, why = _derivation(reply)
                if derivation:
                    planned['derivation'] = derivation
                self._event('solve_conditions_stated', {'check_scope': book.scope_id, 'stated': len(stated),
                                                         'conditions': [c['condition'] for c in stated],
                                                         'dropped': dropped + ([why] if why else []),
                                                         'derivation': len(derivation) if derivation else 0})
            return planned
        if retained is not None and previous is None and replied_plans == 2:
            spec, book = retained
            self._checkbooks[book.scope_id] = book
            self._event('solve_contract_retained', {'check_scope': book.scope_id,
                        'reason': 'two replied plans were invalid; provisional contract requires fresh settlement'})
            return spec
        if examples:
            book = CheckBook(prompt, prompt, examples, [], supplied_origin=examples_origin)
            self._checkbooks[book.scope_id] = book
            return {'contract': prompt, 'checks': book.checks, 'input': None,
                    'checks_by': book.checks_by, 'check_scope': book.scope_id,
                    'examples_origin': examples_origin, 'verify': None}
        if context.get('previous_reply_rejected') and previous is None:
            # Kept for the next round's planner (ERDŐS-979-e: three rounds each began again from a plan with no
            # checks, refused for the same reason).
            self.ws.learn(prompt, 'no plan was accepted: ' + context['previous_reply_rejected'])
        return None

    def attempt(self, request, checks, notes=(), *, check_scope=None):
        text = request + '\n\nChecks run(input) must pass: ' + json.dumps(checks)
        if notes:
            text += '\n\nStored methods that solve parts of this task (call them with use("ID")):\n' + '\n'.join(notes)
        task = ProgramTask(text, checks, context={'solve_check_scope': check_scope})
        run_dir = self.ws.runs / (_now().replace(':', '') + '-' + task.scope_id[:10] + '-' + uuid.uuid4().hex[:6])
        run_dir.mkdir(parents=True, exist_ok=True)
        journal_path = run_dir / 'events.jsonl'
        with Journal(journal_path) as journal, ArtifactMemory(self.ws.memory) as memory:
            before = set(memory.ids('method'))
            tools = ProgramTools(task, memory, journal, self._execute, max_executions=self._executions_left(),
                                 provenance={'producer': self.actor.identity.get('model'), 'run': run_dir.name},
                                 profile='c3')
            journal.append('program_task', {'request': task.request, 'scope': task.scope_id})
            mapping, stored = tools.tools(), set()
            remember = mapping['remember_method']
            def record_candidate(body):
                answer = remember(body)
                if answer.get('status') == 'stored':
                    payload = memory.get(answer['artifact'])
                    stored.add(_execution_fingerprint(payload['body']['source'], payload['dependencies']))
                return answer
            record_candidate.__doc__ = remember.__doc__
            mapping['remember_method'] = record_candidate
            actor = ProgressActor(self.actor, journal, stored, host_validates=self.host_protocol)
            find = mapping['find_methods']
            def guide_retrieval(body):
                return actor.observe_retrieval(body, find(body))
            guide_retrieval.__doc__ = find.__doc__
            mapping['find_methods'] = guide_retrieval
            host = {}
            if self.host_protocol:
                # Retrieval and validation are host work (no model call): the library is searched once with the
                # task's own words before the first call, and every stored method is validated at once.
                host = {'host_tools': {'find_methods': mapping.pop('find_methods')},
                        'prelude': (('find_methods', {'query': request, 'limit': C3_FIND_LIMIT}),),
                        'auto_validate': ('remember_method', 'validate_method')}
                record_candidate.__doc__ = (remember.__doc__ or '') + HOST_PROTOCOL_NOTE
            remaining = self.actor.counter['limit'] - self.actor.counter['used']
            result = investigate(task.request, actor, mapping, tools.checker, journal,
                                 Budget(model_calls=max(0, min(self.attempt_calls, remaining)),
                                        tool_calls=self.tool_calls),
                                 checker_feedback=tools.checker_feedback, normalize=True,
                                 automatic_submission=True, continue_transport_failures=True,
                                 outage_limit=3, detailed_response_feedback=True,
                                 truncation_limit=TRUNCATION_LIMIT, invalid_json_limit=2, **host)
            artifact = (result.answer or {}).get('artifact') if result.status == 'checked' else None
            uses = (memory.get(artifact).get('dependencies') or []) if artifact else []
            partial = None if artifact else self._partial(journal_path, memory, task, checks, run_dir,
                                                        check_scope=check_scope)
        account = _account_attempts(Journal.read(journal_path))
        for key in ('known_input_tokens', 'known_output_tokens'):
            self.usage[key.replace('known_', '')] += account[key]
        self.usage['unknown_usage_calls'] += account['unknown_usage_calls']
        # A stored candidate can finish every diagnostic check without the actor
        # managing to submit its artifact. Re-enter normal qualification before
        # retrying the model; diagnostic pass counts alone never qualify it.
        recovered = self._qualify_full_partial(request, checks, task, partial, run_dir,
                                               check_scope=check_scope,
                                               tool_calls=result.tool_calls) if partial else None
        if recovered and recovered['status'] == 'checked':
            artifact = recovered['artifact']
            uses = partial['uses']
        self._event('solve_attempt_finished', {'check_scope': check_scope, 'run': str(run_dir),
                    'status': result.status, 'artifact': (result.answer or {}).get('artifact')
                    if result.status == 'checked' else None, 'reason': result.reason,
                    'tool_calls': result.tool_calls,
                    'stall': actor.stall, 'partial_artifact': partial['artifact'] if partial else None})
        # Other methods written in this attempt: if one also passes the checks yet answers
        # differently elsewhere, the checks do not pin the answer (see `rival`).
        written = [e['body']['artifact'] for e in Journal.read(journal_path)
                   if e['kind'] == 'method_remembered' and isinstance(e['body'].get('artifact'), str)]
        rivals = list(dict.fromkeys(a for a in written if a != artifact)) if artifact else []
        self._read_written(rivals, checks, check_scope)
        if recovered and recovered['status'] == 'checked':
            return dict(recovered, reused=sorted(set(uses) | ({artifact} & before)),
                        step='model', rivals=rivals)
        return {'status': result.status, 'reason': result.reason, 'artifact': artifact,
                'reused': sorted(set(uses) | ({artifact} & before)), 'run': str(run_dir), 'task': task,
                'partial': partial, 'step': 'model', 'rivals': rivals,
                'tool_calls': recovered['tool_calls'] if recovered else result.tool_calls,
                'qualification_tool_calls': 1 if recovered else 0}

    def _other_agra(self, prompt, spec, best):
        """The other agra: an attempt right on checks the best one fails, shown with it in the repair note, or ''.

        In the kuṭṭaka the agras are provisional values, one satisfying one condition and one the other; the method
        joins them into the value that satisfies both (dvicchedāgra; Clark, Gaṇitapāda 32-33). The evidence frontier
        keeps every attempt whose passed checks no other covers, but repair showed only the best, so two attempts
        each right where the other is wrong were never set side by side. The one shown is the attempt of this check
        scope, not refuted, that passes the most of the checks the best fails."""
        failing = {canonical(c['input']) for c in best.get('failed_checks') or []}
        chosen, covered = None, []
        for (scope, artifact), partial in self._partials.items():
            if scope != spec['check_scope'] or artifact == best.get('artifact') or self._refuted_in(artifact, prompt):
                continue
            right = [c for c in partial['passed_checks'] if canonical(c['input']) in failing]
            if len(right) > len(covered) or (right and len(right) == len(covered)
                                             and len(partial['passed_checks']) > len(chosen['passed_checks'])):
                chosen, covered = partial, right
        if chosen is None:
            return ''
        self._event('solve_repair_two_agras', {'check_scope': spec['check_scope'], 'best': best.get('artifact'),
                    'other': chosen['artifact'], 'covers': [c['input'] for c in covered]})
        return ('\nAnother attempt passes %d of the checks this one fails (%s) and fails others that this one passes. '
                'Each is right where the other is wrong: write one method that keeps what each gets right, by general '
                'logic, never by testing for a check input.\nIts source:\n%s'
                % (len(covered), json.dumps([c['input'] for c in covered])[:400], chosen['source'][:3000]))

    def _qualify_full_partial(self, request, checks, task, partial, run_dir, *, check_scope=None, tool_calls):
        """Try the ordinary checker on an exact current diagnostic success.

        The partial identifies an immutable candidate, not qualification. Check
        scope, source, dependencies and refutations before spending the remaining
        shared execution budget. Keep the failed actor trace separate from this
        controller-driven qualification, which requires no further model call
        but consumes one remaining tool/checker slot from this attempt.
        """
        if tool_calls >= self.tool_calls:
            return None
        scope = check_scope or task.scope_id
        if (partial.get('check_scope') != scope or partial.get('failed_checks') != []
                or canonical(partial.get('passed_checks')) != canonical(checks)):
            return None
        book = self._checkbooks.get(check_scope)
        if book and (book.scope_id != check_scope or canonical(book.checks) != canonical(checks)):
            return None
        prompt = book._request if book else request
        qualification_task = ProgramTask(request + '\n\nChecks run(input) must pass: ' + json.dumps(checks),
                                         checks, context={'solve_check_scope': check_scope})
        from .campaign import _closure
        try:
            with ArtifactMemory(self.ws.memory) as memory:
                artifact = partial['artifact']
                payload = memory.get(artifact)
                if (payload.get('kind') != 'method' or payload['body']['source'] != partial.get('source')
                        or canonical(payload['dependencies']) != canonical(partial.get('uses'))
                        or self._refuted_in(artifact, prompt, memory)):
                    return None
                if any(memory.status(a, s) == 'invalidated' for a in _closure(memory, artifact)
                       for s in {task.scope_id, qualification_task.scope_id}):
                    return None
        except (KeyError, ValueError, TypeError, OSError, PermissionError):
            return None
        accounting = {'tool_calls_before': tool_calls, 'tool_calls': tool_calls + 1,
                      'qualification_tool_calls': 1, 'tool_call_limit': self.tool_calls}
        self._event('solve_full_pass_qualification_started', dict(accounting, artifact=artifact,
                    check_scope=scope, attempt_run=str(run_dir)))
        try:
            outcome = self.admit(request, checks, None, 'full-pass partial', stored=artifact,
                                 check_scope=check_scope, producer='full-pass partial revalidation',
                                 reason='stored candidate qualified on the current checks', step='qualification')
        except (KeyError, ValueError, TypeError, OSError, PermissionError):
            outcome = {'status': 'incomplete', 'run': None}
        outcome.update(tool_calls=tool_calls + 1, qualification_tool_calls=1)
        self._event('solve_full_pass_qualification', dict(accounting, artifact=artifact, check_scope=scope,
                    attempt_run=str(run_dir), run=outcome['run'], status=outcome['status']))
        return outcome

    def _read_written(self, methods, checks, check_scope):
        """Every other method written in an attempt is read when written, not only the one delivered: one whose own
        source returns a check no executed derivation supports is refuted (it would otherwise stay stored, never
        examined, for a later task to reuse where its literal is no check)."""
        if not methods:
            return
        from .demo import load_method
        supported = {canonical(v) for v in self._derived_inputs({'check_scope': check_scope})}
        refuted = self.ws.refuted()
        for method in methods:
            if method in refuted or method in self._known_before:
                continue
            try:
                source, _ = load_method(self.ws.memory, method)
            except (KeyError, OSError):
                continue
            hits = memorized_checks(source, checks)
            if any(canonical(c['input']) not in supported for c in hits):
                self._refute(method, 'returns checks literally instead of computing them (read when written)')

    def _partial(self, journal_path, memory, task, checks, run_dir, *, check_scope=None):
        """Retain nondominated partials, using a bounded sandbox diagnostic pass.

        All admitted candidates are considered, not just the last one. Budgets
        and infrastructure stops remain incomplete evidence. A changed check
        scope starts a separate frontier; scores are never compared across it.
        """
        written = [e['body']['artifact'] for e in Journal.read(journal_path)
                   if e['kind'] == 'method_remembered' and isinstance(e['body'].get('artifact'), str)]
        if not written:
            return None
        scope = check_scope or task.scope_id
        book = self._checkbooks.get(check_scope)
        ids = [r['id'] for r in book.records] if book else [digest(c) for c in checks]
        frontier = self._frontiers.setdefault(scope, EvidenceFrontier(ids))
        with Journal(run_dir / 'partial.jsonl') as journal:
            tools = ProgramTools(task, memory, journal, self._execute, max_executions=self._executions_left(),
                                 provenance={'producer': 'partial-attempt replay'}, profile='c3')
            for artifact in dict.fromkeys(written):
                passed, failed, passed_ids, completed = [], [], [], True
                payload = memory.get(artifact)
                for check_id, check in zip(ids, checks):
                    got = tools.probe_method({'artifact': artifact, 'input': check['input']})
                    ok = got.get('status') == 'completed' and canonical(got.get('result')) == canonical(check['expected'])
                    if ok:
                        passed.append(check); passed_ids.append(check_id)
                    else:
                        detail = dict(check, execution_status=got.get('status'))
                        if got.get('status') == 'completed':
                            detail['got'] = got['result']
                        failed.append(detail)
                    if got.get('status') not in ('completed', 'candidate_error'):
                        completed = False
                        break
                fp = _execution_fingerprint(payload['body']['source'], payload['dependencies'])
                receipt = frontier.observe(fp, artifact, passed_ids, completed=completed)
                self._event('solve_partial_evidence', {'check_scope': scope, 'receipt': receipt,
                            'structural_fingerprint': candidate_fingerprint(payload['body']['source'], payload['dependencies']),
                            'run': str(run_dir), 'diagnostic_journal': str(run_dir / 'partial.jsonl')})
                if completed:
                    self._partials[(scope, artifact)] = {
                        'artifact': artifact, 'source': payload['body']['source'],
                        'passed_checks': passed, 'failed_checks': failed, 'check_scope': scope,
                        'uses': payload['dependencies']}
                if not completed:
                    break
        eligible = frontier.frontier
        if not eligible:
            return None
        # Select a useful representative for the existing splitter; retain all
        # incomparable candidates and their exact pass sets in the journal.
        best = max(eligible, key=lambda r: (r['passed_count'], r['artifact_id']))
        return self._partials[(scope, best['artifact_id'])]

    def audit(self, prompt, spec, partial, examples):
        """Blindly re-derive the expected values of failed model-written checks.

        The auditor sees the task and the inputs, never the method's output. A check is corrected
        only when the auditor's value equals what the method returned and differs from the check.
        This agreement is heuristic, not independent verification. User checks
        never change; every revision retains both values and a new scope."""
        book = self._checkbooks[spec['check_scope']]
        given = {canonical(r['input']) for r in book.records if r['origin'] == 'user'
                 or (book.scope_id, r['id']) in self._oracle_supported}
        failed = [c for c in partial['failed_checks'] if canonical(c['input']) not in given and 'got' in c]
        if not failed:
            return 0
        reply = self._ask(self.auditor, {'task': prompt, 'contract': spec['contract'],
                                         'inputs': [c['input'] for c in failed]})
        values = reply.get('expected')
        if not isinstance(values, list) or len(values) != len(failed):
            return 0
        fixed = 0
        for check, value in zip(failed, values):
            if canonical(value) == canonical(check['got']) and canonical(value) != canonical(check['expected']):
                old_scope = book.scope_id
                try:
                    changed = book.revise(check['input'], value, evidence={
                        'kind': 'blind_model_rederivation', 'artifact': partial['artifact'],
                        'auditor': self.auditor.identity, 'method_result': check['got'],
                        'auditor_expected': value})
                except (ValueError, CheckConflict):   # e.g. a value beyond the check contract's limits
                    changed = False
                if changed:
                    fixed += 1
                    self._register_revision(book, old_scope)
                    self._confirm(prompt, book, {'input': check['input'], 'expected': value})
        spec['checks'], spec['check_scope'] = book.checks, book.scope_id
        return fixed

    def deploy(self, task, artifact, value, run):
        with Journal(Path(run) / 'deploy.jsonl') as journal, ArtifactMemory(self.ws.memory) as memory:
            run_job = lambda job: self._execute(job, self.deploy_executor)  # noqa: E731
            tools = ProgramTools(task, memory, journal, run_job, max_executions=min(1, self._executions_left()),
                                 provenance={'producer': 'deployment'}, profile='c3')
            return tools.probe_method({'artifact': artifact, 'input': value})

    def solve(self, prompt, examples=(), depth=0, *, examples_origin='user'):
        """Run one bounded job and retain its check epochs and partial evidence.

        Exhaustion is an incomplete checkpoint, never a success claim. This
        journal supports inspection; automatic crash resume is not implemented.
        """
        root = self._job is None
        if root:
            examples = list(examples)
            self._continuity_spec = None
            self._continuity_binding = (prompt, examples, examples_origin)
            path = self.ws.runs / ('solve-' + uuid.uuid4().hex) / 'solve.jsonl'
            self._job = Journal(path)
            self._active, self._frontiers, self._partials, self._checkbooks = set(), {}, {}, {}
            self._active_boundary_restatements = set()
            self._oracle_supported = set()
            self._execution_used = 0
            self._sketch_states = {}
            self._mechanical = {}
            self._support, self._tested, self._refuted_here = {}, {}, []
            self._voices, self._disputed, self._piece_of, self._valli_bottom = {}, {}, {}, set()
            self._reference_of, self._unsettled, self._against_both = {}, {}, {}
            self._slow_probes = set()   # (probe source, input) pairs that ran out of their time
            self._probe_values = {}     # (probe source, input) -> what a finished value gave (computed once)
            self._finished_runs = set()   # (method code, input) a probe ran to the end under run(...)
            self._raised = {}
            self._testimony = {}
            with ArtifactMemory(self.ws.memory) as memory:
                self._known_before = set(memory.ids('method'))   # stored by earlier tasks, checked on their own
            self._event('solve_started', {'request': prompt, 'checks_origin': examples_origin,
                        'model_calls_before': self.actor.counter['used'],
                        'model_call_limit': self.actor.counter['limit'],
                        'execution_limit': self.max_executions})
        try:
            try:
                result = self._solve(prompt, examples, depth, examples_origin=examples_origin)
            except CallsExhausted:
                result = {'status': 'incomplete', 'task': prompt, 'reason': 'shared model-call limit reached',
                          'artifact': None, 'reused': [], 'subtasks': []}
                self._event('solve_budget_boundary', {'request': prompt,
                            'model_calls': self.actor.counter['used']})
            if root:
                result['solve_journal'] = str(path)
                result['execution_attempts'] = self._execution_used
                result['mechanical'] = dict(self._mechanical)   # steps taken with no model call, by kind
                result['evidence_frontiers'] = {scope: {'best_count': f.best_count, 'check_count': len(f.check_ids),
                                      'frontier': f.frontier} for scope, f in self._frontiers.items()}
                result['repair_hints'] = self.resume_repairs()
                self._event('solve_finished', {'status': result['status'], 'reason': result.get('reason'),
                            'artifact': result.get('artifact'), 'model_calls': self.actor.counter['used']})
            return result
        except Exception:
            if root:
                self._continuity_spec = None
            raise
        finally:
            if root:
                self._job.close()
                self._job = None

    def _solve(self, prompt, examples, depth, *, examples_origin):
        examples = list(examples)
        outer = self._given.get(prompt, pramana.MISSING)
        ident = self._given[prompt] = digest({'examples': examples, 'origin': examples_origin})
        try:
            result = self._solve_given(prompt, examples, depth, examples_origin=examples_origin)
            if depth > 0 and result.get('status') == 'checked' and result.get('artifact'):
                # a piece: its parent's gate reads its voices under its own given conditions (two pieces worded
                # alike are told apart by them, K8)
                self._piece_of[result['artifact']] = (prompt, ident)
            return result
        finally:
            if outer is not pramana.MISSING:   # a nested solve of the same words: the parent's identity returns
                self._given[prompt] = outer

    def _solve_given(self, prompt, examples, depth, *, examples_origin):
        try:
            retained = self._read_retained_contract(prompt, examples, examples_origin) if depth == 0 else None
            spec = (self.plan(prompt, examples, examples_origin=examples_origin, retained=retained)
                    if retained is not None else self.plan(prompt, examples, examples_origin=examples_origin))
        except ValueError as error:
            self._event('solve_constraints_rejected', {'request': prompt, 'reason': str(error),
                        'origin': examples_origin})
            return {'status': 'needs_examples', 'task': prompt, 'reason': str(error),
                    'artifact': None, 'reused': [], 'subtasks': []}
        if spec is None:
            return {'status': 'needs_examples', 'task': prompt,
                    'reason': 'no checkable contract; add --example with an input and its expected result'}
        self._event('solve_check_contract', self._snapshot(spec))
        try:
            self._resolve_checks(prompt, spec)
        except CheckConflict as error:
            self._event('solve_verification_unresolved', {'request': prompt, 'reason': str(error)})
            return {'status': 'needs_verification', 'task': prompt, 'reason': str(error),
                    'artifact': None, 'reused': [], 'subtasks': []}
        self._carry_confirmed(prompt, spec)
        void = self._settle_given(prompt, spec)
        if void:
            self.ws.learn(prompt, f'{spec["contract"]} -> void: {void["reason"]}', form=pramana.form(spec['checks']))
            return {'status': 'incomplete', 'task': prompt, 'reason': void['reason'], 'artifact': None,
                    'reused': [], 'subtasks': [], 'delivered_by': None, 'void': void}
        if depth == 0:
            self._continuity_spec = spec
        key = subproblem_key(prompt, spec['contract'], spec['checks'])
        restatement = boundary_restatement_key(prompt, spec['contract'], spec['checks'])
        if key in self._active or restatement in self._active_boundary_restatements:
            exact = key in self._active
            self._event('solve_cycle_blocked', {'request': prompt, 'check_scope': spec['check_scope'],
                        'reason': 'exact_active_subproblem' if exact else 'boundary_whitespace_restatement'})
            reason = ('recursive subproblem repeats an active goal and checks' if exact else
                      'recursive subproblem only changes surrounding whitespace of an active goal and checks')
            return {'status': 'incomplete', 'task': prompt, 'reason': reason,
                    'artifact': None, 'reused': [], 'subtasks': []}
        self._active.add(key)
        self._active_boundary_restatements.add(restatement)
        try:
            self._event('solve_check_contract', self._snapshot(spec))
            return self._solve_planned(prompt, examples, depth, spec)
        finally:
            self._active.remove(key)
            self._active_boundary_restatements.remove(restatement)

    def _solve_planned(self, prompt, examples, depth, spec):
        indent = '  ' * depth
        self.say(f'{indent}contract: {spec["contract"]}')
        self.say(f'{indent}checks ({spec["checks_by"]}): {json.dumps(spec["checks"])[:300]}')
        if depth == 0 and self.confirm and not self.confirm(spec):
            return {'status': 'declined', 'task': prompt, 'spec': spec}
        lessons = self.ws.lessons(prompt, form=pramana.form(spec['checks']))

        def request_for(spec):
            text = prompt + '\n\nContract: ' + spec['contract']
            # The instrument's limits, stated before the method is written (the scale and raised notes said them
            # only after a method had run out): what each run has, and Python's own limit on recursion.
            text += ('\n\nEach check and probe runs in a sandbox with about %s seconds and %s of memory; Python\'s '
                     'recursion limit there is its default (1000) unless the method raises it.'
                     % (int(getattr(self.executor, 'timeout', 20) or 20), getattr(self.executor, 'memory', '256m')))
            seconds = getattr(self.deploy_executor, 'timeout', None)
            if depth == 0 and spec.get('input') is not None and seconds and self.deploy_executor is not self.executor:
                # What the method's final run has (first-hand session f: the actor was never told, and a search
                # that cannot finish in it is a step that fails only at the end).
                text += ('\n\nThe checked method is run once on the requested input %s with a limit of %s seconds '
                         'and %s of memory.'
                         % (json.dumps(spec['input'])[:200], int(seconds), getattr(self.deploy_executor, 'memory', '?')))
            return text + ('\n\nMistakes made on similar tasks before: ' + json.dumps(lessons) if lessons else '')

        request = request_for(spec)
        offered = [f'{m["artifact"]}: {m["task"][:160]}'
                   for m in self.ws.helpful_methods(prompt, form=pramana.form(spec['checks']))]
        # Sthira: what is solved stays solved. A stored method that passes these checks is used by id
        # before the model writes anything; the model writes code only when none does.
        parts, split_by = [], None

        def available_methods():
            # A later repair or split must still see pieces built earlier in this solve.
            # Offering an id is not parent qualification: its composition still faces
            # the current checks and every delivery gate.
            notes = [f'{p["artifact"]}: {p["task"]}' for p in parts
                     if p.get('status') == 'checked' and p.get('artifact')
                     and not self._refuted_in(p['artifact'], prompt)]
            return list(dict.fromkeys(notes + offered))

        def retain_parts(more):
            known = {(p['task'], p.get('artifact'), p.get('status')) for p in parts}
            for part in more:
                key = (part['task'], part.get('artifact'), part.get('status'))
                if key not in known:
                    parts.append(part)
                    known.add(key)

        outcome = self.adopt(prompt, spec, indent)
        steps = ['reuse'] if outcome else []
        if outcome is None:
            outcome = self.prastara(prompt, spec, indent)
            steps += [outcome['step']] if outcome else []
        if outcome is None and self._complete_given(prompt, spec):
            # Before the model writes code the given conditions must exist; new ones are tested at once.
            scope = spec['check_scope']
            void = self._settle_given(prompt, spec)
            if void:
                if depth == 0:
                    self._continuity_spec = None
                self.ws.learn(prompt, f'{spec["contract"]} -> void: {void["reason"]}', form=pramana.form(spec['checks']))
                return {'status': 'incomplete', 'task': prompt, 'reason': void['reason'], 'artifact': None,
                        'reused': [], 'subtasks': [], 'delivered_by': None, 'void': void,
                        'contract': spec['contract'], 'checks': spec['checks'], 'checks_by': spec['checks_by'],
                        'evidence': self._evidence(spec, None)}
            request = request_for(spec)
            if spec['check_scope'] != scope:
                self.say(f'{indent}checks after settlement ({spec["checks_by"]}): {json.dumps(spec["checks"])[:300]}')
            if spec['check_scope'] != scope and depth == 0 and self.confirm and not self.confirm(spec):
                return {'status': 'declined', 'task': prompt, 'spec': spec}   # the user sees the settled checks
            if spec['check_scope'] != scope:
                # Settled checks differ from those reuse and prastāra were tried on: try them again (no calls).
                outcome = self.adopt(prompt, spec, indent) or self.prastara(prompt, spec, indent)
                steps += [outcome['step']] if outcome else []
        if outcome is None:
            # A hand check nothing tested is derived again, blindly, before the model writes code.
            scope = spec['check_scope']
            if self._derive_blind(prompt, spec):
                request = request_for(spec)
                self.say(f'{indent}checks after a second derivation ({spec["checks_by"]}): {json.dumps(spec["checks"])[:300]}')
                if depth == 0 and self.confirm and not self.confirm(spec):
                    return {'status': 'declined', 'task': prompt, 'spec': spec}
                outcome = self.adopt(prompt, spec, indent) or self.prastara(prompt, spec, indent)
                steps += [outcome['step']] if outcome else []
        if outcome is None and spec.get('decomposition') and depth < MAX_DEPTH:
            # The planner's own breakdown: solve the named pieces, then assemble them by its recipe.
            parts, outcome = self._assemble(prompt, spec, request, spec['decomposition'], depth, indent)
            steps.append('plan recipe')
            if outcome is not None and outcome['status'] == 'checked':
                split_by = 'model plan, mechanical assembly'
        if outcome is None or outcome['status'] != 'checked':
            resumed = self._resume_partial(prompt, request, spec)
            outcome = resumed or self.attempt(request, spec['checks'], available_methods(), check_scope=spec['check_scope'])
            if resumed:
                steps.append('resume partial')
        best, seen, curve = None, set(), []
        epochs = []
        curve_scope = spec['check_scope']
        tried, stalls, replans = set(), 0, 0
        high = -1   # most checks passed so far under the current checks; progress must beat it

        def observe(outcome):
            nonlocal best, high, curve_scope, curve
            if curve_scope != spec['check_scope']:
                epochs.append({'scope': curve_scope, 'observed_pass_counts': curve})
                curve, curve_scope = [], spec['check_scope']
                best, high = None, -1
                seen.clear()
            partial = outcome.get('partial')
            passed = len(spec['checks']) if outcome['status'] == 'checked' else (
                len(partial['passed_checks']) if partial else 0)
            fingerprint = canonical(partial['source']) if partial else None
            repeated = fingerprint is not None and fingerprint in seen
            if fingerprint:
                seen.add(fingerprint)
            curve.append(passed)
            if partial and not repeated and (best is None or passed > len(best['passed_checks'])):
                best = partial
            if passed > high:
                high = passed
                return True
            return False

        observe(outcome)
        differential_rounds = memorization_rounds = rival_rounds = property_rounds = scale_rounds = against_rounds = 0
        raised_rounds = witness_rounds = 0
        # A checked method sent back by a gate that does not show it wrong on any check (no answer, witness, scale,
        # raised, against both), with the check scope it passed. When the asked-for method fails, this one returns
        # to the gates; a correction that could not be made never discards what it was to correct.
        standing, restored, scale_of, examined = None, set(), {}, set()
        related, relation_rounds = {}, 0   # method -> pairs on which it breaks a stated relation ([] when it held)

        def send_back(outcome):
            nonlocal standing
            standing = (outcome, spec['check_scope'])

        def raised_now(outcome):
            return (outcome['status'] == 'checked' and bool(self._raised.get(outcome['artifact']))
                    and raised_rounds < RAISED_ROUNDS and self._calls_left())

        def ask_raised(outcome):
            """A checked method raised where the plan declares the input valid: a perceived failure, asked once."""
            nonlocal raised_rounds, tried, stalls
            raised_rounds += 1
            send_back(outcome)
            note = self._raised_note(outcome['artifact'])
            self.say(f'{indent}the checked method raises on input(s) the plan declares valid; asking again')
            steps.append('raised')
            outcome = self.attempt(request_for(spec) + note, spec['checks'], available_methods(),
                                   check_scope=spec['check_scope'])
            observe(outcome)
            tried, stalls = set(), 0
            return outcome
        answer_rounds = 0
        while True:
            while outcome['status'] != 'checked' and self._calls_left() and self._executions_left():
                step = self._next_step(best, tried, spec, examples, depth)
                if step is None:
                    stalls += 1
                    if stalls >= MAX_STALLS:
                        break
                    tried = set()
                    continue
                tried.add(step)
                steps.append(step)
                have = len(best['passed_checks']) if best else 0
                self.say(f'{indent}step {len(steps)}: {step} (best so far {have}/{len(spec["checks"])} checks)')
                new = None
                repair_from = None
                if step == 'repair':
                    repair_from = (spec['check_scope'], _execution_fingerprint(best['source'], best.get('uses', [])))
                    replies_before_repair = self.actor.counter.get('replied', 0)
                    note = ('Best attempt so far passes %d of %d checks. Keep what works and fix only the failing '
                            'cases with general logic; never special-case a check input.\nSource:\n%s\n'
                            'Failing checks (with what it returned): %s'
                            % (have, len(spec['checks']), best['source'], json.dumps(best['failed_checks'][:6])))
                    note += self._other_agra(prompt, spec, best)
                    new = self.attempt(request + '\n\n' + note, spec['checks'], available_methods(), check_scope=spec['check_scope'])
                elif step in ('verify', 'audit'):
                    fixed = (self.audit_by_verifier(spec, best, examples) if step == 'verify'
                             else self.audit(prompt, spec, best, examples))
                    if fixed:
                        label = 'verifier' if step == 'verify' else 'blind re-derivation'
                        self.say(f'{indent}{fixed} check(s) corrected by {label}')
                        spec['checks_by'] += f' ({fixed} corrected by {label})'
                        self._event('solve_check_contract', self._snapshot(spec))
                        seen.clear()   # a new check scope requires fresh comparison
                        best, high, request = None, -1, request_for(spec)
                        new = self.attempt(request, spec['checks'], available_methods(), check_scope=spec['check_scope'])
                elif step == 'mati':
                    new = self.mati(prompt, spec, best, request)
                elif step == 'split':
                    more, split_by, new = self.split(prompt, spec, lessons, outcome, best, request, available_methods(),
                                                     depth, indent, retained_parts=parts)
                    retain_parts(more)
                elif step == 'replan' and replans < 2:
                    replans += 1
                    # An identical replan registers its checks under the live scope id; keep the live book so
                    # that a replan given up below leaves the current spec intact.
                    live = self._checkbooks.get(spec['check_scope'])
                    again = self.plan(prompt, examples, previous=spec['contract'], examples_origin=spec['examples_origin'])
                    if again:
                        self._event('solve_check_contract', self._snapshot(again))
                        try:
                            self._resolve_checks(prompt, again)
                        except CheckConflict as error:
                            self._event('solve_verification_unresolved', {'request': prompt, 'reason': str(error)})
                            self._checkbooks[spec['check_scope']] = live
                            continue
                        self._carry_confirmed(prompt, again)
                        if self._settle_given(prompt, again):
                            self._checkbooks[spec['check_scope']] = live
                            continue   # a contract whose checks cannot be settled is not worth coding to
                        self._derive_blind(prompt, again)   # the replan's hand checks are derived again too (S11)
                        self._event('solve_check_contract', self._snapshot(again))
                        # observe() resets evidence only if the scope actually changes.
                        # An identical replan must retain the strongest earlier partial.
                        spec = again
                        if depth == 0:
                            self._continuity_spec = spec
                        request = request_for(spec)
                        self.say(f'{indent}new contract: {spec["contract"]}')
                        new = self.attempt(request, spec['checks'], available_methods(), check_scope=spec['check_scope'])
                if new is None:
                    continue
                outcome = new
                improved = observe(outcome)
                if repair_from:
                    if improved or outcome['status'] == 'checked':
                        self._stalled_repairs.discard(repair_from)
                    elif self.actor.counter.get('replied', 0) > replies_before_repair:
                        # A replied repair did not advance the live check frontier. Reduce the task before
                        # another whole-task repair or hole search, including after a round boundary.
                        self._stalled_repairs.add(repair_from)
                        self._event('solve_repair_stalled', {'check_scope': repair_from[0],
                                    'candidate_fingerprint': repair_from[1], 'best_passed': high,
                                    'next_strategy': 'split' if depth < MAX_DEPTH else 'remaining bounded ladder'})
                if improved:
                    tried, stalls = set(), 0

            if outcome['status'] != 'checked' and standing is not None:
                kept, scope = standing
                standing = None
                if (scope == spec['check_scope'] and kept['artifact'] not in restored
                        and not self._refuted_in(kept['artifact'], prompt)):
                    restored.add(kept['artifact'])
                    self._event('solve_checked_method_kept', {'artifact': kept['artifact'], 'check_scope': scope,
                                'asked_for': steps[-1] if steps else None, 'reason': outcome.get('reason')})
                    self.say(f'{indent}the method asked for did not pass the checks; the checked method stands')
                    steps.append('kept')
                    outcome = kept

            if outcome['status'] == 'checked' and self._test_set_aside(prompt, spec, outcome):
                steps.append('set-aside check')
                self.say(f'{indent}a check set aside before code returns, and the method cannot face it; asking again')
                request = request_for(spec)
                outcome = self.attempt(request, spec['checks'], available_methods(), check_scope=spec['check_scope'])
                observe(outcome)
                tried, stalls = set(), 0
                continue
            memorized, culprits = self._memorized(outcome, spec, prompt) if outcome['status'] == 'checked' else ([], [])
            # Refuted: only methods written in this job whose own source returns the checks literally. One that
            # merely runs a stored method holding such a literal is set aside for this task, not marked wrong.
            for culprit in culprits if memorized else []:
                self._refute(culprit, 'returns checks literally instead of computing them', prompt)
            if memorized and memorization_rounds >= MEMORIZATION_ROUNDS:
                outcome = dict(outcome, status='incomplete', artifact=None, reused=[],
                               reason='the method returns some checks literally instead of computing them')
                break
            if memorized:
                # A method that special-cases its checks passes them and is wrong in general. The
                # check it could not pass honestly may itself be wrong: re-derive it blindly against
                # the best honest attempt, then ask again for the general method.
                memorization_rounds += 1
                steps.append('unmemorize')
                self.say(f'{indent}the method returns {len(memorized)} check(s) literally; asking for the general method')
                fixed = self.audit(prompt, spec, best, examples) if best else 0
                if fixed:
                    self.say(f'{indent}{fixed} check(s) corrected by blind re-derivation')
                    spec['checks_by'] += f' ({fixed} corrected by blind re-derivation)'
                    self._event('solve_check_contract', self._snapshot(spec))
                    seen.clear()
                    best, high = None, -1
                request = request_for(spec)
                note = ('\n\nA previous method returned the expected values of these checks literally, for '
                        'their exact inputs, instead of computing them: %s. That is rejected. Write the '
                        'general method; never special-case a check input.' % json.dumps(
                            [c['input'] for c in memorized])[:600])
                outcome = self.attempt(request + note, spec['checks'], available_methods(), check_scope=spec['check_scope'])
                observe(outcome)
                tried, stalls = set(), 0
                continue
            if (outcome['status'] == 'checked' and depth == 0 and spec.get('input') is not None
                    and answer_rounds < ANSWER_ROUNDS and self._calls_left() and self._executions_left() > 3):
                answer_rounds += 1
                if self._no_answer(spec, outcome['artifact']):
                    # A perceived failure (first-hand session g): a method that searches a fixed range passes every
                    # small check and returns null on the input the task is about. The climb goes on there. Asked
                    # before the rival and property tests, which would ask the auditor for the open answer itself.
                    self.say(f'{indent}the checked method returns null on the requested input: no answer; asking again')
                    steps.append('no answer')
                    send_back(outcome)
                    note = ('\n\nA method that passes every check returned null on the requested input %s: no '
                            'answer. The task asks for the answer there. If the method searches a fixed range, it must '
                            'search until the answer is found instead; keep it correct on every check.\nThe method:\n%s'
                            % (json.dumps(spec['input'])[:300], self._source_of(outcome['artifact'])[:4000]))
                    outcome = self.attempt(request_for(spec) + note, spec['checks'], available_methods(),
                                           check_scope=spec['check_scope'])
                    observe(outcome)
                    tried, stalls = set(), 0
                    continue
            if (outcome['status'] == 'checked' and depth == 0 and self.witness is not None
                    and outcome['artifact'] not in self._testimony and self._executions_left() > 3):
                said = self._testimony[outcome['artifact']] = self._testify(spec, outcome['artifact'])
                if (said and said.get('outcome') == 'disagrees' and witness_rounds < WITNESS_ROUNDS
                        and self._calls_left()):
                    witness_rounds += 1
                    self.say(f'{indent}the published sequence {said["sequence"]} contradicts the checked method; '
                             'asking again')
                    steps.append('witness')
                    send_back(outcome)
                    note = ('\n\nA method that passes every check agrees with the published sequence %s (%s) on its '
                            'first %d terms, and differs from its table at these indices (n, the method, the published '
                            'value): %s. Find where the method goes wrong for larger n and fix it, keeping every '
                            'check.\nThe method:\n%s' % (said['sequence'], said['name'][:160], said['asked_terms'],
                                                         json.dumps(said['disagrees'])[:800],
                                                         self._source_of(outcome['artifact'])[:4000]))
                    outcome = self.attempt(request_for(spec) + note, spec['checks'], available_methods(),
                                           check_scope=spec['check_scope'])
                    observe(outcome)
                    tried, stalls = set(), 0
                    continue
            if (outcome['status'] == 'checked' and spec.get('established_by') and outcome['artifact'] not in examined
                    and self._executions_left() > 3):
                examined.add(outcome['artifact'])
                added = self.pariksha(prompt, spec, outcome['artifact'])
                if added:
                    self.say(f'{indent}parīkṣā: {added} new check(s) where what is known and a blind re-derivation '
                             'agree against the method')
                    steps.append('pariksha')
                    request = request_for(spec)
                    outcome = self.attempt(request, spec['checks'], available_methods(), check_scope=spec['check_scope'])
                    observe(outcome)
                    tried, stalls = set(), 0
                    continue
            if outcome['status'] == 'checked' and outcome.get('rivals') and rival_rounds < RIVAL_ROUNDS:
                rival_rounds += 1
                added, settled = self.rival(prompt, spec, outcome)
                if added:
                    self.say(f'{indent}rival testing: {added} new check(s) where two methods that pass the '
                             'checks disagree')
                    steps.append('rival')
                    request = request_for(spec)
                    outcome = settled if settled and settled['status'] == 'checked' else self.attempt(
                        request, spec['checks'], available_methods(), check_scope=spec['check_scope'])
                    observe(outcome)
                    tried, stalls = set(), 0
                    continue
            if outcome['status'] == 'checked' and spec.get('verify') and property_rounds < PROPERTY_ROUNDS:
                property_rounds += 1
                added = self.property_test(prompt, spec, outcome['artifact'])
                if added:
                    self.say(f'{indent}property testing: {added} new check(s) where the property and a blind '
                             're-derivation agree against the method')
                    steps.append('property')
                    request = request_for(spec)
                    outcome = self.attempt(request, spec['checks'], available_methods(), check_scope=spec['check_scope'])
                    observe(outcome)
                    tried, stalls = set(), 0
                    continue
            if (outcome['status'] == 'checked' and spec.get('relations') and outcome['artifact'] not in related
                    and self._executions_left() > 4):
                related[outcome['artifact']] = broken = self.relation_test(spec, outcome['artifact'])
                if broken and relation_rounds < RELATION_ROUNDS and self._calls_left():
                    relation_rounds += 1
                    send_back(outcome)
                    self.say(f'{indent}relation testing: the method breaks a relation the plan states on '
                             f'{len(broken)} pair(s); asking again')
                    steps.append('relation')
                    note = ('\n\nThe plan states how the result changes when the input is moved, and on these inputs '
                            'the method\'s two results break it (input, result, moved input, result): %s. At least one '
                            'of each pair is wrong. Work each out step by step from the task and fix the method where '
                            'it is wrong, keeping every check.\nThe relation:\n%s\nThe method:\n%s'
                            % (json.dumps(broken[:6], default=str)[:1500], spec['relations'][:1500],
                               self._source_of(outcome['artifact'])[:4000]))
                    outcome = self.attempt(request_for(spec) + note, spec['checks'], available_methods(),
                                           check_scope=spec['check_scope'])
                    observe(outcome)
                    tried, stalls = set(), 0
                    continue
            if (outcome['status'] == 'checked' and outcome['artifact'] not in scale_of
                    and (spec.get('large_inputs') or _nested_inputs(spec.get('checks') or []))):
                # Every checked method is run at the top of the range once, the one delivered too, so the result's
                # scale names the method it describes; the method is asked for again at most SCALE_ROUNDS times.
                slow = self.scale_test(spec, outcome['artifact'])
                scale_of[outcome['artifact']] = spec['scale_outcome']
                if slow and scale_rounds < SCALE_ROUNDS and self._calls_left():
                    scale_rounds += 1
                    send_back(outcome)
                    # Not wrong on its checks, but a method that cannot finish on a valid input fails it: a
                    # perceived failure (the climb goes on) where a small-checks-only loop would stop.
                    self.say(f'{indent}scale testing: the method runs out on {len(slow)} valid input(s) at the '
                             'top of the stated range; asking for one that scales')
                    steps.append('scale')
                    request = request_for(spec)
                    ran_out = [{'input': json.dumps(value)[:120], 'ran out': reason}
                               for value, reason in zip(slow, spec.get('scale_reasons') or [])]
                    note = ('\n\nA method that passes every check ran out of time or memory on these valid inputs at '
                            'the top of the stated range (built by %s); how each ran out: %s. Every valid input must finish. '
                            'Where time ran out, use an algorithm whose cost fits the stated sizes (a closed form, a '
                            'sieve, dynamic programming over digits or states, a logarithmic or polynomial method) '
                            'instead of enumerating; where the recursion depth ran out, raise the limit (sys.setrecursionlimit) '
                            'or keep an explicit stack; where memory ran out, bound what is built. Keep it correct on '
                            'every check.\nThe slow method:\n%s'
                            % ((spec.get('large_inputs') or 'nesting a check %d levels deep' % NEST_DEPTH)[:300],
                               json.dumps(ran_out or slow)[:900],
                               self._source_of(outcome['artifact'])[:4000]))
                    outcome = self.attempt(request + note, spec['checks'], available_methods(),
                                           check_scope=spec['check_scope'])
                    observe(outcome)
                    tried, stalls = set(), 0
                    continue
            if raised_now(outcome):
                outcome = ask_raised(outcome)
                continue
            if (outcome['status'] != 'checked' or differential_rounds >= DIFFERENTIAL_ROUNDS
                    or not spec.get('reference')):
                break
            differential_rounds += 1
            added = self.differential(prompt, spec, outcome['artifact'])
            against = self._against_both.pop(outcome['artifact'], None)
            if not added and against and against_rounds < AGAINST_ROUNDS and self._calls_left():
                # A perceived failure: the climb goes on where the method's value stands against two derivations.
                against_rounds += 1
                send_back(outcome)
                self.say(f'{indent}differential testing: on {len(against)} input(s) the reference and a blind '
                         're-derivation each reject the method\'s value; asking again')
                steps.append('against both')
                note = ('\n\nOn these valid inputs the method\'s result agrees with neither of two independent '
                        'derivations, a slow reference and a blind re-derivation (each value shown; they also disagree '
                        'with each other, so neither is a check): %s. Work each one out step by step from the task, '
                        'and fix the method where it is wrong, keeping every check.\nThe method:\n%s'
                        % (json.dumps(against, default=str)[:1500], self._source_of(outcome['artifact'])[:4000]))
                outcome = self.attempt(request_for(spec) + note, spec['checks'], available_methods(),
                                       check_scope=spec['check_scope'])
                observe(outcome)
                tried, stalls = set(), 0
                continue
            if not added and raised_now(outcome):   # a crash shared with the reference
                outcome = ask_raised(outcome)
                continue
            if not added:
                break
            self.say(f'{indent}differential testing: {added} new check(s) where the method and the reference disagree')
            steps.append('differential')
            request = request_for(spec)
            outcome = self.attempt(request, spec['checks'], available_methods(), check_scope=spec['check_scope'])
            observe(outcome)
            tried, stalls = set(), 0
        epochs.append({'scope': curve_scope, 'observed_pass_counts': curve})
        if outcome['status'] != 'checked' and not self._executions_left():
            outcome['reason'] = 'shared execution limit reached'
        if outcome.get('artifact'):   # the reference the method was delivered under, for the piece gate (T8)
            self._reference_of[outcome['artifact']] = digest(spec['reference']) if spec.get('reference') else None
        result = {'status': outcome['status'], 'task': prompt, 'contract': spec['contract'],
                  'checks': spec['checks'], 'checks_by': spec['checks_by'], 'artifact': outcome['artifact'],
                  'reused': outcome['reused'], 'subtasks': parts, 'split_by': split_by,
                  'reason': outcome['reason'], 'progress': curve, 'progress_epochs': epochs, 'strategies': steps,
                  'check_evidence': self._snapshot(spec), 'verification_scope': 'supplied_finite_checks_only',
                  # Whether the delivered method was run at the top of the stated range: held, slow (still slow
                  # after the asks), not_run (no input could be built or run), or not_asked (no expression).
                  'scale': scale_of.get(outcome['artifact']) or ('not_run' if spec.get('large_inputs') else 'not_asked'),
                  # Whether the delivered method keeps the relations the plan states (II 16: two positions of the
                  # gnomon; IV 13: one moment seen from four places): held, broken, discarded, or not_asked.
                  'relations': spec.get('relation_outcomes', {}).get(outcome['artifact'])
                  or ('discarded' if spec.get('relations_discarded') else 'not_asked'),
                  # The step that wrote the method delivered: model, reuse, assembly, recipe or mati.
                  'delivered_by': outcome.get('step', 'model') if outcome['status'] == 'checked' else None}
        if outcome.get('composition'):
            result['composition'] = outcome['composition']
        held_back = outcome['artifact'] if outcome['status'] == 'checked' else None   # for an unverified answer
        bad = self._refuted_in(outcome['artifact'], prompt) if outcome['status'] == 'checked' else []
        if bad:
            # One gate for every path: a method shown wrong, or one that runs a method shown wrong,
            # is never delivered, whatever step produced it.
            self._event('solve_delivery_refused', {'artifact': outcome['artifact'], 'refuted': bad})
            result.update(status='incomplete', artifact=None, reused=[],
                          reason='the method is, or runs, a method shown wrong')
        if result['status'] == 'checked':
            dispute = self._property_dispute_gate(prompt, spec, outcome['artifact'])
            if dispute:
                # Every candidate must face unresolved inputs, even after a new artifact, replan or round.
                self._event('solve_delivery_refused', dict(dispute, by='unsettled property evidence'))
                result.update(status='incomplete', artifact=None, reused=[], reason=dispute['reason'],
                              disputed_property=dispute, property_evidence=dispute['property_evidence'])
        unsettled = next((self._unsettled[outcome['artifact'], by] for by in ('reference', 'rival')
                          if (outcome['artifact'], by) in self._unsettled), None)
        if result['status'] == 'checked' and depth == 0 and spec.get('input') is None and unsettled:
            # A tie is not a check: the delivered method claims every valid input, and on these it stands against
            # one other voice with no blind re-derivation heard (no model call left, or no usable reply). It is
            # offered as unverified below. With a requested input the answer gate judges the voices there.
            self._event('solve_delivery_refused', dict(unsettled, artifact=outcome['artifact'],
                                                       by='unsettled disagreement'))
            result.update(status='incomplete', artifact=None, reused=[], unsettled=unsettled,
                          reason='the method and the %s disagree on %d input(s) and no blind re-derivation '
                                 'settled them' % (unsettled['by'], unsettled['disputed']))
        if result['status'] == 'checked':
            with ArtifactMemory(self.ws.memory) as memory:
                result['source'] = memory.get(outcome['artifact'])['body']['source']
            pairs = [(c['input'], c['expected']) for c in spec['checks']]
            if spec.get('input') is not None and not self._executions_left():
                result['status'] = 'incomplete'
                result['reason'] = 'shared execution limit reached before the final run on the requested input'
            elif spec.get('input') is not None and depth == 0:
                seconds = getattr(self.deploy_executor, 'timeout', None)
                from .demo import load_method
                code = digest(dict(zip(('source', 'libraries'), load_method(self.ws.memory, outcome['artifact']))))
                before = self.ws.unfinished(code, spec['input'])
                if before is not None and (seconds is None or before >= seconds):
                    # The same method on the same input, with no more time than it had: it would not finish again
                    # (an indefinite search: each such final run may take hours).
                    delivered = {'status': 'not_run', 'reason': 'executor status timeout'}
                    self._event('solve_final_run_skipped', {'artifact': outcome['artifact'], 'seconds': before})
                else:
                    delivered = self.deploy(outcome['task'], outcome['artifact'], spec['input'], outcome['run'])
                result['input'] = spec['input']
                result['answer'] = delivered.get('result') if delivered.get('status') == 'completed' else None
                if delivered.get('status') != 'completed':
                    result['status'] = 'incomplete'
                    result['reason'] = 'the checked method failed on the requested input'
                    if 'timeout' in str(delivered.get('reason')):
                        limit = before if delivered.get('status') == 'not_run' else seconds
                        result['reason'] += (': it did not finish within %s s' % limit if limit else
                                             ': it did not finish within the time limit')
                        if delivered.get('status') == 'not_run':
                            result['reason'] += ' in an earlier round (not run again)'
                        else:
                            self.ws.remember_unfinished(code, spec['input'], seconds)
                        result['unfinished'] = {'artifact': outcome['artifact'], 'input': spec['input'],
                                                'seconds': limit, 'memory': getattr(self.deploy_executor, 'memory', None)}
                elif result['answer'] is None:
                    # The task asked for an answer; null is none (first-hand session g: it was delivered as checked).
                    result['status'] = 'incomplete'
                    result['reason'] = 'the checked method returned null on the requested input: no answer'
                else:
                    pairs.append((spec['input'], result['answer']))
                    if outcome.get('composition'):
                        result['reused'] = sorted(set(result['reused']) |
                                                  set(delivered.get('dependency_calls', [])))
                    disputed = (self._answer_gate(prompt, spec, result['answer'], outcome['artifact'])
                                or self._piece_gate(prompt, outcome['artifact'], spec['input']))
                    if disputed:
                        result.update(status='incomplete', artifact=None, reused=[], reason=disputed['reason'])
                        result['disputed_answer'] = disputed
                        result.pop('answer', None)
            elif spec.get('input') is not None and self._executions_left():
                # A piece planned with an input of its own faces the same answer gate before the parent
                # assembles it (its answer is not deployed: it is computed once in the sandbox).
                from .demo import load_method
                source, libraries = load_method(self.ws.memory, outcome['artifact'])
                got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), [spec['input']], libraries)
                if isinstance(got, list) and len(got) == 1 and isinstance(got[0], dict) and 'value' in got[0]:
                    disputed = (self._answer_gate(prompt, spec, got[0]['value'], outcome['artifact'])
                                or self._piece_gate(prompt, outcome['artifact'], spec['input']))
                    if disputed:
                        result.update(status='incomplete', artifact=None, reused=[], reason=disputed['reason'])
                        result['disputed_answer'] = disputed
            if spec.get('property_overruled') and not spec.get('verify'):
                result['property_evidence'] = dict(spec['property_overruled'])
            if spec.get('verify') and result['status'] == 'checked':
                cached = spec.get('property_cache') or {}
                keys = [pramana.key({'input': i, 'expected': o}) for i, o in pairs]
                fresh_pairs = [pair for pair, k in zip(pairs, keys) if k not in cached]
                ran = self.verify(spec, fresh_pairs) if fresh_pairs else []
                if ran is None and len(fresh_pairs) == len(pairs):
                    verdicts = None   # the verifier did not run at all
                else:
                    fresh = iter(ran if ran is not None else [None] * len(fresh_pairs))
                    verdicts = [cached[k] if k in cached else next(fresh) for k in keys]
                result['property_evidence'] = {'source_digest': digest(spec['verify']),
                                              'support': 'model_authored_heuristic', 'verdicts': verdicts}
                self._event('solve_property_evidence', dict(result['property_evidence'],
                            check_scope=spec['check_scope'], artifact=outcome['artifact'], pairs=pairs))
                if verdicts is None or any(v is None for v in verdicts):
                    # The model's verifier failed to run: that is missing evidence, not a wrong answer.
                    # The result stands on the finite checks alone, and says so.
                    result['property_evidence']['resolution'] = 'verifier gave no usable verdict; finite checks only'
                elif any(v is False for v in verdicts):
                    resolution = self._settle_property(prompt, spec, pairs, verdicts)
                    if depth == 0:
                        # This late dispute/overrule has no portable obligation record in the small
                        # contract checkpoint. Do not export a verifier whose verdict was contested.
                        self._continuity_spec = None
                    result['property_evidence']['resolution'] = resolution
                    if resolution.startswith('verifier overruled'):
                        self._withdraw_support(spec['contract'], pramana.PROPERTY)
                    else:
                        result['status'] = 'incomplete'
                        result['reason'] = 'model property disagrees with checked outputs; ' + resolution
            said = self._testimony.get(result.get('artifact')) if depth == 0 else None
            if said:
                result['witness'] = said
                if result['status'] == 'checked' and said.get('outcome') == 'disagrees':
                    result['status'] = 'incomplete'
                    result['reason'] = ('the checked method is contradicted by the published sequence %s at n = %s'
                                        % (said['sequence'], ', '.join(str(d['n']) for d in said['disagrees'][:4])))
            if result['status'] == 'checked' or (result.get('reason') or '').startswith('shared execution limit'):
                # A method that passed its checks stays usable even when the budget stopped its final run.
                stated = self._condition_record(spec, outcome['artifact']) if spec.get('conditions') else []
                if stated:
                    result['conditions'] = stated
                derivation = spec.get('derivation') if self.conditions else None
                if derivation:
                    result['derivation'] = {'text': derivation, 'status': DERIVATION_UNVERIFIED}
                self.ws.remember_solved(prompt, spec['contract'], outcome['artifact'], form=pramana.form(spec['checks']),
                                        evidence=self._evidence(spec, outcome['artifact']),
                                        supersedes=[r['artifact'] for r in self._refuted_here if r.get('task') == prompt],
                                        conditions=stated, derivation=derivation)
                if outcome.get('assembly'):
                    assembly = outcome['assembly']
                    self.ws.remember_split(prompt, assembly['subtasks'], json.loads(assembly['recipe_json']))
        if result['status'] != 'checked':
            result['delivered_by'] = None
            result.pop('composition', None)
            if not (result.get('reason') or '').startswith('shared execution limit'):   # a budget is not a lesson
                self.ws.learn(prompt, f'{spec["contract"]} -> not solved: {result["reason"]}',
                              form=pramana.form(spec['checks']))
        result['evidence'] = self._evidence(spec, result['artifact'] if result['status'] == 'checked' else None)
        if result['status'] != 'checked':
            result['void'] = self._void(result, spec, best)
            if depth == 0:
                notes = {}
                if best and best.get('artifact'):
                    notes[best['artifact']] = ('not verified: passes %d of %d checks; nothing else has refuted it'
                                               % (len(best.get('passed_checks') or []), len(spec['checks'])))
                if held_back:
                    notes[held_back] = ('not verified: it passed every check and was held back (%s)'
                                        % str(result.get('reason'))[:200])
                unverified = self._unverified(prompt, spec, [held_back, (best or {}).get('artifact')], notes)
                if unverified:
                    result['unverified'] = unverified
        return result

    def _voice(self, prompt, spec, value, kind, said, source=None):
        """Keep what a derivation said about an input of this task, for the answer gate (kept per task, given
        conditions and input: two pieces worded alike never share their voices; and on every input, not only the
        requested one, since a parent hands a piece inputs of its own: `_piece_gate`). Voices outlive a
        replan that rewords the contract: a stale voice can only void an answer, while a dropped one can let the
        same mistake through again (ninth review, P-a, against the eighth's P2)."""
        if said is not pramana.MISSING:   # on every input: a parent may hand this one to the piece (H2)
            self._voices.setdefault((prompt, self._given_id(prompt), canonical(value)), []).append((kind, said, source))

    def _voice_key(self, prompt, spec):
        return (prompt, self._given_id(prompt), canonical(spec['input']))

    def _piece_gate(self, prompt, artifact, value):
        """The answer gate for the pieces of a decomposition, on the inputs this level hands them (closes H2). A
        piece planned without a requested input of its own records what its derivations said about every input
        (`_voice`); here the method is run once on the requested input with each call of a piece written in this job
        traced (which piece, on what input, answering what), and each piece's answer there faces the piece's own
        voices on that input by the gate's rule: if another value has as many voices (the piece counts one), the
        whole is disputed. Returns the void record, or None. One sandbox run, only when a piece holds voices."""
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, artifact)
        pieces = {a: self._piece_of[a] for a in libraries if a in self._piece_of}
        voiced = {(p, g) for p, g, _ in self._voices}
        if not pieces or not any(p in voiced for p in pieces.values()) or not self._executions_left():
            return None
        got = self._sandbox_job(TRACE_RUN % source, value, libraries)
        if not (isinstance(got, dict) and isinstance(got.get('calls'), list)):
            return None
        refuted = self.ws.refuted()
        for call in got['calls']:
            if not (isinstance(call, list) and len(call) == 3 and call[0] in pieces
                    and isinstance(call[2], dict) and 'value' in call[2]):
                continue   # a call that raised, or whose answer could not be read, says nothing
            (piece, piece_given), given, answer = pieces[call[0]], call[1], call[2]['value']
            live = self._reference_of.get(call[0])
            recorded = [(k, v, src) for k, v, src in _heard(self._voices.get((piece, piece_given, canonical(given)), []), answer)
                        if not (k == 'reference' and src != live)]
            said = [v for kind, v, source in recorded if v is not None
                    and not (kind == 'rival' and (source in refuted or source == call[0]))]
            agree = 1 + sum(1 for v in said if same_value(v, answer))
            rivalling = [sum(1 for v in said if same_value(v, d) and not same_value(v, answer))
                         for d in said if not same_value(d, answer)]
            if rivalling and max(rivalling) >= agree:
                record = {'input': value, 'piece': piece, 'piece_input': given, 'piece_answer': answer,
                          'voices': said, 'reason': 'a piece\'s answer on the input it is handed is disputed: another '
                                                     'value has as many voices'}
                self._event('solve_answer_disputed', dict(record, artifact=artifact, by='piece voices'))
                return record
        return None

    def _answer_gate(self, prompt, spec, answer, artifact):
        """Śūnya at the last step: the answer to the requested input is delivered only if nothing outvotes it.

        A delivery rule that leans safe (a void costs one answer, a wrong delivery costs trust):
          - the planner's reference, if it survived settlement and the planner declared this input a small
            one, is run on it now (in every round): if it disagrees, the answer is void; if no execution is
            left to run it, the answer is not delivered either; if it raised or did not finish (a timeout),
            it says nothing there;
          - every voice recorded about this input is counted (blind re-derivations; rival methods not shown
            wrong, other than the method delivered; the reference when it agrees; a null from a derivation
            is no voice unless the answer is null), values compared as the suite scores them, voice by voice: if
            another value has as many voices as the answer (the method counts one), the answer is disputed and void.
        Returns the void record, or None."""
        value, key = spec['input'], canonical(spec['input'])
        refuted = self.ws.refuted()
        live = digest(spec['reference']) if spec.get('reference') else None
        recorded = [(k, v, src) for k, v, src in _heard(self._voices.get(self._voice_key(prompt, spec), []), answer)
                    if not (k == 'reference' and src != live)]   # a reference set aside since says nothing (S8)
        voices = [(kind, said) for kind, said, source in recorded
                  if not (kind == 'rival' and (source in refuted or source == artifact))
                  and (said is not None or answer is None)]
        small = {canonical(v) for v in spec.get('small_inputs') or []}
        record = {'input': value, 'answer': answer, 'voices': [{'kind': k, 'value': v} for k, v in voices]}
        if spec.get('reference') and key in small:
            if not self._executions_left():
                self._event('solve_answer_disputed', dict(record, artifact=artifact, by='reference not run'))
                return dict(record, reason='no execution was left to run the reference on the requested input')
            out = self._sandbox(DIFFERENTIAL_RUN % (spec['reference'], 'reference'), [value])
            if not (isinstance(out, list) and len(out) == 1 and isinstance(out[0], dict)):
                record['reference'] = {'error': 'did not finish'}   # a timeout or a sandbox failure: no voice
            elif 'value' in out[0]:
                record['reference'] = out[0]['value']
                if not same_value(out[0]['value'], answer):
                    self._event('solve_answer_disputed', dict(record, artifact=artifact, by='reference'))
                    return dict(record, reason='the reference disagrees with the answer to the requested input')
                if not any(kind == 'reference' for kind, _ in voices):   # the reference is one voice
                    voices.append(('reference', out[0]['value']))
            else:   # it ran and raised there: it says nothing about this input
                record['reference'] = {'error': out[0].get('error') or 'did not finish'}
        # Counted voice by voice, in no order (tolerance is not transitive): a voice within tolerance of the
        # answer is for the answer; another value's voices are those within its tolerance and not the answer's.
        said = [v for _, v in voices]
        agree = 1 + sum(1 for v in said if same_value(v, answer))
        rivalling = [sum(1 for v in said if same_value(v, d) and not same_value(v, answer))
                     for d in said if not same_value(d, answer)]
        if rivalling and max(rivalling) >= agree:
            self._event('solve_answer_disputed', dict(record, artifact=artifact, by='voices'))
            return dict(record, reason='the answer to the requested input is disputed: another value has as many voices')
        return None

    def _evidence(self, spec, artifact):
        """Pramāṇa of a result: the support of each check it rests on, and what it was tested on beyond them.

        `support` is the weakest label among the checks (pramana.summarize): 'given' only when every
        check is the user's; 'hand' when any check rests on the model's worked value alone."""
        book = self._checkbooks.get(spec['check_scope'])
        labels = [self._support_label(book, r) for r in book.records] if book else []
        return {'support': pramana.summarize(labels),
                'checks': {label: labels.count(label) for label in pramana.ORDER if label in labels},
                'tested_beyond_checks': dict(self._tested.get(artifact, {})) if artifact else {}}

    def _unverified(self, prompt, spec, candidates, notes=None):
        """Never less than the model alone: when nothing is verified, the best attempt nothing has shown wrong is
        still returned, marked unverified, never as checked. First a method that passed every check but was held
        back (by a guard or the answer gate), then the attempt that passed the most checks; a method that is, or
        runs, one shown wrong is never offered. On the requested input it is run once (its answer is unverified)."""
        for artifact in candidates:
            if not artifact or self._refuted_in(artifact, prompt):
                continue
            out = {'artifact': artifact, 'status': 'unverified',   # a value labelled for what it is (āsanna)
                   'note': (notes or {}).get(artifact) or 'not verified: the best attempt nothing has shown wrong'}
            if spec.get('input') is not None and self._executions_left():
                from .demo import load_method
                source, libraries = load_method(self.ws.memory, artifact)
                got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), [spec['input']], libraries)
                if isinstance(got, list) and len(got) == 1 and isinstance(got[0], dict) and 'value' in got[0]:
                    out['input'], out['answer'] = spec['input'], got[0]['value']
            self._event('solve_unverified', out)
            return out
        return None

    def _void(self, result, spec, best):
        """Śūnya: no verified answer, with what is known kept in its place.

        Nothing is delivered, but the place is not empty: the checks as settled and their support,
        the best attempt and which checks it passes, and the methods shown wrong on the way."""
        return {'reason': result.get('reason'),
                'checks': spec['checks'],
                'support': self._evidence(spec, None)['checks'],
                'best_attempt': ({'artifact': best.get('artifact'), 'passed': len(best.get('passed_checks') or []),
                                  'of': len(spec['checks'])} if best else None),
                'refuted': list(self._refuted_here)}

    def _settle_property(self, prompt, spec, pairs, verdicts):
        """A model verifier that rejects outputs which passed every check: settle it, don't stop.

        The auditor re-derives the disputed values blindly (it sees neither output nor verdict). If it
        agrees with every disputed output, the verifier is the faulty side and is overruled. Otherwise
        the dispute is real and is reported as such."""
        disputed = [pair for pair, verdict in zip(pairs, verdicts) if verdict is False]
        book = self._checkbooks.get(spec.get('check_scope'))
        anchors = {canonical((r['input'], r['expected'])) for r in (book.records if book else [])
                   if r['origin'] == 'user'}
        disputed = [pair for pair in disputed if canonical(pair) not in anchors]
        if not disputed:
            return "verifier overruled: it rejects the user's own examples, which stand"
        if not self._calls_left():
            return 'unsettled: no model calls left for a blind re-derivation'
        reply = self._ask(self.auditor, {'task': prompt, 'contract': spec['contract'],
                                         'inputs': [i for i, _ in disputed]})
        values = reply.get('expected')
        if not isinstance(values, list) or len(values) != len(disputed):
            return 'unsettled: the blind re-derivation gave no usable values'
        if all(v is None for v in values):
            return 'unsettled: the blind re-derivation could not work out the disputed values'
        if all(v is not None and canonical(v) == canonical(out) for v, (_, out) in zip(values, disputed)):
            return 'verifier overruled: a blind re-derivation agrees with the checked outputs'
        return 'a blind re-derivation also disagrees with the checked outputs'

    def _sandbox(self, source, value):
        """Run model-written source in the sandbox once; the JSON result, or None on any failure."""
        return self._sandbox_job(source, value, None)

    def _sandbox_once(self, source, value, libraries):
        try:
            job = {'source': source, 'input': value}
            if libraries:
                job['libraries'] = libraries
            response = self._execute(job)
            if response.get('status') != 'completed' or response.get('exit_code') != 0:
                return None
            return json.loads(response['output']).get('result')
        except Exception:
            return None

    def _substitution(self, spec, artifact, source, libraries, supported):
        """Run the substitution test (check_substitution.py) on a method that passed its checks: one sandbox
        execution. None when there is nothing to test, too few executions are left, or the run was cut short."""
        if self._executions_left() <= 3:   # deploy and the answer gate's reference come after
            return None
        prepared = check_substitution.prepare({'': source, **libraries}, spec['checks'],
                                              spec.get('small_inputs') or [])
        if prepared is None:
            return None
        rows = self._sandbox(check_substitution.RUN, prepared['payload'])
        tested = check_substitution.read(prepared, rows, spec['checks'], supported)
        self._count('substitution_tested' if tested else 'substitution_cut')
        self._event('solve_substitution', {'artifact': artifact, 'constants': len(prepared['constants']),
                    'inputs': len(prepared['inputs']), 'judged': tested['judged'] if tested else None,
                    'special': [c['input'] for c, _ in tested['special']] if tested else None})
        return tested

    def _question_literals(self, prompt, spec, tested, sources):
        """Ask the blind auditor about the method around each literal the substitution test found (see
        _memorized), in one call. Returns (disputed (check, source key) pairs, culprit source keys).

        The auditor is a derivation by hand: its floats are compared within a hand's rounding (`_close`), and it
        may answer null for an input the contract does not define (no answer). A dispute around the literal (the
        method's own rule disagrees with it near the check) sets that method aside for this task, remembered by
        its source (the same source is disputed again without asking); it never refutes a method for every task,
        since one noisy derivation must not. A dispute at the check's own input (a hand value the auditor
        disputes, copied as a literal) is a copied wrong check: its holder is a culprit, as the text's are. With
        no call left, or no usable reply, nothing is disputed (stated: the method stands)."""
        task = (prompt, self._given_id(prompt))
        before = self._disputed.setdefault(task, set())
        book = self._checkbooks.get(spec.get('check_scope'))
        backed = {canonical(r['input']) for r in (book.records if book else [])
                  if self._support_label(book, r) in (pramana.GIVEN, pramana.REFERENCE, pramana.TWO_DERIVATIONS)}
        hits, disputed, culprits, asked = [], [], [], []
        for check, key in tested['special']:
            if digest(sources.get(key, '')) in before:
                disputed.append((check, key))
                continue
            own = canonical(check['input']) not in backed
            claims = [(check['input'], check['expected'], True)] if own else []
            claims += [(v, a, False) for v, a in tested['around'].get(json.dumps(check['input'], sort_keys=True), [])[:3]]
            hits.append((check, key, claims))
            asked += [v for v, _, _ in claims if canonical(v) not in {canonical(x) for x in asked}]
        if not asked or prompt is None or not self._calls_left():
            return disputed, culprits
        reply = self._ask(self.auditor, {'task': prompt, 'contract': spec['contract'], 'inputs': asked})
        audited = reply.get('expected') if isinstance(reply, dict) else None
        if not isinstance(audited, list) or len(audited) != len(asked):
            self._event('solve_literal_questioned', {'inputs': asked, 'outcome': 'no_reply'})
            return disputed, culprits
        said = {canonical(v): a for v, a in zip(asked, audited)}

        words = {c['expected'].strip().lower() for c in spec['checks'] if isinstance(c['expected'], str)}
        words |= {a.strip().lower() for _, _, claims in hits for _, a, _ in claims if isinstance(a, str)}

        def answered(v, answer):
            # Null is no answer; so is a word saying the input is not defined, unless the word is one of the task's
            # own answers ("valid" / "invalid"): then it is an answer like any other (S3).
            a = said.get(canonical(v))
            return a is not None and not (isinstance(a, str) and a.strip().lower() in UNDEFINED
                                          and a.strip().lower() not in words)
        for check, key, claims in hits:
            against = [(v, own) for v, answer, own in claims
                       if answered(v, answer) and not _close(said.get(canonical(v)), answer)]
            if against:
                disputed.append((check, key))
                before.add(digest(sources.get(key, '')))
                if any(own for _, own in against) and key not in culprits:
                    culprits.append(key)
        self._count('literal_disputed' if disputed else 'literal_confirmed')
        self._event('solve_literal_questioned', {'inputs': asked, 'auditor': audited,
                    'disputed': [c['input'] for c, _ in disputed],
                    'outcome': 'disputed' if disputed else 'confirmed'})
        return disputed, culprits

    def _derived_inputs(self, spec):
        """The inputs of the active checks an executed derivation supports (the reference, two derivations)."""
        book = self._checkbooks.get(spec.get('check_scope'))
        return [r['input'] for r in (book.records if book else [])
                if self._support_label(book, r) in (pramana.REFERENCE, pramana.TWO_DERIVATIONS)]

    def _memorized(self, outcome, spec, prompt=None):
        """Checks returned literally by the method or by any stored method it runs (a wrapper hides nothing).

        Every method it runs is examined, those stored by earlier tasks too: a stored special case that
        equals one of these checks is never delivered here. Only methods written during this job are
        returned as culprits to refute; one stored by an earlier task was checked on that task's own checks,
        and a literal in it equal to one of these (a days-in-a-month table composed into a new task) may be
        its own logic, so it is set aside for this task without being marked wrong for every other."""
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, outcome['artifact'])
        found, culprits = [], []
        # Every check is looked for: a literal that answers a check is never delivered, whatever supports the
        # check (a method special-casing even a correct check may be wrong where no check reaches). Support only
        # decides refutation: a literal equal to a check the reference or two derivations support is a correct
        # fact at that input, so its method is set aside for this task without being marked wrong.
        supported_inputs = self._derived_inputs(spec)
        supported = {canonical(v) for v in supported_inputs}
        # Two readings. The text (check_memorization.py) decides: what it finds is set aside, and refuted below.
        # The substitution test (check_substitution.py) runs the method with each constant changed and finds a
        # literal written for one input whatever its form. A literal is a warning about the method around it, not
        # about the check: a wrong method patches even a correct check where its own rule fails (a leap-year
        # patch), and a correct table entry ("h": 3600) looks the same. So what only the test finds is a question
        # about the method: the blind auditor is asked, in one call, about the check's input (unless the user, the
        # reference or two derivations already support it) and about a few inputs made from it that the method
        # answers by its own rule. The literal stands only if the auditor agrees with the method everywhere it
        # answered; disputed, the method is set aside, refuted if written in this job (as the text's culprits),
        # and the dispute is kept for this task. A question gives no check any support.
        tested = self._substitution(spec, outcome['artifact'], source, libraries, supported_inputs)
        read = {}
        for artifact, text in [(outcome['artifact'], source), *libraries.items()]:
            read[artifact] = judge_memorized(text, spec['checks'], _known_inputs(spec), supported_inputs)
        decided = any(hits for hits, _ in read.values()) or not all(readable for _, readable in read.values())
        disputed, copied = [], []
        if tested and tested['special'] and not decided:   # the text has decided: no call is spent (Q2)
            disputed, copied = self._question_literals(prompt, spec, tested, {'': source, **libraries})
        for artifact, (hits, readable) in read.items():
            if not readable:
                # too deep to read: set aside here, not marked wrong (the substitution test never clears it)
                self._event('solve_memorized_checks', {'artifact': outcome['artifact'], 'culprits': [],
                            'inputs': [c['input'] for c in hits], 'unreadable': artifact})
                found += [c for c in hits if c not in found]
                continue
            # Refuted for every task if written in this job and holding a literal of a check no executed
            # derivation supports. A piece written for a subtask is held to the parent's checks too: it was not
            # shown them, but a model that shares the planner's mistake writes it into the piece (the second
            # review's 7 -> 22), and a later task would reuse it. The cost, stated: a correct piece whose
            # special case equals a parent's hand check is refuted as well (F7, K9).
            if (hits and artifact not in self._known_before
                    and any(canonical(c['input']) not in supported for c in hits)):
                culprits.append(artifact)
            found += [c for c in hits if c not in found]
        for check, key in disputed:
            holder = outcome['artifact'] if key == '' else key
            if key in copied and holder not in self._known_before and holder not in culprits:
                culprits.append(holder)   # a copied wrong check; a dispute around a literal sets aside only
            if check not in found:
                found.append(check)
        if found:
            self._event('solve_memorized_checks', {'artifact': outcome['artifact'], 'culprits': culprits,
                        'inputs': [c['input'] for c in found]})
        return found, culprits

    def _refuted_in(self, artifact, prompt=None, memory=None):
        """The refuted methods among `artifact` and every stored method it runs: by id, for any task; by
        source, for this task (the same source stored again under a new id after a replan dropped its
        counterexample is still wrong here). Read from the live memory (`memory` if already open), not a
        copy of it, so it costs little when asked for every reuse candidate."""
        from .campaign import _closure
        refuted = self.ws.refuted()
        sources = self.ws.refuted_sources(prompt) if prompt is not None else set()
        if not (refuted or sources):
            return []

        def scan(memory):
            try:
                texts = {a: memory.get(a)['body'].get('source', '') for a in _closure(memory, artifact)
                         if memory.get(a).get('kind') == 'method'}
            except KeyError:
                return []
            return sorted(a for a, text in texts.items() if a in refuted or digest(text) in sources)
        if memory is not None:
            return scan(memory)
        with ArtifactMemory(self.ws.memory) as memory:
            return scan(memory)

    def _refute(self, artifact, reason, prompt=None):
        """A checked method shown wrong: never offered or reused again, here or on later tasks; its source
        is also refused for this task (`prompt`) under any new id."""
        receipt = self._event('solve_refuted', {'artifact': artifact, 'reason': reason})
        try:
            with ArtifactMemory(self.ws.memory) as memory:
                source_digest = digest(memory.get(artifact)['body']['source'])
        except (KeyError, TypeError):
            source_digest = None
        self.ws.refute(artifact, reason, source_digest, prompt)
        self._refuted_here.append({'artifact': artifact, 'reason': reason, 'task': prompt})
        if receipt:
            with ArtifactMemory(self.ws.memory) as memory:
                try:
                    memory.invalidate(artifact, reason, [receipt])
                except KeyError:
                    pass

    def _property_dispute_gate(self, prompt, spec, artifact):
        """Replay uncertain inputs against each candidate; changing its identity settles nothing.

        Agreement with a blind auditor closes that disagreement for this candidate. Otherwise a live
        reference or an independently confirmed check must agree with the candidate. A failed or
        budget-blocked replay remains unresolved. The discarded property supplies no evidence here,
        and none of these hypotheses is installed as a check or a permanent refutation.
        """
        records = self.ws.property_disputes(prompt, self._given_id(prompt))
        if not records:
            return None
        hypotheses = {}
        for record in records:
            for hypothesis in record['dispute']['hypotheses']:
                hypotheses[canonical([hypothesis['input'], hypothesis['auditor_value']])] = hypothesis
        inputs = {canonical(h['input']): h['input'] for h in hypotheses.values()}
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, artifact)
        outputs = {}
        values = list(inputs.values())
        for start in range(0, len(values), MAX_PROBES):
            if not self._executions_left():
                break
            batch = values[start:start + MAX_PROBES]
            got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), batch, libraries)
            if isinstance(got, list) and len(got) == len(batch):
                outputs.update({canonical(value): out['value'] for value, out in zip(batch, got)
                                if isinstance(out, dict) and 'value' in out})
        confirmed = {canonical(c['input']): c['expected']
                     for c in self.ws.confirmed(prompt, self._given_id(prompt))}
        unresolved = [h for h in hypotheses.values()
                      if canonical(h['input']) not in outputs or not (
                          same_value(outputs[canonical(h['input'])], h['auditor_value']) or
                          (canonical(h['input']) in confirmed and
                           same_value(outputs[canonical(h['input'])], confirmed[canonical(h['input'])])))]
        # The surviving reference may resolve an auditor's mistaken value. It still must execute on
        # this exact input; merely repairing an unrelated differential check is insufficient.
        if unresolved and spec.get('reference'):
            ref_values = list({canonical(h['input']): h['input'] for h in unresolved
                               if canonical(h['input']) in outputs}.values())
            supported = set()
            for start in range(0, len(ref_values), MAX_PROBES):
                if not self._executions_left():
                    break
                batch = ref_values[start:start + MAX_PROBES]
                got = self._sandbox(DIFFERENTIAL_RUN % (spec['reference'], 'reference'), batch)
                if isinstance(got, list) and len(got) == len(batch):
                    supported.update(canonical(value) for value, out in zip(batch, got)
                                     if isinstance(out, dict) and 'value' in out
                                     and same_value(out['value'], outputs[canonical(value)]))
            unresolved = [h for h in unresolved if canonical(h['input']) not in supported]
        self._event('solve_property_dispute_replayed', {'artifact': artifact, 'inputs': len(inputs),
                    'executed': len(outputs), 'unresolved': unresolved})
        if unresolved:
            return {'artifact': artifact,
                    'reason': 'the property was overruled, and other auditor disagreements remain unsettled',
                    'hypotheses': unresolved, 'property_evidence': records[-1]['property_evidence']}
        return None

    def differential(self, prompt, spec, artifact):
        """Compare a checked method with the planner's slow reference on small inputs.

        Both run in the sandbox (the method with every stored method it uses, so an inherited
        error shows too). Where they disagree, the auditor re-derives the value blindly; it sees
        neither result. A disagreement becomes a new check only when the auditor agrees with the
        reference and not with the method. A reference the auditor contradicts is ignored. Returns
        the number of checks added (they form a new check scope)."""
        batches = _probe_batches(spec)
        if not spec.get('reference') or not batches or not self._executions_left():
            return 0
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, artifact)
        # The planner's small inputs, then inputs near the checks, each in its own sandbox run, so a
        # reference too slow on one batch still gives evidence on the other.
        inputs, method, reference = [], [], []
        for batch in batches:
            if not self._executions_left():
                break
            got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), batch, libraries)
            want = self._sandbox(DIFFERENTIAL_RUN % (spec['reference'], 'reference'), batch)
            if isinstance(got, list) and isinstance(want, list) and len(got) == len(want) == len(batch):
                inputs, method, reference = inputs + batch, method + got, reference + want
        evidence = {'artifact': artifact, 'reference_digest': digest(spec['reference']), 'inputs': len(inputs)}
        if not inputs:
            self._event('solve_differential', dict(evidence, outcome='not_run'))
            return 0
        disputed = []
        small = {canonical(v) for v in spec.get('small_inputs') or []}
        # Both raised where the plan declares the input valid: not "no evidence" but a shared failure (first-hand
        # session r: Python's limit on writing long integers was in the method and the reference alike). An
        # exception is no value of the contract, whatever the other derivation does.
        shared = [{'input': json.dumps(value)[:120], 'raised': got['error'], 'reference raised': want['error'],
                   'by': 'the method and the reference alike'}
                  for value, got, want in zip(inputs, method, reference)
                  if canonical(value) in small and isinstance(got, dict) and 'error' in got
                  and isinstance(want, dict) and 'error' in want]
        if shared:
            self._raised.setdefault(artifact, []).extend(shared)
            evidence['shared_raised'] = len(shared)
        for value, want in zip(inputs, reference):   # a voice where the planner declared the input small (K7's rule)
            if canonical(value) in small and isinstance(want, dict) and 'value' in want:
                self._voice(prompt, spec, value, 'reference', want['value'], digest(spec['reference']))
        for value, got, want in zip(inputs, method, reference):
            if not isinstance(want, dict) or 'value' not in want:
                continue  # the reference could not do this input: no evidence
            if isinstance(got, dict) and got.get('timeout') is True:
                continue  # the method ran out of its time on this input: no evidence, as a whole batch cut short was
            if not (isinstance(got, dict) and 'value' in got and same_value(got['value'], want['value'])):
                disputed.append((value, got, want['value']))
        # Only inputs both sides computed count as held; one the reference could not run is no evidence.
        evidence['agreed'] = sum(1 for got, want in zip(method, reference)
                                 if isinstance(want, dict) and 'value' in want and isinstance(got, dict)
                                 and 'value' in got and same_value(got['value'], want['value']))
        if artifact not in self._valli_bottom:   # a vallī method's bottom is the reference: agreement proves nothing
            self._tested_on(artifact, 'reference', evidence['agreed'])
        if not disputed:
            self._unsettled.pop((artifact, 'reference'), None)
            self._event('solve_differential', dict(evidence, outcome='agree'))
            return 0
        disputed = _requested_first(disputed, spec)[:6]
        # Method and reference are one voice each on these inputs: a tie until a blind re-derivation speaks.
        unsettled = {'by': 'reference', 'disputed': len(disputed), 'inputs': [value for value, _, _ in disputed]}
        if not self._calls_left():
            self._unsettled[artifact, 'reference'] = unsettled
            self._event('solve_differential', dict(evidence, outcome='unsettled_no_calls', disputed=len(disputed)))
            return 0
        reply = self._ask(self.auditor, {'task': prompt, 'contract': spec['contract'],
                                         'inputs': [value for value, _, _ in disputed]})
        values = reply.get('expected')
        if not isinstance(values, list) or len(values) != len(disputed):
            self._unsettled[artifact, 'reference'] = unsettled
            self._event('solve_differential', dict(evidence, outcome='unsettled_no_auditor', disputed=len(disputed)))
            return 0
        self._unsettled.pop((artifact, 'reference'), None)
        for (value, _, _), audit in zip(disputed, values):
            self._voice(prompt, spec, value, 'auditor', audit)
        confirmed = [{'input': value, 'expected': want} for (value, got, want), audit in zip(disputed, values)
                     if same_value(audit, want) and not (isinstance(got, dict) and 'value' in got
                                                         and same_value(audit, got['value']))]
        # Where the auditor answers and agrees with neither (within a hand's rounding), two derivations reject the
        # method's value though they disagree with each other (W4-n h-r4-collide: a particle starting at the wall).
        # No value has two voices, so nothing becomes a check. It is a perceived failure: the method is asked for
        # again once, shown both values. It does not bar delivery: in W4-l h-c4-puzzle the same split stood over a
        # right method (a hand search said 20, a reference that gave up said -1; the method's 23 was right).
        against = [{'input': value, 'method': got.get('value', got) if isinstance(got, dict) else got,
                    'reference': want, 'blind_re_derivation': audit}
                   for (value, got, want), audit in zip(disputed, values)
                   if audit is not None and not _close(audit, want)
                   and not (isinstance(got, dict) and 'value' in got and _close(audit, got['value']))]
        if against and not confirmed:
            self._against_both[artifact] = against
        return self._extend_checks(prompt, spec, artifact, confirmed, 'solve_differential',
                                   dict(evidence, disputed=len(disputed),
                                        disputed_inputs=[value for value, _, _ in disputed],
                                        method_values=[got for _, got, _ in disputed],
                                        reference_values=[want for _, _, want in disputed],
                                        auditor_values=values, against_both=len(against)),
                                   'differential testing: the reference and a blind re-derivation agree against it',
                                   'differential testing')

    def _extend_checks(self, prompt, spec, artifact, confirmed, kind, evidence, refutation, label):
        """Add checks two independent derivations agree on, refute the method they contradict (when
        `artifact` is given), and move the spec to the new check scope. Returns the checks added.

        Stronger evidence displaces weaker: when the check contract is full, checks resting on a hand
        value alone make room, last first. The refutation stands whether or not a check finds room:
        the two derivations against the method do not depend on it."""
        book = self._checkbooks[spec['check_scope']]
        records = book.records
        supplied = [{'input': r['input'], 'expected': r['expected']} for r in records if r['source'] == 'supplied']
        hand = [{'input': r['input'], 'expected': r['expected']} for r in records
                if r['source'] != 'supplied' and self._support_label(book, r) == pramana.HAND]
        proposed = [{'input': r['input'], 'expected': r['expected']} for r in records if r['source'] != 'supplied']
        self._event(kind, dict(evidence, outcome='disagree', confirmed=confirmed))
        if not confirmed:
            return 0
        if artifact:
            self._refute(artifact, refutation, prompt)
        for check in confirmed:
            self._remember_confirmed(prompt, spec, check)   # kept for this task even when no slot is free now
        new, fitting, displaced = _admit_one_by_one(prompt, spec, supplied, proposed, confirmed, displaceable=hand)
        if new is None or not fitting:
            self._event(kind, dict(evidence, outcome='rejected', reason='no confirmed check fits the check contract'))
            return 0
        if displaced:
            self._event(kind, dict(evidence, outcome='displaced', displaced=displaced))
        self._checkbooks[new.scope_id] = new
        self._carry_oracle_support(book.scope_id, new)
        for added in fitting:   # labels of the checks that stayed carry over: same contract, same pairs
            self._set_support(new, added, pramana.TWO_DERIVATIONS)
        spec['checks'], spec['check_scope'] = new.checks, new.scope_id
        spec['checks_by'] += f' (+{len(fitting)} from {label})'
        return len(fitting)

    def _tested_on(self, artifact, kind, count):
        """Inputs beyond the checks on which a method was tested and held (its upapatti, as evidence)."""
        if artifact and count:
            tested = self._tested.setdefault(artifact, {})
            tested[kind] = tested.get(kind, 0) + count

    def _condition_test(self, spec):
        """The verify property run on each stated condition's tempting wrong output. Marks each condition with what the
        property said, and returns the conditions whose violating output it accepts (it does not use them). A property
        that rejects them all has used every condition the plan states; one that gives no verdict says nothing."""
        conditions = spec['conditions']
        told = self.verify(spec, [(c['input'], c['violating']) for c in conditions])
        if not told or all(v is None for v in told):
            for condition in conditions:
                condition['tested'] = 'the property gave no verdict'
            self._event('solve_condition_test', {'check_scope': spec['check_scope'], 'stated': len(conditions),
                                                 'outcome': 'no verdict: the property did not run on them',
                                                 'property_rejects': 0, 'property_accepts': 0, 'blind_to': []})
            return []
        for condition, verdict in zip(conditions, told):
            condition['tested'] = {True: 'the property accepts its violating output',
                                   False: 'the property rejects its violating output'}.get(verdict,
                                                                                            'the property gave no verdict')
        blind_to = [c['condition'] for c, verdict in zip(conditions, told) if verdict is True]
        if blind_to:
            spec['property_condition_blind'] = blind_to
            self.say('the verify property accepts an output the plan says breaks a stated condition ("%s"): its '
                     'acceptance supports no check' % blind_to[0][:80])
        self._event('solve_condition_test', {'check_scope': spec['check_scope'], 'stated': len(conditions),
                                             'outcome': 'tested', 'property_rejects': sum(v is False for v in told),
                                             'property_accepts': len(blind_to), 'blind_to': blind_to})
        return blind_to

    def _condition_record(self, spec, artifact):
        """The checked method on each stated condition's input, set beside the tempting wrong output: a record kept with
        the method (bīja), not a gate. Whether the planner's violating outputs are right has not been measured on a real
        model, so a method that gives one is reported, never refused on this alone. The probe runs already computed
        these values, so this normally runs nothing new."""
        conditions = spec.get('conditions') or []
        if not conditions or not artifact:
            return []
        # Values the probes already computed are reused by _sandbox_job (no execution spent); only the rest run, and
        # with no execution left they are 'not run'.
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, artifact)
        got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), [c['input'] for c in conditions], libraries)
        rows = []
        for n, condition in enumerate(conditions):
            entry = got[n] if isinstance(got, list) and len(got) == len(conditions) else None
            method = _condition_outcome(entry, condition['violating'])
            tested = condition.get('tested', 'not tested')
            if tested.startswith('the property') and not spec.get('verify'):
                tested += ' (the property was later set aside)'   # what it said is no longer a witness
            rows.append({'condition': condition['condition'], 'input': condition['input'],
                         'violating': condition['violating'], 'tested': tested, 'method': method})
        broken = [r['condition'] for r in rows if r['method'] == 'gives the violating output']
        if broken:
            self.say('the checked method gives the tempting wrong output of a stated condition ("%s"); reported, not '
                     'refused' % broken[0][:80])
        self._event('solve_condition_record', {'artifact': artifact, 'conditions': rows, 'broken': broken})
        return rows

    def property_test(self, prompt, spec, artifact):
        """Upapatti: the method's stated rationale, tested where no check reaches.

        The planner's verify(input, output) property runs on the method's own outputs for the small
        inputs and the inputs near the checks; substitution needs no expected value. Where it rejects
        an output, the auditor re-derives the value blindly. A check is added only when the property
        accepts the auditor's value and the method's value differs: two derivations against the
        method, which is refuted. Where the auditor agrees with the method, the property is wrong
        there and is discarded. Other auditor disagreements in that batch then remain hypotheses:
        a discarded property cannot certify them or refute the method. Returns the checks added."""
        batches = _probe_batches(spec)
        if not spec.get('verify') or not batches or not self._executions_left():
            return 0
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, artifact)
        pairs, verdicts = [], []
        for batch in batches:
            if not self._executions_left():
                break
            got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), batch, libraries)
            if isinstance(got, list) and len(got) == len(batch):
                batch_pairs = [(value, out['value']) for value, out in zip(batch, got)
                               if isinstance(out, dict) and 'value' in out]
                # Each probe batch keeps its own verdicts. A large or slow later batch cannot erase
                # evidence already obtained, and concatenating batches must not exceed the job limit.
                checked = self.verify(spec, batch_pairs) if batch_pairs and self._executions_left() else None
                if checked is not None:
                    pairs += batch_pairs
                    verdicts += checked
        evidence = {'artifact': artifact, 'property_digest': digest(spec['verify']), 'inputs': len(pairs)}
        if not pairs:
            self._event('solve_property_test', dict(evidence, outcome='not_run'))
            return 0
        evidence['accepted'] = sum(v is True for v in verdicts)
        if spec.get('property_condition_blind'):
            # Its rejections still count (it tests some of the conditions); its acceptances are no evidence.
            evidence['condition_blind'] = spec['property_condition_blind']
        else:
            self._tested_on(artifact, 'property', evidence['accepted'])
        rejected = _requested_first([pair for pair, verdict in zip(pairs, verdicts) if verdict is False], spec)[:6]
        if not rejected:
            self._event('solve_property_test', dict(evidence, outcome='agree'))
            return 0
        self._count('property_disputed')
        if not self._calls_left():
            self._event('solve_property_test', dict(evidence, outcome='unsettled_no_calls', disputed=len(rejected)))
            return 0
        reply = self._ask(self.auditor, {'task': prompt, 'contract': spec['contract'],
                                         'inputs': [value for value, _ in rejected]})
        values = reply.get('expected')
        if not isinstance(values, list) or len(values) != len(rejected):
            self._event('solve_property_test', dict(evidence, outcome='unsettled_no_auditor', disputed=len(rejected)))
            return 0
        for (value, _), audited in zip(rejected, values):
            self._voice(prompt, spec, value, 'auditor', audited)
        # A property is a witness only while it survives settlement. Mixed evidence cannot be settled by
        # voting across different inputs: one disproved property and one auditor are not two derivations.
        recorded = dict(evidence, disputed=len(rejected), disputed_inputs=[value for value, _ in rejected],
                        method_values=[out for _, out in rejected], auditor_values=values)
        # A null from the auditor is no agreement: it is also what a derivation by hand says when it cannot work the
        # value out (first-hand session g: on the Erdős input the method's null and the auditor's null overruled a
        # property that rightly rejected null, and null was delivered as a checked answer).
        agrees = [audited is not None and same_value(audited, out) for (_, out), audited in zip(rejected, values)]
        against = [(value, audited) for ((value, _), audited), agreed in zip(zip(rejected, values), agrees)
                   if not agreed and audited is not None]
        if any(agrees):
            # The property rejects an output a blind re-derivation confirms: it is the faulty side there.
            spec['property_overruled'] = {'source_digest': digest(spec['verify']), 'support': 'model_authored_heuristic',
                                          'verdicts': list(verdicts),
                                          'resolution': 'verifier overruled: a blind re-derivation agrees with an '
                                                        'output it rejects on an input beyond the checks'}
            spec['verify'] = None
            self._withdraw_support(spec['contract'], pramana.PROPERTY)
            self._tested.get(artifact, {}).pop('property', None)
            self._event('solve_property_test', dict(recorded, outcome='property_overruled',
                                                     overruled_on=[value for (value, _), agreed in zip(rejected, agrees)
                                                                   if agreed]))
            if against:
                spec['property_dispute'] = {
                    'artifact': artifact,
                    'reason': 'the property was overruled, and other auditor disagreements remain unsettled',
                    'hypotheses': [{'input': value, 'method_value': out, 'auditor_value': audited}
                                   for ((value, out), audited), agreed in zip(zip(rejected, values), agrees)
                                   if not agreed and audited is not None]}
                self._event('solve_property_test', dict(recorded, outcome='unsettled_property_conflict',
                                                       hypotheses=spec['property_dispute']['hypotheses']))
                self.ws.remember_property_dispute(prompt, self._given_id(prompt), spec['property_dispute'],
                                                  spec['property_overruled'])
            return 0
        accepted = self.verify(spec, against) if against and self._executions_left() else None
        confirmed = [{'input': value, 'expected': audited} for (value, audited), ok
                     in zip(against, accepted or [None] * len(against)) if ok is True]
        return self._extend_checks(prompt, spec, artifact, confirmed, 'solve_property_test', recorded,
                                   'property testing: the property and a blind re-derivation agree against it',
                                   'property testing')

    def rival(self, prompt, spec, outcome):
        """Compare a checked method with the other methods the model wrote in the same attempt.

        The checks are written by the model too, and can be wrong. A correct method that fails a
        wrong check is then rewritten until something fits it, and the last fit is what passes. Two
        methods that disagree, on a check or elsewhere, show that the checks do not settle the
        answer. Each rival runs on the check inputs in one sandbox run, and on the planner's small
        inputs and the inputs near the checks in runs of their own, so a slow probe never hides a
        contested check. Where it disagrees with the method, the auditor re-derives the value blindly
        (it sees neither result), and the two-derivation rule applies both ways:
          - auditor and rival against a model-written check: the check is corrected;
          - auditor and rival against the method elsewhere: a new check;
          - auditor and method against the rival: a new check too, and the rival is not used.
        The method is refuted when anything stands against it. The rival is qualified on the new
        checks, with no model call, only when nothing stands against it. User checks and checks an
        oracle supports are never contested: a rival that fails one is ignored. Returns (checks
        corrected or added, the rival's qualification outcome or None)."""
        refuted = self.ws.refuted()
        rivals = [r for r in outcome.get('rivals') or [] if r != outcome['artifact'] and r not in refuted]
        rivals = rivals[-MAX_RIVALS:]
        if not rivals or not self._executions_left():
            return 0, None
        book = self._checkbooks[spec['check_scope']]
        given = {canonical(r['input']) for r in book.records if r['origin'] == 'user'
                 or (book.scope_id, r['id']) in self._oracle_supported}
        checks = spec['checks']
        batches = _probe_batches(spec)
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, outcome['artifact'])
        method_runs = []   # (batch, the method's values) for each probe batch that ran
        for batch in batches:
            if not self._executions_left():
                break
            got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), batch, libraries)
            if isinstance(got, list) and len(got) == len(batch):
                method_runs.append((batch, got))
        for rival in reversed(rivals):
            if not self._executions_left():
                break
            other_source, other_libraries = load_method(self.ws.memory, rival)
            if memorized_checks(other_source, checks, _known_inputs(spec), self._derived_inputs(spec)):
                continue
            on_checks = self._sandbox_job(DIFFERENTIAL_RUN % (other_source, 'run'), [c['input'] for c in checks],
                                          other_libraries)
            if not (isinstance(on_checks, list) and len(on_checks) == len(checks)) or any(
                    not (isinstance(got, dict) and 'value' in got) for got in on_checks):
                continue   # a rival that fails to run on a check is no evidence about it
            contested = [(c['input'], c['expected'], got['value']) for c, got in zip(checks, on_checks)
                         if not same_value(got['value'], c['expected'])]
            # A check the reference or two derivations support already has two voices with the method on its
            # side and cannot be contested. One the property accepts is protected only if the property tells
            # the two values apart: a property that would accept the rival's value too is no witness.
            if any(canonical(value) in given for value, _, _ in contested):
                continue   # a rival that fails a given condition is no alternative
            labels = {canonical(r['input']): self._support_label(book, r) for r in book.records}
            protected = {k for k, label in labels.items() if label in (pramana.GIVEN, pramana.REFERENCE,
                                                                       pramana.TWO_DERIVATIONS)}
            by_property = [(value, rv) for value, _, rv in contested if labels.get(canonical(value)) == pramana.PROPERTY]
            if by_property and spec.get('verify') and self._executions_left():
                told = self.verify(spec, by_property) or [None] * len(by_property)
                protected |= {canonical(value) for (value, _), verdict in zip(by_property, told) if verdict is False}
            if any(canonical(value) in given or canonical(value) in protected for value, _, _ in contested):
                continue
            elsewhere = []
            for batch, got in method_runs:
                if not self._executions_left():
                    break
                want = self._sandbox_job(DIFFERENTIAL_RUN % (other_source, 'run'), batch, other_libraries)
                if not (isinstance(want, list) and len(want) == len(batch)):
                    continue
                elsewhere += [(value, m['value'], r['value']) for value, m, r in zip(batch, got, want)
                              if isinstance(m, dict) and 'value' in m and isinstance(r, dict) and 'value' in r
                              and not same_value(m['value'], r['value'])]
            # The requested input goes first among the inputs beyond the checks (the cap of six must not cut it),
            # and the rival's value there is kept for the answer gate whatever the cap (ninth review, M1).
            asked = spec.get('input')
            on_asked = [d for d in elsewhere if asked is not None and canonical(d[0]) == canonical(asked)]
            disputed = (contested + on_asked + [d for d in elsewhere if d not in on_asked])[:6]
            # The reference, where it survived settlement, is a third voice on the inputs beyond the checks; it
            # is counted with the others, never decides alone, and is not asked about the requested input,
            # which may lie outside the small inputs a reference is written for.
            reference_says = {}
            small = {canonical(v) for v in spec.get('small_inputs') or []}
            voiced = [v for v, _, _ in disputed if canonical(v) not in {canonical(c['input']) for c in checks}
                      and (spec.get('input') is None or canonical(v) != canonical(spec['input']) or canonical(v) in small)]
            if voiced and spec.get('reference') and self._executions_left():
                ref = self._sandbox(DIFFERENTIAL_RUN % (spec['reference'], 'reference'), voiced)
                if isinstance(ref, list) and len(ref) == len(voiced):
                    reference_says = {canonical(v): want['value'] for v, want in zip(voiced, ref)
                                      if isinstance(want, dict) and 'value' in want}
                    for v in voiced:   # a voice where the planner declared the input small (K7's rule)
                        if canonical(v) in reference_says and canonical(v) in small:
                            self._voice(prompt, spec, v, 'reference', reference_says[canonical(v)],
                                        digest(spec['reference']))
            evidence = {'artifact': outcome['artifact'], 'rival': rival,
                        'inputs': sum(len(b) for b, _ in method_runs),
                        'contested_checks': len(contested), 'disagreements': len(elsewhere)}
            self._count('rival_tested')
            if not disputed:
                self._event('solve_rival', dict(evidence, outcome='agree'))
                self._tested_on(outcome['artifact'], 'rival', sum(
                    sum(1 for m in got if isinstance(m, dict) and 'value' in m) for _, got in method_runs))
                continue
            self._count('rival_disputed')
            # What the rival said about the requested input is kept for the answer gate (`_answer_gate`) before
            # the auditor is asked: a disagreement the auditor cannot settle still disputes the answer.
            for value, _, r in disputed + [d for d in on_asked if d not in disputed]:
                self._voice(prompt, spec, value, 'rival', r, rival)   # only where the auditor is asked (S6)
            unsettled = {'by': 'rival', 'disputed': len(disputed), 'inputs': [value for value, _, _ in disputed]}
            if not self._calls_left():
                self._unsettled[outcome['artifact'], 'rival'] = unsettled
                self._event('solve_rival', dict(evidence, outcome='unsettled_no_calls'))
                return 0, None
            reply = self._ask(self.auditor, {'task': prompt, 'contract': spec['contract'],
                                             'inputs': [value for value, _, _ in disputed]})
            audited = reply.get('expected')
            if not isinstance(audited, list) or len(audited) != len(disputed):
                self._unsettled[outcome['artifact'], 'rival'] = unsettled
                self._event('solve_rival', dict(evidence, outcome='unsettled_no_auditor'))
                return 0, None
            check_inputs = {canonical(c['input']) for c in checks}
            missing = object()

            def against(value, loser, winner, audit):
                """The auditor sides with `winner` and the reference, where it spoke, does not side with `loser`."""
                ref = reference_says.get(canonical(value), missing)
                return (same_value(audit, winner) and not same_value(audit, loser)
                        and (ref is missing or not same_value(ref, loser)))
            with_rival = [(value, m, r, a) for (value, m, r), a in zip(disputed, audited) if against(value, m, r, a)]
            with_method = [(value, m, r, a) for (value, m, r), a in zip(disputed, audited)
                           if against(value, r, m, a) and canonical(value) not in check_inputs]
            for (value, m, r), a in zip(disputed, audited):
                self._voice(prompt, spec, value, 'auditor', a)
            corrected = 0
            for value, _, rival_value, audit in with_rival:
                if canonical(value) not in check_inputs:
                    continue
                old_scope = book.scope_id
                try:
                    changed = book.revise(value, _like(audit, rival_value), evidence={
                        'kind': 'blind_model_rederivation', 'artifact': rival, 'auditor': self.auditor.identity,
                        'method_result': rival_value, 'auditor_expected': audit,
                        'contested_by': 'rival method', 'delivered_candidate': outcome['artifact']})
                except (ValueError, CheckConflict):
                    continue
                if changed:
                    corrected += 1
                    self._register_revision(book, old_scope)
                    self._confirm(prompt, book, {'input': value, 'expected': _like(audit, rival_value)})
            if corrected:
                spec['checks'], spec['check_scope'] = book.checks, book.scope_id
                spec['checks_by'] += f' ({corrected} corrected by rival testing)'
            known = {canonical(c['input']) for c in spec['checks']}
            against_method = [{'input': value, 'expected': r} for value, _, r, _ in with_rival if canonical(value) not in known]
            against_rival = [{'input': value, 'expected': m} for value, m, _, _ in with_method if canonical(value) not in known]
            method_wrong = bool(corrected or against_method)
            if method_wrong:
                self._refute(outcome['artifact'], 'rival testing: another method and a blind re-derivation agree against it',
                             prompt)
            added = self._extend_checks(prompt, spec, None, against_method + against_rival, 'solve_rival',
                                        dict(evidence, corrected=corrected, method_values=[m for _, m, _ in disputed]),
                                        '', 'rival testing') if (against_method or against_rival) else 0
            request = outcome['task'].request.split('\n\nChecks run(input) must pass: ')[0]
            if not method_wrong:
                self._event('solve_rival', dict(evidence, outcome='method_upheld', rival_contradicted=len(with_method)))
                if not added:
                    continue
                # The method stands on stronger checks: it is qualified on them again, with no model call.
                return added, self.admit(request, spec['checks'], None, 'method', check_scope=spec['check_scope'],
                                         stored=outcome['artifact'], producer='rival testing',
                                         reason='upheld by a blind re-derivation against a rival',
                                         step=outcome.get('step', 'model'))
            if with_method:
                # Both are wrong somewhere: neither is delivered; the next attempt meets every new check.
                self._event('solve_rival', dict(evidence, outcome='both_contradicted', corrected=corrected))
                return corrected + added, None
            self._event('solve_rival', dict(evidence, outcome='method_refuted', corrected=corrected, added=added))
            again = self.admit(request, spec['checks'], None, 'rival', check_scope=spec['check_scope'],
                               stored=rival, producer='rival testing',
                               reason='rival: passed the checks it settled', step='rival')
            if again['status'] == 'checked':
                self._count('rival_delivered')
            return corrected + added, again
        return 0, None

    def pariksha(self, prompt, spec, artifact):
        """Parīkṣā, the examination of what is known (THE_WHOLE.md: the II 6 / II 21 gap, two rules never set
        against each other). A checked method is set against the stored methods settlement recognized as this task's
        function (`_established`, up to PARIKSHA_KNOWN): both are run on the stored methods' own check inputs, put in
        this task's fields, and on inputs varied from this task's checks (the inputs between them). Where they
        differ the blind re-derivation is asked once. Where it agrees with what is known, two derivations stand
        against the method: the checks are added and the method is refuted, as differential testing does. Where it
        agrees with the method, the stored method is contradicted here: recorded, not refuted, since it may be right
        for its own task. Returns the checks added."""
        known = [e for e in spec.get('established_by') or [] if isinstance(e, dict)][:PARIKSHA_KNOWN]
        form = pramana.form(spec['checks'])
        if not known or form is None:
            return 0
        task_in = json.loads(form)

        def into_task(value, assignment):   # a stored method's input, given in this task's fields, or None
            if assignment is None:
                return value
            if all(field is None for field in assignment.values()):
                return value.get(next(iter(assignment))) if isinstance(value, dict) else None
            if (not isinstance(value, dict) or not isinstance(task_in, dict) or set(assignment) - set(value)
                    or set(assignment.values()) != set(task_in)):
                return None
            return {field: value[role] for role, field in assignment.items()}
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, artifact)
        stored = {}
        inputs, seen = [], {canonical(c['input']) for c in spec['checks']}
        for entry in known:
            try:
                with ArtifactMemory(self.ws.memory) as memory:
                    examples = (memory.get(entry['method']).get('applicability') or {}).get('examples') or []
                stored_source, inherited = load_method(self.ws.memory, entry['method'])
            except (KeyError, ValueError, TypeError, OSError):
                continue
            stored.update(inherited)
            stored[entry['method']] = stored_source
            for example in examples:
                value = into_task(example.get('input'), entry['assignment'])
                if value is not None and canonical(value) not in seen:
                    seen.add(canonical(value))
                    inputs.append(value)
        for value in pramana.varied_inputs([c['input'] for c in spec['checks']]):
            if canonical(value) not in seen:
                seen.add(canonical(value))
                inputs.append(value)
        inputs = inputs[:MAX_PROBES]
        if not inputs or self._executions_left() < 2 + len(known):
            return 0
        mine = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), inputs, libraries)
        theirs = self._sandbox_job(DIFFERENTIAL_RUN % (solve_prastara.ROLE_SCREEN % (
            [[e['method'], e['assignment']] for e in known],), 'screen'),
            [[n, v] for n in range(len(known)) for v in inputs], stored)
        if not (isinstance(mine, list) and len(mine) == len(inputs)
                and isinstance(theirs, list) and len(theirs) == len(known) * len(inputs)):
            self._event('solve_pariksha', {'artifact': artifact, 'outcome': 'not_run', 'known': known})
            return 0
        disputed = []   # (input, the method's value, what is known, the stored method)
        for i, value in enumerate(inputs):
            got = mine[i]
            if not (isinstance(got, dict) and 'value' in got):
                continue
            for n, entry in enumerate(known):
                said = theirs[n * len(inputs) + i]
                if isinstance(said, dict) and 'value' in said and not same_value(said['value'], got['value']):
                    disputed.append((value, got['value'], said['value'], entry['method']))
                    break
        evidence = {'artifact': artifact, 'known': known, 'inputs': len(inputs), 'disputed': len(disputed)}
        if not disputed:
            self._tested_on(artifact, 'knowledge', len(inputs))
            self._event('solve_pariksha', dict(evidence, outcome='agree'))
            return 0
        disputed = disputed[:6]
        if not self._calls_left():
            self._event('solve_pariksha', dict(evidence, outcome='unsettled_no_calls'))
            return 0
        reply = self._ask(self.auditor, {'task': prompt, 'contract': spec['contract'],
                                         'inputs': [value for value, _, _, _ in disputed]})
        values = reply.get('expected')
        if not isinstance(values, list) or len(values) != len(disputed):
            self._event('solve_pariksha', dict(evidence, outcome='unsettled_no_auditor'))
            return 0
        for (value, _, _, _), audit in zip(disputed, values):
            self._voice(prompt, spec, value, 'auditor', audit)
        confirmed = [{'input': value, 'expected': theirs_value} for (value, mine_value, theirs_value, _), audit
                     in zip(disputed, values) if audit is not None and same_value(audit, theirs_value)
                     and not same_value(audit, mine_value)]
        contradicted = [{'input': value, 'stored_method': method, 'known': theirs_value, 'method': mine_value}
                        for (value, mine_value, theirs_value, method), audit in zip(disputed, values)
                        if audit is not None and same_value(audit, mine_value) and not same_value(audit, theirs_value)]
        if contradicted:
            # A contradiction inside what is known, seen: the stored method is not this task's function there.
            self._event('solve_pariksha', dict(evidence, outcome='known_contradicted', contradicted=contradicted))
        return self._extend_checks(prompt, spec, artifact, confirmed, 'solve_pariksha',
                                   dict(evidence, disputed_inputs=[d[0] for d in disputed],
                                        method_values=[d[1] for d in disputed], known_values=[d[2] for d in disputed],
                                        auditor_values=values),
                                   'parīkṣā: what is known and a blind re-derivation agree against it', 'parīkṣā')

    def relation_test(self, spec, artifact):
        """Relations between two runs of a method, stated by the plan (`relations`: moves and agree).

        An unknown fixed by two observations from two positions (Gaṇitapāda 16: the gnomon moved by a known amount;
        Golapāda 13: one moment told from four cities; IV 1 and 27: symmetry) needs no value worked out by anyone:
        if the task's result is unchanged when a list is reversed, the method run on an input and on its reverse must
        agree, on inputs of any size. The relation is model-written, so it is first tested on the checks: where a
        check's input moves to another check's input, agree must hold on their expected values, and a relation a
        check pair contradicts is discarded for the task. Then the method is run on the check inputs, the planner's
        small inputs and inputs varied from them, and on up to RELATION_MOVES moves of each. Returns the pairs on
        which it breaks the relation ([] when none, or when nothing could be run). No model call."""
        source = spec.get('relations')
        bases, seen = [], set()
        for value in [c['input'] for c in spec['checks']] + list(spec.get('small_inputs') or []) + \
                pramana.varied_inputs([c['input'] for c in spec['checks']]):
            if canonical(value) not in seen:
                seen.add(canonical(value))
                bases.append(value)
        bases = bases[:MAX_PROBES]
        job = RELATION_RUN % (source, RELATION_MOVES)
        moved = self._sandbox(job, {'do': 'moves', 'inputs': bases}) if bases else None
        outcomes = spec.setdefault('relation_outcomes', {})
        if not (isinstance(moved, list) and len(moved) == len(bases)):
            self._event('solve_relation_test', {'artifact': artifact, 'outcome': 'not_run'})
            return []
        moves = [(x, m) for x, got in zip(bases, moved) if isinstance(got, dict)
                 for m in (got.get('moved') or []) if canonical(m) != canonical(x)]
        expected = {canonical(c['input']): c['expected'] for c in spec['checks']}
        on_checks = [(x, expected[canonical(x)], m, expected[canonical(m)]) for x, m in moves
                     if canonical(x) in expected and canonical(m) in expected]
        if on_checks:
            told = self._sandbox(job, {'do': 'agree', 'pairs': on_checks})
            if isinstance(told, list) and len(told) == len(on_checks) and any(
                    isinstance(t, dict) and t.get('agree') is False for t in told):
                # A relation that its own given conditions contradict is not knowledge (pramāṇa before use).
                spec['relations'], spec['relations_discarded'] = None, True
                self._event('solve_relation_test', {'artifact': artifact, 'outcome': 'discarded',
                            'contradicted_by_checks': [p[0] for p, t in zip(on_checks, told)
                                                       if isinstance(t, dict) and t.get('agree') is False][:6]})
                return []
        from .demo import load_method
        method_source, libraries = load_method(self.ws.memory, artifact)
        values = list({canonical(v): v for pair in moves for v in pair}.values())[:2 * MAX_PROBES]
        got = self._sandbox_job(DIFFERENTIAL_RUN % (method_source, 'run'), values, libraries) if values else None
        if not (isinstance(got, list) and len(got) == len(values)):
            self._event('solve_relation_test', {'artifact': artifact, 'outcome': 'not_run'})
            return []
        result = {canonical(v): g['value'] for v, g in zip(values, got) if isinstance(g, dict) and 'value' in g}
        pairs = [(x, result[canonical(x)], m, result[canonical(m)]) for x, m in moves
                 if canonical(x) in result and canonical(m) in result]
        told = self._sandbox(job, {'do': 'agree', 'pairs': pairs}) if pairs else None
        if not (isinstance(told, list) and len(told) == len(pairs)):
            self._event('solve_relation_test', {'artifact': artifact, 'outcome': 'not_run', 'pairs': len(pairs)})
            return []
        broken = [{'input': x, 'result': y, 'moved': m, 'moved_result': my}
                  for (x, y, m, my), t in zip(pairs, told) if isinstance(t, dict) and t.get('agree') is False]
        held = sum(isinstance(t, dict) and t.get('agree') is True for t in told)
        outcomes[artifact] = 'broken' if broken else 'held' if held else 'not_run'
        if held and not broken:
            self._tested_on(artifact, 'relation', held)
        self._event('solve_relation_test', {'artifact': artifact, 'outcome': outcomes[artifact], 'pairs': len(pairs),
                    'held': held, 'broken': broken[:6]})
        return broken

    def _no_answer(self, spec, artifact):
        """Whether the method returns null on the requested input, run once as a probe (a value it cannot finish
        within the probe's time says nothing here: the final run has its own limit)."""
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, artifact)
        got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), [spec['input']], libraries)
        said = isinstance(got, list) and len(got) == 1 and isinstance(got[0], dict) and 'value' in got[0]
        self._event('solve_answer_probe', {'artifact': artifact, 'outcome': 'null' if said and got[0]['value'] is None
                                           else 'answer' if said else 'not_finished'})
        return said and got[0]['value'] is None

    def _raised_note(self, artifact):
        """What the actor is told when a checked method raised on inputs the plan declares valid."""
        raised = self._raised.pop(artifact, [])
        return ('\n\nA method that passes every check raised an exception on these inputs, which the plan gives as '
                'valid: %s. An exception is no result. On a valid input return the result the contract defines; for an '
                'input the contract calls invalid, return what it says to return. Keep it correct on every check.'
                '\nThe method:\n%s' % (json.dumps(raised[:6])[:1500], self._source_of(artifact)[:4000]))

    def _testify(self, spec, artifact):
        """Ask the witness about a checked method of a sequence task (one non-negative integer in, an integer out).

        The method is run on WITNESS_TERMS consecutive values from the smallest check input; the witness finds a
        published sequence holding all of them at the same indices; the method is then compared with the table at
        up to WITNESS_PROBES larger indices it reaches, the requested input first. Returns a testimony record, or
        None when the task is not a sequence. No model call."""
        field = _sequence_field(spec['checks'])
        if field is None:
            return None
        wrap = (lambda n: n) if field == '' else (lambda n: {field: n})
        start = max(0, min(c['input'] if field == '' else c['input'][field] for c in spec['checks']))
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, artifact)
        small = list(range(start, start + WITNESS_TERMS))
        got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), [wrap(n) for n in small], libraries) or []
        values = {}
        for n, entry in zip(small, got):
            if not (isinstance(entry, dict) and type(entry.get('value')) is int):
                break   # consecutive terms only
            values[n] = entry['value']
        record = {'witness': self.witness.name, 'asked_terms': len(values)}
        found = self.witness.find_all(values)
        if not found:
            record['outcome'] = 'not found'
            self._event('solve_witness', dict(record, artifact=artifact))
            return record
        requested = spec.get('input')
        at = requested if field == '' else (requested.get(field) if isinstance(requested, dict) else None)
        heard = []   # each sequence that holds the method's first terms, examined in the search's order
        for number, name, published in found:
            if heard and not self._executions_left():
                break
            table = self.witness.table(number) or published
            beyond = sorted(n for n in table if n > small[-1])
            picks = [at] if isinstance(at, int) and at in table and at > small[-1] else []
            for n in ([beyond[-1]] if beyond else []) + [10 ** k for k in range(2, 9)] + beyond[len(beyond) // 2:][:1]:
                if n in table and n > small[-1] and n not in picks and len(picks) < WITNESS_PROBES:
                    picks.append(n)
            got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), [wrap(n) for n in picks],
                                    libraries) if picks else []
            compared, disagrees = 0, []
            for n, entry in zip(picks, got or []):
                if isinstance(entry, dict) and 'value' in entry:
                    compared += 1
                    if entry['value'] != table[n]:
                        disagrees.append({'n': n, 'method': entry['value'], 'published': table[n]})
            heard.append(dict(sequence=number, name=name, compared_beyond=compared, table_reaches=max(table),
                              at_requested=table.get(at) if isinstance(at, int) else None, disagrees=disagrees))
            if not disagrees:
                break   # a sequence that holds the first terms agrees further on: the method is that sequence
        # Testimony is examined, not taken from the first source that agrees (Brahmagupta's Tantraparīkṣā): the
        # method is contradicted only when every sequence that holds its first terms parts from it later.
        chosen = next((h for h in heard if not h['disagrees']), heard[0])
        record.update(chosen, outcome='disagrees' if chosen['disagrees'] else 'agrees',
                      support='published table (testimony, not derived)',
                      examined=[h['sequence'] for h in heard])
        self._event('solve_witness', dict(record, artifact=artifact))
        return record

    def _source_of(self, artifact):
        with ArtifactMemory(self.ws.memory) as memory:
            return memory.get(artifact)['body']['source']

    def large_inputs(self, spec):
        """The planner's large inputs, built by its expression in the sandbox, one job per input (cached)."""
        if 'large_values' not in spec:
            values = []
            expression = spec.get('large_inputs')
            for index in range(MAX_LARGE_INPUTS if isinstance(expression, str) else 0):
                if not self._executions_left():
                    break
                value = self._sandbox(LARGE_INPUT_RUN % expression, index)
                if value is None:
                    break
                values.append(value)
            spec['large_values'] = values
            self._event('solve_large_inputs', {'check_scope': spec['check_scope'], 'expression': expression,
                        'built': len(values), 'sizes': [len(canonical(v)) for v in values]})
        return spec['large_values']

    def scale_test(self, spec, artifact):
        """Run the method once on each of the planner's large inputs; the ones it does not finish in time.

        Only running out counts: a timeout (the scorer's limit is the same), a kill at the memory limit, or a
        failed run whose traceback ends in MemoryError or RecursionError. Any other exception may be the method
        rightly refusing an input the planner got wrong, so it is not a scale failure. When no input could be
        built or run, the test did not run, and says so (it never reads as held). No model call."""
        from .demo import load_method
        source, libraries = load_method(self.ws.memory, artifact)
        slow, ran, reasons, raised = [], [], [], []
        code = digest({'source': source, 'libraries': libraries or {}})
        declared = self.large_inputs(spec) if spec.get('large_inputs') else []
        nested = [v for v in _nested_inputs(spec.get('checks') or []) if canonical(v) not in {canonical(d) for d in declared}]
        for value in declared + nested:
            if (code, canonical(value)) in self._finished_runs:
                # A probe of this code finished on this input within the probe's time, which is less than this
                # run's: the run would finish (session p: the large input was the requested input).
                ran.append({'size': len(canonical(value)), 'status': 'completed', 'exhausted': False,
                            'by': 'an earlier probe of the same code'})
                continue
            if not self._executions_left():
                break
            job = {'source': source, 'input': value}
            if libraries:
                job['libraries'] = libraries
            try:
                response = self._execute(job)
            except Exception as error:
                ran.append({'size': len(canonical(value)), 'status': 'not_run', 'error': type(error).__name__})
                continue
            status = response.get('status') if isinstance(response, dict) else 'invalid_response'
            diagnostics = str(response.get('diagnostics') or '') if isinstance(response, dict) else ''
            last = next((line.strip() for line in reversed(diagnostics.splitlines()) if line.strip()), '')
            failed = status == 'completed' and response.get('exit_code') not in (0, None)
            exhausted = (status == 'timeout' or (failed and response.get('exit_code') == 137)
                         or (failed and last.startswith(('MemoryError', 'RecursionError'))))
            ran.append({'size': len(canonical(value)), 'status': status, 'exhausted': exhausted})
            if failed and not exhausted and any(value is d for d in declared):   # the planner declared it valid
                raised.append({'input': json.dumps(value)[:120], 'raised': last[:200], 'by': 'the scale test'})
            if exhausted:
                slow.append(value)
                # How it ran out, for the actor (first-hand session r: a recursion limit and an unbounded result
                # were told as "use an algorithm whose cost fits", which named neither).
                reasons.append('did not finish within the time limit' if status == 'timeout' else
                               'killed at the memory limit' if response.get('exit_code') == 137 else last[:160])
        completed = sum(r['status'] in ('completed', 'timeout') for r in ran)
        outcome = 'slow' if slow else 'held' if completed else 'not_run'
        spec['scale_outcome'], spec['scale_reasons'] = outcome, reasons
        if raised:
            self._raised.setdefault(artifact, []).extend(raised)
        self._count('scale_' + outcome)
        self._event('solve_scale_test', {'check_scope': spec['check_scope'], 'artifact': artifact,
                    'runs': ran, 'timed_out': len(slow), 'outcome': outcome, 'raised': len(raised),
                    'reason': None if completed else 'no large input was built or could be run'})
        return slow

    def _sandbox_job(self, source, value, libraries):
        """One sandbox run. In a probe batch (DIFFERENTIAL_RUN) a value that ran out of its time before in this
        solve, under the same source, is not run again: it is reported out of time at once (first-hand session f:
        the same method was run on the same never-ending input three times, 20 seconds each)."""
        if not (source.startswith(DIFFERENTIAL_HEAD) and isinstance(value, list) and value):
            return self._sandbox_once(source, value, libraries)
        key = digest({'source': source, 'libraries': libraries or {}})
        slow, known = self._slow_probes, self._probe_values
        # A value that finished before under the same source is not computed again either (first-hand session p:
        # the method ran four times on the requested input, 9 s each: the null probe, the scale test, the
        # differential and the final run). A method is a function of its input; only identical jobs are reused.
        out = [{'timeout': True} if (key, canonical(v)) in slow else known.get((key, canonical(v))) for v in value]
        run = [n for n, entry in enumerate(out) if entry is None]
        if run:
            got = self._sandbox_once(source, [value[n] for n in run], libraries)
            if not (isinstance(got, list) and len(got) == len(run)):
                return got if len(run) == len(value) else None
            method = _probe_method(source, libraries)
            for n, entry in zip(run, got):
                out[n] = entry
                if isinstance(entry, dict) and entry.get('timeout') is True:
                    # Only a value that had time and still did not finish is not run again. One cut short because
                    # earlier values in its batch spent the time says nothing, and is heard again in a later run.
                    if not isinstance(entry.get('seconds'), (int, float)) or 2 * entry['seconds'] >= PROBE_BUDGET:
                        slow.add((key, canonical(value[n])))
                elif isinstance(entry, dict) and ('value' in entry or 'error' in entry):
                    known[(key, canonical(value[n]))] = entry
                    if method is not None and 'value' in entry:
                        self._finished_runs.add((method, canonical(value[n])))
        return out

    def verify(self, spec, pairs):
        """The property verifier on (input, output) pairs: a list of True/False/None, or None."""
        if not spec.get('verify') or not pairs:
            return None
        result = self._sandbox(VERIFY_RUN % spec['verify'], [[i, o] for i, o in pairs])
        return result if (isinstance(result, list) and len(result) == len(pairs)
                          and all(v is None or type(v) is bool for v in result)) else None

    def audit_by_verifier(self, spec, partial, examples):
        """Record a model-property-supported revision, not a truth certificate.

        A generated predicate can be wrong or underconstrained. User anchors and
        host-oracle-supported values cannot be overwritten by that predicate.
        """
        book = self._checkbooks[spec['check_scope']]
        given = {canonical(r['input']) for r in book.records if r['origin'] == 'user'
                 or (book.scope_id, r['id']) in self._oracle_supported}
        failed = [c for c in partial['failed_checks'] if canonical(c['input']) not in given and 'got' in c]
        verdicts = self.verify(spec, [(c['input'], c['got']) for c in failed] +
                               [(c['input'], c['expected']) for c in failed])
        if not verdicts:
            return 0
        fixed = 0
        for check, got_ok, expected_ok in zip(failed, verdicts[:len(failed)], verdicts[len(failed):]):
            if got_ok is True and expected_ok is False:
                old_scope = book.scope_id
                try:
                    changed = book.revise(check['input'], check['got'], evidence={
                        'kind': 'model_property_verifier', 'source_digest': digest(spec['verify']),
                        'artifact': partial['artifact'], 'method_value_accepted': True,
                        'old_expected_accepted': False})
                except (ValueError, CheckConflict):
                    changed = False
                if changed:
                    fixed += 1
                    self._register_revision(book, old_scope)
        spec['checks'], spec['check_scope'] = book.checks, book.scope_id
        return fixed

    def admit(self, request, checks, source, name, *, check_scope=None, uses=(), stored=None,
              producer='mati search over model-written holes', reason='mati: holes filled by search', step='mati',
              derivation=None):
        """Qualify source found by the system on the checks, with no model call.

        The source is stored first, unless `stored` names a method already in memory, which is
        then qualified as it is (reuse by id). `candidate` is the artifact tried, checked or not.
        A `derivation` (a compiled recipe's manifest) is kept in the method's provenance, and its
        reuse is what the qualifying executions actually called."""
        task = ProgramTask(request + '\n\nChecks run(input) must pass: ' + json.dumps(checks), checks,
                           context={'solve_check_scope': check_scope})
        run_dir = self.ws.runs / (_now().replace(':', '') + '-' + step + '-' + uuid.uuid4().hex[:6])
        run_dir.mkdir(parents=True, exist_ok=True)
        with Journal(run_dir / 'events.jsonl') as journal, ArtifactMemory(self.ws.memory) as memory:
            before = set(memory.ids('method'))
            provenance = {'producer': producer}
            if derivation is not None:
                provenance['derivation'] = derivation
            tools = ProgramTools(task, memory, journal, self._execute, max_executions=self._executions_left(),
                                 provenance=provenance, profile='c3')
            journal.append('program_task', {'request': task.request, 'scope': task.scope_id})
            if stored is None:
                candidate = tools.remember_method_c3({'name': name, 'description': name, 'source': source,
                                                     'uses': list(uses)})['artifact']
            else:
                candidate, uses = stored, memory.get(stored)['dependencies']
            ok = tools.checker(task.request, {'artifact': candidate})
            feedback = tools.checker_feedback(task.request, {'artifact': candidate}) if not ok else None
        reused = sorted(set(uses) | ({candidate} & before)) if ok else []
        if derivation is not None and ok:
            reused = sorted({aid for event in Journal.read(run_dir / 'events.jsonl')
                             if event['kind'] == 'program_execution_result'
                             for aid in event['body'].get('dependency_calls', [])})
        return {'status': 'checked' if ok else 'incomplete', 'artifact': candidate if ok else None,
                'reason': reason if ok else 'filling failed qualification', 'reused': reused, 'run': str(run_dir),
                'task': task, 'partial': None, 'validation': feedback, 'candidate': candidate, 'step': step}

    def _valli_step_refuted(self, spec, reduction):
        """Test a proposed reduction step on the checks' and the planner's small inputs (solve_valli.step_check).
        Returns up to four counterexamples, or [] when the step held, was not testable or nothing could be run."""
        source = solve_valli.step_check(reduction, spec.get('reference'))
        inputs, seen = [], set()
        for value in [c['input'] for c in spec['checks']] + list(spec.get('small_inputs') or []):
            if canonical(value) not in seen:
                seen.add(canonical(value))
                inputs.append(value)
        inputs = inputs[:MAX_PROBES]
        if source is None or not inputs or not self._executions_left():
            self._event('solve_valli_step', {'check_scope': spec['check_scope'], 'outcome': 'not_run',
                                             'reduction_digest': digest(reduction)})
            return []
        out = self._sandbox(source, inputs)
        if not (isinstance(out, list) and len(out) == len(inputs)):
            self._event('solve_valli_step', {'check_scope': spec['check_scope'], 'outcome': 'not_run',
                                             'reduction_digest': digest(reduction)})
            return []
        wrong, held = [], 0
        for value, got in zip(inputs, out):
            if not isinstance(got, dict) or 'skip' in got or 'direct' not in got:
                continue
            if 'raised' not in got and same_value(got.get('combined'), got['direct']):
                held += 1
            else:
                wrong.append(dict({'input': value}, **{k: got[k] for k in ('parts', 'answers', 'combined', 'raised',
                                                                          'direct') if k in got}))
        self._event('solve_valli_step', {'check_scope': spec['check_scope'], 'reduction_digest': digest(reduction),
                                         'outcome': 'refuted' if wrong else 'held' if held else 'not_applicable',
                                         'held': held, 'tested': held + len(wrong), 'counterexamples': wrong[:4]})
        return wrong[:4]

    def valli(self, request, spec, reduction, indent=''):
        """Vallī (solve_valli.py): the model's reduction step, assembled into a well-founded recursion.

        The recursion checks at run time that every reduced input is smaller, keeps the chain of
        answers, and computes the bottom with the planner's reference when there is one. A reduction
        that returns checks literally is rejected before it runs. The assembled method is qualified
        on the current checks with no further model call."""
        self._count('valli_tried')
        if memorized_checks(reduction, spec['checks'], _known_inputs(spec), self._derived_inputs(spec)):
            self._event('solve_valli', {'check_scope': spec['check_scope'], 'outcome': 'memorizes_checks'})
            return None
        source = solve_valli.assemble(reduction, spec.get('reference'))
        if source is None:
            self._event('solve_valli', {'check_scope': spec['check_scope'], 'outcome': 'incomplete_reduction',
                        'reduction_digest': digest(reduction)})
            return None
        outcome = self.admit(request, spec['checks'], source, 'valli reduction', check_scope=spec['check_scope'],
                             producer='valli: model reduction step, mechanical recursion',
                             reason='valli: reduced to small inputs and rebuilt by combine', step='valli')
        bottom = 'reference' if spec.get('reference') and solve_valli.defines(spec['reference'], 'reference') else 'base'
        self._event('solve_valli', {'check_scope': spec['check_scope'], 'outcome': outcome['status'],
                    'artifact': outcome['artifact'], 'bottom': bottom, 'reduction_digest': digest(reduction),
                    'validation': outcome['validation']})
        if outcome['status'] == 'checked':
            self._count('valli_checked')
            if bottom == 'reference':
                self._valli_bottom.add(outcome['artifact'])
            self.say(f'{indent}valli: the reduction, rebuilt from the {bottom} at the bottom, passes every check')
        return outcome

    def krama(self, request, spec, steps, indent=''):
        """Krama (solve_krama.py): the model's one step, taken step after step by Kuttaka, breadth first, with
        every state remembered. A step that returns checks literally is rejected before it runs. The assembled
        method is qualified on the current checks with no further model call."""
        self._count('krama_tried')
        if memorized_checks(steps, spec['checks'], _known_inputs(spec), self._derived_inputs(spec)):
            self._event('solve_krama', {'check_scope': spec['check_scope'], 'outcome': 'memorizes_checks'})
            return None
        source = solve_krama.assemble(steps)
        if source is None:
            self._event('solve_krama', {'check_scope': spec['check_scope'], 'outcome': 'incomplete_step',
                                        'steps_digest': digest(steps)})
            return None
        outcome = self.admit(request, spec['checks'], source, 'krama steps', check_scope=spec['check_scope'],
                             producer='krama: model step, mechanical walk',
                             reason='krama: the step taken breadth first from the start', step='krama')
        self._event('solve_krama', {'check_scope': spec['check_scope'], 'outcome': outcome['status'],
                                    'artifact': outcome['artifact'], 'steps_digest': digest(steps)})
        if outcome['status'] == 'checked':
            self._count('krama_checked')
            self.say(f'{indent}krama: the step, taken step after step from the start, passes every check')
        return outcome

    def _krama_feedback(self, spec, steps):
        """Why a step was not accepted: the failed checks, each with the path the walk took there."""
        source = solve_krama.trace(steps)
        if source is None:
            return ('the steps must define start(input), moves(input, state), done(input, state) and '
                    'answer(input, state, moves)')
        got = self._sandbox(source, [c['input'] for c in spec['checks']]) if self._executions_left() else None
        if not (isinstance(got, list) and len(got) == len(spec['checks'])):
            return 'the walk did not finish on the checks (too many states, or too slow): take a smaller or surer step'
        failed = [dict({'input': c['input'], 'expected': c['expected']}, **g) for c, g in zip(spec['checks'], got)
                  if not (isinstance(g, dict) and 'got' in g and same_value(g['got'], c['expected']))]
        self._event('solve_krama_feedback', {'check_scope': spec['check_scope'], 'failed': len(failed)})
        return ('Kuttaka took your step from the start, breadth first; on these checks the result is wrong. Each '
                'shows what it returned and the path of states it took (or how many states it saw when no state was '
                'done). Find the first wrong state on the path and fix the step: %s'
                % json.dumps(failed, default=str)[:3000])

    def _recalled_methods(self, request, form, limit, keep=None):
        """(artifact, name, input shape, output shape) for stored methods in recall order: those recalled for the
        task (by wording, by input form), then those that take a part of its input, then the rest of the library,
        newest first; refuted ones left out, and, with `keep`, those whose shapes it rejects; at most `limit`.
        Each one's shapes are read from the checks it was stored with."""
        refuted = self.ws.refuted()
        recalled = [m['artifact'] for m in self.ws.helpful_methods(request, form=form)]
        recalled += self.ws.methods_taking(pramana.part_forms(form), limit=limit)
        recalled += [r['artifact'] for r in reversed(self.ws._read(self.ws.solved_path))]
        methods = []
        with ArtifactMemory(self.ws.memory) as memory:
            for artifact in dict.fromkeys(recalled):
                if artifact in refuted or len(methods) >= limit:
                    continue
                if self._refuted_in(artifact, request, memory):
                    continue
                try:
                    payload = memory.get(artifact)
                except KeyError:
                    continue
                examples = (payload.get('applicability') or {}).get('examples') or []
                if not examples:
                    continue
                entry = (artifact, payload['body'].get('name') or artifact[:8],
                         json.loads(pramana.form(examples)), json.loads(pramana.output_form(examples)))
                if keep is None or keep(entry):
                    methods.append(entry)
        return methods

    def roles(self, request, spec, indent=''):
        """Recall by role (solve_prastara.roles): a stored method that solves this task once the task's fields are
        put in its fields, however the two tasks were worded and whatever their fields are called.

        Word recall finds a method only when the new task is worded like the old one, and form recall only when
        its fields have the same names. The methods here are any in the library whose input has fields that
        this task's input can fill (ROLE_METHODS of them, in recall order). Every assignment is screened on the
        first check in one sandbox run, and those that hold there on every check; the first that gives every
        expected value is compiled as a recipe and qualified the ordinary way (`compose`), with no model call."""
        form, out_form = pramana.form(spec['checks']), pramana.output_form(spec['checks'])
        if form is None or out_form is None:
            return None
        task_in, task_out = json.loads(form), json.loads(out_form)
        fits = lambda entry: bool(solve_prastara.roles([entry], task_in, task_out, limit=1)[1])
        methods = self._recalled_methods(request, form, ROLE_METHODS, keep=fits)
        if not methods:
            return None
        complete, listed = solve_prastara.roles(methods, task_in, task_out)
        if not listed:
            return None
        from .demo import load_method
        libraries = {}
        for artifact in dict.fromkeys(m for m, _, _, _ in listed):
            source, inherited = load_method(self.ws.memory, artifact)
            libraries.update(inherited)
            libraries[artifact] = source
        checks = spec['checks']
        summary = {'check_scope': spec['check_scope'], 'methods': [m[0] for m in methods], 'listed': len(listed),
                   'complete': complete}

        def screen(candidates, inputs):
            """For each candidate, its results on `inputs`, or None for every one when the run was lost."""
            out = []
            per_run = max(1, ROLE_SCREEN_VALUES // len(inputs))
            for start in range(0, len(candidates), per_run):
                chunk = candidates[start:start + per_run]
                if self._executions_left() <= 3:   # qualification, deploy and the answer gate come after
                    return out + [None] * (len(candidates) - len(out))
                source = solve_prastara.ROLE_SCREEN % ([[m, a] for m, a, _, _ in chunk],)
                got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'screen'),
                                        [[n, v] for n in range(len(chunk)) for v in inputs], libraries)
                self._count('roles_screened')
                if not (isinstance(got, list) and len(got) == len(chunk) * len(inputs)):
                    out += [None] * len(chunk)
                    continue
                out += [got[n * len(inputs):(n + 1) * len(inputs)] for n in range(len(chunk))]
            return out

        def holds(rows, wanted):
            return rows is not None and all(isinstance(g, dict) and 'value' in g and same_value(g['value'], c['expected'])
                                            for g, c in zip(rows, wanted))
        first = screen(listed, [checks[0]['input']])
        survivors = [c for c, rows in zip(listed, first) if holds(rows, checks[:1])]
        whole = screen(survivors, [c['input'] for c in checks]) if len(checks) > 1 else \
            [rows for rows in first if holds(rows, checks[:1])]
        for (artifact, assignment, formula, recipe), rows in zip(survivors, whole):
            if not holds(rows, checks):
                continue
            outcome = self.compose(request, spec, recipe)
            if outcome and outcome['status'] == 'checked':
                self._count('roles_found')
                outcome['step'] = 'roles'
                outcome['composition']['roles'] = {'method': artifact, 'assignment': assignment, 'formula': formula,
                                                   'complete': complete}
                self._event('solve_roles', dict(summary, survivors=len(survivors), found={
                    'method': artifact, 'assignment': assignment, 'formula': formula,
                    'artifact': outcome['artifact']}))
                self.say(f'{indent}recalled by role: {formula} passes every check, no model call')
                return outcome
        self._event('solve_roles', dict(summary, survivors=len(survivors), found=None))
        return None

    def prastara(self, request, spec, indent=''):
        """Prastāra with bhāvanā (solve_prastara.py): every well-formed small composition of stored methods.

        The methods are those recalled for the task (by wording, by input form), then those that take a
        part of its input, then the rest of the library, newest first, up to PRASTARA_METHODS; each one's
        input and output forms are read from the checks it was stored with. Each recipe, in order, is
        compiled and screened on every check input in one sandbox run; the first whose outputs match
        every check is stored and qualified the ordinary way (`compose`), with no model call, and its
        place and formula are recorded, with whether the list was complete. None when nothing fits."""
        form = pramana.form(spec['checks'])
        out_form = pramana.output_form(spec['checks'])
        if form is None or out_form is None:
            return None
        outcome = self.roles(request, spec, indent)
        if outcome is not None:
            return outcome
        methods = self._recalled_methods(request, form, PRASTARA_METHODS)
        if not methods:
            return None
        complete, listed = solve_prastara.recipes(methods, json.loads(form), json.loads(out_form), PRASTARA_LIMIT,
                                                  tried=self._adopt_tried.get(spec['check_scope'], set()))
        from .demo import load_method
        inputs = [c['input'] for c in spec['checks']]
        screened, loaded = 0, {}
        summary = {'check_scope': spec['check_scope'], 'methods': [m[0] for m in methods],
                   'listed': len(listed), 'complete': complete}
        for place, formula, recipe in listed:
            if self._executions_left() < len(inputs) + 1:
                break
            try:
                with ArtifactMemory(self.ws.memory) as memory:
                    compiled = compile_recipe(recipe, resolve_method=memory.get)
            except (ValueError, KeyError, PermissionError):
                continue
            libraries = {}
            for dependency in compiled.uses:
                if dependency not in loaded:
                    loaded[dependency] = load_method(self.ws.memory, dependency)
                source, inherited = loaded[dependency]
                libraries.update(inherited)
                libraries[dependency] = source
            got = self._sandbox_job(DIFFERENTIAL_RUN % (compiled.source, 'run'), inputs, libraries)
            screened += 1
            self._count('prastara_screened')
            if not (isinstance(got, list) and len(got) == len(inputs)
                    and all(isinstance(g, dict) and 'value' in g and same_value(g['value'], c['expected'])
                            for g, c in zip(got, spec['checks']))):
                continue
            outcome = self.compose(request, spec, recipe)
            if outcome and outcome['status'] == 'checked':
                self._count('prastara_found')
                outcome['step'] = 'prastara'
                outcome['composition']['prastara'] = {'place': place, 'formula': formula, 'complete': complete}
                self._event('solve_prastara', dict(summary, screened=screened,
                            found={'place': place, 'formula': formula, 'artifact': outcome['artifact']}))
                self.say(f'{indent}prastara: {formula} (place {place}) passes every check, no model call')
                return outcome
        self._event('solve_prastara', dict(summary, screened=screened, found=None))
        return None

    def compose(self, request, spec, recipe, bindings=None):
        """Compile exact wiring, then qualify the whole method; never inherit truth from its parts."""
        table = memorized_in_recipe(recipe, spec['checks'])
        if table:
            self._event('solve_composition_rejected', {'reason': 'the recipe answers checks from its own literals',
                        'inputs': [c['input'] for c in table]})
            return None
        try:
            with ArtifactMemory(self.ws.memory) as memory:
                compiled = compile_recipe(recipe, bindings=bindings, resolve_method=memory.get)
        except (ValueError, KeyError, PermissionError) as error:
            self._event('solve_composition_rejected', {'reason': str(error)})
            return None
        self._event('solve_composition_recipe', {'identity': compiled.identity,
                    'check_scope': spec['check_scope'], 'manifest': compiled.manifest,
                    'dependencies': list(compiled.uses)})
        outcome = self.admit(request, spec['checks'], compiled.source, 'mechanical composition',
                             check_scope=spec['check_scope'], uses=compiled.uses,
                             producer='mechanical composition', reason='recipe: passed current finite checks',
                             step='recipe', derivation=dict(compiled.manifest, identity=compiled.identity))
        outcome['composition'] = {'identity': compiled.identity, 'manifest': compiled.manifest,
                                  'dependencies': list(compiled.uses), 'model_glue_calls': 0,
                                  'support': 'current_finite_checks_only'}
        self._count('recipe_tried')
        if outcome['status'] == 'checked':
            self._count('recipe_assembled')
        self._event('solve_composition_result', {'identity': compiled.identity,
                    'check_scope': spec['check_scope'], 'status': outcome['status'],
                    'artifact': outcome['artifact'], 'validation': outcome['validation'],
                    'executed_dependencies': outcome['reused'], 'run': outcome['run']})
        return outcome

    def _assemble(self, prompt, spec, request, decomposition, depth, indent):
        """Build explicitly named pieces and interpret their finite assembly recipe."""
        try:
            decomposition = _decomposition(decomposition)
            # Reject unavailable direct references before spending calls on unrelated pieces.
            refuted = self.ws.refuted()
            with ArtifactMemory(self.ws.memory) as memory:
                for ref in inspect_recipe(decomposition['recipe'])['methods']:
                    if re.fullmatch(r'[0-9a-f]{64}', ref) and memory.get(ref)['kind'] != 'method':
                        raise ValueError('recipe reference is not a method')
                    if ref in refuted:
                        raise ValueError('recipe references a method shown wrong')
        except (ValueError, KeyError, PermissionError) as error:
            self._event('solve_decomposition_rejected', {'reason': str(error)})
            return [], None
        subtasks = decomposition['subtasks']
        self._event('solve_decomposition_started', {'check_scope': spec['check_scope'],
                    'subtasks': subtasks, 'recipe_json': json.dumps(decomposition['recipe'], ensure_ascii=True),
                    'remaining_subtasks': len(subtasks)})
        parts, bindings = [], {}
        for index, sub in enumerate(subtasks):
            if not self._calls_left() or not self._executions_left():
                break
            self.say(f'{indent}- subtask (recipe): {sub["request"][:120]}')
            part = self._solve_piece(sub, spec, depth, len(subtasks) - index)
            parts.append(part)
            if part.get('status') == 'checked' and part.get('artifact'):
                bindings[sub['id']] = part['artifact']
            self._event('solve_decomposition_piece', {'id': sub['id'], 'status': part['status'],
                        'artifact': part.get('artifact'), 'remaining_subtasks': len(subtasks) - index - 1})
        if len(bindings) != len(subtasks):
            self._event('solve_decomposition_boundary', {'reason': 'some pieces remain unchecked',
                        'checked_pieces': sorted(bindings), 'subtask_count': len(subtasks)})
            return parts, None
        outcome = self.compose(request, spec, decomposition['recipe'], bindings)
        if outcome and outcome['status'] == 'checked':
            outcome['assembly'] = {'subtasks': subtasks,
                                   'recipe_json': json.dumps(decomposition['recipe'], ensure_ascii=True)}
        return parts, outcome

    def mati(self, prompt, spec, best, request):
        """Enumerate literal holes; qualify each candidate in its own bounded sandbox.

        The catalogue cursor and unfinished validations are retained. A page,
        execution cap or timeout is never reported as exhausting all fillings.
        The earlier RD-1 typed grammar is distinct from this constant-choice port.
        """
        reply = self._ask(self.sketcher, {'task': prompt, 'contract': spec['contract'], 'source': best['source'],
                                          'failed_checks': best['failed_checks'][:6],
                                          'dependencies': best.get('uses', [])})
        holes, template = reply.get('holes'), reply.get('source')
        try:
            catalogue = SketchCatalogue(template, holes)
        except ValueError as error:
            self._event('solve_mati_rejected', {'reason': str(error), 'proposal': reply})
            return None
        binding = (spec['check_scope'], catalogue.identity, tuple(best.get('uses', [])))
        state = self._sketch_states.setdefault(binding, {'cursor': 0, 'incomplete': []})
        self._event('solve_mati_catalogue', {'catalogue': catalogue.identity, 'scope': spec['check_scope'],
                    'source': template, 'holes': holes, 'total': catalogue.total,
                    'start': state['cursor'], 'uses': best.get('uses', [])})
        # Best, not first (the cakravala's choice: the constraint admits, the objective ranks).
        # Admissible fillings pass every check; with a reference, a few more are tried after the
        # first and ranked by agreement with it on the small inputs. Without one, or when none
        # passes every check, earlier fillings rank first (the model lists likely values first).
        outcome, passing, near, extra = None, [], None, None
        known = {canonical(c['input']) for c in spec['checks']}
        rankable = bool(spec.get('reference')) and any(canonical(v) not in known
                                                       for v in spec.get('small_inputs') or [])
        try:
            for candidate in catalogue.candidates(start=state['cursor'], limit=min(256, self._executions_left())):
                if not self._executions_left() or (extra is not None and extra <= 0):
                    break
                if extra is not None:
                    extra -= 1
                self._event('solve_mati_candidate', {'catalogue': catalogue.identity,
                            'scope': spec['check_scope'], **candidate})
                if candidate.get('status', 'ready') != 'ready':
                    state['cursor'] = candidate['index'] + 1
                    if candidate['status'] != 'invalid_syntax':
                        state['incomplete'].append(candidate['index'])
                    self._event('solve_mati_validation', {'catalogue': catalogue.identity,
                                'index': candidate['index'], 'outcome': candidate['status'],
                                'reason': candidate['reason'], 'next_cursor': state['cursor']})
                    continue
                outcome = self.admit(request, spec['checks'], candidate['source'], 'mati filling',
                                     check_scope=spec['check_scope'], uses=best.get('uses', []))
                state['cursor'] = candidate['index'] + 1
                unknown = (outcome['status'] != 'checked'
                           and (outcome.get('validation') or {}).get('status') != 'failed')
                if unknown:
                    state['incomplete'].append(candidate['index'])
                self._event('solve_mati_validation', {'catalogue': catalogue.identity,
                            'index': candidate['index'], 'outcome': outcome['status'],
                            'artifact': outcome['artifact'], 'validation': outcome.get('validation'),
                            'run': outcome['run'], 'next_cursor': state['cursor']})
                if outcome['status'] == 'checked':
                    passing.append((candidate, outcome))
                    if not rankable or len(passing) >= MATI_COMPARE:
                        break
                    if extra is None:
                        extra = MATI_EXTRA
                    continue
                if unknown:
                    break  # Preserve unknown validation instead of disguising it as refutation.
                near = self._nearer(near, outcome, spec, best)
        except ValueError as error:
            self._event('solve_mati_rejected', {'catalogue': catalogue.identity, 'reason': str(error)})
            state['incomplete'].append(state['cursor'])
        self._event('solve_mati_boundary', {'catalogue': catalogue.identity, 'scope': spec['check_scope'],
                    'next_cursor': state['cursor'], 'total': catalogue.total,
                    'incomplete_candidates': state['incomplete'],
                    'all_candidates_checked': state['cursor'] == catalogue.total and not state['incomplete']})
        self._count('mati')
        if passing:
            self._count('mati_checked')
            chosen, outcome = self._rank_by_reference(spec, passing)
            self.say(f'    mati: holes filled with {chosen["values"]}'
                     + (f' (best of {len(passing)} fillings that pass every check)' if len(passing) > 1 else ''))
            return outcome
        if near is not None:
            self._count('mati_partial')
            self.say(f'    mati: best filling passes {len(near["partial"]["passed_checks"])}/{len(spec["checks"])} checks')
            return near
        return outcome

    def _nearer(self, near, outcome, spec, best):
        """The filling that passes the most checks so far, as a partial, if it beats `best`."""
        validation, artifact = outcome.get('validation') or {}, outcome.get('candidate')
        if validation.get('status') != 'failed' or not artifact:
            return near
        failed_at = set(validation.get('counterexample_checks') or [])
        if validation.get('infrastructure_checks') or validation.get('checks_completed') != len(spec['checks']):
            return near  # checks that did not run are not evidence either way
        details = {d['check_index']: d for d in validation.get('failed_checks') or []}
        passed, failed = [], []
        for index, check in enumerate(spec['checks']):
            if index not in failed_at:
                passed.append(check)
                continue
            detail = details.get(index, {})
            row = dict(check, execution_status='completed' if 'actual' in detail else 'candidate_error')
            if 'actual' in detail:
                row['got'] = detail['actual']
            failed.append(row)
        floor = len(best['passed_checks']) if best and 'passed_checks' in best else -1
        if near is not None:
            floor = max(floor, len(near['partial']['passed_checks']))
        if len(passed) <= floor:
            return near
        with ArtifactMemory(self.ws.memory) as memory:
            payload = memory.get(artifact)
        partial = {'artifact': artifact, 'source': payload['body']['source'], 'passed_checks': passed,
                   'failed_checks': failed, 'check_scope': spec['check_scope'], 'uses': payload['dependencies']}
        self._partials[(spec['check_scope'], artifact)] = partial
        return dict(outcome, reason='mati: best filling is a partial', partial=partial)

    def _rank_by_reference(self, spec, passing):
        """Of fillings that pass every check, the one agreeing most with the reference (earliest on ties)."""
        if len(passing) == 1:
            return passing[0]
        known = {canonical(c['input']) for c in spec['checks']}
        inputs = [v for v in spec.get('small_inputs') or [] if canonical(v) not in known]
        if not inputs or not self._executions_left():
            return passing[0]
        want = self._sandbox(DIFFERENTIAL_RUN % (spec['reference'], 'reference'), inputs)
        if not (isinstance(want, list) and len(want) == len(inputs)):
            return passing[0]
        from .demo import load_method
        scores = []   # None: the filling's batch did not run, which is no evidence either way
        for candidate, outcome in passing:
            if not self._executions_left():
                break
            source, libraries = load_method(self.ws.memory, outcome['artifact'])
            got = self._sandbox_job(DIFFERENTIAL_RUN % (source, 'run'), inputs, libraries)
            if not (isinstance(got, list) and len(got) == len(inputs)):
                scores.append(None)
                continue
            scores.append(sum(isinstance(w, dict) and 'value' in w and isinstance(g, dict) and 'value' in g
                              and same_value(g['value'], w['value']) for g, w in zip(got, want)))
        scored = [i for i, score in enumerate(scores) if score is not None]
        if not scored:
            return passing[0]
        ranked = max(scored, key=lambda i: (scores[i], -i))
        receipt = self._event('solve_mati_ranked', {'scope': spec['check_scope'], 'inputs': len(inputs),
                              'agreement': scores, 'chosen': passing[ranked][0]['index'],
                              'artifacts': [o['artifact'] for _, o in passing]})
        self._count('mati_ranked')
        if ranked:
            self._count('mati_not_first')
        losers = [passing[i][1] for i in scored if scores[i] < scores[ranked]]
        if receipt and losers:
            # A filling the reference contradicts more often must not be offered to later tasks.
            with ArtifactMemory(self.ws.memory) as memory:
                for outcome in losers:
                    memory.invalidate(outcome['artifact'], 'mati: the reference disagrees more often than '
                                      'with the chosen filling', [receipt], scope=outcome['task'].scope_id)
        return passing[ranked]

    def _record_split_outcome(self, spec, subtasks, parts, outcome, *, recipe=None, rejections=()):
        """Keep failed decomposition evidence for the next split, without carrying truth claims.

        Proposal identity covers the full request and checks. A child's actual
        check scope is recorded only when its solve result supplies one; the
        proposed interface is not substituted for its planned qualification scope.
        """
        def text(value, limit=240):
            return value[:limit] if isinstance(value, str) else ''

        def obstruction(result):
            reason = text(result.get('reason'), 1000)
            if result.get('budget_boundary') or reason in (
                    'model budget exhausted', 'tool budget exhausted', 'shared execution limit reached',
                    'insufficient remaining budget for this piece and its siblings') or reason.startswith(
                        'shared execution limit reached before'):
                return 'budget'
            if reason in ('recursive subproblem repeats an active goal and checks',
                          'recursive subproblem only changes surrounding whitespace of an active goal and checks'):
                return 'cycle'
            if reason in ('repeated invalid JSON replies', 'replies cut at the token limit'):
                return 'protocol'
            if reason in ('model transport failure', 'transport outage threshold reached'):
                return 'transport'
            return 'unsolved'

        children = []
        for index, sub in enumerate(subtasks[:4]):
            part = parts[index] if index < len(parts) else None
            attempted = (part is not None and part.get('reason') !=
                         'insufficient remaining budget for this piece and its siblings')
            record = {'proposal_identity': digest({'request': sub['request'], 'checks': sub['checks']}),
                      'request': text(sub['request']), 'attempted': attempted,
                      'check_scope': None, 'qualification': 'not_checked'}
            if part is None:
                record.update(status='not_attempted', reason='no child solve result in this breakdown',
                              obstruction='budget' if not self._calls_left() or not self._executions_left()
                              else 'not_attempted')
            else:
                evidence = part.get('check_evidence') or {}
                scope = evidence.get('scope') if isinstance(evidence, dict) else None
                if isinstance(scope, str) and re.fullmatch(r'[0-9a-f]{64}', scope):
                    record['check_scope'] = scope
                artifact = part.get('artifact')
                checked = (part.get('status') == 'checked' and isinstance(artifact, str)
                           and re.fullmatch(r'[0-9a-f]{64}', artifact))
                record.update(status=text(part.get('status'), 48), reason=text(part.get('reason')),
                              obstruction=None if checked else obstruction(part))
                if checked:
                    record.update(qualification='child_finite_checks_only', checked_artifact=artifact)
            children.append(record)
        result = outcome or {}
        record = {'parent_check_scope': spec['check_scope'], 'evidence_role': 'scheduling_only',
                  'origin': 'child_results' if parts else 'proposal_validation' if rejections else 'split_result',
                  'children': children, 'parent_status': text(result.get('status', 'unresolved'), 48),
                  'reason': text(result.get('reason', 'no checked assembly produced')),
                  'recipe_identity': digest(recipe) if recipe is not None else None,
                  'proposal_rejections': [text(reason) for reason in list(rejections)[-2:]]}
        history = self._split_outcomes.setdefault(spec['check_scope'], [])
        history.append(record)
        del history[:-3]
        while len(self._split_outcomes) > 32:
            self._split_outcomes.pop(next(iter(self._split_outcomes)))
        self._event('solve_split_outcome', record)

    def split(self, prompt, spec, lessons, outcome, partial, request, offered, depth, indent, *, retained_parts=()):
        past = self.ws.similar_splits(prompt)
        context = {'task': prompt, 'contract': spec['contract'], 'failure': outcome['reason'],
                   'checks': spec['checks'], 'input': spec.get('input'),
                   'checks_by': spec.get('checks_by'),
                   'available_methods': list(offered),
                   'lessons': lessons, 'similar_breakdowns': [{'task': p['task'], 'subtasks': p['subtasks']}
                                                               for p in past]}
        for item, prior in zip(context['similar_breakdowns'], past):
            if prior.get('recipe_json'):
                item['recipe_json'] = prior['recipe_json']
        if partial is not None:
            context['partial_attempt'] = partial
        frontier = self._frontiers.get(spec['check_scope'])
        if frontier:
            context['partial_frontier'] = frontier.frontier
        context['progress_rule'] = ('Use a different subproblem or method. Preserve useful partials; '
                                    'do not repeat this goal and checks. Model checks remain provisional.')
        previous = self._split_outcomes.get(spec['check_scope'])
        if previous:
            context['previous_breakdown_outcomes'] = json.loads(canonical(previous))
            context['progress_rule'] += (
                ' Previous breakdown outcomes are scheduling diagnostics, not new checks or proof. '
                'Use failed child results to choose a genuinely smaller missing operation or correct the wiring; '
                'do not simply repeat a cycle-blocked goal. Preserve available checked pieces. '
                'Budget and transport obstructions do not show a child is impossible: it may be retried '
                'when enough resources are available.')
        subtasks, decomposition, reduction, fallback = [], None, None, []
        rejections = []
        walked, walked_last = None, None   # krama: the model's step, taken by Kuttaka (checked / last tried)

        def finish(parts, split_by, assembled):
            self._record_split_outcome(spec, subtasks, parts, assembled,
                                       recipe=decomposition['recipe'] if decomposition else None,
                                       rejections=rejections)
            return parts, split_by, assembled

        for _ in range(2):
            if not self._calls_left():
                break
            reply = self._ask(self.splitter, context)
            if isinstance(reply.get('steps'), str) and reply['steps'].strip():
                walked_last = self.krama(request, spec, reply['steps'], indent)
                if walked_last and walked_last['status'] == 'checked':
                    walked = walked_last
                    break
                # A wrong step, perceived: the path Kuttaka took on each failed check is shown, and the step is
                # asked for again (the states are remembered outside the thinker, so it need not replay them).
                reason = self._krama_feedback(spec, reply['steps'])
                rejections.append(reason)
                context = dict(context, previous_reply_rejected=reason)
                continue
            if isinstance(reply.get('reduction'), str) and reply['reduction'].strip():
                wrong = self._valli_step_refuted(spec, reply['reduction'])
                if wrong:
                    # The step itself does not hold where the reference can judge both sides: a wrong first step,
                    # shown with its counterexample, and asked for again (the checks alone would not show it).
                    reason = ('the reduction step does not hold: on these inputs, combine(input, the answers for '
                              'reduce(input)) differs from the answer for input itself (every answer here from the '
                              'reference): %s. Fix reduce or combine so that the step holds on every input it '
                              'applies to.' % json.dumps(wrong, default=str)[:1500])
                    rejections.append(reason)
                    context = dict(context, previous_reply_rejected=reason)
                    continue
                reduction = reply['reduction']
                break
            candidates = []
            try:
                raw = reply.get('subtasks', [])
                if type(raw) is not list or len(raw) > 4:
                    raise ValueError('subtasks must be a list of at most four objects')
                for sub in raw:
                    if (type(sub) is not dict or not isinstance(sub.get('request'), str)
                            or not sub['request'].strip() or not _checks(sub.get('checks'))):
                        raise ValueError('each subtask needs a nonempty request and input/expected checks')
                    book = CheckBook(sub['request'], sub['request'], sub['checks'], [], supplied_origin='model')
                    candidates.append(dict(sub, checks=book.checks))
                if reply.get('recipe') is not None:
                    # Keep a valid set of pieces as a fallback, but give the model its bounded chance
                    # to correct the wiring before spending calls solving those pieces.
                    fallback = candidates
                    decomposition = _decomposition({'subtasks': raw, 'recipe': reply['recipe']})
                elif not candidates:
                    raise ValueError('return nonempty subtasks, an assembly recipe, or a reduction')
                subtasks = candidates
                break
            except ValueError as error:
                self._event('solve_decomposition_rejected', {'reason': str(error)})
                rejections.append(str(error))
                context = dict(context, previous_reply_rejected=str(error))
                if fallback:
                    context['previous_subtasks'] = fallback
                    context['previous_recipe'] = reply.get('recipe')
        if decomposition is None and not subtasks:
            subtasks = fallback
        if reduction is not None:
            return finish([], 'self-similar reduction (valli)', self.valli(request, spec, reduction, indent))
        if walked is not None or (walked_last is not None and decomposition is None and not subtasks):
            return finish([], 'one step, taken step after step by Kuttaka (krama)', walked or walked_last)
        split_by = 'model'
        if decomposition is None and not subtasks and past and past[0]['similarity'] >= 0.5:
            subtasks, split_by = past[0]['subtasks'], 'system (a breakdown that worked on a similar task)'
            if past[0].get('recipe_json'):
                try:
                    decomposition = _decomposition({'subtasks': subtasks,
                                                     'recipe': json.loads(past[0]['recipe_json'])})
                except ValueError as error:
                    self._event('solve_decomposition_rejected', {'reason': str(error)})
                    rejections.append(str(error))
        parts = []
        assembled = None
        if decomposition is not None:
            # The splitter's own recipe first: assembled by the compiler, no glue written by the model.
            parts, assembled = self._assemble(prompt, spec, request, decomposition, depth, indent)
            if assembled and assembled['status'] == 'checked':
                return finish(parts, split_by + ' recipe, mechanical assembly', assembled)
        if not parts:
            # No recipe, or a recipe rejected before any piece was solved: the pieces still stand.
            remaining = subtasks or (decomposition or {}).get('subtasks', [])
            for index, sub in enumerate(remaining):
                self.say(f'{indent}- subtask ({split_by}): {sub["request"][:120]}')
                parts.append(self._solve_piece(sub, spec, depth, len(remaining) - index))
        solved = [p for p in parts if p.get('status') == 'checked' and p.get('artifact')]
        notes = [f'{p["artifact"]}: {p["task"]}' for p in solved]
        if not notes:
            return finish(parts, split_by, assembled)
        # Then back up the chain by a fixed rule; the model writes the glue only if none fits.
        current_ids = list(dict.fromkeys(p['artifact'] for p in solved))
        earlier_ids = list(dict.fromkeys(p['artifact'] for p in retained_parts
                           if p.get('status') == 'checked' and p.get('artifact')
                           and p['artifact'] not in current_ids
                           and not self._refuted_in(p['artifact'], prompt)))
        # Complementary pieces from separate breakdowns may already finish the parent without
        # asking the same weak actor to invent glue. Each combination faces the parent's checks.
        outcome = self.assemble(request, spec, earlier_ids + current_ids, indent) if earlier_ids else None
        if outcome is not None:
            # Do not persist the latest breakdown alone as if it supplied every required piece.
            return finish(parts, split_by + ' with retained pieces, mechanical assembly', outcome)
        outcome = self.assemble(request, spec, current_ids, indent)
        if outcome is None:
            if not self._calls_left():
                return finish(parts, split_by, assembled)
            outcome = self.attempt(request, spec['checks'], notes + offered, check_scope=spec['check_scope'])
        if outcome['status'] == 'checked' and split_by == 'model':
            self.ws.remember_split(prompt, [{'request': s['request'], 'checks': s['checks']} for s in subtasks])
        return finish(parts, split_by, outcome)

    def assemble(self, request, spec, parts, indent=''):
        """Compose solved parts by a fixed rule (solve_compose), with no model call.

        The first composition that passes every check of the parent is the result, reusing the
        parts by id. None when no composition fits; the failed candidates stay unqualified."""
        tried = []
        self._count('assembly_tried')
        for label, source, uses in compositions(parts):
            if not self._executions_left():
                break
            outcome = self.admit(request, spec['checks'], source, 'assembled: ' + label,
                                 check_scope=spec['check_scope'], uses=uses,
                                 producer='fixed composition of solved parts', step='assembly',
                                 reason='assembled from solved parts by a fixed rule (' + label + ')')
            tried.append({'rule': label, 'uses': uses, 'status': outcome['status'],
                          'candidate': outcome['candidate']})
            if outcome['status'] == 'checked':
                self._event('solve_assembly', {'check_scope': spec['check_scope'], 'tried': tried,
                            'artifact': outcome['artifact']})
                self.say(f'{indent}assembled from the parts by a fixed rule ({label}), no model call')
                self._count('assembled')
                return outcome
        self._event('solve_assembly', {'check_scope': spec['check_scope'], 'tried': tried, 'artifact': None})
        return None

    def adopt(self, request, spec, indent=''):
        """A stored method that already passes these checks is used as it is, by id, with no model call.

        Candidates are the checked methods of the most similar earlier tasks, tried in that order
        on the current checks in the sandbox. None when none passes them all."""
        tried = []
        for method in self.ws.helpful_methods(request, form=pramana.form(spec['checks'])):
            if not self._executions_left():
                break
            self._adopt_tried.setdefault(spec['check_scope'], set()).add(method['artifact'])
            if self._refuted_in(method['artifact'], request):
                continue   # it runs a method shown wrong: the delivery gate would refuse it
            outcome = self.admit(request, spec['checks'], None, None, check_scope=spec['check_scope'],
                                 stored=method['artifact'], producer='reuse of a stored method by id', step='reuse',
                                 reason='a stored method passes these checks as it is')
            tried.append({'artifact': method['artifact'], 'similarity': method['similarity'],
                          'status': outcome['status']})
            if outcome['status'] == 'checked':
                self._event('solve_adoption', {'check_scope': spec['check_scope'], 'tried': tried,
                            'artifact': outcome['artifact']})
                self.say(f'{indent}reused stored method {outcome["artifact"][:12]} as it is, no model call')
                self._count('reused_as_is')
                return outcome
        if tried:
            self._event('solve_adoption', {'check_scope': spec['check_scope'], 'tried': tried, 'artifact': None})
        return None

    def _next_step(self, best, tried, spec, examples, depth):
        """Cakravala-style: the next strategy is chosen from the evidence, each at most once per stall.

        Placed as in the kuṭṭaka. A failed check that rests on the model's hand value alone is
        questioned before code is rewritten to fit it (pramāṇa first). The guess (mati) belongs at
        the bottom, where little is unknown. A nearly right method gets a repair; a broad failure, or
        a repair that made no progress on this exact candidate and scope, reduces before another
        whole-task attempt or hole search. An unavailable or unsuccessful split leaves the remaining
        bounded strategies usable."""
        book = self._checkbooks[spec['check_scope']]
        given = {canonical(r['input']) for r in book.records if r['origin'] == 'user'
                 or (book.scope_id, r['id']) in self._oracle_supported}
        suspect = best and any(canonical(c['input']) not in given for c in best['failed_checks'])
        records = {canonical(r['input']): r for r in book.records}
        guessed = best and any(canonical(c['input']) in records
                               and self._support_label(book, records[canonical(c['input'])]) == pramana.HAND
                               for c in best['failed_checks'])
        little_unknown = best is not None and len(best['failed_checks']) <= max(1, len(spec['checks']) // 3)
        stalled_repair = best is not None and (
            spec['check_scope'], _execution_fingerprint(best['source'], best.get('uses', []))) in self._stalled_repairs
        reduce, guess = ('split', depth < MAX_DEPTH), ('mati', best is not None)
        # A nearly right candidate's hand-only failures are still questioned before repair or reduction.
        ladder = [('audit', bool(guessed and little_unknown))]
        if not little_unknown or stalled_repair:
            ladder.append(reduce)
        ladder += [('repair', best is not None),
                  ('verify', bool(suspect and spec.get('verify'))),
                  ('audit', bool(suspect))]
        ladder += [guess, reduce] if little_unknown else [reduce, guess]
        ladder.append(('replan', True))
        return next((step for step, ok in ladder if ok and step not in tried), None)

    def _calls_left(self):
        return self.actor.counter['used'] < self.actor.counter['limit']


def stats(workspace, out=sys.stdout):
    rows = workspace.history()
    if not rows:
        print('No tasks yet.', file=out)
        return
    print(f'{"#":>3}  {"status":10} {"calls":>5} {"tokens":>8} {"reused":>6}  task', file=out)
    for i, r in enumerate(rows, 1):
        tokens = r['input_tokens'] + r['output_tokens']
        print(f'{i:>3}  {r["status"]:10} {r["model_calls"]:>5} {tokens:>8} {len(r["reused"]):>6}  '
              f'{r["task"][:60]}', file=out)
    half = len(rows) // 2
    for label, part in (('first half', rows[:half]), ('second half', rows[half:])):
        if part:
            solved = sum(r['status'] == 'checked' for r in part)
            calls = sum(r['model_calls'] for r in part) / len(part)
            print(f'{label}: {solved}/{len(part)} solved, {calls:.1f} model calls per task', file=out)
    print('Only checked results count as solved; each was checked against the checks shown with it.', file=out)


def build_models(args, counter):
    common = dict(protocol=args.protocol, key_env=args.key_env, timeout=args.model_timeout,
                  max_tokens=args.max_tokens, parse_mode='extract',
                  network_secret=bool(args.key_env) and args.network_secret)
    if args.protocol == 'openai':
        # Hosted endpoints: streamed replies with provider sampling defaults, as in C1; the
        # unstreamed request can time out upstream (HTTP 502) on long reasoning replies.
        common.update(stream=True, sampling_fields=False, reasoning_hint='low')
    planner = JsonModel(args.model, args.base_url, instructions=PLAN_INSTRUCTIONS, **common)
    splitter = JsonModel(args.model, args.base_url, instructions=SPLIT_INSTRUCTIONS, **common)
    auditor = JsonModel(args.model, args.base_url, instructions=AUDIT_INSTRUCTIONS, **common)
    sketcher = JsonModel(args.model, args.base_url, instructions=MATI_INSTRUCTIONS, **common)
    actor = JsonModel(args.model, args.base_url, instructions=SOLVE_ACTOR_INSTRUCTIONS, **common)
    return (Metered(planner, counter), Metered(actor, counter), Metered(splitter, counter),
            Metered(auditor, counter), Metered(sketcher, counter))


def _confirm(spec):
    if not sys.stdin.isatty():
        return True
    answer = input('Use these checks? [Y/n] ').strip().lower()
    return answer in ('', 'y', 'yes')


def main(argv=None):
    p = argparse.ArgumentParser(prog='kuttaka', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('task', help='the task in plain words, or "stats", or "library" (the method library as a whole)')
    p.add_argument('--example', action='append', default=[], type=json.loads,
                   help='a check you trust: {"input": ..., "expected": ...}; repeatable')
    p.add_argument('--workspace', default=os.environ.get('KUTTAKA_HOME', '~/.kuttaka'))
    p.add_argument('--model', default=os.environ.get('KUTTAKA_MODEL'))
    p.add_argument('--base-url', default=os.environ.get('KUTTAKA_BASE_URL', 'http://localhost:11434'))
    p.add_argument('--protocol', choices=['ollama', 'openai'],
                   default=os.environ.get('KUTTAKA_PROTOCOL', 'ollama'))
    p.add_argument('--key-env', help='name of the environment variable holding the API key, never the key')
    p.add_argument('--network-secret', action='store_true', help='the key is injected by a network proxy')
    p.add_argument('--max-calls', type=int, default=80, help='hard limit on model calls per round')
    p.add_argument('--max-executions', type=int, default=512,
                   help='sandbox call limit shared across all rounds, subtasks, diagnostics, composition, Mati and verification')
    p.add_argument('--max-tokens', type=int, default=16384)
    p.add_argument('--model-timeout', type=float, default=300)
    p.add_argument('--run-timeout', type=float,
                   help='seconds for the final run of the checked method on the requested input (up to 86400; '
                        'checks always run with 20 s)')
    p.add_argument('--run-memory', default='256m', choices=PythonExecutor.MEMORY_LIMITS,
                   help='sandbox memory for that final run')
    p.add_argument('--rounds', type=int, default=1,
                   help='if a round ends incomplete, start another on the same workspace (with what the earlier '
                        'rounds kept), up to this many rounds, each with its own --max-calls')
    p.add_argument('--yes', action='store_true', help='do not ask before using model-proposed checks')
    p.add_argument('--json', action='store_true', help='print the full result as JSON')
    p.add_argument('--witness', choices=['oeis'], help='ask a published table about a checked method of a sequence '
                   'task (others\' work as a witness; off by default; never in a registered comparison)')
    p.add_argument('--conditions', action='store_true',
                   help='ask the planner for each condition the task states, with its tempting wrong output; test '
                        'verify against them and keep them with the stored method (off by default: a prompt change '
                        'not yet run on a real model)')
    args = p.parse_args(argv)
    if args.max_executions < 0:
        p.error('--max-executions must be nonnegative')
    if args.rounds < 1:
        p.error('--rounds must be at least 1')
    if args.run_timeout is not None and not 0 < args.run_timeout <= PythonExecutor.LONG_RUN_LIMIT:
        p.error(f'--run-timeout must be in (0, {PythonExecutor.LONG_RUN_LIMIT}]')
    ws = Workspace(args.workspace)
    if args.task == 'stats':
        stats(ws)
        return 0
    if args.task == 'library':
        from .shastra import build, render
        library = build(ws)
        print(json.dumps(library, indent=1) if args.json else render(library))
        return 0
    if not args.model:
        p.error('set --model or KUTTAKA_MODEL (for example llama3.2 with Ollama)')
    for example in args.example:
        if _checks([example]) is None:
            p.error('each --example must be {"input": ..., "expected": ...}')
    ready = preflight()
    if not ready['ready']:
        print(f'Docker sandbox not ready ({ready.get("reason")}); methods run only inside it.', file=sys.stderr)
        return 2
    result = solve_task(args.task, args.example, settings=args, workspace=ws,
                        confirm=None if args.yes else _confirm,
                        say=(lambda text: None) if args.json else print)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _report(result, result['cost'])
    return 0 if result['status'] == 'checked' else 1


def settings(**overrides):
    """Model and limit settings with the CLI's defaults, for callers that do not parse argv."""
    values = dict(model=os.environ.get('KUTTAKA_MODEL'),
                  base_url=os.environ.get('KUTTAKA_BASE_URL', 'http://localhost:11434'),
                  protocol=os.environ.get('KUTTAKA_PROTOCOL', 'ollama'), key_env=None, network_secret=False,
                  max_calls=80, max_executions=512, max_tokens=16384, model_timeout=300,
                  run_timeout=None, run_memory='256m', rounds=1)
    values.update({k: v for k, v in overrides.items() if v is not None})
    return argparse.Namespace(**values)


def round_signature(result, workspace):
    """What a round leaves behind that the next round can build on (aviśeṣa: iterate until no difference).

    Two rounds with the same signature differ in nothing the next one could use: the same status and
    best progress and partial executable frontier under the same checks, the same stalled-repair hints,
    nothing newly confirmed, shown wrong or solved (a method not
    stored before, which the next round could reuse; reusing one adds nothing), and no lesson the workspace did not already hold (the next planner and
    actor read the lessons; one repeated word for word teaches nothing new)."""
    progress = result.get('progress') or []
    lessons = sorted({digest(l['mistake']) for l in workspace._read(workspace.lessons_path)})
    repair_hints = sorted((scope, fingerprint) for scope, fingerprints in (result.get('repair_hints') or {}).items()
                          for fingerprint in set(fingerprints))
    partials = sorted((scope, row['candidate_id'], tuple(sorted(row['passed_ids'])))
                      for scope, frontier in (result.get('evidence_frontiers') or {}).items()
                      for row in frontier.get('frontier', []))
    return json.dumps([result.get('status'), max(progress) if progress else None,
                       (result.get('check_evidence') or {}).get('scope'),
                       len(workspace._read(workspace.confirmed_path)), len(workspace._read(workspace.refuted_path)),
                       len({r['artifact'] for r in workspace._read(workspace.solved_path)}), digest(lessons),
                       repair_hints, partials])


def solve_task(task, examples=(), *, settings, workspace, confirm=None, say=lambda text: None,
               models=None, executor=None, deploy_executor=None):
    """Solve one task and record its cost in the workspace; the shared entry point of the CLI and the API.

    With settings.rounds > 1, an incomplete result starts another round on the same workspace
    (stored methods, partial attempts, breakdowns and the lessons of the round before), each round
    with its own max_calls, until a round is checked or the rounds are used up. max_executions is
    shared across all rounds; cost reports its aggregate usage. `models` (planner,
    actor, splitter, auditor, sketcher) and `executor` replace the configured endpoint and the
    Docker sandbox, for tests."""
    # Others' work as a witness only when asked for (--witness oeis): never in a registered comparison.
    from .witness import OEISWitness
    witness = OEISWitness() if getattr(settings, 'witness', None) == 'oeis' else None
    run_timeout = getattr(settings, 'run_timeout', None)
    if deploy_executor is None and run_timeout:
        deploy_executor = PythonExecutor(timeout=run_timeout, trace='distinct', long_run=True,
                                         memory=getattr(settings, 'run_memory', '256m'))
    executor = executor or PythonExecutor(timeout=20, trace='distinct')
    rounds, history, previous = max(1, getattr(settings, 'rounds', 1) or 1), [], None
    remaining_executions = settings.max_executions
    resume_candidates = {}
    resume_repairs = {}
    resume_contract = None
    for number in range(1, rounds + 1):
        counter = {'used': 0, 'limit': settings.max_calls}
        if models is None:
            wired = build_models(settings, counter)
        else:
            wired = tuple(None if m is None else Metered(m, counter) for m in models)
        planner, actor, splitter, auditor, sketcher = wired
        solver = Solver(workspace, planner, actor, executor, splitter=splitter, auditor=auditor,
                        sketcher=sketcher, deploy_executor=deploy_executor,
                         max_executions=remaining_executions, confirm=confirm if number == 1 else None, say=say,
                         resume_candidates=resume_candidates, resume_repairs=resume_repairs,
                         resume_contract=resume_contract,
                         **({'witness': witness} if witness is not None else {}),   # a substitute Solver need not take it
                         **({'conditions': True} if getattr(settings, 'conditions', False) else {}))
        if number > 1:
            say(f'round {number} of {rounds}: continuing from what the earlier rounds kept')
        result = solver.solve(task, list(examples))
        resume_candidates = solver.resume_candidates()
        resume_repairs = solver.resume_repairs()
        if counter.get('replied'):
            resume_contract = solver.resume_contract()
        used_executions = result.get('execution_attempts', 0)
        remaining_executions -= used_executions
        entry = {'task': task, 'status': result['status'], 'model_calls': counter['used'],
                 'execution_attempts': used_executions,
                 'reused': result.get('reused', []), 'subtasks': len(result.get('subtasks', [])),
                 **{k: solver.usage[k] for k in ('input_tokens', 'output_tokens', 'unknown_usage_calls')}}
        workspace.record(entry)
        history.append(dict(entry, round=number, reason=result.get('reason')))
        if result['status'] in ('checked', 'declined') or remaining_executions <= 0:
            break
        signature = round_signature(result, workspace)
        if number > 1 and signature == previous:
            history[-1]['stopped'] = 'no change since the round before (aviśeṣa); further rounds would repeat it'
            say('round changed nothing the next could use: stopping')
            break
        previous = signature
    total = dict(entry, **{k: sum(h[k] for h in history)
                           for k in ('model_calls', 'execution_attempts', 'input_tokens', 'output_tokens', 'unknown_usage_calls')})
    return dict(result, cost=total, rounds=history)


def _report(result, entry):
    print()
    if result['status'] == 'checked':
        if 'answer' in result:
            print(f'Answer for input {json.dumps(result["input"])}: {json.dumps(result["answer"])}')
        print(f'Method {result["artifact"][:12]} passed {len(result["checks"])} checks '
              f'(written by {result["checks_by"]}). Source:\n')
        print(result['source'])
        evidence = result.get('evidence') or {}
        if evidence:
            beyond = ', '.join(f'{n} by {kind}' for kind, n in evidence.get('tested_beyond_checks', {}).items())
            print(f'Support: {evidence["support"]} ({", ".join(f"{n} {k}" for k, n in evidence["checks"].items())})'
                  f'{"; held on inputs beyond the checks: " + beyond if beyond else ""}.')
        # Its limits, said with it (Golapāda 47-48): a checked method that runs out at the top of the stated range
        # is still checked on its finite checks, and the report says so rather than leave it unsaid.
        if result.get('scale') == 'slow':
            print('Limit: it ran out of time, memory or recursion on valid inputs at the top of the stated range '
                  '(the scale test); passing the checks does not cover those sizes.')
        elif result.get('scale') == 'not_run':
            print('Limit: the inputs at the top of the stated range could not be built or run; scale is untested.')
        if result.get('relations') == 'broken':
            print('Limit: on some inputs it breaks a relation the plan states (two runs that must agree do not).')
        witness = result.get('witness')
        if witness and witness.get('sequence'):
            print(f'Testimony: the published sequence {witness["sequence"]} {witness.get("outcome")} with it '
                  f'(compared beyond the first terms at {witness.get("compared_beyond", 0)} indices; not a derivation).')
    else:
        print(f'Void, no verified answer: {result.get("reason")}. The lesson is kept for next time.')
        best = (result.get('void') or {}).get('best_attempt')
        if best:
            print(f'Kept in its place: the best attempt {str(best["artifact"])[:12]} passes {best["passed"]} of '
                  f'{best["of"]} checks.')
        unverified = result.get('unverified')
        if unverified and 'answer' in unverified:
            print(f'UNVERIFIED answer for input {json.dumps(unverified["input"])}: {json.dumps(unverified["answer"])} '
                  f'(method {unverified["artifact"][:12]}; {unverified.get("note") or "not verified"})')
    reused = ', '.join(a[:12] for a in entry['reused']) or 'none'
    print(f'\n{entry["model_calls"]} model calls, {entry["input_tokens"] + entry["output_tokens"]} tokens'
          f'{" (some usage unknown)" if entry["unknown_usage_calls"] else ""}; reused methods: {reused}; '
          f'subtasks: {entry["subtasks"]}')


if __name__ == '__main__':
    sys.exit(main())
