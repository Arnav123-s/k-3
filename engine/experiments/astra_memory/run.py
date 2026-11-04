"""Authored memory experiments; finite truth tables and codecs are supplied.

Run from repository root into a new directory:
python -B -m experiments.astra_memory.run --output /tmp/astra-memory-new
"""

import argparse
import hashlib
from itertools import accumulate, product
import json
from pathlib import Path
import zlib

from kuttaka_runtime import ArtifactMemory, Journal
from kuttaka_runtime.artifacts import canonical
from kuttaka_runtime.query_memory import QueryMemory, QueryTable
from .codecs import reconstruct


def artifact(body, *, dependencies=()):
    return {'kind': 'representation', 'body': body, 'dependencies': list(dependencies),
            'provenance': {'author': 'researcher', 'campaign': 'astra-memory-v1'},
            'applicability': {'qualification': 'finite declared queries and supplied answers only'}}


def retain_view(table, encoding, label, memory, journal):
    assessment = table.assess_view(encoding)
    compiled = table.compile_view(encoding)
    aid = memory.put(artifact({'label': label, 'encoder_table': encoding,
                              'compiled': json.loads(compiled.serialized())}))
    evidence = journal.append('view_assessment', {'artifact': aid, **assessment})
    if assessment['status'] == 'exact':
        memory.qualify(aid, table.scope_id, 'finite-query-audit-v1', [evidence])
    return aid, compiled, assessment


def changing_questions(memory, journal):
    histories = {''.join(map(str, bits)): list(bits) for bits in product((0, 1), repeat=4)}
    states = list(histories)
    context = {'domain': 'all four-bit histories', 'answer_semantics': 'exact integers-v1'}
    archive = memory.put(artifact({'states': histories, 'context': context}))
    counts = {s: sum(bits) for s, bits in histories.items()}
    narrow = QueryTable(states, {'count': 'number of ones'}, {'count': counts}, context=context)
    original, deployed, old_assessment = retain_view(narrow, counts, 'count view', memory, journal)
    assert old_assessment['status'] == 'exact'
    assert deployed.answer(2, 'count', context=context) == {'status': 'answer', 'answer': 2}
    assert deployed.answer(2, 'last', context=context)['status'] == 'unknown'
    del histories
    archived = memory.get(archive)['body']
    histories = archived['states']
    last = {s: bits[-1] for s, bits in histories.items()}
    recovered_counts = {s: sum(bits) for s, bits in histories.items()}
    journal.append('source_reload', {'archive': archive, 'purpose': 'answer and refine for new query'})
    expanded = QueryTable(states, {'count': 'number of ones', 'last': 'last recorded bit'},
                          {'count': recovered_counts, 'last': last}, context=archived['context'])
    failed = expanded.assess_view(counts)
    assert failed['status'] == 'lossy'
    witness = journal.append('new_query_collision', failed)
    question = artifact({'question': 'Which retained source distinguishes histories with the same count but different last bits?',
                         'collision_witnesses': failed['collision_witnesses'],
                         'generation': 'authored loss-boundary question template'})
    question['kind'] = 'question'
    question['provenance']['evidence'] = witness
    question_id = memory.put(question)
    memory.invalidate(original, 'count alone cannot recover last bit', [witness], scope=expanded.scope_id)
    assert memory.status(original, narrow.scope_id) == 'qualified'
    assert memory.status(original, expanded.scope_id) == 'invalidated'
    ambiguous = expanded.compile_view(counts).answer(2, 'last', context=context)
    assert ambiguous['status'] == 'ambiguous'
    # Refinement uses the archived source distinctions. It does not recover
    # order from a count alone, and no unknown history is guessed.
    refinement = expanded.refine_view(counts)
    refined, compiled, assessed = retain_view(expanded, refinement, 'count plus last', memory, journal)
    for state in states:
        assert compiled.answer(refinement[state], 'last', context=context)['answer'] == last[state]
    full = QueryTable(states, {'history': 'complete ordered history'},
                      {'history': histories}, context=context)
    complete = full.assess_view(full.signature_view())
    journal.append('refinement_source', {'refined_artifact': refined, 'archive': archive,
                                       'attribution': 'complete supplied table, no inferred recovery'})
    return {'state_count': 16, 'count_classes': old_assessment['signature_class_count'],
            'count_fixed_bits_lower_bound': old_assessment['minimum_fixed_bits'],
            'count_plus_last_classes': assessed['signature_class_count'],
            'count_plus_last_fixed_bits_lower_bound': assessed['minimum_fixed_bits'],
            'full_history_classes': complete['signature_class_count'],
            'full_history_fixed_bits_lower_bound': complete['minimum_fixed_bits'],
            'new_query_answer_from_count': ambiguous,
            'narrow_qualification_preserved': memory.status(original, narrow.scope_id),
            'expanded_scope_status': memory.status(original, expanded.scope_id),
            'old_scope': narrow.scope_id, 'new_scope': expanded.scope_id,
            'old_artifact': original, 'refined_artifact': refined, 'source_archive': archive,
            'source_reloaded_before_refinement': True,
            'loss_boundary_question': question_id,
            'count_decoder_bytes': len(deployed.serialized()),
            'count_encoder_table_bytes': len(canonical(counts)),
            'refined_decoder_bytes': len(compiled.serialized()),
            'refined_encoder_table_bytes': len(canonical(refinement))}


