"""Syntactic dependency projection of a supplied Boolean transition system.

A conservative causal cone preserves goal truth, action guards and constant
costs. It avoids enumerating irrelevant Boolean state dimensions. The DSL and
proof rule are engineered, not model-discovered.
"""
from itertools import product
import hashlib
from .query_memory import _canonical
from .planning_memory import compile_model


def _dependencies(expr, variables, depth=0):
    if depth > 32:
        raise ValueError('expression nesting limit')
    if type(expr) is bool:
        return set()
    if isinstance(expr, str) and expr in variables:
        return {expr}
    if not isinstance(expr, list) or not expr:
        raise ValueError('invalid Boolean expression')
    op = expr[0]
    if op == 'not' and len(expr) == 2:
        return _dependencies(expr[1], variables, depth+1)
    if op in ('and', 'or', 'xor') and len(expr) == 3:
        return _dependencies(expr[1], variables, depth+1) | _dependencies(expr[2], variables, depth+1)
    raise ValueError('invalid Boolean expression')


def evaluate(expr, state):
    if type(expr) is bool:
        return expr
    if isinstance(expr, str):
        return state[expr]
    op = expr[0]
    a = evaluate(expr[1], state)
    if op == 'not':
        return not a
    b = evaluate(expr[2], state)
    if op == 'and':
        return a and b
    if op == 'or':
        return a or b
    if op == 'xor':
        return a != b
    raise ValueError('invalid expression')


def compile_factored(spec, *, context, complete, max_relevant=12, preserve="behavior"):
    """Project before enumeration; all assignments use pre-action values.

    'behavior' preserves action traces; 'goal_cost' prunes irrelevant actions
    while preserving minimum goal cost under nonnegative costs.
    No hidden target labels or task dispatch. Reject large relevant cones rather
    than presenting an incomplete projection as exact.
    """
    if preserve not in ('behavior', 'goal_cost'):
        raise ValueError('unknown preservation obligation')
    if complete is not True:
        raise ValueError('requires a declared complete factored model')
    if type(max_relevant) is not int or not 0 <= max_relevant <= 16:
        raise ValueError('invalid relevant-variable ceiling')
    if not isinstance(spec, dict) or set(spec) != {'variables', 'initial', 'goal', 'actions'}:
        raise ValueError('invalid model shape')
    if len(_canonical(spec)) > 1000000:
        raise ValueError('model serialization limit')
    names = spec['variables']
    if not isinstance(names, list) or not 1 <= len(names) <= 2048:
        raise ValueError('invalid variables')
    if any(not isinstance(v, str) or not v for v in names) or len(set(names)) != len(names):
        raise ValueError('invalid variable name')
    variables = set(names)
    initial, actions = spec['initial'], spec['actions']
    if not isinstance(initial, dict) or set(initial) != variables or any(type(x) is not bool for x in initial.values()):
        raise ValueError('invalid initial state')
    if not isinstance(actions, dict) or not 1 <= len(actions) <= 64:
        raise ValueError('invalid actions')
    relevant = _dependencies(spec['goal'], variables)
    dependencies, guards = {}, {}
    for action, row in actions.items():
        if not action or not isinstance(row, dict) or set(row) != {'guard', 'cost', 'assign'}:
            raise ValueError('invalid action')
        if type(row['cost']) is not int or not 0 <= row['cost'] <= 10**12:
            raise ValueError('invalid action cost')
        guards[action] = _dependencies(row['guard'], variables)
        if preserve == 'behavior':
            relevant |= guards[action]
        if not isinstance(row['assign'], dict) or set(row['assign']) - variables:
            raise ValueError('invalid assignments')
        dependencies[action] = {v: _dependencies(expr, variables) for v, expr in row['assign'].items()}
    # In goal-cost mode retain only actions that can write relevant state.
    # Closure then pulls in guards and any actions that can enable them.
    while True:
        selected = set(actions) if preserve == 'behavior' else {
            a for a, assignments in dependencies.items() if set(assignments) & relevant}
        expanded = set(relevant)
        for action in selected:
            expanded |= guards[action]
            assignments = dependencies[action]
            for variable in relevant:
                expanded |= assignments.get(variable, {variable})
        if expanded == relevant:
            break
        relevant = expanded
    if len(relevant) > max_relevant:
        raise ValueError('relevant-variable ceiling reached; no exact memory produced')
    ordered = sorted(relevant)
    def key(state):
        return ''.join('1' if state[v] else '0' for v in ordered) or '_'
    model = {}
    for bits in product((False, True), repeat=len(ordered)):
        state = dict(zip(ordered, bits))
        edges = {}
        for action, row in actions.items():
            if action in selected and evaluate(row['guard'], state):
                after = {v: evaluate(row['assign'].get(v, v), state) for v in ordered}
                edges[action] = {'to': key(after), 'cost': row['cost']}
        model[key(state)] = {'observation': {'goal': evaluate(spec['goal'], state)}, 'transitions': edges}
    record, encoder, audit = compile_model(model, context=context, complete=True)
    if preserve == 'goal_cost':
        record['preservation'] = 'minimum_goal_cost'
        record['omitted_actions'] = sorted(set(actions) - selected)
        audit['quotient_json_bytes'] = len(_canonical(record).encode())
    start = encoder[key(initial)]
    return record, start, {**audit, 'preservation': preserve, 'retained_actions': sorted(selected),
        'removed_actions': sorted(set(actions)-selected), 'source_variables': len(names), 'retained_variables': ordered,
        'eliminated_variables': len(names)-len(ordered), 'source_state_count_power_of_two': len(names),
        'enumerated_projected_states': len(model), 'source_spec_bytes': len(_canonical(spec).encode()),
        'source_spec_digest': hashlib.sha256(_canonical(spec).encode()).hexdigest(),
        'rule': 'syntactic dependency closure; conservative, not minimal variable selection'}
