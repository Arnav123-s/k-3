"""Independent scoring of a delivered answer to the Erdős #979 task (builder-written, never shown to the system).

    python3 experiments/erdos979/score.py answer.json [--bound 5076013506001]

Success requires all of:
  1. every representation is four primes p1 <= p2 <= p3 <= p4 whose fourth powers sum to n (substitution);
  2. n has exactly WAYS representations in total, counted here independently of the delivered program;
  3. no smaller number has exactly WAYS representations. The builder's reference search
     (ref4.py) is complete below --bound; above it, minimality needs the independent search
     `minimal.py`, and the verdict says which evidence was used.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from task import WAYS  # noqa: E402

REFERENCE = {1: 64, 2: 726724, 3: 141339844, 4: 199898912404}  # builder's reference; none with 5 below the bound
REFERENCE_BOUND = 5076013506001


def is_prime(n):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d, s = d // 2, s + 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def fourth_root(m):
    r = round(m ** 0.25)
    for c in (r - 1, r, r + 1):
        if c > 0 and c ** 4 == m:
            return c
    return None


def all_representations(n):
    """Every p1 <= p2 <= p3 <= p4 with p1^4 + ... + p4^4 = n (exact integer arithmetic)."""
    primes = [p for p in range(2, int(n ** 0.25) + 2) if is_prime(p) and p ** 4 < n]
    found = []
    for i, a in enumerate(primes):
        if 4 * a ** 4 > n:
            break
        for j in range(i, len(primes)):
            b = primes[j]
            if a ** 4 + 3 * b ** 4 > n:
                break
            for k in range(j, len(primes)):
                c = primes[k]
                rest = n - a ** 4 - b ** 4 - c ** 4
                if rest < c ** 4:
                    break
                d = fourth_root(rest)
                if d is not None and is_prime(d):
                    found.append([a, b, c, d])
    return found


def score(answer, bound=REFERENCE_BOUND, minimal_evidence=None):
    verdict = {'ways': WAYS, 'success': False}
    if not isinstance(answer, dict) or type(answer.get('n')) is not int or not isinstance(answer.get('representations'), list):
        return dict(verdict, reason='answer is not {"n": int, "representations": [...]}')
    n, reps = answer['n'], answer['representations']
    verdict['n'] = n
    bad = [r for r in reps if not (isinstance(r, list) and len(r) == 4 and all(type(p) is int for p in r)
                                   and r == sorted(r) and all(is_prime(p) for p in r) and sum(p ** 4 for p in r) == n)]
    verdict['substitution'] = 'all valid' if not bad and reps else f'invalid: {bad[:3]}'
    actual = all_representations(n)
    verdict['independent_count'] = len(actual)
    verdict['listed_all'] = sorted(map(list, reps)) == actual
    if bad or len(actual) != WAYS or not verdict['listed_all']:
        return dict(verdict, reason='representations or count wrong')
    if WAYS in REFERENCE:
        verdict['minimal'] = n == REFERENCE[WAYS]
        verdict['minimal_by'] = 'builder reference'
    elif n < bound:
        verdict['minimal'] = False
        verdict['minimal_by'] = f'builder reference: no number below {bound} has exactly {WAYS} ways'
    elif minimal_evidence is not None:
        verdict['minimal'] = minimal_evidence.get('first') == n and minimal_evidence.get('complete_below', 0) > n
        verdict['minimal_by'] = 'independent search minimal.py'
    else:
        verdict['minimal'] = None
        verdict['minimal_by'] = 'pending: run minimal.py up to n'
    verdict['success'] = verdict['minimal'] is True
    return verdict


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('answer')
    p.add_argument('--minimal', help='JSON output of minimal.py')
    a = p.parse_args()
    evidence = json.loads(Path(a.minimal).read_text()) if a.minimal else None
    print(json.dumps(score(json.loads(Path(a.answer).read_text()), minimal_evidence=evidence), indent=1))
