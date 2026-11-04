"""Bounded exact polynomial-coefficient shift relations from finite samples.

Fits one supplied family ``sum_j P_j(n) x[n+j] = 0`` with a fixed order and
uniform degree bound. Results describe an exact finite fit only; they do not
establish an infinite law. Common polynomial factors are not removed.
"""

from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from typing import Any

from .recurrence import (
    MAX_INPUT_BITS,
    MAX_INTERMEDIATE_BITS,
    _Budget,
    _check_fraction,
    _determinant,
    _parse_fraction,
    _parse_result_fraction,
    _qstr,
    _rref,
)


FORMAT = "polynomial-shift-relation-v1"
MAX_SAMPLES = 128
MAX_ORDER = 4
MAX_DEGREE = 4
MAX_COLUMNS = 25
MAX_STEPS = 2_000_000
MAX_INDEX = 1_000_000
MAX_PREDICTION_STEPS = 512
INTERPRETATION = "A finite exact relation only; infinite validity is not inferred."
COEFFICIENT_LAYOUT = "shift-major; each polynomial has ascending powers of absolute n"


class _WorkBudget(_Budget):
    def __init__(self):
        super().__init__(MAX_STEPS)


def _parse_values(values):
    if not isinstance(values, list) or not values or len(values) > MAX_SAMPLES:
        raise ValueError(f"values must be a nonempty list of at most {MAX_SAMPLES} exact rationals")
    return [_parse_fraction(v) for v in values]


def _strings(values):
    return [_qstr(v) for v in values]


