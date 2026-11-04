"""Different preservation obligations on the same authored complete model."""
import argparse
import hashlib
import json
from pathlib import Path
from kuttaka_runtime.factored_memory import compile_factored, evaluate
from kuttaka_runtime.planning_memory import plan
from kuttaka_runtime.query_memory import _canonical


def balanced_or(names):
    if len(names) == 1:
        return names[0]
    mid = len(names)//2
    return ['or', balanced_or(names[:mid]), balanced_or(names[mid:])]


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    names = ['key', 'door'] + [f'n{i}' for i in range(998)]
    spec = {'variables': names, 'initial': {v: False for v in names}, 'goal': 'door',
        'actions': {'collect': {'guard': True, 'cost': 1, 'assign': {'key': True}},
                    'open': {'guard': 'key', 'cost': 2, 'assign': {'door': 'key'}},
                    'noise': {'guard': balanced_or(names[2:]), 'cost': 0,
                              'assign': {v: ['not', v] for v in names[2:]}}}}
    context = {'goal': 'door open', 'version': 'goal-cost-obligation-v1'}
    try:
        compile_factored(spec, context=context, complete=True)
    except ValueError as error:
        baseline = {'status': 'incomplete', 'reason': str(error)}
    else:
        raise AssertionError('expected full-behavior relevant-variable ceiling')
    record, start, audit = compile_factored(spec, context=context, complete=True, preserve='goal_cost')
    bundle = {'record': record, 'start': start, 'context': context}
    (output/'deployed.json').write_text(_canonical(bundle))
    loaded = json.loads((output/'deployed.json').read_text())
    result = plan(loaded['record'], loaded['start'], context=loaded['context'])
    state, cost = spec['initial'].copy(), 0
    for action in result['actions']:
        row = spec['actions'][action]
        assert evaluate(row['guard'], state)
        state = {v: evaluate(row['assign'].get(v, v), state) for v in names}
        cost += row['cost']
    assert evaluate(spec['goal'], state) and cost == result['cost'] == 3
    report = {'behavior_control': baseline, 'goal_cost_audit': audit, 'plan': result,
              'concrete_execution_checked': True, 'deployed_bytes': (output/'deployed.json').stat().st_size,
              'implementation_sha256': hashlib.sha256(Path('kuttaka_runtime/factored_memory.py').read_bytes()).hexdigest(),
              'implementation_bytes': Path('kuttaka_runtime/factored_memory.py').stat().st_size,
              'scope': 'known complete model; engineered relevance pruning; not preserved full behavior'}
    (output/'results.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
