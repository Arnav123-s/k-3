"""Authored finite planning model; no LLM or independently measured world."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.planning_memory import compile_model, plan, observe_transition
from kuttaka_runtime.query_memory import _canonical


def run(output, phases=32, lanes=512):
    output.mkdir(parents=True, exist_ok=False)
    context = {'goal': 'reach last phase', 'model': 'authored deterministic lane process v1'}
    model = {}
    for phase in range(phases):
        for lane in range(lanes):
            key = f'{phase:03}:{lane:04}'
            edges = {} if phase == phases-1 else {
                'advance': {'to': f'{phase+1:03}:{lane:04}', 'cost': 1},
                'cycle': {'to': f'{phase:03}:{(lane+1)%lanes:04}', 'cost': 0}}
            model[key] = {'observation': {'goal': phase == phases-1}, 'transitions': edges}
    began = perf_counter()
    record, encoder, audit = compile_model(model, context=context, complete=True)
    compilation_seconds = perf_counter() - began
    start = encoder['000:0000']
    raw = dict(record, nodes=model)
    began = perf_counter()
    baseline = plan(raw, '000:0000', context=context)
    baseline_seconds = perf_counter() - began
    began = perf_counter()
    abstract = plan(record, start, context=context)
    abstract_seconds = perf_counter() - began
    assert abstract['cost'] == baseline['cost'] == phases-1
    # Execute chosen abstract actions on the concrete source independently.
    state, cost, monitored = '000:0000', 0, start
    for action in abstract['actions']:
        edge = model[state]['transitions'][action]
        state, cost = edge['to'], cost + edge['cost']
        checked = observe_transition(record, monitored, action, observation=model[state]['observation'], cost=edge['cost'], context=context)
        assert checked['status'] == 'consistent_step'
        monitored = checked['state']
    assert model[state]['observation']['goal'] and cost == abstract['cost']
    with ArtifactMemory(output / 'memory') as memory:
        aid = memory.put({'kind': 'representation', 'body': record, 'dependencies': [],
            'provenance': {'source': 'authored complete model', 'compiler': 'partition refinement'},
            'applicability': {'context': context, 'model_digest': record['source_digest']}})
    surprise = observe_transition(record, start, 'advance', observation={'goal': True}, cost=1, context=context)
    assert surprise['status'] == 'model_mismatch' and surprise['state'] is None
    # Only the quotient and current block are needed for the next planning call.
    del model, encoder, raw, record
    with ArtifactMemory(output / 'memory') as memory:
        deployed = memory.get(aid)['body']
        reloaded = plan(deployed, start, context=context)
    assert reloaded == abstract
    implementation = Path('kuttaka_runtime/planning_memory.py').read_bytes()
    results = dict(observed_surprise=surprise, audit=audit, baseline=baseline, abstract=abstract, reload_identical=True,
        artifact=aid, deployed_start=start, concrete_execution_checked=True,
        compilation_seconds=compilation_seconds, baseline_seconds=baseline_seconds,
        abstract_seconds=abstract_seconds, implementation_source_bytes=len(implementation),
        implementation_sha256=hashlib.sha256(implementation).hexdigest(),
        store_bytes=sum(p.stat().st_size for p in (output/'memory').rglob('*') if p.is_file()),
        limits=['model is authored and assumed complete', 'compilation needs full source and encoder',
                'new goals or new evidence may require discarded distinctions',
                'runtime, imported canonicalizer, artifact store code and filesystem allocation excluded from source-byte count'])
    (output/'results.json').write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.output)