def context_and_gaps(memory, journal):
    # Modern toy conventions, not a Sanskrit numeral decoder.
    unit_contexts = [{'decoder': 'integer-scale-v1', 'unit': unit} for unit in ('metre', 'millimetre')]
    views = []
    for context, metres in zip(unit_contexts, (1000, 1)):
        table = QueryTable(['reading'], {'length': 'physical length in metres'},
                           {'length': {'reading': metres}}, context=context)
        aid, view, _ = retain_view(table, {'reading': 1000}, context['unit'], memory, journal)
        views.append(view)
    correct = [view.answer(1000, 'length', context=context)['answer']
               for view, context in zip(views, unit_contexts)]
    mismatch = views[0].answer(1000, 'length', context=unit_contexts[1])
    assert correct == [1000, 1] and mismatch['status'] == 'context_mismatch'

    # Two supplied completions share a fragment. Neither is a lost historical text.
    context = {'source': 'invented sequence fragment', 'status': 'modern authored alternatives'}
    queries = {'prefix': 'first three entries', 'next': 'fourth entry', 'author': 'historical author'}
    table = QueryTable(['completion-A', 'completion-B'], queries,
                       {'prefix': {'completion-A': [1, 4, 9], 'completion-B': [1, 4, 9]},
                        'next': {'completion-A': 16, 'completion-B': 17}}, context=context)
    _, view, audit = retain_view(table, {s: [1, 4, 9] for s in table.states},
                                 'fragment with unresolved completion', memory, journal)
    prefix = view.answer([1, 4, 9], 'prefix', context=context)
    next_value = view.answer([1, 4, 9], 'next', context=context)
    author = view.answer([1, 4, 9], 'author', context=context)
    assert prefix['status'] == 'answer' and next_value['status'] == 'ambiguous'
    assert author['status'] == 'unknown' and audit['status'] == 'incomplete'
    journal.append('context_and_gap_checks', {'unit_answers_metres': correct,
        'cross_context': mismatch, 'fragment_prefix': prefix, 'next_entry': next_value, 'author': author})
    return {'same_payload_unit_answers_metres': correct, 'cross_context': mismatch,
            'fragment_known': prefix, 'fragment_completion': next_value,
            'unwitnessed_authorship': author}


