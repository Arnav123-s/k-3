"""Engineered exact abstraction for complete finite deterministic models.

Partition refinement preserves observations, action availability, and integer
costs along every action sequence. No claim covers unobserved transitions.
"""
import hashlib
import heapq
import json
from .query_memory import _canonical


def _copy(x):
    return json.loads(_canonical(x))


def _partition(signatures):
    codes = {key: i for i, key in enumerate(sorted(set(signatures.values())))}
    return {s: codes[key] for s, key in signatures.items()}


def compile_model(model, *, context, complete, max_refinements=1024):
    """Return quotient, temporary state encoder, and refinement accounting.

    Compilation uses the entire supplied model. After encoding the current
    state, deployment can drop both that model and the encoder.
    """
    if complete is not True:
        raise ValueError("exact planning requires an explicit complete-model declaration")
    if type(max_refinements) is not int or not 1 <= max_refinements <= 100000:
        raise ValueError('invalid refinement ceiling')
    model = _copy(model)
    context = _copy(context)
    if not isinstance(model, dict) or not 1 <= len(model) <= 100000:
        raise ValueError('expected 1..100000 states')
    for state, row in model.items():
        if not state or not isinstance(row, dict) or set(row) != {'observation', 'transitions'}:
            raise ValueError('invalid state record')
        obs, edges = row['observation'], row['transitions']
        if not isinstance(obs, dict) or type(obs.get('goal')) is not bool:
            raise ValueError('observation requires Boolean goal')
        if not isinstance(edges, dict) or len(edges) > 64:
            raise ValueError('invalid transitions')
        for action, edge in edges.items():
            if not action or not isinstance(edge, dict) or set(edge) != {'to', 'cost'}:
                raise ValueError('invalid transition')
            if not isinstance(edge['to'], str) or edge['to'] not in model:
                raise ValueError('transition leaves declared domain')
            if type(edge['cost']) is not int or not 0 <= edge['cost'] <= 10**12:
                raise ValueError('cost must be a bounded nonnegative integer')
    blocks = _partition({s: _canonical(row['observation']) for s, row in model.items()})
    history = [len(set(blocks.values()))]
    while True:
        if len(history) > max_refinements:
            raise ValueError('refinement ceiling reached; no exact quotient produced')
        signatures = {s: _canonical([blocks[s], [[a, e['cost'], blocks[e['to']]]
                    for a, e in sorted(row['transitions'].items())]]) for s, row in model.items()}
        refined = _partition(signatures)
        history.append(len(set(refined.values())))
        # Refinement only splits classes. Equal counts imply a fixed partition,
        # even if deterministic numbering has changed.
        if history[-1] == history[-2]:
            blocks = refined
            break
        blocks = refined
    nodes = {}
    for state, block in blocks.items():
        row = model[state]
        node = {'observation': row['observation'], 'transitions': {
            a: {'to': str(blocks[e['to']]), 'cost': e['cost']}
            for a, e in row['transitions'].items()}}
        key = str(block)
        if key in nodes and nodes[key] != node:
            raise AssertionError('unstable partition')
        nodes[key] = node
    record = {'format': 'astra-planning-memory-v1', 'context': context, 'nodes': nodes, 'complete_model_assumed': True,
              'source_digest': hashlib.sha256(_canonical(model).encode()).hexdigest()}
    encoder = {s: str(block) for s, block in blocks.items()}
    return record, encoder, {'class_counts': history, 'source_states': len(model),
        'retained_states': len(nodes), 'source_json_bytes': len(_canonical(model).encode()),
        'quotient_json_bytes': len(_canonical(record).encode()),
        'encoder_json_bytes': len(_canonical(encoder).encode()),
        'scope': 'complete supplied deterministic model only; truth of model is not certified'}


