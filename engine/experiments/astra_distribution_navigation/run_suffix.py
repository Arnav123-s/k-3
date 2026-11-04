"""Separate authored comparison of prefix and suffix counterexample processing."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from .run import (ROOT, LIMITS, SAMPLING_CONTRACT, SAMPLING_SEEDS, _fixed_cases,
                  _run_arm, _source_snapshot, _summary, _truth)
from experiments.astra_model_experiments.run import _generated_world
from kuttaka_runtime.store import Journal, canonical


def cases():
    prior = {item['case']: item for item in _fixed_cases()}
    selected = [prior[name] for name in ('generated_10_223_colors', 'long_trigger_6',
        'long_trigger_8', 'kmp_substring_5', 'kmp_substring_7')]
    for size in (8, 12):
        for seed in (307, 331):
            for partial in (False, True):
                name = f'generated_{size}_{seed}_' + ('colors' if partial else 'visible')
                selected.append({'case': name, 'origin': 'fresh-authored-generated-variant-v2',
                    'factory': lambda n=size, s=seed, p=partial: _generated_world(n,s,partially_observed=p)[0],
                    'truth_factory': lambda n=size, s=seed, p=partial: _generated_world(n,s,partially_observed=p)[1]})
    return selected


def arms():
    return [(f'{mode}-persistent-{seed}', mode, {'strategy': 'distribution',
        'sampling': {**SAMPLING_CONTRACT, 'seed': seed, 'persistence': True}})
        for mode in ('prefixes', 'suffix') for seed in SAMPLING_SEEDS]


def hybrid_arms():
    return [(f'{mode}-{strategy}-{seed}', mode, {'strategy': strategy,
        'sampling': {**SAMPLING_CONTRACT, 'seed': seed, 'persistence': True}})
        for mode,strategy in (('prefixes','distribution'), ('suffix','distribution'),
                              ('suffix','hybrid')) for seed in SAMPLING_SEEDS]


def compact_arms():
    return [(f'hybrid-{"compact" if compact else "full"}-{seed}', 'suffix',
        {'strategy': 'hybrid', 'hybrid_compact': compact,
         'sampling': {**SAMPLING_CONTRACT, 'seed': seed, 'persistence': True}})
        for compact in (False, True) for seed in SAMPLING_SEEDS]


def run(output, study='v2'):
    if study not in ('v2', 'v3', 'v4'):
        raise ValueError('unknown study')
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False)
    source_hashes = _source_snapshot(root)
    source_names = ['run_suffix.py', 'PROTOCOL_SUFFIX.md']
    if study in ('v3', 'v4'):
        source_names.append('PROTOCOL_HYBRID.md')
    if study == 'v4':
        source_names.append('PROTOCOL_COMPACT.md')
    for name in source_names:
        path = Path(__file__).parent / name
        relative = str(path.relative_to(ROOT))
        raw = path.read_bytes()
        (root/'sources'/relative).write_bytes(raw)
        source_hashes[relative] = hashlib.sha256(raw).hexdigest()
    matrix = cases()
    policies = {'v2': arms, 'v3': hybrid_arms, 'v4': compact_arms}[study]()
    truth = _truth(matrix)
    truth_bytes = canonical(truth).encode()
    (root/'evaluator-truth.json').write_bytes(truth_bytes+b'\n')
    initial_goals = [item['case'] for item in matrix
                     if item['factory']().outputs[item['factory']().initial] == 'G']
    assert len(matrix) == 13 and len(policies) == (9 if study == 'v3' else 6) and not initial_goals
    design = {'schema': 'astra-distribution-refinement-study-'+study, 'case_count': 13,
        'run_count': len(matrix)*len(policies), 'cases': [c['case'] for c in matrix],
        'arms': [{'label': label, 'counterexample_strategy': mode, **options}
                 for label,mode,options in policies],
        'limits': LIMITS, 'source_sha256': source_hashes,
        'truth_sha256': hashlib.sha256(truth_bytes).hexdigest(),
        'initial_goal_cases': initial_goals, 'python_version': sys.version,
        'endpoint_calls': 0, 'statistical_certificates': False,
        'attribution': 'engineered counterexample processing and optional outcome-cover hybrid; authored development worlds'}
    (root/'design.json').write_text(canonical(design)+'\n')
    rows = []
    for case in matrix:
        for label,mode,options in policies:
            row = _run_arm(root, case, (label,options['strategy'],options['sampling']),
                           counterexample_strategy=mode,
                           hybrid_compact=options.get('hybrid_compact', False))
            row['counterexample_strategy'] = mode
            events = Journal.read(root/row['journal'])
            row['refinement_events'] = [e for e in events if e['kind'].startswith('active_navigation_')
                and ('suffix' in e['kind'] or 'refinement' in e['kind'] or 'processing' in e['kind'])]
            rows.append(row)
            (root/'partial-results.json').write_text(canonical(rows)+'\n')
    summary = _summary(rows, policies)
    summary['claim_boundary'] += '; comparison is prefix versus suffix processing on exposed and fresh authored variants'
    (root/'results.json').write_text(canonical({'rows':rows,'summary':summary})+'\n')
    (root/'partial-results.json').unlink()
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--study', choices=['v2', 'v3', 'v4'], default='v2')
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.study), indent=2, sort_keys=True))
