"""Authored constructor for certified radix inversion of monotone polynomials.

The polynomial and monotonicity contract are supplied. The update expression is
constructed by substitution, then independently checked by polynomial algebra.
This is a bounded modern method compiler, not discovery of the source relation.
Only the closed JSON representation below is interpreted; no source is executed.
"""
from math import comb


VERSION = 'monotone-polynomial-radix-inverse-v1'


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('invalid ' + name)


def _coefficients(values):
    if not isinstance(values, list) or not 2 <= len(values) <= 9:
        raise ValueError('degree must be between one and eight')
    if any(type(c) is not int or c < 0 or c.bit_length() > 256 for c in values):
        raise ValueError('coefficients must be bounded nonnegative integers')
    if values[-1] == 0:
        raise ValueError('positive leading coefficient required')


def construct(coefficients, radix=10):
    """Construct a new update rule from P; no root formulas are in the library."""
    _coefficients(coefficients)
    _integer(radix, 2, 16, 'radix')
    terms = []
    for degree, coefficient in enumerate(coefficients):
        if coefficient:
            for j in range(1, degree + 1):
                terms.append({'coefficient': coefficient * comb(degree, j),
                              'powers': [degree-j, j, j]})
    terms.sort(key=lambda term: term['powers'])
    record = {'version': VERSION, 'coefficients': list(coefficients),
              'radix': radix, 'increment': terms}
    validate(record)
    return record


def _add(left, right, scale=1):
    result = dict(left)
    for powers, coefficient in right.items():
        result[powers] = result.get(powers, 0) + scale*coefficient
        if not result[powers]:
            del result[powers]
    return result


def _multiply(left, right):
    result = {}
    for p, a in left.items():
        for q, b in right.items():
            powers = tuple(x+y for x, y in zip(p, q))
            result[powers] = result.get(powers, 0) + a*b
    return {p: c for p, c in result.items() if c}


def _substitute(coefficients, variable):
    result = {}
    for c in reversed(coefficients):
        result = _add(_multiply(result, variable), {(0, 0, 0): c})
    return result


def validate(record):
    """Check the exact identity by Horner substitution, independently of comb()."""
    if not isinstance(record, dict) or set(record) != {'version', 'coefficients', 'radix', 'increment'}:
        raise ValueError('invalid method record')
    if record['version'] != VERSION:
        raise ValueError('unknown method version')
    _coefficients(record['coefficients'])
    _integer(record['radix'], 2, 16, 'radix')
    terms = record['increment']
    if not isinstance(terms, list) or not 1 <= len(terms) <= 36:
        raise ValueError('invalid increment term count')
    actual = {}
    for term in terms:
        if not isinstance(term, dict) or set(term) != {'coefficient', 'powers'}:
            raise ValueError('invalid increment term')
        powers = term['powers']
        if not isinstance(powers, list) or len(powers) != 3:
            raise ValueError('invalid increment powers')
        for power in powers:
            _integer(power, 0, 8, 'increment power')
        coefficient = term['coefficient']
        if type(coefficient) is not int or coefficient <= 0 or coefficient.bit_length() > 264:
            raise ValueError('invalid increment coefficient')
        if tuple(powers) in actual:
            raise ValueError('duplicate increment term')
        actual[tuple(powers)] = coefficient
    # r + s*d and r, respectively. Expanding by repeated multiplication is
    # a separate checker implementation from the constructor's binomial sum.
    translated = _substitute(record['coefficients'], {(1, 0, 0): 1, (0, 1, 1): 1})
    original = _substitute(record['coefficients'], {(1, 0, 0): 1})
    if actual != _add(translated, original, -1):
        raise ValueError('increment identity failed')
    return True


def _evaluate(coefficients, x):
    result = 0
    for c in reversed(coefficients):
        result = result*x + c
    return result


def _increment(terms, r, s, d):
    return sum(term['coefficient'] * r**term['powers'][0]
               * s**term['powers'][1] * d**term['powers'][2] for term in terms)


def solve(record, target, max_steps=4096):
    """Execute the generated increment; certify P(r) <= target < P(r+1).

    Each bracketing evaluation or candidate digit costs one step. Compilation,
    identity checking and final certificate arithmetic are outside this probe
    count, and must not be reported as free work or a speedup over binary search.
    """
    validate(record)
    if type(target) is not int or target < 0 or target.bit_length() > 1024:
        raise ValueError('target must be a nonnegative integer of at most 1024 bits')
    _integer(max_steps, 0, 10000, 'step budget')
    coefficients, radix = record['coefficients'], record['radix']
    trace = []
    if target < coefficients[0]:
        return {'status': 'no_solution', 'reason': 'target below P(0)', 'steps': 0, 'trace': trace}
    root, remainder, upper = 0, target-coefficients[0], 1
    steps = 0

    def incomplete():
        return {'status': 'incomplete', 'reason': 'step budget exhausted',
                'partial_root': root, 'partial_remainder': remainder,
                'steps': steps, 'trace': trace}

    while True:
        if steps == max_steps:
            return incomplete()
        steps += 1
        value = _evaluate(coefficients, upper)
        trace.append({'kind': 'bracket', 'upper': upper, 'value': value})
        if value > target:
            break
        upper *= radix
    stride = upper // radix
    while stride:
        for digit in range(radix-1, -1, -1):
            if steps == max_steps:
                return incomplete()
            steps += 1
            delta = _increment(record['increment'], root, stride, digit)
            accepted = delta <= remainder
            trace.append({'kind': 'digit', 'root': root, 'stride': stride, 'digit': digit,
                          'delta': delta, 'remainder': remainder, 'accepted': accepted})
            if accepted:
                root += stride*digit
                remainder -= delta
                break
        stride //= radix
    # Check the original relation, not merely the generated recurrence's state.
    value, next_value = _evaluate(coefficients, root), _evaluate(coefficients, root+1)
    if not value <= target < next_value or remainder != target-value:
        raise ArithmeticError('constructed method failed its independent final certificate')
    return {'status': 'solved', 'root': root, 'remainder': remainder,
            'next_gap': next_value-target, 'steps': steps, 'trace': trace}