def plan(record, start, *, context):
    """Shortest path using only quotient state, with Dijkstra on integer costs.

    The record is trusted compiler output or externally hash-pinned storage.
    Structural checks cannot certify its relationship to a discarded source.
    """
    record = _copy(record)
    if record.get('format') != 'astra-planning-memory-v1' or record.get('complete_model_assumed') is not True:
        raise ValueError('unknown memory format')
    if _canonical(record['context']) != _canonical(context):
        return {'status': 'context_mismatch'}
    nodes = record['nodes']
    # Validate the deployed graph using the same model contract. This validation
    # is linear below: do not run partition refinement again during planning.
    if not isinstance(nodes, dict) or not nodes or len(nodes) > 100000:
        raise ValueError('invalid nodes')
    for row in nodes.values():
        if not isinstance(row, dict) or set(row) != {'observation', 'transitions'}:
            raise ValueError('invalid node')
        if not isinstance(row['observation'], dict) or type(row['observation'].get('goal')) is not bool:
            raise ValueError('invalid goal')
        if not isinstance(row['transitions'], dict) or len(row['transitions']) > 64:
            raise ValueError('invalid transitions')
        for a, edge in row['transitions'].items():
            if not a or not isinstance(edge, dict) or set(edge) != {'to', 'cost'}:
                raise ValueError('invalid edge')
            if not isinstance(edge['to'], str) or edge['to'] not in nodes:
                raise ValueError('invalid successor')
            if type(edge['cost']) is not int or not 0 <= edge['cost'] <= 10**12:
                raise ValueError('invalid cost')
    if not isinstance(start, str) or start not in nodes:
        return {'status': 'unknown_start'}
    queue = [(0, start)]
    best, parent, expanded = {start: 0}, {}, 0
    while queue:
        cost, state = heapq.heappop(queue)
        if cost != best[state]:
            continue
        expanded += 1
        if nodes[state]['observation']['goal']:
            actions = []
            terminal = state
            while state != start:
                state, action = parent[state]
                actions.append(action)
            return {'status': 'plan', 'actions': list(reversed(actions)), 'cost': cost,
                    'terminal': terminal, 'expanded': expanded}
        for action, edge in sorted(nodes[state]['transitions'].items()):
            target, new_cost = edge['to'], cost + edge['cost']
            if target not in best or new_cost < best[target]:
                best[target] = new_cost
                parent[target] = (state, action)
                heapq.heappush(queue, (new_cost, target))
    return {'status': 'no_path', 'expanded': expanded,
            'scope': 'within the supplied complete model only'}


def observe_transition(record, current, action, *, observation, cost, context):
    """Check an actual step against a trusted compiled prediction.

    A discrepancy invalidates the current state estimate. Never guess a new
    abstract state from an observation that might alias several hidden states.
    Agreement is only a local check, not proof of the underlying model.
    """
    record = _copy(record)
    observation = _copy(observation)
    if record.get('format') != 'astra-planning-memory-v1' or record.get('complete_model_assumed') is not True:
        raise ValueError('invalid memory format')
    if _canonical(record['context']) != _canonical(context):
        return {'status': 'context_mismatch', 'state': None}
    if type(cost) is not int or cost < 0:
        raise ValueError('observed cost must be a nonnegative integer')
    nodes = record['nodes']
    if not isinstance(current, str) or current not in nodes:
        return {'status': 'unknown_start', 'state': None}
    if not isinstance(action, str) or action not in nodes[current]['transitions']:
        return {'status': 'unmodeled_action', 'state': None}
    edge = nodes[current]['transitions'][action]
    expected = nodes[edge['to']]['observation']
    if _canonical(observation) != _canonical(expected) or cost != edge['cost']:
        return {'status': 'model_mismatch', 'state': None,
                'expected': {'observation': expected, 'cost': edge['cost']},
                'observed': {'observation': observation, 'cost': cost},
                'requires': 'new evidence and model revision before certified replanning'}
    return {'status': 'consistent_step', 'state': edge['to'],
            'scope': 'local observation agreement only'}