def _digest(strings):
    raw = json.dumps(strings, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _validate_shape(order, degree, start_index):
    if type(order) is not int or not 1 <= order <= MAX_ORDER:
        raise ValueError(f"order must be an integer from 1 to {MAX_ORDER}")
    if type(degree) is not int or not 0 <= degree <= MAX_DEGREE:
        raise ValueError(f"degree must be an integer from 0 to {MAX_DEGREE}")
    if (order + 1) * (degree + 1) > MAX_COLUMNS:
        raise ValueError(f"relation has more than {MAX_COLUMNS} coefficients")
    if type(start_index) is not int or not 0 <= start_index <= MAX_INDEX:
        raise ValueError(f"start_index must be an integer from 0 to {MAX_INDEX}")


def _matrix(values, order, degree, start_index):
    """Rows are exact linear constraints on shift-major polynomial coefficients."""
    rows = []
    for offset in range(len(values) - order):
        n = start_index + offset
        powers = [n ** k for k in range(degree + 1)]
        rows.append([values[offset + shift] * powers[k]
                     for shift in range(order + 1) for k in range(degree + 1)])
    return rows


def _flat_basis(matrix, columns, budget):
    reduced, row_ids, pivots = _rref([row[:] for row in matrix], columns, budget)
    pivot_map = {column: row for row, column in pivots}
    free = [column for column in range(columns) if column not in pivot_map]
    basis = []
    for free_column in free:
        vector = [Fraction(0) for _ in range(columns)]
        vector[free_column] = Fraction(1)
        for column, row in pivot_map.items():
            vector[column] = _check_fraction(-reduced[row][free_column])
        # Canonical scale makes persisted certificates deterministic.
        first = next(value for value in vector if value)
        vector = [_check_fraction(value / first) for value in vector]
        basis.append(vector)
    return basis, row_ids, pivots


def _rank_minor(matrix, row_ids, pivots, budget):
    rank = len(pivots)
    rows = [row_ids[row] for row, _ in pivots]
    columns = [column for _, column in pivots]
    square = [[matrix[row][column] for column in columns] for row in rows]
    determinant = _determinant(square, budget)
    if rank and not determinant:
        raise AssertionError("elimination pivots failed to produce a nonzero minor")
    return {"rows": rows, "columns": columns, "determinant": _qstr(determinant)}


def _nested(flat, order, degree):
    width = degree + 1
    return [[_qstr(value) for value in flat[j * width:(j + 1) * width]]
            for j in range(order + 1)]


def _poly(coefficients, n):
    return _check_fraction(sum((_check_fraction(coefficient * (n ** power))
                                for power, coefficient in enumerate(coefficients)), Fraction(0)))


def _predictive(flat, order, degree, start_index, sample_count):
    leading = flat[order * (degree + 1):(order + 1) * (degree + 1)]
    if not any(leading):
        return False, "highest-shift polynomial is identically zero"
    return True, None


def _observed_singularities(flat, order, degree, start_index, sample_count):
    leading = flat[order * (degree + 1):(order + 1) * (degree + 1)]
    return [start_index + offset for offset in range(sample_count-order)
            if _poly(leading, start_index+offset) == 0]


def fit_shift_relation(values, order, degree, start_index=0) -> dict[str, Any]:
    """Fit one fixed polynomial-shift family exactly over rational samples.

    The model is ``sum(P_j(n) * x[n+j], j=0..order) == 0`` where each
    ``P_j`` has degree at most ``degree``. Coefficients are normalized by
    dividing by their first nonzero entry. No common-polynomial-factor
    reduction is performed, so equivalent nonprimitive relations can exist.
    """
    parsed = _parse_values(values)
    _validate_shape(order, degree, start_index)
    if start_index + len(parsed) > MAX_INDEX:
        raise ValueError("observed indices exceed maximum index")
    columns = (order + 1) * (degree + 1)
    matrix = _matrix(parsed, order, degree, start_index)
    budget = _WorkBudget()
    basis, row_ids, pivots = _flat_basis(matrix, columns, budget)
    rank = len(pivots)
    nullity = len(basis)
    rank_minor = _rank_minor(matrix, row_ids, pivots, budget)
    training = _strings(parsed)
    result = {
        "format": FORMAT,
        "status": "no_fit" if nullity == 0 else ("unique" if nullity == 1 else "underdetermined"),
        "order": order,
        "degree": degree,
        "start_index": start_index,
        "sample_count": len(parsed),
        "training_values": training,
        "training_digest": _digest(training),
        "coefficient_layout": COEFFICIENT_LAYOUT,
        "rank": rank,
        "nullspace_basis": [[_qstr(value) for value in vector] for vector in basis],
        "coefficients": _nested(basis[0], order, degree) if nullity == 1 else None,
        "predictive": False,
        "predictive_reason": None,
        "observed_singular_indices": [],
        "rank_minor": rank_minor,
        "limits": {"max_samples": MAX_SAMPLES, "max_order": MAX_ORDER,
                   "max_degree": MAX_DEGREE, "max_columns": MAX_COLUMNS,
                   "max_input_bits": MAX_INPUT_BITS,
                   "max_intermediate_bits": MAX_INTERMEDIATE_BITS,
                   "max_elimination_steps": MAX_STEPS,
                   "max_index": MAX_INDEX,
                   "max_prediction_steps": MAX_PREDICTION_STEPS},
        "interpretation": INTERPRETATION,
    }
    if nullity == 1:
        predictive, reason = _predictive(basis[0], order, degree, start_index, len(parsed))
        result["predictive"] = predictive
        result["predictive_reason"] = reason
        result['observed_singular_indices'] = _observed_singularities(
            basis[0], order, degree, start_index, len(parsed))
    elif nullity > 1:
        result["predictive_reason"] = "multiple relations fit; no relation is selected"
    else:
        result["predictive_reason"] = "the fixed family has no nonzero relation for these samples"
    return result


def _parse_basis(result, columns):
    raw = result.get("nullspace_basis")
    if not isinstance(raw, list) or len(raw) > columns:
        raise ValueError("nullspace_basis must be a list bounded by the coefficient count")
    basis = []
    for vector in raw:
        if not isinstance(vector, list) or len(vector) != columns:
            raise ValueError("invalid nullspace vector shape")
        basis.append([_parse_result_fraction(value) for value in vector])
    return basis


def _verify_minor(matrix, witness, expected_rank):
    if not isinstance(witness, dict) or set(witness) != {"rows", "columns", "determinant"}:
        return False
    rows, columns = witness.get("rows"), witness.get("columns")
    if (not isinstance(rows, list) or not isinstance(columns, list)
            or len(rows) != expected_rank or len(columns) != expected_rank
            or len(set(rows)) != expected_rank or len(set(columns)) != expected_rank
            or any(type(r) is not int or not 0 <= r < len(matrix) for r in rows)
            or any(type(c) is not int or not 0 <= c < (len(matrix[0]) if matrix else 0) for c in columns)):
        return False
    try:
        claimed = _parse_result_fraction(witness["determinant"])
        square = [[matrix[r][c] for c in columns] for r in rows]
        actual = _determinant(square, _WorkBudget())
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return (actual == claimed and
            (actual != 0 if expected_rank else actual == 1))


def _rank_of_vectors(vectors, columns):
    if not vectors:
        return 0
    transpose = [[vectors[col][row] for col in range(len(vectors))] for row in range(columns)]
    _, _, pivots = _rref(transpose, len(vectors), _WorkBudget())
    return len(pivots)


def verify_shift_relation(values, result) -> bool:
    """Verify training equations, complete nullspace basis, and rank certificate.

    This verifier does not call ``fit_shift_relation``. The rank minor supplies
    a lower bound on matrix rank; the independent nullspace basis supplies the
    matching upper bound.
    """
    try:
        parsed = _parse_values(values)
        if not isinstance(result, dict):
            return False
        required = {"format", "status", "order", "degree", "start_index", "sample_count",
                    "training_values", "training_digest", "coefficient_layout", "rank",
                    "nullspace_basis", "coefficients", "predictive", "predictive_reason",
                    "rank_minor", "limits", "interpretation", "observed_singular_indices"}
        if set(result) != required or result.get("format") != FORMAT:
            return False
        order, degree, origin = result.get("order"), result.get("degree"), result.get("start_index")
        _validate_shape(order, degree, origin)
        if origin + len(parsed) > MAX_INDEX:
            return False
        training = _strings(parsed)
        if (result.get("training_values") != training or result.get("training_digest") != _digest(training)
                or type(result.get("sample_count")) is not int or result["sample_count"] != len(parsed)
                or result.get("coefficient_layout") != COEFFICIENT_LAYOUT
                or result.get("interpretation") != INTERPRETATION):
            return False
        limits = {"max_samples": MAX_SAMPLES, "max_order": MAX_ORDER,
                  "max_degree": MAX_DEGREE, "max_columns": MAX_COLUMNS,
                  "max_input_bits": MAX_INPUT_BITS,
                  "max_intermediate_bits": MAX_INTERMEDIATE_BITS,
                  "max_elimination_steps": MAX_STEPS, "max_index": MAX_INDEX,
                  "max_prediction_steps": MAX_PREDICTION_STEPS}
        if result.get("limits") != limits:
            return False
        columns = (order + 1) * (degree + 1)
        matrix = _matrix(parsed, order, degree, origin)
        basis = _parse_basis(result, columns)
        status = result.get("status")
        if status not in {"unique", "underdetermined", "no_fit"}:
            return False
        if any(not any(vector) for vector in basis):
            return False
        for vector in basis:
            for row in matrix:
                if sum((a * b for a, b in zip(row, vector)), Fraction(0)):
                    return False
        if _rank_of_vectors(basis, columns) != len(basis):
            return False
        rank = result.get("rank")
        if type(rank) is not int or not 0 <= rank <= columns or rank + len(basis) != columns:
            return False
        if not _verify_minor(matrix, result.get("rank_minor"), rank):
            return False
        expected_status = "no_fit" if len(basis) == 0 else ("unique" if len(basis) == 1 else "underdetermined")
        if status != expected_status:
            return False
        if status == "unique":
            coefficients = result.get("coefficients")
            if not isinstance(coefficients, list) or len(coefficients) != order + 1:
                return False
            flat = []
            for poly in coefficients:
                if not isinstance(poly, list) or len(poly) != degree + 1:
                    return False
                flat.extend(_parse_result_fraction(value) for value in poly)
            if flat != basis[0] or not flat or next(value for value in flat if value) != 1:
                return False
            predictive, reason = _predictive(flat, order, degree, origin, len(parsed))
            singularities = result.get('observed_singular_indices')
            return (result.get("predictive") is predictive and result.get("predictive_reason") == reason
                    and isinstance(singularities, list)
                    and all(type(n) is int for n in singularities)
                    and singularities == _observed_singularities(flat, order, degree, origin, len(parsed)))
        if result.get("coefficients") is not None or result.get("predictive") is not False:
            return False
        if result.get('observed_singular_indices') != []:
            return False
        if status == "underdetermined":
            return result.get("predictive_reason") == "multiple relations fit; no relation is selected"
        return result.get("predictive_reason") == "the fixed family has no nonzero relation for these samples"
    except (TypeError, ValueError, KeyError, ZeroDivisionError, OverflowError, StopIteration):
        return False


def predict_shift_relation(result, prefix, start_index=None, steps=1, *, continuation=True) -> dict[str, Any]:
    """Conditionally forecast; continuation requires the full retained training prefix.

    A past singular transition requires an observed seed beyond it, not permanent
    rejection of the relation. Every requested update must still be nonsingular.
    Explicit continuation=False evaluates a new indexed seed sequence, not evidence
    about the process that produced the training observations.
    """
    if not isinstance(result, dict) or result.get("status") != "unique":
        raise ValueError("prediction requires a unique shift relation")
    if not verify_shift_relation(result.get("training_values"), result):
        raise ValueError("shift relation certificate is invalid")
    if result.get("predictive") is not True:
        raise ValueError(result.get("predictive_reason") or "relation is not executable")
    if type(steps) is not int or not 0 <= steps <= MAX_PREDICTION_STEPS:
        raise ValueError(f"steps must be an integer from 0 to {MAX_PREDICTION_STEPS}")
    sequence = _parse_values(prefix)
    origin = result["start_index"] if start_index is None else start_index
    if type(origin) is not int or not 0 <= origin <= MAX_INDEX:
        raise ValueError(f"start_index must be an integer from 0 to {MAX_INDEX}")
    if type(continuation) is not bool:
        raise ValueError('continuation must be a literal boolean')
    if continuation:
        training = result['training_values']
        if (origin != result['start_index'] or len(sequence) < len(training)
                or _strings(sequence[:len(training)]) != training):
            raise ValueError('continuation must retain the entire indexed training prefix')
    if origin + len(sequence) + steps > MAX_INDEX:
        raise ValueError("prediction exceeds maximum index")
    order, degree = result["order"], result["degree"]
    if len(sequence) < order:
        raise ValueError("prefix must contain at least order seed values")
    polys = [[_parse_result_fraction(value) for value in poly]
             for poly in result["coefficients"]]
    for offset in range(len(sequence) - order):
        n = origin + offset
        equation = _check_fraction(sum((_check_fraction(_poly(polys[j], n) * sequence[offset + j])
                                        for j in range(order + 1)), Fraction(0)))
        if equation:
            raise ValueError("prefix contradicts the fitted relation")
    predictions = []
    for _ in range(steps):
        n = origin + len(sequence) - order
        leading = _poly(polys[order], n)
        if not leading:
            raise ValueError(f"highest-shift polynomial is zero at prediction index {n}")
        previous = _check_fraction(sum((_check_fraction(_poly(polys[j], n)
                                                         * sequence[len(sequence) - order + j])
                                        for j in range(order)), Fraction(0)))
        next_value = _check_fraction(-previous / leading)
        predictions.append(_qstr(next_value))
        sequence.append(next_value)
    return {"values": predictions,
            "execution_mode": "continuation" if continuation else "new_indexed_seeds",
            "interpretation": "conditional extrapolation from this finite relation and supplied indexed prefix; verify prospectively"}
