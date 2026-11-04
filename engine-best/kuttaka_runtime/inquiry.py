"""Evidence-driven inquiry over supplied candidates, including explicit surprises.

This is an engineered finite inquiry policy. Candidate generation, measurement
semantics, noise calibration and relevance judgments remain external inputs.
"""

from dataclasses import asdict
import json
import math

from .beliefs import FiniteBelief
from .store import canonical


def _definition(belief):
    return {
        'description_lengths': dict(belief.description_lengths),
        'likelihoods': {x: {h: dict(row) for h, row in table.items()}
                        for x, table in belief.likelihoods.items()},
    }


def _state(belief):
    probabilities = belief.posterior()
    logs = belief.log_posterior()
    return {'posterior': None if probabilities is None else dict(probabilities),
            # Null means impossible; a finite log can represent an underflowed p.
            'log_posterior': None if logs is None else {
                h: None if value == -math.inf else value for h, value in logs.items()},
            'inconsistent': belief.inconsistent}


class InquirySession:
    """Precommit, observe, update, and preserve a question on model mismatch.

    ``observe`` is a trusted, bounded callback accepting an experiment ID and
    returning a JSON outcome. ``journal`` is a Journal-compatible evidence sink.
    ``memory`` optionally persists question artifacts. Each experiment ID is
    attempted at most once. All likelihoods and length penalties are supplied.
    ``step`` can be exposed as a callable tool to the existing investigate loop.
    Its inner measurement ceiling is additional to the outer tool-call ceiling.
    """

    def __init__(self, lengths, likelihoods, observe, journal, *, memory=None,
                 max_observations=16, provenance):
        if type(max_observations) is not int or max_observations < 0:
            raise ValueError('max_observations must be a nonnegative integer')
        self.belief = FiniteBelief(lengths, likelihoods)
        self.observe_callback = observe
        self.journal = journal
        self.memory = memory
        self.max_observations = max_observations
        self.attempts = 0
        self._attempted = set()
        self._provenance = json.loads(canonical(provenance))
        self.definition_hash = journal.append('hypothesis_definition', {
            **_definition(self.belief), 'provenance': self._provenance,
            'interpretation': 'conditional finite model; supplied likelihoods',
            'max_observations': max_observations})

    def step(self, request):
        """Input: {costs: {experiment_id: positive estimate}, challenge: bool}.

        Select an informative measurement. If challenge=true and no available
        experiment distinguishes candidates, try an unobserved experiment at
        the lowest supplied cost (ID breaks ties). Such a challenge can expose
        misspecification even when all currently surviving candidates agree.
        Returns status, observation, conditional belief, and any mismatch question.
        """
        if (not isinstance(request, dict) or set(request) != {'costs', 'challenge'}
                or type(request['challenge']) is not bool or not isinstance(request['costs'], dict)):
            raise ValueError('costs and literal challenge bool required')
        costs = dict(request['costs'])
        for experiment, cost in costs.items():
            if experiment not in self.belief.likelihoods:
                raise ValueError('unknown experiment')
            if (isinstance(cost, bool) or not isinstance(cost, (int, float))
                    or not math.isfinite(cost) or cost <= 0):
                raise ValueError('costs must be finite positive estimates')
        if self.belief.inconsistent:
            return {'status': 'needs_revision', **_state(self.belief)}
        if self.attempts >= self.max_observations:
            return {'status': 'budget_exhausted', **_state(self.belief)}
        available = {x: cost for x, cost in costs.items() if x not in self._attempted}
        choice = self.belief.choose_experiment(available)
        mode = 'information_gain'
        if choice:
            experiment = choice[0]
        elif request['challenge'] and available:
            experiment = min(available, key=lambda x: (available[x], x))
            mode = 'agreement_challenge'
        else:
            return {'status': 'no_discriminator', **_state(self.belief)}
        predicted = {h: dict(row) for h, row in self.belief.predict(experiment).items()}
        proposal = self.journal.append('inquiry_proposal', {
            'definition': self.definition_hash, 'experiment': experiment,
            'predictions': predicted, 'mode': mode, 'supplied_cost': available[experiment],
            'scores': {x: asdict(s) for x, s in self.belief.last_scores.items()},
            **_state(self.belief)})
        self.attempts += 1
        self._attempted.add(experiment)
        self.journal.append('measurement_attempt', {'proposal': proposal, 'number': self.attempts})
        try:
            observed = json.loads(canonical(self.observe_callback(experiment)))
        except Exception as error:
            self.journal.append('measurement_error', {'proposal': proposal, 'type': type(error).__name__})
            return {'status': 'measurement_error', 'experiment': experiment, **_state(self.belief)}
        evidence = self.journal.append('measurement', {
            'proposal': proposal, 'experiment': experiment, 'outcome': observed})
        update = self.belief.observe(experiment, observed)
        question = None
        if update.status == 'mismatch':
            question = {
                'kind': 'question', 'dependencies': [],
                'body': {
                    'question': 'Which assumption, representation, or measurement contract excludes this observation?',
                    'experiment': experiment, 'observed': observed, 'predictions': predicted,
                    'definition': self.definition_hash,
                    'generation': 'engineered mismatch template; no causal diagnosis yet',
                },
                'provenance': {'evidence': evidence, 'proposal': proposal},
                'applicability': {'purpose': 'investigate this discrepancy; not an established explanation'},
            }
        question_id = self.memory.put(question) if question is not None and self.memory is not None else None
        result = {'status': update.status, 'experiment': experiment, 'mode': mode,
                  'outcome': observed, 'evidence': evidence, 'question': question,
                  'question_id': question_id, **_state(self.belief)}
        self.journal.append('belief_update', result)
        return result

    def revise(self, lengths, likelihoods, *, provenance):
        """Propose replacement models and replay ALL evidence, including conflicts.

        The supplied replacement can be fitted to old evidence. Its replay fit
        is explicitly retrospective and is not independent validation.
        """
        candidate = FiniteBelief(lengths, likelihoods)
        for event in self.belief.evidence:
            if event.experiment not in candidate.likelihoods:
                raise ValueError('revision must account for every historical observation')
        proposal = self.journal.append('hypothesis_revision', {
            'previous_definition': self.definition_hash, **_definition(candidate),
            'provenance': json.loads(canonical(provenance)),
            'qualification': 'retrospective fit only; fresh challenge still required'})
        revised, update = self.belief.revise(lengths, likelihoods)
        self.belief = revised
        self.definition_hash = proposal
        self.journal.append('revision_replay', {
            'definition': proposal, 'evidence_count': len(revised.evidence),
            'status': update.status, **_state(revised)})
        return {'status': update.status, **_state(revised)}
