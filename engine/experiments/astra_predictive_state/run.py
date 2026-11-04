"""Exploratory, authored research using the runtime's inquiry and durable memory.

No live model, blind evaluation, historical authorship or field transfer claim.
Run with python -m experiments.astra_predictive_state.run --output NEW_DIRECTORY.
"""
import argparse
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
from pathlib import Path

from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.beliefs import outcome_key
from kuttaka_runtime.inquiry import InquirySession
from kuttaka_runtime.recurrence import fit_recurrence, predict_recurrence, verify_recurrence
from kuttaka_runtime.rational_ratio import fit_ratio, predict_ratio, verify_ratio
from kuttaka_runtime.store import Journal, canonical


ROOT = Path(__file__).resolve().parents[2]
SINE_PATH = ROOT / 'docs/research/predictive-state/sine_constraints.py'


def sine_module():
    spec = importlib.util.spec_from_file_location('sine_constraints', SINE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rational_rotation(count):
    # A unit-circle rotation with cos(h)=3/5, sin(h)=4/5. This is NOT the
    # historical spacing pi/48, whose cosine is irrational.
    x, y = Fraction(0), Fraction(1)
    values = []
    for _ in range(count):
        values.append(str(x))
        x, y = (3*x + 4*y)/5, (-4*x + 3*y)/5
    return values


def fixtures():
    squares = [str(n*n) for n in range(9)]
    delayed = squares.copy()
    delayed[-1] = str(int(delayed[-1]) + 1)
    return {
        'squares': squares,
        'quarter_rotation': [str((0, 1, 0, -1)[n % 4]) for n in range(9)],
        'rational_rotation': rational_rotation(9),
        'constant_velocity_position': [str(7+3*n) for n in range(9)],
        'geometric': [str(2**n) for n in range(9)],
        'delayed_change': delayed,
    }


def collision(values, width):
    """A witness against deterministic next-output prediction from this window.

    Absence of a witness in finite data is not a sufficiency proof.
    """
    seen = {}
    for start in range(len(values)-width):
        key = tuple(values[start:start+width])
        next_value = values[start+width]
        if key in seen and seen[key][1] != next_value:
            return {'state': list(key), 'first_start': seen[key][0],
                    'second_start': start, 'next_outputs': [seen[key][1], next_value]}
        seen[key] = (start, next_value)
    return None


def mass(value):
    return {outcome_key(value): 1.0}


def ramanujan_terms(count):
    return [str(Fraction(math.factorial(4*k)*(1103+26390*k),
                math.factorial(k)**4 * 396**(4*k))) for k in range(count)]


def polynomial_product(*factors):
    coefficients = [Fraction(1)]
    for factor in factors:
        product = [Fraction(0)] * (len(coefficients) + len(factor) - 1)
        for i, a in enumerate(coefficients):
            for j, b in enumerate(factor):
                product[i+j] += a * b
        coefficients = product
    return coefficients


def ramanujan_ratio_identity(fit):
    """Check an identity against the factorial formula, independently of fitting.

    Does not prove Ramanujan's infinite sum or identify a law from samples alone.
    """
    degree = fit['degree']
    values = [Fraction(c) for c in fit['coefficients']]
    inferred_p = values[:degree+1]
    inferred_q = [Fraction(1)] + values[degree+1:]
    known_p = polynomial_product([4], [1, 4], [2, 4], [3, 4], [27493, 26390])
    known_q = polynomial_product([396**4], [1, 1], [1, 1], [1, 1], [1103, 26390])
    left = polynomial_product(inferred_p, known_q)
    right = polynomial_product(known_p, inferred_q)
    width = max(len(left), len(right))
    left += [Fraction(0)] * (width-len(left))
    right += [Fraction(0)] * (width-len(right))
    # Q_known has positive coefficients; its positive constant ensures Q_known(n)>0 for n>=0.
    return {'cross_product_identity': left == right,
            'known_denominator_positive_for_nonnegative_index':
                known_q[0] > 0 and all(c >= 0 for c in known_q),
            'inferred_denominator_positive_for_nonnegative_index':
                inferred_q[0] > 0 and all(c >= 0 for c in inferred_q),
            'claim': 'symbolic term-ratio identity against supplied factorial expression; not a proof of the pi sum'}


def replay_method(memory, journal, artifact_id, prefix, steps, context):
    """Retrieve and execute the exact persisted data-only recurrence artifact."""
    payload = memory.get(artifact_id)
    if payload['applicability']['context'] != context:
        raise ValueError('context mismatch')
    if memory.status(artifact_id, 'finite-fit:' + context) != 'qualified':
        raise ValueError('finite-fit scope is not qualified')
    scope = 'conditional-forecast:' + context
    if memory.status(artifact_id, scope) == 'invalidated':
        raise ValueError('counterexample has invalidated this forecasting context')
    fit = payload['body']
    journal.append('method_execution_attempt', {'artifact': artifact_id,
        'context': context, 'prefix': prefix, 'steps': steps})
    result = predict_recurrence(fit, prefix, steps=steps)
    journal.append('method_execution_result', {'artifact': artifact_id, 'result': result})
    return result


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    methods, reports = [], []
    with Journal(output / 'events.jsonl') as journal:
        journal.append('research_design', {
            'attribution': 'engineered constructor; analyst-authored exploratory fixtures',
            'training_samples': 6, 'future_samples': 3, 'max_order': 4,
            'selection': 'same constructor and limit for every exact sequence',
            'warning': 'known development fixtures, not blind or sealed evaluation',
            'endpoint_calls': 0})
        with ArtifactMemory(output / 'memory') as memory:
            for name, values in fixtures().items():
                prefix = values[:6]
                result = fit_recurrence(prefix, max_order=4)
                if result['status'] != 'unique' or not verify_recurrence(prefix, result):
                    raise AssertionError('expected independently verified finite fit')
                aid = memory.put({'kind': 'method', 'body': result, 'dependencies': [],
                    'provenance': {'producer': 'engineered exact recurrence constructor'},
                    'applicability': {'context': name,
                        'contract': 'finite fit; forecasts conditional on continuation assumption'}})
                evidence = journal.append('finite_fit_verified', {'artifact': aid,
                    'context': name, 'order': result['order'], 'sample_count': len(prefix)})
                memory.qualify(aid, 'finite-fit:' + name, 'exact-rational-certificate-check', [evidence])
                methods.append((name, aid))
        # A fresh store handle rehydrates every method; forecasts are executed by ID.
        with ArtifactMemory(output / 'memory') as memory:
            for name, aid in methods:
                values = fixtures()[name]  # authored observation instrument, outside fitter
                prefix = values[:6]
                fit = memory.get(aid)['body']
                predictions = replay_method(memory, journal, aid, prefix, 3, name)['values']
                tables = {str(i): {'constructed': mass(predictions[i-6]),
                                  'last_value': mass(prefix[-1])} for i in range(6, 9)}
                observed = []

                def measure(experiment):
                    index = int(experiment)
                    observed.append(values[index])
                    return values[index]

                inquiry = InquirySession({'constructed': 0, 'last_value': 0}, tables,
                    measure, journal, memory=memory, max_observations=3,
                    provenance={'source': 'authored sequence instrument', 'artifact': aid,
                                'likelihood': 'deterministic point masses, no noise model'})
                outcomes, revision = [], None
                for index in range(6, 9):
                    result = inquiry.step({'costs': {str(index): 1}, 'challenge': True})
                    outcomes.append(result)
                    if result['status'] == 'mismatch':
                        memory.invalidate(aid, 'committed forecast contradicted', [result['evidence']],
                                          scope='conditional-forecast:' + name)
                        # Keep ALL observations; no silent resetting to a convenient suffix.
                        revision = fit_recurrence(prefix + observed, max_order=4)
                        journal.append('revision_attempt', {'parent': aid, 'fit': revision,
                            'reason': 'counterexample; all observations retained',
                            'qualification': 'no independent future evidence for revision'})
                        break
                scope = 'conditional-forecast:' + name
                blocked = False
                if memory.status(aid, scope) == 'invalidated':
                    try:
                        replay_method(memory, journal, aid, prefix, 1, name)
                    except ValueError:
                        blocked = True
                    else:
                        raise AssertionError('invalidated forecast method reused')
                order = fit['order']
                state = {'coefficients': fit['coefficients'], 'tail': prefix[-order:]}
                reports.append({'name': name, 'artifact': aid, 'order': order,
                    'coefficients': fit['coefficients'], 'predictions': predictions,
                    'observations': observed,
                    'matches': sum(a == b for a, b in zip(predictions, observed)),
                    'inquiry_statuses': [x['status'] for x in outcomes],
                    'question_ids': [x['question_id'] for x in outcomes if x.get('question_id')],
                    'revision_status': None if revision is None else revision['status'],
                    'invalidated_reuse_blocked': blocked,
                    'predictive_state_json_bytes': len(canonical(state).encode()),
                    'finite_fit_with_evidence_json_bytes': len(canonical(fit).encode()),
                    'training_literal_json_bytes': len(canonical(prefix).encode())})

            # Use the built inquiry/question store on the actual historical research.
            # These hypotheses were already examined by the analyst: this is an
            # auditable re-execution, NOT a preregistered blind discovery result.
            sine = sine_module()
            history = sine.run_checks()
            historical_questions = []
            checks = {
                'constant_nearest': lambda: sine.coefficient_feasibility(sine._source_table())['coefficient_feasible'],
                'constant_floor': lambda: sine.coefficient_feasibility(sine._source_table(), 'floor')['coefficient_feasible'],
                'constant_ceil': lambda: sine.coefficient_feasibility(sine._source_table(), 'ceil')['coefficient_feasible'],
                'affine_nearest': lambda: sine.affine_nearest_feasibility(sine._source_table())['coefficient_feasible'],
                'single_radius': lambda: not sine.single_radius_radical_certificate(sine._source_table())['full_model_rejected'],
            }
            for name, callback in checks.items():
                inquiry = InquirySession({name: 0}, {'exact_check': {name: mass(True)}},
                    lambda _, callback=callback: callback(), journal, memory=memory,
                    max_observations=1, provenance={'source': 'transcribed sine table',
                        'interpretation': 'retrospective test of an explicit modern hypothesis'})
                result = inquiry.step({'costs': {'exact_check': 1}, 'challenge': True})
                historical_questions.append({'hypothesis': name, 'status': result['status'],
                    'question_id': result['question_id']})

            # A family boundary: Ramanujan's 1914 series terms have a variable
            # rational ratio. Test, do not smuggle this ratio into the fitter.
            terms = ramanujan_terms(12)
            outside = {}
            for name, values in {
                'rounded_historical_sines': [str(x) for x in sine._cumulative(sine._source_table())],
                'ramanujan_1914_series_terms': terms,
            }.items():
                fit = fit_recurrence(values, max_order=4)
                if not verify_recurrence(values, fit):
                    raise AssertionError('negative fit certificate rejected')
                outside[name] = fit
                journal.append('model_class_boundary', {'case': name, 'fit': fit})

            ratio = fit_ratio(terms, max_degree=4)
            if ratio['status'] != 'unique' or not verify_ratio(terms, ratio):
                raise AssertionError('rational-ratio extension did not produce a checked finite fit')
            ratio_id = memory.put({'kind': 'method', 'body': ratio, 'dependencies': [],
                'provenance': {'producer': 'engineered rational-ratio constructor',
                    'family_selection': 'analyst expanded language after constant-order failure'},
                'applicability': {'context': 'ramanujan-series',
                    'contract': 'finite-data ratio fit; conditional indexed prediction'}})
            journal.append('language_expansion', {'artifact': ratio_id,
                'previous_class': 'constant recurrence order <= 4',
                'new_class': 'Q(n)x[n+1]=P(n)x[n], Q(0)=1, degree <=4',
                'selected_by': 'development analyst, not evaluated model'})

        with ArtifactMemory(output / 'memory') as memory:
            ratio = memory.get(ratio_id)['body']
            journal.append('method_execution_attempt', {'artifact': ratio_id,
                'context': 'ramanujan-series', 'start_index': 11, 'steps': 3})
            forecast = predict_ratio(ratio, 11, ratio['training_values'][-1], steps=3)
            journal.append('method_execution_result', {'artifact': ratio_id, 'result': forecast})
            tables = {str(i): {'ratio': mass(forecast['values'][i-12])} for i in range(12, 15)}
            inquiry = InquirySession({'ratio': 0}, tables,
                lambda i: ramanujan_terms(int(i)+1)[-1], journal, memory=memory,
                max_observations=3, provenance={'source': 'authored factorial expression',
                    'artifact': ratio_id, 'warning': 'known research example, not a blind holdout'})
            updates = [inquiry.step({'costs': {str(i): 1}, 'challenge': True}) for i in range(12, 15)]
            identity = ramanujan_ratio_identity(ratio)
            if not (identity['cross_product_identity'] and
                    identity['inferred_denominator_positive_for_nonnegative_index']):
                raise AssertionError('inferred ratio failed independent symbolic identity')
            ratio_report = {'artifact': ratio_id, 'degree': ratio['degree'],
                'training_terms': 12, 'subsequent_terms': 3,
                'matches': sum(row['outcome'] == forecast['values'][j] for j, row in enumerate(updates)),
                'inquiry_statuses': [row['status'] for row in updates],
                'symbolic_identity_check': identity,
                'attribution': 'data-fitted coefficients within an analyst-selected language'}
            journal.append('ratio_research_result', ratio_report)

        scalar = collision(fixtures()['quarter_rotation'], 1)
        pair = collision(fixtures()['quarter_rotation'], 2)
        if scalar is None or pair is not None:
            raise AssertionError('state-aliasing control failed')
        source_paths = [Path(__file__), ROOT / 'kuttaka_runtime/recurrence.py', SINE_PATH,
                        ROOT / 'docs/research/aryabhata/reconstruct.py',
                        ROOT / 'kuttaka_runtime/rational_ratio.py']
        result = {'attribution': 'ENGINEERED; exploratory first-hand use of existing runtime',
            'endpoint_calls': 0, 'independent_field_transfer': False,
            'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in source_paths},
            'cases': reports, 'historical_checks': history,
            'historical_inquiries': historical_questions,
            'no_fit_within_constant_order_4': outside,
            'rational_ratio_language_expansion': ratio_report,
            'state_aliasing': {'scalar_counterexample': scalar,
                'two_value_counterexample_in_this_sample': pair,
                'claim': 'scalar witness is conclusive for this state map; absence is not proof'},
            'memory_caveat': 'working state excludes cold evidence, code and metadata; rational bit lengths grow'}
        journal.append('research_summary', result)
    result['artifact_store_bytes'] = sum(p.stat().st_size for p in (output/'memory').rglob('*') if p.is_file())
    (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({'cases': [{k: row[k] for k in ('name', 'order', 'matches', 'revision_status')}
                               for row in result['cases']],
                      'historical_inquiries': result['historical_inquiries'],
                      'endpoint_calls': result['endpoint_calls']}, indent=2))
