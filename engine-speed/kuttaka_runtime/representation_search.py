"""Forward-only investigation over a declared polynomial/shift language.

An engineered controller generates representation sizes, fits parameters, gathers
fresh evidence and retracts failed forecasts. It does not invent its grammar,
measurement contract, noise model, or objective. Conditional support is not truth.
"""
from collections import Counter
import hashlib
from pathlib import Path
import uuid

from .beliefs import outcome_key
from .inquiry import InquirySession
from .recurrence import _parse_fraction
from .shift_relation import fit_shift_relation, predict_shift_relation, verify_shift_relation
from .store import canonical, digest


class RepresentationSearch:
    """Select lag and index dependence without receiving a task-family label.

    ``observe_next`` is a trusted bounded callback with no arguments. Only the
    next scalar exact-rational observation is allowed. The fixed index origin is
    zero; every observation is retained. StopIteration signals end of input.
    No clean-resume implementation is claimed.
    Distinct runs require distinct journals, even if their context labels match.
    """

    def __init__(self, memory, journal, observe_next, *, context,
                 max_observations=16, max_fits=128, max_order=4, max_degree=4,
                 max_columns=12, min_fresh=2, allowed_pairs=None):
        limits = [(max_observations, 0, 64), (max_fits, 0, 1024),
                  (max_order, 1, 4), (max_degree, 0, 4),
                  (max_columns, 2, 25), (min_fresh, 1, 64)]
        if any(type(v) is not int or not lo <= v <= hi for v, lo, hi in limits):
            raise ValueError('search limits outside bounded contract')
        if not callable(observe_next) or not isinstance(context, str) or not context or len(context) > 1024:
            raise ValueError('bounded observer and nonempty context required')
        pairs = [(r, d) for r in range(1, max_order+1) for d in range(max_degree+1)
                 if (r+1)*(d+1) <= max_columns]
        if allowed_pairs is not None:
            if (not isinstance(allowed_pairs, list) or not allowed_pairs or
                    any(not isinstance(p, (tuple, list)) or len(p) != 2 or
                        any(type(x) is not int for x in p) or tuple(p) not in pairs for p in allowed_pairs)):
                raise ValueError('allowed_pairs must be a nonempty subset of the declared grammar')
            pairs = sorted(set(tuple(p) for p in allowed_pairs))
        # Dimensions are a declared exploration preference, not a learned prior.
        pairs.sort(key=lambda p: ((p[0]+1)*(p[1]+1), p[0], p[1]))
        self.memory, self.journal, self.observe_next = memory, journal, observe_next
        self.context = context
        self.max_observations, self.max_fits, self.min_fresh = max_observations, max_fits, min_fresh
        self.history, self.decisions = [], []
        self.families = {p: {'status': 'pending', 'last_fit_size': -1} for p in pairs}
        self.candidates, self.programs = {}, {}
        self.fit_calls = self.forecast_attempts = self.measurement_attempts = 0
        self.certificate_json_bytes = 0
        self.fit_status_counts = Counter()
        self.status = 'running'
        self.run_id = uuid.uuid4().hex
        self.scope = 'conditional-stream:' + digest({'context': context, 'run_id': self.run_id})
        self.source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.start_evidence = journal.append('representation_search_start', {
            'context': context, 'run_id': self.run_id, 'scope': self.scope,
            'pairs': [list(p) for p in pairs],
            'max_observations': max_observations, 'max_fits': max_fits,
            'max_columns': max_columns, 'min_fresh': min_fresh,
            'source_sha256': self.source_sha256,
            'policy': 'increasing parameter count; retain all unique predictive fits; forward-only challenge',
            'attribution': 'engineered bounded grammar/controller; no evaluated-model invention',
            'observation_contract': 'trusted next scalar, exact rational, origin zero; no noise model'})

    def _construct(self):
        count = len(self.history)
        if not count:
            return
        for (order, degree), family in self.families.items():
            if family['status'] in ('unique', 'no_fit', 'non_predictive') or family['last_fit_size'] == count:
                continue
            columns = (order+1)*(degree+1)
            # More than one null direction is unavoidable below this bound.
            # It is a data requirement, not empirical evidence against a family.
            if count-order < columns-1:
                family['status'] = 'needs_data'
                continue
            if self.fit_calls >= self.max_fits:
                family['status'] = 'budget_exhausted'
                continue
            self.fit_calls += 1
            family['last_fit_size'] = count
            self.journal.append('representation_fit_attempt', {
                'order': order, 'degree': degree, 'samples': count, 'number': self.fit_calls})
            try:
                result = fit_shift_relation(self.history, order, degree, start_index=0)
                if not verify_shift_relation(self.history, result):
                    raise ValueError('constructor certificate did not verify')
            except (ValueError, ArithmeticError) as error:
                family['status'] = 'incomplete'
                self.fit_status_counts['incomplete'] += 1
                self.journal.append('representation_fit_incomplete', {
                    'order': order, 'degree': degree, 'error_type': type(error).__name__})
                continue
            self.fit_status_counts[result['status']] += 1
            self.certificate_json_bytes += len(canonical(result).encode())
            family['status'] = result['status']
            predictive = result['status'] == 'unique' and result['predictive']
            program = digest({'coefficients': result['coefficients'], 'origin': 0}) if predictive else None
            previous = self.programs.get(program)
            payload = {'kind': 'method' if predictive else 'representation',
                'body': result, 'dependencies': [],
                'provenance': {'constructor': 'polynomial-shift exact fitter', 'controller': self.source_sha256},
                'applicability': {'context': self.context, 'contract': 'finite-data polynomial-shift relation'}}
            if previous and self.candidates[previous]['blocked']:
                payload['provenance']['seed_refinement_from'] = previous
            aid = self.memory.put(payload)
            evidence = self.journal.append('representation_candidate_fit', {
                'artifact': aid, 'order': order, 'degree': degree, 'samples': count,
                'status': result['status'], 'predictive': predictive})
            family['certificate'] = aid
            if not predictive:
                if result['status'] == 'unique':
                    family['status'] = 'non_predictive'
                continue
            if previous and not self.candidates[previous]['blocked'] and not self.candidates[previous]['failed']:
                aid = previous
            else:
                self.programs[program] = aid
                self.candidates[aid] = {'hits': 0, 'failed': False, 'blocked': False, 'evidence': [],
                    'order': order, 'degree': degree, 'created_at': count,
                    'program_bytes': len(canonical(result['coefficients']).encode())}
                self.memory.qualify(aid, 'finite-fit:' + self.scope, 'exact-shift-certificate', [evidence])
            family['artifact'] = aid

    def _forecast(self):
        predictions, blocked = {}, []
        for aid, candidate in self.candidates.items():
            if candidate['failed'] or candidate['blocked'] or self.memory.status(aid, self.scope) == 'invalidated':
                continue
            self.forecast_attempts += 1
            self.journal.append('representation_forecast_attempt', {
                'artifact': aid, 'index': len(self.history), 'number': self.forecast_attempts})
            try:
                payload = self.memory.get(aid)
                if (payload['applicability']['context'] != self.context or
                        self.memory.status(aid, 'finite-fit:' + self.scope) != 'qualified'):
                    raise ValueError('method binding or qualification changed')
                value = predict_shift_relation(payload['body'], self.history,
                                               start_index=0, steps=1)['values'][0]
            except (ValueError, ArithmeticError) as error:
                blocked.append(aid)
                self.journal.append('representation_forecast_blocked', {
                    'artifact': aid, 'index': len(self.history), 'error_type': type(error).__name__,
                    'interpretation': 'cannot forecast; not a falsified equation'})
                continue
            predictions[aid] = value
            self.journal.append('representation_forecast', {
                'artifact': aid, 'index': len(self.history), 'prediction': value})
        return predictions, blocked

    def step(self):
        if self.status != 'running':
            return {'status': self.status}
        if self.measurement_attempts >= self.max_observations:
            self.status = 'observation_limit'
            return {'status': self.status}
        self._construct()
        predictions, blocked = self._forecast()
        index = len(self.history)
        eligible = [aid for aid in predictions if self.candidates[aid]['hits'] >= self.min_fresh]
        distinct = set(predictions.values())
        if blocked:
            mode, value = 'prediction_blocked', None
        elif len(distinct) > 1:
            mode, value = 'ambiguous', None
        elif distinct and eligible:
            mode, value = 'conditional_agreement', next(iter(distinct))
        elif distinct:
            mode, value = 'needs_fresh_validation', None
        else:
            mode, value = 'needs_representation', None
        decision = {'index': index, 'mode': mode, 'value': value,
                    'candidate_ids': sorted(predictions), 'predictions': predictions,
                    'blocked_ids': blocked, 'eligible_ids': sorted(eligible)}
        self.journal.append('representation_search_decision', decision)
        self.measurement_attempts += 1

        input_exhausted = False

        def observe(_):
            nonlocal input_exhausted
            # No index, name, generator or future sample enters the callback.
            try:
                return str(_parse_fraction(self.observe_next()))
            except StopIteration:
                input_exhausted = True
                raise

        if predictions:
            lengths = {aid: self.candidates[aid]['program_bytes'] * 8 for aid in predictions}
            table = {'next': {aid: {outcome_key(v): 1.0} for aid, v in predictions.items()}}
            inquiry = InquirySession(lengths, table, observe, self.journal,
                memory=self.memory, max_observations=1,
                provenance={'context': self.context, 'index': index,
                    'interpretation': 'fresh one-step challenge; finite candidate pool only'})
            outcome = inquiry.step({'costs': {'next': 1}, 'challenge': True})
            if outcome['status'] == 'measurement_error':
                self.status = 'input_exhausted' if input_exhausted else 'measurement_error'
                self.journal.append('representation_search_stopped', {'status': self.status, 'index': index})
                return {'status': self.status, 'decision': decision}
            actual, evidence = outcome['outcome'], outcome['evidence']
        else:
            proposal = self.journal.append('representation_observation_attempt', {'index': index})
            try:
                actual = observe('next')
            except Exception as error:
                self.journal.append('representation_observation_error', {
                    'proposal': proposal, 'type': type(error).__name__})
                self.status = 'input_exhausted' if input_exhausted else 'measurement_error'
                self.journal.append('representation_search_stopped', {'status': self.status, 'index': index})
                return {'status': self.status, 'decision': decision}
            evidence = self.journal.append('representation_observation', {
                'proposal': proposal, 'index': index, 'value': actual})
        self.history.append(actual)
        for aid, predicted in predictions.items():
            candidate = self.candidates[aid]
            if predicted == actual:
                candidate['hits'] += 1
                candidate['evidence'].append(evidence)
                if candidate['hits'] == self.min_fresh:
                    self.memory.qualify(aid, self.scope, 'fresh-sequential-agreement-only', candidate['evidence'])
            else:
                candidate['failed'] = True
                self.memory.invalidate(aid, 'prospectively committed forecast contradicted', [evidence], scope=self.scope)
                qid = self.memory.put({'kind': 'question', 'dependencies': [],
                    'body': {'question': 'What representation or observation assumption must change?',
                             'artifact': aid, 'index': index, 'predicted': predicted, 'observed': actual},
                    'provenance': {'counterevidence': evidence},
                    'applicability': {'context': self.context, 'claim': 'diagnostic, not an explanation'}})
                self.journal.append('representation_counterexample', {'artifact': aid, 'question': qid,
                    'evidence': evidence, 'history_preserved': len(self.history)})
                for family in self.families.values():
                    if family.get('artifact') == aid:
                        family['status'] = 'pending'
        # A leading root blocks extrapolation but does not falsify a relation.
        # Recheck its admissibility against the newly observed transition.
        for aid in blocked:
            self.candidates[aid]['blocked'] = True
            for family in self.families.values():
                if family.get('artifact') == aid:
                    family['status'] = 'pending'
        decision = {**decision, 'observed': actual,
                    'correct': None if value is None else value == actual}
        self.decisions.append(decision)
        self.journal.append('representation_search_outcome', decision)
        return decision

    def report(self):
        issued = [d for d in self.decisions if d['value'] is not None]
        family_states = {f['status'] for f in self.families.values()}
        active = [c for c in self.candidates.values() if not c['failed'] and not c['blocked']]
        if family_states & {'incomplete', 'budget_exhausted'}:
            representation_status = 'search_incomplete'
        elif family_states <= {'no_fit', 'non_predictive'}:
            representation_status = 'no_executable_relation_in_searched_families'
        elif any(c['hits'] >= self.min_fresh for c in active):
            representation_status = 'has_conditionally_supported_candidates'
        elif active:
            representation_status = 'needs_fresh_validation'
        else:
            representation_status = 'needs_more_data_or_different_language'
        return {'status': self.status, 'observations': len(self.history),
            'representation_status': representation_status,
            'measurement_attempts': self.measurement_attempts,
            'fit_calls': self.fit_calls, 'forecast_attempts': self.forecast_attempts,
            'certificate_json_bytes': self.certificate_json_bytes,
            'coefficient_json_bytes': sum(c['program_bytes'] for c in self.candidates.values()),
            'complexity_proxy': 'coefficient JSON bytes only; not full description length or calibrated confidence',
            'min_fresh': self.min_fresh,
            'scope': self.scope,
            'issued_predictions': len(issued), 'correct_issued': sum(d['correct'] for d in issued),
            'wrong_issued': sum(not d['correct'] for d in issued),
            'abstentions': len(self.decisions)-len(issued), 'candidate_count': len(self.candidates),
            'rejections': sum(f['status'] == 'no_fit' for f in self.families.values()),
            'fit_status_counts': dict(self.fit_status_counts), 'history': list(self.history),
            'decisions': list(self.decisions),
            'families': [{'order': p[0], 'degree': p[1], **f} for p, f in self.families.items()],
            'candidate_support': {aid: dict(c) for aid, c in self.candidates.items()},
            'interpretation': 'conditional forecasts in a bounded engineered language; no general-law claim'}

    def run(self):
        while self.status == 'running':
            self.step()
        # Use already observed evidence to finish the bounded fit work; no new
        # observation or prospective credit is created at this boundary.
        self._construct()
        result = self.report()
        self.journal.append('representation_search_summary', result)
        return result
