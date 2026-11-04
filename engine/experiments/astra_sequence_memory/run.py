"""Reproducible authored data, automatic codec selection, actual disk reload."""
import argparse
import json
from pathlib import Path
import random
from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.sequence_memory import encode, restore, read, digest


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(7326)
    cases = {
        'progression': [7 + 13*i for i in range(4096)],
        'square_numbers': [i*i for i in range(4096)],
        'constant_acceleration_samples': [2 + 3*i + 5*i*i for i in range(4096)],
        'cubic_accumulation': [i**3 for i in range(4096)],
        'random_integers': [rng.randrange(-100000, 100000) for _ in range(4096)],
        'periodic_outside_family': [0, 1, 0, -1]*1024,
        'short_ambiguous_fragment': [1, 4, 9],
    }
    rare = cases['square_numbers'][:]
    rare[2345] += 137
    cases['square_with_exception'] = rare
    reports = {}
    with ArtifactMemory(output / 'memory') as memory:
        for name, values in cases.items():
            context = {'dataset': name, 'semantics': 'finite observed integers, index starts at zero'}
            record, costs = encode(values, context=context)
            aid = memory.put({'kind': 'representation', 'body': record, 'dependencies': [],
                'provenance': {'producer': 'bounded automatic codec search', 'input': 'authored experiment'},
                'applicability': {'domain': 'retained observations only'}})
            reports[name] = dict(artifact=aid, codec=record['codec'], costs=costs,
                observation_digest=digest(values), context=context, count=len(values))
    # Fresh persistence handle; reconstruct from artifact, not original rows.
    del cases, rare, values, record
    with ArtifactMemory(output / 'memory') as memory:
        for name, report in reports.items():
            record = memory.get(report['artifact'])['body']
            recovered = restore(record, context=report['context'])
            assert digest(recovered) == report['observation_digest']
            report['disk_reload_exact'] = True
            report['future_read'] = read(record, report['count'], context=report['context'])
            assert report['future_read']['status'] == 'unknown'
    # Explicit underdetermination: identical observations, distinct next entry.
    a = lambda i: (i+1)**2
    b = lambda i: (i+1)**2 + i*(i-1)*(i-2)
    assert [a(i) for i in range(3)] == [b(i) for i in range(3)]
    results = {'cases': reports, 'fragment_counterexample': {'observed': [1,4,9],
        'next_candidates': [a(3), b(3)], 'claim': 'compatible continuations, not recovered lost text'},
        'artifact_store_bytes': sum(p.stat().st_size for p in (output / 'memory').rglob('*') if p.is_file()),
        'scope': 'automatic selection in an authored bounded family; no LLM and no out-of-domain predictive claim'}
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    results = run(args.output)
    for name, row in results['cases'].items():
        c = row['costs']
        print(name, row['codec'], c['literal_bytes'], c['payload_bytes'], row['disk_reload_exact'])