def generator_memory(root, memory, journal):
    codec_source = Path(__file__).with_name('codecs.py').read_bytes()
    decoder = artifact({'language': 'Python3', 'source': codec_source.decode(),
                        'source_sha256': hashlib.sha256(codec_source).hexdigest(),
                        'execution': 'trusted reviewed experiment module only'})
    decoder['kind'] = 'method'
    decoder_id = memory.put(decoder)
    differences = [225, 224, 222, 219, 215, 210, 205, 199, 191, 183, 174, 164,
                   154, 143, 131, 119, 106, 93, 79, 65, 51, 37, 22, 7]
    cases = {
        'arithmetic_progression': (list(range(7, 7 + 3*4096, 3)),
            {'generator': 'arithmetic_progression', 'count': 4096, 'start': 7, 'step': 3, 'residuals': []},
            {'meaning': 'authored integer sequence; no recovered historical procedure'}),
        'sine_table': (list(accumulate(differences)),
            {'generator': 'tested_225_recurrence', 'count': 24, 'residuals': []},
            {'meaning': 'transmitted cumulative table in dossier', 'radius': 3438,
             'spacing_arcminutes': 225, 'source': 'docs/research/aryabhata/README.md section 6'}),
    }
    result = {}
    for name, (observed, spec, context) in cases.items():
        predicted = reconstruct(spec)
        residuals = [[i, actual - estimate] for i, (actual, estimate) in enumerate(zip(observed, predicted))
                     if actual != estimate]
        spec['residuals'] = residuals
        assert reconstruct(spec) == observed
        raw = canonical({'values': observed, 'context': context})
        representation = {'spec': spec, 'context': context, 'decoder_artifact': decoder_id}
        structured = canonical(representation)
        representation_id = memory.put(artifact(representation, dependencies=[decoder_id]))
        (root / f'{name}-raw.json').write_bytes(raw)
        (root / f'{name}-reconstruct.json').write_bytes(structured)
        result[name] = {'entries': len(observed), 'uncorrected_mismatches': len(residuals),
            'uncorrected_endpoint': predicted[-1], 'observed_endpoint': observed[-1],
            'exact_after_residuals': True, 'literal_payload_bytes': len(raw),
            'literal_zlib_bytes': len(zlib.compress(raw, 9)),
            'reconstruction_payload_bytes': len(structured), 'decoder_source_bytes': len(codec_source),
            'reconstruction_plus_decoder_bytes': len(structured) + len(codec_source),
            'reconstruction_and_decoder_zlib_bytes': len(zlib.compress(structured, 9)) + len(zlib.compress(codec_source, 9)),
            'representation_artifact': representation_id, 'decoder_artifact': decoder_id,
            'qualification_scope': f'reconstruction:{name}:finite-data-v1'}
        checked = journal.append('reconstruction_check', {'case': name, **result[name]})
        for aid in (decoder_id, representation_id):
            memory.qualify(aid, result[name]['qualification_scope'],
                           'trusted-decoder-identity-and-exact-case-v1', [checked])
    return result


def run(root):
    root.mkdir(parents=True, exist_ok=False)
    with ArtifactMemory(root / 'memory') as memory, Journal(root / 'evidence.jsonl') as journal:
        changing = changing_questions(memory, journal)
        contexts = context_and_gaps(memory, journal)
        generators = generator_memory(root, memory, journal)
        memory_stats = memory.stats()
    with ArtifactMemory(root / 'memory') as memory:
        retained = memory.get_qualified(changing['refined_artifact'], changing['new_scope'])['body']
        restored = QueryMemory.from_serialized(canonical(retained['compiled']))
        restored_answer = restored.answer(retained['encoder_table']['0011'], 'last',
                            context={'domain': 'all four-bit histories', 'answer_semantics': 'exact integers-v1'})
        assert restored_answer == {'status': 'answer', 'answer': 1}
        assert memory.status(changing['old_artifact'], changing['old_scope']) == 'qualified'
        for name, result in generators.items():
            record = memory.get_qualified(result['representation_artifact'], result['qualification_scope'])['body']
            decoder = memory.get(record['decoder_artifact'])['body']
            installed_source = Path(__file__).with_name('codecs.py').read_bytes()
            assert decoder['source_sha256'] == hashlib.sha256(installed_source).hexdigest()
            assert decoder['source'].encode() == installed_source
            expected = json.loads((root / f'{name}-raw.json').read_bytes())['values']
            assert reconstruct(record['spec']) == expected
            result['reopened_decoder_binding_and_values_verified'] = True
    # Copy exact implementations used, including the runner/encoder recipes.
    repo = Path(__file__).resolve().parents[2]
    sources = ['kuttaka_runtime/query_memory.py', 'kuttaka_runtime/artifacts.py',
               'kuttaka_runtime/store.py', 'experiments/astra_memory/codecs.py',
               'experiments/astra_memory/run.py']
    implementation = {path: (repo / path).read_text() for path in sources}
    source_bytes = canonical(implementation)
    (root / 'implementation.json').write_bytes(source_bytes)
    results = {'attribution': 'authored finite contracts and generators; no live model or learned memory policy',
        'changing_questions': changing, 'context_and_gaps': contexts, 'generators': generators,
        'restored_answer': restored_answer, 'artifact_storage': memory_stats,
        'implementation_snapshot_bytes': len(source_bytes),
        'implementation_sha256': hashlib.sha256(source_bytes).hexdigest(),
        'evidence_journal_bytes': (root / 'evidence.jsonl').stat().st_size,
        'all_files_before_results_bytes': sum(p.stat().st_size for p in root.rglob('*') if p.is_file()),
        'accounting_boundary': 'all recorded payload/source/evidence bytes; excludes Python/stdlib, filesystem overhead, transient RAM, and this results file; bit bounds are not deployed sizes'}
    (root / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
