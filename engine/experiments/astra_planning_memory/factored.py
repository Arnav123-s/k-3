"""Authored factored model with 1000 Boolean variables; no LLM."""
import argparse
import hashlib
import json
from pathlib import Path
from kuttaka_runtime.factored_memory import compile_factored
from kuttaka_runtime.planning_memory import plan
from kuttaka_runtime.query_memory import _canonical


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    names = ['key', 'door'] + [f'irrelevant_{i}' for i in range(998)]
    spec = {'variables': names, 'initial': {v: False for v in names}, 'goal': 'door',
        'actions': {
            'collect': {'guard': True, 'cost': 1, 'assign': {'key': True}},
            'open': {'guard': 'key', 'cost': 2, 'assign': {'door': 'key'}},
            'noise': {'guard': True, 'cost': 0, 'assign': {v: ['not', v] for v in names[2:]}}}}
    context = {'goal': 'open door', 'model': 'authored Boolean transition system v1'}
    record, start, audit = compile_factored(spec, context=context, complete=True)
    bundle = {'record': record, 'start': start, 'context': context}
    serialized = _canonical(bundle).encode()
    (output/'deployed.json').write_bytes(serialized)
    del spec, record, names, bundle
    loaded = json.loads((output/'deployed.json').read_bytes())
    result = plan(loaded['record'], loaded['start'], context=loaded['context'])
    assert result['actions'] == ['collect', 'open'] and result['cost'] == 3
    sources = [Path('kuttaka_runtime')/name for name in
               ('factored_memory.py', 'planning_memory.py', 'query_memory.py')]
    report = {'audit': audit, 'plan': result, 'deployed_json_bytes': len(serialized),
              'implementation': {str(p): {'bytes': p.stat().st_size,
                  'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources},
              'scope': 'engineered Boolean projection on authored complete model, not discovered world structure'}
    (output/'results.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.output)
