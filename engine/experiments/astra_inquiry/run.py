"""Deterministic mechanism demonstration; every candidate and world is authored.

Run from repository root:
  python -m experiments.astra_inquiry.run --output /tmp/astra-inquiry-new
The output directory must not exist. No model, network, or generated code runs.
"""

import argparse
import json
from pathlib import Path

from kuttaka_runtime import Journal
from kuttaka_runtime.artifacts import ArtifactMemory, canonical as artifact_bytes
from kuttaka_runtime.beliefs import FiniteBelief, outcome_key
from kuttaka_runtime.inquiry import InquirySession


def point(value):
    return {outcome_key(value): 1.0}


def tables(candidates, experiments):
    return {name: {h: point(program(value)) for h, program in candidates.items()}
            for name, value in experiments.items()}


def artifact(kind, body, *, dependencies=()):
    return {'kind': kind, 'body': body, 'dependencies': list(dependencies),
            'provenance': {'author': 'researcher', 'experiment': 'astra-finite-inquiry-v1'},
            'applicability': {'scope': 'authored development fixtures; no general qualification'}}


def identifying_case(name, candidates, experiments, truth, root, memory):
    prediction_tables = tables(candidates, experiments)
    lengths = {h: 0 for h in candidates}  # Explicit uniform prior, not learned code lengths.
    with Journal(root / f'{name}.jsonl') as journal:
        session = InquirySession(lengths, prediction_tables,
                                 lambda exp: truth(experiments[exp]), journal,
                                 memory=memory, provenance='researcher-authored candidates/world')
        result = session.step({'costs': {x: 1 for x in experiments}, 'challenge': False})
        active_calls = session.attempts
        snapshot = artifact('representation', {
            'description_lengths': lengths, 'likelihoods': prediction_tables,
            'evidence': [{'experiment': e.experiment, 'outcome': json.loads(e.outcome)}
                         for e in session.belief.evidence],
            'conditional_posterior': dict(session.belief.posterior()),
        })
        snapshot_id = memory.put(snapshot)
        assert memory.put(snapshot) == snapshot_id
        live = [h for h, p in session.belief.posterior().items() if p > 0]
        assert len(live) == 1
        # Same candidate/table access, supplied fixed-order selection control.
        passive = FiniteBelief(lengths, prediction_tables)
        passive_calls = 0
        for exp in sorted(experiments):
            passive.observe(exp, truth(experiments[exp]))
            passive_calls += 1
            if sum(p > 0 for p in passive.posterior().values()) == 1:
                break
        return {'selected_experiment': result['experiment'], 'surviving_candidate': live[0],
                'active_observations': active_calls, 'fixed_order_observations': passive_calls,
                'snapshot': snapshot_id, 'snapshot_canonical_bytes': len(artifact_bytes(snapshot))}


