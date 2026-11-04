"""Reproducible bounded probes with an explicitly conditional IID risk bound."""

from __future__ import annotations

from fractions import Fraction
from copy import deepcopy
import hashlib
from pathlib import Path
import random

from .store import digest


def _fraction(pair, name):
    if (not isinstance(pair, (tuple, list)) or len(pair) != 2
            or any(type(x) is not int for x in pair)
            or not 0 < pair[0] < pair[1] <= 10000):
        raise ValueError(name + ' must be an integer fraction strictly between zero and one')
    return Fraction(*pair)


def sample_count(epsilon, delta, round_number, *, max_tests=4096):
    """Smallest n with (1-epsilon)^n <= delta/(r(r+1)), using exact arithmetic.

    A null count is a limit outcome, never a smaller accepted sample size.
    """
    epsilon, delta = _fraction(epsilon, 'epsilon'), _fraction(delta, 'delta')
    if (type(round_number) is not int or not 1 <= round_number <= 4096
            or type(max_tests) is not int or not 0 <= max_tests <= 4096):
        raise ValueError('round/test bounds outside supported contract')
    allocation = delta / (round_number * (round_number + 1))
    remaining = Fraction(1)
    for count in range(max_tests + 1):
        if remaining <= allocation:
            return count
        if count < max_tests:
            remaining *= 1 - epsilon
    return None


class ProbeStream:
    """One persistent seed/contract and risk-allocation counter per learner.

    Fixed-seed PRNG runs are reproducible experiments, not IID certificates.
    The mathematical bound applies only under the separately stated independent
    sampling assumption. No host may silently promote that assumption to fact.
    """

    def __init__(self, actions, *, seed, max_length=16, persistence=True,
                 epsilon=(1, 20), delta=(1, 20)):
        if (not isinstance(actions, (tuple, list)) or not 1 <= len(actions) <= 32
                or any(not isinstance(a, str) or not a or len(a) > 64 for a in actions)
                or len(set(actions)) != len(actions)):
            raise ValueError('invalid action vocabulary')
        if (type(seed) is not int or not 0 <= seed < 2**64
                or type(max_length) is not int or not 0 <= max_length <= 32
                or type(persistence) is not bool):
            raise ValueError('invalid sampling seed, length or persistence')
        eps, risk = _fraction(epsilon, 'epsilon'), _fraction(delta, 'delta')
        self.actions = tuple(actions)
        self._contract = {'actions': list(actions), 'seed': seed, 'max_length': max_length,
            'persistence': persistence, 'epsilon': [eps.numerator, eps.denominator],
            'delta': [risk.numerator, risk.denominator],
            'distribution': 'uniform length 0..L; uniform first action; subsequent half-repeat/half-uniform'
                if persistence else 'uniform length 0..L; independent uniform actions',
            'randomness': 'fixed-seed Python random.Random; reproducible, not certified IID',
            'scope': 'probability of any trace disagreement on a bounded reset word'}
        self._rng = random.Random(seed)
        self.round = 0
        self.source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    @property
    def contract(self):
        return deepcopy(self._contract)

    def construct(self, model, *, max_tests=4096, max_work=1000000):
        """Commit one whole bounded schedule; never return partial words as complete."""
        from .active_navigation import validate_model
        if not validate_model(model) or model['actions'] != list(self.actions):
            raise ValueError('model/action vocabulary mismatch')
        if (type(max_tests) is not int or not 0 <= max_tests <= 4096
                or type(max_work) is not int or not 0 <= max_work <= 1000000):
            raise ValueError('invalid schedule budget')
        self.round += 1  # Repeated attempts, including identical maps, spend a new allocation.
        contract = self.contract
        base = {'status': 'construction_limit', 'model_digest': digest(model),
            'source_sha256': self.source_sha256, 'contract': contract,
            'round': self.round, 'words': [], 'work': 0, 'schedule_hash': None,
            'statistical_certificate': False,
            'interpretation': 'finite seeded probes; ideal-IID risk theorem is conditional only'}
        if self.round > 4096:
            return {**base, 'reason': 'sampling round ceiling exhausted'}
        allocation = Fraction(*contract['delta']) / (self.round * (self.round + 1))
        base['round_risk'] = [allocation.numerator, allocation.denominator]
        # Each exact survival update and each generated symbol/length costs a unit.
        possible = min(max_tests, max_work)
        count = sample_count(contract['epsilon'], contract['delta'],
                             self.round, max_tests=possible)
        base['work'] = possible if count is None else count
        if count is None:
            return {**base, 'reason': 'required sample count exceeds test/work ceiling'}
        base['expected_words'] = count
        words = []
        for _ in range(count):
            if base['work'] >= max_work:
                return {**base, 'reason': 'sampling construction work exhausted'}
            base['work'] += 1
            length = self._rng.randrange(contract['max_length'] + 1)
            word = []
            for _ in range(length):
                if base['work'] >= max_work:
                    return {**base, 'reason': 'sampling construction work exhausted'}
                base['work'] += 1
                if word and contract['persistence'] and self._rng.randrange(2) == 0:
                    action = word[-1]
                else:
                    action = self.actions[self._rng.randrange(len(self.actions))]
                word.append(action)
            # Duplicates are independent draws under the ideal model; never deduplicate.
            words.append(word)
        base.update(status='constructed', words=words)
        base['schedule_hash'] = digest(base)
        return base
