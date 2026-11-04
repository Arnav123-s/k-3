"""Authored bounded integer generators for memory accounting, not learned methods."""


def reconstruct(spec):
    count = spec['count']
    if type(count) is not int or not 0 <= count <= 100_000:
        raise ValueError('count outside demonstration bounds')
    if spec['generator'] == 'arithmetic_progression':
        values = [spec['start'] + i * spec['step'] for i in range(count)]
    elif spec['generator'] == 'tested_225_recurrence':
        difference, total, values = 225, 0, []
        for _ in range(count):
            total += difference
            values.append(total)
            difference -= (2 * total + 225) // 450
    else:
        raise ValueError('unknown authored generator')
    # Corrections affect reconstructed entries, not recurrence evolution.
    for index, residual in spec['residuals']:
        values[index] += residual
    return values
