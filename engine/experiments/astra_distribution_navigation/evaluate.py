"""Exact evaluator-only disagreement mass for bounded reset-word distributions.

This module inspects finite world tables and a finite Moore candidate. It never
calls environment callbacks and must not be imported by the learner.
"""

from __future__ import annotations

from fractions import Fraction

from kuttaka_runtime.active_navigation import validate_model


MAX_LENGTH = 32
MAX_STATES = 128
MAX_ACTIONS = 32
MAX_DP_CELLS = MAX_STATES * MAX_STATES * MAX_ACTIONS
MAX_DP_WORK = 10_000_000
MAX_OUTPUT_CHARS = 256
MAX_OUTPUT_BYTES = 1024


def _validate_world(world):
    actions = getattr(world, "actions", None)
    transitions = getattr(world, "transitions", None)
    outputs = getattr(world, "outputs", None)
    initial = getattr(world, "initial", None)
    if (not isinstance(actions, (list, tuple)) or not actions or len(actions) > MAX_ACTIONS
            or any(not isinstance(action, str) or not action or len(action) > 64 for action in actions)
            or len(set(actions)) != len(actions)):
        raise ValueError("world must expose a unique bounded action list")
    if not isinstance(transitions, dict) or not transitions or len(transitions) > MAX_STATES:
        raise ValueError("world must expose a bounded nonempty transition table")
    if not isinstance(outputs, dict) or set(outputs) != set(transitions):
        raise ValueError("world outputs must cover exactly the transition states")
    if any(not isinstance(state, str) or not state or len(state) > 128 for state in transitions):
        raise ValueError("world state identifiers must be bounded nonempty strings")
    if initial not in transitions:
        raise ValueError("world initial state is missing")
    for state, row in transitions.items():
        if not isinstance(row, dict) or set(row) != set(actions):
            raise ValueError("world transition rows must cover every declared action")
        if any(target not in transitions for target in row.values()):
            raise ValueError("world transition points to an undeclared state")
        output = outputs[state]
        if (not isinstance(output, str) or not output.strip()
                or len(output) > MAX_OUTPUT_CHARS
                or len(output.encode("utf-8")) > MAX_OUTPUT_BYTES):
            raise ValueError("world outputs must be bounded nonempty strings")
    return list(actions), transitions, outputs, initial


def disagreement_mass(world, model, *, max_length=16, persistence=False):
    """Compute exact trace-disagreement probability over bounded reset words.

    Word length is uniform on ``0..max_length``. The first action is uniform.
    Thereafter actions are IID uniform unless ``persistence`` is true; in that
    mode the next action is drawn by flipping a fair coin between copying the
    previous action and choosing uniformly from the complete alphabet. The
    result concerns this finite distribution only, never unbounded equivalence.
    """
    if type(max_length) is not int or not 0 <= max_length <= MAX_LENGTH:
        raise ValueError(f"max_length must be an integer in 0..{MAX_LENGTH}")
    if type(persistence) is not bool:
        raise ValueError("persistence must be a literal bool")
    if not validate_model(model):
        raise ValueError("invalid Moore model")
    actions, transitions, outputs, initial = _validate_world(world)
    if list(model["actions"]) != actions:
        raise ValueError("model and world action alphabets/order differ")

    # Each entry is the exact probability of reaching a still-matching product
    # state after the current number of actions. ``None`` marks no prior symbol.
    model_initial = model["initial"]
    if outputs[initial] != model["outputs"][model_initial]:
        return _result(Fraction(1, 1), max_length, persistence)
    live = {(initial, model_initial, None): Fraction(1, 1)}
    survival = [Fraction(1, 1)]
    alphabet_size = len(actions)
    work = 0

    for _length in range(1, max_length+1):
        next_live = {}
        for (world_state, model_state, previous), mass in live.items():
            work += alphabet_size
            if work > MAX_DP_WORK:
                raise ValueError("exact product evaluation exceeded its bounded work ceiling")
            if previous is None or not persistence:
                probabilities = {action: Fraction(1, alphabet_size) for action in actions}
            else:
                probabilities = {action: Fraction(1, 2*alphabet_size) for action in actions}
                probabilities[previous] += Fraction(1, 2)
            for action, action_mass in probabilities.items():
                next_world = transitions[world_state][action]
                next_model = model["transitions"][model_state][action]
                if outputs[next_world] != model["outputs"][next_model]:
                    continue
                key = (next_world, next_model, action)
                next_live[key] = next_live.get(key, Fraction(0, 1)) + mass*action_mass
        if len(next_live) > MAX_DP_CELLS:
            raise ValueError("exact product evaluation exceeded its bounded state-cell ceiling")
        live = next_live
        survival.append(sum(live.values(), Fraction(0, 1)))

    mean_survival = sum(survival, Fraction(0, 1)) / (max_length+1)
    return _result(Fraction(1, 1)-mean_survival, max_length, persistence)


def _result(mass, max_length, persistence):
    policy = ("first uniform; subsequent uniform IID" if not persistence else
              "first uniform; subsequent half-copy previous, half-uniform alphabet")
    return {"numerator": mass.numerator, "denominator": mass.denominator,
            "value": float(mass), "max_length": max_length,
            "persistence": persistence,
            "scope": f"exact trace-disagreement mass under uniform length 0..{max_length}; {policy}; no claim outside support"}
