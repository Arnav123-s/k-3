"""Exact finite integer memory; automatic selection from a bounded codec family.

Modern finite-difference construction, not a recovered historical algorithm.
No extrapolation or arbitrary stored-code execution is permitted.
"""
import hashlib
import json
import zlib
from pathlib import Path

FORMAT = 'astra-sequence-memory-v1'
MAX_COUNT = 10000
MAX_DEGREE = 16


def pack(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(pack(value)).hexdigest()


def decoder_digest():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _integers(values):
    if not isinstance(values, list) or len(values) > MAX_COUNT:
        raise ValueError('expected bounded integer list')
    if any(type(x) is not int or x.bit_length() > 512 for x in values):
        raise ValueError('expected integers of at most 512 bits')


def _generate(seeds, count):
    state = seeds[:]
    output = []
    for _ in range(count):
        output.append(state[0])
        for j in range(len(state) - 1):
            state[j] += state[j + 1]
    return output


def _seeds(values, degree):
    row = values[:degree + 1]
    seeds = []
    while row:
        seeds.append(row[0])
        row = [b - a for a, b in zip(row, row[1:])]
    return seeds


def encode(values, *, context, max_degree=8):
    """Choose smallest zlib-9 JSON payload among literal and difference codecs.

    Full decoder source is shared and separately charged in the cost report.
    Candidate search uses all observations, so its errors are not test errors.
    """
    _integers(values)
    if type(max_degree) is not int or not 0 <= max_degree <= MAX_DEGREE:
        raise ValueError('invalid degree bound')
    # Round-trip makes an independent JSON snapshot and rejects nonfinite values.
    context = json.loads(pack(context))
    common = dict(format=FORMAT, count=len(values), context=context,
                  source_digest=digest(values), decoder_digest=decoder_digest())
    candidates = [dict(common, codec='literal', values=values[:])]
    for degree in range(min(max_degree + 1, len(values))):
        seeds = _seeds(values, degree)
        generated = _generate(seeds, len(values))
        residuals = [[i, actual - predicted] for i, (actual, predicted)
                     in enumerate(zip(values, generated)) if actual != predicted]
        candidates.append(dict(common, codec='differences', seeds=seeds, residuals=residuals))
    sizes = [len(zlib.compress(pack(c), 9)) for c in candidates]
    selected = min(range(len(candidates)), key=lambda i: (sizes[i], i))
    record = candidates[selected]
    if restore(record, context=context) != values:
        raise AssertionError('internal reconstruction check failed')
    source_bytes = len(Path(__file__).read_bytes())
    return record, {'selection_metric': 'zlib9_payload_bytes', 'selected': selected,
                    'candidate_bytes': sizes, 'literal_bytes': sizes[0],
                    'payload_bytes': sizes[selected], 'decoder_source_bytes': source_bytes,
                    'total_bytes': sizes[selected] + source_bytes,
                    'boundary': 'payload plus shared decoder source; excludes Python/zlib/runtime and research logs'}


def restore(record, *, context):
    """Validate a record and recover exactly its observed finite sequence.

    Digests detect accidental corruption, not adversarial replacement of both
    payload and digest. Use ArtifactMemory identity for externally pinned bytes.
    """
    if not isinstance(record, dict):
        raise ValueError('invalid record')
    common = {'format', 'count', 'context', 'source_digest', 'decoder_digest', 'codec'}
    codec = record.get('codec')
    extra = {'values'} if codec == 'literal' else {'seeds', 'residuals'}
    if codec not in ('literal', 'differences') or set(record) != common | extra:
        raise ValueError('invalid record shape')
    if record['format'] != FORMAT or record['decoder_digest'] != decoder_digest():
        raise ValueError('decoder version mismatch')
    if pack(context) != pack(record['context']):
        raise ValueError('context mismatch')
    count = record['count']
    if type(count) is not int or not 0 <= count <= MAX_COUNT:
        raise ValueError('invalid count')
    if codec == 'literal':
        _integers(record['values'])
        values = record['values'][:]
        if len(values) != count:
            raise ValueError('count mismatch')
    else:
        seeds, residuals = record['seeds'], record['residuals']
        # Differences and residuals can be larger than source integers.
        if not isinstance(seeds, list) or not 1 <= len(seeds) <= min(count, MAX_DEGREE + 1):
            raise ValueError('invalid seeds')
        if any(type(x) is not int or x.bit_length() > 1024 for x in seeds):
            raise ValueError('invalid seeds')
        if not isinstance(residuals, list) or len(residuals) > count:
            raise ValueError('invalid residuals')
        values = _generate(seeds, count)
        previous = -1
        for pair in residuals:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError('invalid residual')
            i, delta = pair
            if type(i) is not int or not previous < i < count:
                raise ValueError('invalid residual index')
            if type(delta) is not int or not delta or delta.bit_length() > 1024:
                raise ValueError('invalid residual value')
            values[i] += delta
            previous = i
        _integers(values)
    if digest(values) != record['source_digest']:
        raise ValueError('reconstruction digest mismatch')
    return values


def read(record, index, *, context):
    """Unknown future entries are not supplied by a compression hypothesis."""
    values = restore(record, context=context)
    if type(index) is not int or not 0 <= index < len(values):
        return {'status': 'unknown', 'reason': 'outside retained observations'}
    return {'status': 'answer', 'answer': values[index]}
