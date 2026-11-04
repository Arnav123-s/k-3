"""Finite Bayesian hypothesis tracking over supplied likelihood tables.

This module evaluates declarative probability tables only. It does not execute
candidate source code, infer a noise model, or decide whether evidence is true.
Experiment costs are supplied by the caller and are not measured runtime costs.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .store import canonical


_LOG2 = math.log(2.0)
_NORMALIZATION_TOLERANCE = 1e-12
_INFORMATION_TOLERANCE_BITS = 1e-12


def outcome_key(value: object) -> str:
    """Return the canonical JSON key used for an observation in a table."""
    return canonical(value)


def _normalize_log_weights(values: Mapping[str, float]) -> dict[str, float] | None:
    """Normalize in a centered coordinate system to preserve small log terms."""
    peak = max(values.values())
    if peak == -math.inf:
        return None
    centered = {key: value - peak for key, value in values.items()}
    log_normalizer = math.log(math.fsum(math.exp(value) for value in centered.values()))
    return {key: value - log_normalizer for key, value in centered.items()}


def _probability(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{where} must be a finite probability')
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise ValueError(f'{where} must be a finite probability in [0, 1]')
    return result


def _validate_outcome_key(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f'{where} must be a canonical JSON outcome key')
    try:
        decoded = json.loads(value)
        if canonical(decoded) != value:
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError(f'{where} must be a canonical JSON outcome key') from None
    return value


@dataclass(frozen=True)
class Evidence:
    experiment: str
    outcome: str
    status: str


@dataclass(frozen=True)
class Update:
    status: str
    posterior: Mapping[str, float] | None
    log_posterior: Mapping[str, float] | None
    evidence_index: int
    reason: str = ''


@dataclass(frozen=True)
class ExperimentScore:
    information_gain_bits: float
    supplied_cost: float
    gain_per_cost: float


class FiniteBelief:
    """Bayesian belief over a finite, caller-declared set of hypotheses.

    ``description_lengths`` maps immutable hypothesis IDs to finite nonnegative
    lengths in bits. Their prior is proportional to ``2**(-length)``.
    ``likelihoods`` has shape ``experiment -> hypothesis -> outcome_key -> p``;
    every candidate row must be a complete normalized distribution. Outcomes
    are keys returned by :func:`outcome_key`. A point mass models exact evidence;
    positive noisy likelihoods remain positive in log space and are never hard
    eliminated by an observation.
    """

    def __init__(self, description_lengths: Mapping[str, float],
                 likelihoods: Mapping[str, Mapping[str, Mapping[str, float]]]):
        if not isinstance(description_lengths, Mapping) or not description_lengths:
            raise ValueError('at least one hypothesis description length is required')
        lengths: dict[str, float] = {}
        for hypothesis, raw_length in description_lengths.items():
            if not isinstance(hypothesis, str) or not hypothesis:
                raise ValueError('hypothesis IDs must be nonempty strings')
            if isinstance(raw_length, bool) or not isinstance(raw_length, (int, float)):
                raise ValueError('description lengths must be finite nonnegative bits')
            length = float(raw_length)
            if not math.isfinite(length) or length < 0:
                raise ValueError('description lengths must be finite nonnegative bits')
            lengths[hypothesis] = length
        if not isinstance(likelihoods, Mapping) or not likelihoods:
            raise ValueError('at least one experiment likelihood matrix is required')

        frozen_tables = {}
        for experiment, matrix in likelihoods.items():
            if not isinstance(experiment, str) or not experiment:
                raise ValueError('experiment IDs must be nonempty strings')
            if not isinstance(matrix, Mapping) or set(matrix) != set(lengths):
                raise ValueError(f'{experiment}: matrix must include every candidate exactly once')
            frozen_rows = {}
            for hypothesis in lengths:
                row = matrix[hypothesis]
                if not isinstance(row, Mapping) or not row:
                    raise ValueError(f'{experiment}/{hypothesis}: nonempty outcome distribution required')
                probabilities = {}
                for raw_key, raw_probability in row.items():
                    key = _validate_outcome_key(raw_key, f'{experiment}/{hypothesis} outcome')
                    if key in probabilities:
                        raise ValueError(f'{experiment}/{hypothesis}: duplicate outcome key')
                    probabilities[key] = _probability(raw_probability, f'{experiment}/{hypothesis} probability')
                total = math.fsum(probabilities.values())
                if not math.isfinite(total) or abs(total - 1.0) > _NORMALIZATION_TOLERANCE:
                    raise ValueError(f'{experiment}/{hypothesis}: outcome probabilities must sum to 1')
                frozen_rows[hypothesis] = MappingProxyType(probabilities)
            frozen_tables[experiment] = MappingProxyType(frozen_rows)

        self.description_lengths = MappingProxyType(lengths)
        self.likelihoods = MappingProxyType(frozen_tables)
        self._hypotheses = tuple(lengths)
        raw_log_weights = {hypothesis: -length * _LOG2
                           for hypothesis, length in lengths.items()}
        self._log_weights = _normalize_log_weights(raw_log_weights)
        self._evidence: list[Evidence] = []
        self._inconsistent = False
        self._last_scores: Mapping[str, ExperimentScore] = MappingProxyType({})

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(self._evidence)

    @property
    def inconsistent(self) -> bool:
        return self._inconsistent

    @property
    def last_scores(self) -> Mapping[str, ExperimentScore]:
        """Information-gain details from the latest selection attempt."""
        return self._last_scores

    def _probabilities(self, log_weights: Mapping[str, float] | None = None) -> dict[str, float] | None:
        if self._inconsistent:
            return None
        weights = self._log_weights if log_weights is None else log_weights
        return {hypothesis: math.exp(weights[hypothesis]) for hypothesis in self._hypotheses}

    def posterior(self) -> Mapping[str, float] | None:
        """Return normalized posterior probabilities, or None after a conflict."""
        values = self._probabilities()
        return None if values is None else MappingProxyType(values)

    def log_posterior(self) -> Mapping[str, float] | None:
        """Return normalized log probabilities, preserving tiny probabilities."""
        if self._inconsistent:
            return None
        return MappingProxyType(dict(self._log_weights))

    def last_compatible_posterior(self) -> tuple[Mapping[str, float], Mapping[str, float]]:
        """Return the last compatible distribution even after a conflict.

        Once inconsistent, this is conditional only on accepted evidence before
        the conflict; it is not a posterior for the complete evidence log.
        """
        probabilities = {h: math.exp(self._log_weights[h]) for h in self._hypotheses}
        return MappingProxyType(probabilities), MappingProxyType(dict(self._log_weights))

    def predict(self, experiment: str) -> Mapping[str, Mapping[str, float]]:
        """Return the full per-hypothesis likelihood table to precommit."""
        try:
            return self.likelihoods[experiment]
        except KeyError:
            raise KeyError(f'unknown experiment: {experiment}') from None

    def observe(self, experiment: str, outcome: object) -> Update:
        """Update from one observation; conflicts never fabricate a posterior."""
        if experiment not in self.likelihoods:
            raise KeyError(f'unknown experiment: {experiment}')
        if any(item.experiment == experiment for item in self._evidence):
            raise ValueError('experiment ID already observed; use a new ID for an independent draw')
        key = outcome_key(outcome)
        if self._inconsistent:
            self._evidence.append(Evidence(experiment, key, 'conflict'))
            return Update('mismatch', None, None, len(self._evidence) - 1,
                          'belief already has conflicting evidence; explicit revision required')

        likelihood = {h: self.likelihoods[experiment][h].get(key, 0.0)
                      for h in self._hypotheses}
        log_likelihood = {h: (math.log(p) if p > 0.0 else -math.inf)
                          for h, p in likelihood.items()}
        unnormalized = {h: self._log_weights[h] + log_likelihood[h]
                        for h in self._hypotheses}
        normalized = _normalize_log_weights(unnormalized)
        if normalized is None:
            self._evidence.append(Evidence(experiment, key, 'conflict'))
            self._inconsistent = True
            return Update('mismatch', None, None, len(self._evidence) - 1,
                          'observation has zero likelihood under every declared candidate')

        self._log_weights = normalized
        self._evidence.append(Evidence(experiment, key, 'accepted'))
        return Update('updated', MappingProxyType(self._probabilities() or {}),
                      MappingProxyType(dict(self._log_weights)), len(self._evidence) - 1)

    def choose_experiment(self, costs: Mapping[str, float]) -> tuple[str, float] | None:
        """Choose maximal expected information gain (bits) / supplied cost.

        Costs must be finite positive caller estimates. This is not a claim about
        actual tool, model, or wall-clock cost. Returns None when all available
        experiments are equivalent or information gain is numerically negligible.
        """
        if self._inconsistent:
            self._last_scores = MappingProxyType({})
            return None
        if not isinstance(costs, Mapping):
            raise ValueError('costs must map experiment IDs to positive finite values')
        current = dict(self._log_weights)
        best: tuple[str, float] | None = None
        scores: dict[str, ExperimentScore] = {}
        self._last_scores = MappingProxyType({})
        for experiment, raw_cost in costs.items():
            if experiment not in self.likelihoods:
                raise ValueError(f'cost supplied for unknown experiment: {experiment}')
            if any(item.experiment == experiment for item in self._evidence):
                continue
            if isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float)):
                raise ValueError('experiment costs must be positive finite numbers')
            cost = float(raw_cost)
            if not math.isfinite(cost) or cost <= 0:
                raise ValueError('experiment costs must be positive finite numbers')
            table = self.likelihoods[experiment]
            outcomes = sorted({key for row in table.values() for key in row})
            expected_entropy = 0.0
            for key in outcomes:
                log_joint = {h: current[h] + math.log(table[h].get(key, 0.0))
                             if table[h].get(key, 0.0) > 0.0 else -math.inf
                             for h in self._hypotheses}
                conditional = _normalize_log_weights(log_joint)
                if conditional is None:
                    continue
                peak = max(log_joint.values())
                centered_log_mass = math.log(math.fsum(
                    math.exp(value - peak) for value in log_joint.values() if value != -math.inf))
                # Compute P(outcome) in ordinary space. Underflow means this
                # branch contributes below representable precision to expected gain.
                p_observation = math.exp(peak) * math.exp(centered_log_mass)
                entropy = -math.fsum(math.exp(value) * value / _LOG2
                                     for value in conditional.values() if value != -math.inf)
                expected_entropy += p_observation * entropy
            prior_entropy = -math.fsum(math.exp(value) * value / _LOG2
                                       for value in current.values() if value != -math.inf)
            gain = max(0.0, prior_entropy - expected_entropy)
            if gain <= _INFORMATION_TOLERANCE_BITS:
                continue
            score = gain / cost
            if not math.isfinite(score):
                raise ValueError('supplied cost is too small for a finite information-gain score')
            scores[experiment] = ExperimentScore(gain, cost, score)
            if best is None or score > best[1]:
                best = (experiment, score)
        self._last_scores = MappingProxyType(scores)
        return best

    def revise(self, description_lengths: Mapping[str, float],
               likelihoods: Mapping[str, Mapping[str, Mapping[str, float]]]) -> tuple['FiniteBelief', Update]:
        """Create a new candidate set and replay all evidence under supplied tables.

        This is an explicit model revision, not a silent reset. The replacement
        matrices must give predictions for every historical experiment. If any
        historical observation is unsupported by all replacement candidates,
        the returned belief is marked inconsistent and has no posterior.
        """
        revised = FiniteBelief(description_lengths, likelihoods)
        last_update = Update('updated', revised.posterior(), revised.log_posterior(), -1)
        for evidence in self._evidence:
            if evidence.experiment not in revised.likelihoods:
                raise ValueError(f'revision lacks historical experiment: {evidence.experiment}')
            last_update = revised._observe_key(evidence.experiment, evidence.outcome)
        return revised, last_update

    def _observe_key(self, experiment: str, key: str) -> Update:
        """Replay helper; key is already canonical and from recorded evidence."""
        if self._inconsistent:
            self._evidence.append(Evidence(experiment, key, 'conflict'))
            return Update('mismatch', None, None, len(self._evidence) - 1,
                          'replay already has conflicting evidence')
        likelihood = {h: self.likelihoods[experiment][h].get(key, 0.0)
                      for h in self._hypotheses}
        unnormalized = {h: self._log_weights[h] + (math.log(p) if p > 0 else -math.inf)
                        for h, p in likelihood.items()}
        normalized = _normalize_log_weights(unnormalized)
        if normalized is None:
            self._evidence.append(Evidence(experiment, key, 'conflict'))
            self._inconsistent = True
            return Update('mismatch', None, None, len(self._evidence) - 1,
                          'historical observation has zero likelihood under every candidate')
        self._log_weights = normalized
        self._evidence.append(Evidence(experiment, key, 'accepted'))
        return Update('updated', MappingProxyType(self._probabilities() or {}),
                      MappingProxyType(dict(self._log_weights)), len(self._evidence) - 1)