def unknown_problem(root, memory):
    # The physical law is linear; the primary readout clips. The inference
    # system initially has only unsaturated readout predictions.
    clip = lambda x: max(-4, min(4, -2*x))
    candidates = {'k1': lambda x: -x, 'k2': lambda x: -2*x, 'k3': lambda x: -3*x}
    experiments = {'small': 1, 'large': 3}
    old_tables = tables(candidates, experiments)
    with Journal(root / 'unknown-problem.jsonl') as journal:
        def environment(exp):
            if exp == 'independent_readout':
                return {'primary': -4, 'reference': -6}
            if exp == 'negative':
                return 4
            return clip(experiments[exp])
        session = InquirySession({h: 0 for h in candidates}, old_tables, environment,
                                 journal, memory=memory, provenance='authored physical/readout fixture')
        small = session.step({'costs': {'small': 1}, 'challenge': False})
        assert small['posterior']['k2'] == 1
        old = memory.put(artifact('representation', {'readout': 'y=-2x', 'conditional_on': small['evidence']}))
        dependent = memory.put(artifact('method', {'use': 'predict using the retained readout'}, dependencies=[old]))
        # Deliberately narrow host attestation: fitting one fixture is not broad truth.
        scope = 'small-input-development-example'
        for aid in (old, dependent):
            memory.qualify(aid, scope, 'fixture-checker-v1', [small['evidence']])
        stopped = session.step({'costs': {'large': 1}, 'challenge': False})
        assert stopped['status'] == 'no_discriminator'
        surprise = session.step({'costs': {'large': 1}, 'challenge': True})
        assert surprise['status'] == 'mismatch'
        memory.invalidate(old, 'readout extrapolation contradicted; applicability needs review', [surprise['evidence']])
        assert memory.status(dependent, scope) == 'invalidated'
        assert memory.get(old)['body']['readout'] == 'y=-2x'  # Failed idea preserved.
        # A failed world model can still define a diagnostic residual. This is
        # an inspiration/provenance link, not a dependency on that model's truth.
        diagnostic = artifact('representation', {
            'role': 'residual relative to the rejected baseline',
            'experiment': 'large', 'observed': -4, 'baseline_prediction': -6,
            'residual': surprise['outcome'] - (-6),
        })
        diagnostic['provenance']['inspired_by'] = old
        diagnostic['provenance']['measurement'] = surprise['evidence']
        diagnostic_id = memory.put(diagnostic)
        diagnostic_evidence = journal.append('diagnostic_check', {
            'artifact': diagnostic_id, 'identity': '-4 - (-6) = 2',
            'measurement': surprise['evidence'], 'baseline_artifact': old,
            'claim': 'arithmetic residual only; no claim the rejected law is true'})
        memory.qualify(diagnostic_id, 'single-residual-arithmetic', 'integer-subtraction',
                       [surprise['evidence'], diagnostic_evidence])
        assert memory.status(old, scope) == 'invalidated'
        assert memory.get_qualified(diagnostic_id, 'single-residual-arithmetic')['body']['residual'] == 2

        # Expansion is supplied by the researcher, NOT discovered by an LLM.
        # Both hypotheses fit all primary readouts, yet assign different causes.
        expanded = {x: dict(row) for x, row in old_tables.items()}
        for x, value in experiments.items():
            expanded[x].update({'force_limit': point(clip(value)), 'sensor_limit': point(clip(value))})
        expanded['negative'] = {h: point(program(-3)) for h, program in candidates.items()}
        expanded['negative'].update({'force_limit': point(4), 'sensor_limit': point(4)})
        expanded['independent_readout'] = {
            'k1': point({'primary': -3, 'reference': -3}),
            'k2': point({'primary': -6, 'reference': -6}),
            'k3': point({'primary': -9, 'reference': -9}),
            'force_limit': point({'primary': -4, 'reference': -4}),
            'sensor_limit': point({'primary': -4, 'reference': -6}),
        }
        lengths = {**{h: 0 for h in candidates}, 'force_limit': 2, 'sensor_limit': 2}
        replay = session.revise(lengths, expanded, provenance='authored expansion after seeing mismatch')
        assert replay['posterior']['force_limit'] == replay['posterior']['sensor_limit'] == .5
        assert session.step({'costs': {'negative': 1}, 'challenge': False})['status'] == 'no_discriminator'
        fresh = session.step({'costs': {'negative': 1, 'independent_readout': 2}, 'challenge': False})
        assert fresh['experiment'] == 'independent_readout'
        assert fresh['posterior']['sensor_limit'] == 1
        return {
            'before_challenge': stopped['status'], 'model_mismatch': surprise['status'],
            'discovered_question': surprise['question_id'], 'question_generation': 'mismatch template',
            'failed_artifact_retained': old, 'dependent_status': memory.status(dependent, scope),
            'reused_in_different_role': diagnostic_id, 'new_role': 'diagnostic residual, not physical law',
            'revision_source': 'researcher supplied', 'replayed_observations': 2,
            'primary_only_causal_status': 'force cap and sensor cap observationally equivalent',
            'fresh_discriminator': fresh['experiment'],
            'final_candidate': 'sensor_limit', 'measurement_attempts': session.attempts,
        }


def run(root):
    root.mkdir(parents=True, exist_ok=False)
    with ArtifactMemory(root / 'memory') as memory:
        cases = {}
        cases['mathematics'] = identifying_case('mathematics', {
            'square': lambda n: n*n,
            'pronic': lambda n: n*(n+1),
            'triangular': lambda n: n*(n+1)//2,
        }, {'00': 0, '01': 1, '02': 2}, lambda n: n*n, root, memory)
        cases['software'] = identifying_case('software', {
            'sort': sorted, 'reverse': lambda xs: list(reversed(xs)), 'preserve': list,
        }, {'00': [1, 1], '01': [1, 2], '02': [3, 1, 2]}, sorted, root, memory)
        cases['simulated_physics'] = identifying_case('simulated_physics', {
            'k1': lambda x: -x, 'k2': lambda x: -2*x, 'k3': lambda x: -3*x,
        }, {'00': 0, '01': 1, '02': 2}, lambda x: -2*x, root, memory)
        surprise = unknown_problem(root, memory)
        stats = memory.stats()
    # Reopen from disk and actually reconstruct retained beliefs, preserving all
    # observations rather than replacing them by a prose summary.
    with ArtifactMemory(root / 'memory') as memory:
        for case in cases.values():
            retained = memory.get(case['snapshot'])['body']
            restored = FiniteBelief(retained['description_lengths'], retained['likelihoods'])
            for event in retained['evidence']:
                restored.observe(event['experiment'], event['outcome'])
            assert dict(restored.posterior()) == retained['conditional_posterior']
            case['reopened_belief_identical'] = True
        assert memory.get(surprise['discovered_question'])['kind'] == 'question'
    results = {
        'attribution': 'authored finite mechanisms; no live model, learned investigator, or transfer claim',
        'candidate_and_observation_spaces': 'finite and supplied; worlds differ, selection algorithm shared',
        'cases': cases, 'unasked_problem': surprise, 'storage': stats,
        'inquiry_journal_bytes': sum(p.stat().st_size for p in root.glob('*.jsonl')),
        'storage_note': 'blob compression/dedup; journal and metadata costs included separately; no optimality claim',
    }
    (root / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
