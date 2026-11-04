"""Bounded exact fits of rational constant-coefficient sequence recurrences.

The constructor identifies a minimal recurrence consistent with the supplied finite
prefix under a bounded positive-order rational model class. It does not establish an
infinite law. Predictions are conditional extrapolations and must be tested prospectively.
"""

from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import re
from typing import Any


FORMAT = "rational-shift-recurrence-v1"
MAX_SAMPLES = 512
MAX_ORDER = 16
MAX_STEPS = 2_000_000
MAX_INPUT_BITS = 512
MAX_INTERMEDIATE_BITS = 4096
MAX_INPUT_TEXT = 1024
MAX_CERTIFICATE_TEXT = 2 * MAX_INTERMEDIATE_BITS + 2
MAX_PREDICTION_STEPS = 1024
INTERPRETATION = "A finite exact fit only; infinite validity is not inferred."

_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_RATIONAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:/(?:0|[1-9][0-9]*))?\Z")


class _Budget:
    def __init__(self, limit: int = MAX_STEPS):
        self.limit = limit
        self.steps = 0

    def tick(self, count: int = 1) -> None:
        self.steps += count
        if self.steps > self.limit:
            raise ValueError("exact elimination step budget exceeded")


def _qstr(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _check_fraction(value: Fraction) -> Fraction:
    if (value.numerator.bit_length() > MAX_INTERMEDIATE_BITS
            or value.denominator.bit_length() > MAX_INTERMEDIATE_BITS):
        raise ValueError("exact rational intermediate exceeds bit limit")
    return value


def _parse_fraction(value: Any, *, input_value: bool = True) -> Fraction:
    text_limit = MAX_INPUT_TEXT if input_value else MAX_CERTIFICATE_TEXT
    if type(value) is int:
        result = Fraction(value)
    elif isinstance(value, str) and len(value) <= text_limit and _RATIONAL.fullmatch(value):
        try:
            result = Fraction(value)
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError("invalid exact rational string") from error
        if _qstr(result) != value:
            raise ValueError("rational strings must use canonical reduced form")
    else:
        raise ValueError("values must be integers or canonical rational strings; floats and booleans are rejected")
    bit_limit = MAX_INPUT_BITS if input_value else MAX_INTERMEDIATE_BITS
    if result.numerator.bit_length() > bit_limit or result.denominator.bit_length() > bit_limit:
        raise ValueError("rational value exceeds bit limit")
    return result


def _parse_result_fraction(value: Any) -> Fraction:
    if not isinstance(value, str):
        raise ValueError("certificate rationals must be canonical strings")
    return _parse_fraction(value, input_value=False)


def _parse_values(values: Any) -> list[Fraction]:
    if not isinstance(values, list) or not values or len(values) > MAX_SAMPLES:
        raise ValueError(f"values must be a nonempty list of at most {MAX_SAMPLES} exact rationals")
    return [_parse_fraction(value) for value in values]


def _training_strings(values: list[Fraction]) -> list[str]:
    return [_qstr(value) for value in values]


def _canonical_digest(values: list[str]) -> str:
    raw = json.dumps(values, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _rref(matrix: list[list[Fraction]], columns: int, budget: _Budget):
    """Reduced row-echelon form with original row identities and pivot columns."""
    rows = len(matrix)
    if any(len(row) != columns for row in matrix):
        raise ValueError("internal matrix shape mismatch")
    row_ids = list(range(rows))
    pivots: list[tuple[int, int]] = []
    pivot_row = 0
    for column in range(columns):
        selected = next((row for row in range(pivot_row, rows) if matrix[row][column]), None)
        if selected is None:
            continue
        if selected != pivot_row:
            matrix[pivot_row], matrix[selected] = matrix[selected], matrix[pivot_row]
            row_ids[pivot_row], row_ids[selected] = row_ids[selected], row_ids[pivot_row]
        pivot = matrix[pivot_row][column]
        for j in range(column, columns):
            budget.tick()
            matrix[pivot_row][j] = _check_fraction(matrix[pivot_row][j] / pivot)
        for row in range(rows):
            if row == pivot_row:
                continue
            factor = matrix[row][column]
            if factor:
                for j in range(column, columns):
                    budget.tick()
                    matrix[row][j] = _check_fraction(matrix[row][j] - factor * matrix[pivot_row][j])
        pivots.append((pivot_row, column))
        pivot_row += 1
        if pivot_row == rows:
            break
    return matrix, row_ids, pivots


def _system(values: list[Fraction], order: int):
    equations = len(values) - order
    matrix = [[values[n + j] for j in range(order)] + [values[n + order]]
              for n in range(equations)]
    return matrix, equations


def _left_inconsistency_witness(matrix: list[list[Fraction]], rhs: list[Fraction],
                                order: int, budget: _Budget) -> list[Fraction]:
    """Find lambda with lambda^T A=0 and lambda^T b != 0 by nullspace elimination."""
    equation_count = len(rhs)
    transpose = [[matrix[row][column] for row in range(equation_count)]
                 for column in range(order)]
    reduced, _, pivots = _rref(transpose, equation_count, budget)
    pivot_columns = {column for _, column in pivots}
    for free in range(equation_count):
        if free in pivot_columns:
            continue
        vector = [Fraction(0) for _ in range(equation_count)]
        vector[free] = Fraction(1)
        for row, column in pivots:
            vector[column] = _check_fraction(-reduced[row][free])
        dot_rhs = _check_fraction(sum((x * y for x, y in zip(vector, rhs)), Fraction(0)))
        budget.tick(equation_count)
        if dot_rhs:
            return vector
    raise AssertionError("inconsistent system had no left-null witness")


def _rejection_witness(values: list[Fraction], order: int, budget: _Budget) -> dict[str, Any]:
    augmented, equation_count = _system(values, order)
    matrix = [row[:order] for row in augmented]
    rhs = [row[-1] for row in augmented]
    vector = _left_inconsistency_witness(matrix, rhs, order, budget)
    dot_rhs = _check_fraction(sum((x * y for x, y in zip(vector, rhs)), Fraction(0)))
    return {"order": order, "lambda": [_qstr(value) for value in vector],
            "dot_b": _qstr(dot_rhs)}


def _determinant(matrix: list[list[Fraction]], budget: _Budget) -> Fraction:
    size = len(matrix)
    if any(len(row) != size for row in matrix):
        raise ValueError("determinant matrix must be square")
    work = [[Fraction(value) for value in row] for row in matrix]
    determinant = Fraction(1)
    sign = 1
    for column in range(size):
        pivot_row = next((row for row in range(column, size) if work[row][column]), None)
        if pivot_row is None:
            return Fraction(0)
        if pivot_row != column:
            work[column], work[pivot_row] = work[pivot_row], work[column]
            sign = -sign
        pivot = work[column][column]
        determinant = _check_fraction(determinant * pivot)
        for row in range(column + 1, size):
            if not work[row][column]:
                continue
            factor = work[row][column] / pivot
            for j in range(column + 1, size):
                budget.tick()
                work[row][j] = _check_fraction(work[row][j] - factor * work[column][j])
            work[row][column] = Fraction(0)
    return _check_fraction(determinant * sign)


def _base_result(values: list[Fraction], max_order: int, search_limit: int) -> dict[str, Any]:
    training_values = _training_strings(values)
    return {
        "format": FORMAT,
        "status": None,
        "order": None,
        "max_order": max_order,
        "search_limit": search_limit,
        "sample_count": len(values),
        "training_values": training_values,
        "training_digest": _canonical_digest(training_values),
        "coefficients": None,
        "particular": None,
        "nullvector": None,
        "lower_order_witnesses": [],
        "full_rank_witness": None,
        "limits": {"max_samples": MAX_SAMPLES, "max_order": MAX_ORDER,
                   "max_input_bits": MAX_INPUT_BITS,
                   "max_intermediate_bits": MAX_INTERMEDIATE_BITS,
                   "max_elimination_steps": MAX_STEPS,
                   "max_prediction_steps": MAX_PREDICTION_STEPS},
        "interpretation": INTERPRETATION,
    }


def fit_recurrence(values, max_order=8) -> dict[str, Any]:
    """Find the minimal positive order rational recurrence fitting ``values`` exactly.

    Coefficients are oldest-to-newest in
    ``x[n+order] = sum(coefficients[j] * x[n+j])``. Order zero is never used;
    consequently the all-zero sequence has minimum positive order 1, but its
    coefficient is underdetermined and no predictive coefficient is certified.
    Orders with no observed recurrence equation are not searched, except for the
    one-sample case, which returns an explicit underdetermined order-1 result.
    """
    parsed = _parse_values(values)
    if type(max_order) is not int or not 1 <= max_order <= MAX_ORDER:
        raise ValueError(f"max_order must be an integer from 1 to {MAX_ORDER}")
    search_limit = min(max_order, len(parsed) - 1)
    result = _base_result(parsed, max_order, search_limit)
    budget = _Budget()
    rejected: list[dict[str, Any]] = []

    if search_limit == 0:
        result.update({"status": "underdetermined", "order": 1,
                       "particular": ["0"], "nullvector": ["1"],
                       "reason": "one observation supplies no recurrence equation"})
        return result

    for order in range(1, search_limit + 1):
        augmented, equation_count = _system(parsed, order)
        reduced, row_ids, pivots = _rref(augmented, order + 1, budget)
        coefficient_pivots = [(row, column) for row, column in pivots if column < order]
        inconsistent = any(not any(row[:order]) and row[order] for row in reduced)
        if inconsistent:
            rejected.append(_rejection_witness(parsed, order, budget))
            continue

        pivot_by_column = {column: row for row, column in coefficient_pivots}
        particular = [Fraction(0) for _ in range(order)]
        for column, row in pivot_by_column.items():
            particular[column] = reduced[row][order]
        free_columns = [column for column in range(order) if column not in pivot_by_column]
        result["order"] = order
        result["lower_order_witnesses"] = rejected
        if free_columns:
            free = free_columns[0]
            nullvector = [Fraction(0) for _ in range(order)]
            nullvector[free] = Fraction(1)
            for column, row in pivot_by_column.items():
                nullvector[column] = _check_fraction(-reduced[row][free])
            result.update({"status": "underdetermined",
                           "particular": [_qstr(value) for value in particular],
                           "nullvector": [_qstr(value) for value in nullvector],
                           "reason": "minimal consistent order has non-unique coefficients"})
            return result

        coefficients = particular
        if len(coefficient_pivots) != order:
            raise AssertionError("full-rank recurrence lacks all coefficient pivots")
        minor_rows = [row_ids[row] for row, _ in coefficient_pivots]
        original_matrix, _ = _system(parsed, order)
        minor = [[original_matrix[row][column] for column in range(order)] for row in minor_rows]
        determinant = _determinant(minor, budget)
        if not determinant:
            raise AssertionError("selected pivot rows do not form a full-rank minor")
        result.update({"status": "unique", "coefficients": [_qstr(value) for value in coefficients],
                       "full_rank_witness": {"minor_rows": minor_rows,
                                             "determinant": _qstr(determinant)}})
        return result

    result.update({"status": "no_fit", "lower_order_witnesses": rejected,
                   "reason": "no exact recurrence of any searched positive order fits this prefix"})
    return result


def _verify_witness(values: list[Fraction], witness: Any, expected_order: int) -> bool:
    if not isinstance(witness, dict) or set(witness) != {"order", "lambda", "dot_b"}:
        return False
    if type(witness["order"]) is not int or witness["order"] != expected_order:
        return False
    order = expected_order
    equations = len(values) - order
    if equations <= 0 or not isinstance(witness["lambda"], list) or len(witness["lambda"]) != equations:
        return False
    try:
        lam = [_parse_result_fraction(item) for item in witness["lambda"]]
        claimed_dot = _parse_result_fraction(witness["dot_b"])
    except (TypeError, ValueError):
        return False
    if not any(lam):
        return False
    matrix, _ = _system(values, order)
    for column in range(order):
        if sum((lam[row] * matrix[row][column] for row in range(equations)), Fraction(0)):
            return False
    dot_b = sum((lam[row] * matrix[row][-1] for row in range(equations)), Fraction(0))
    return bool(dot_b) and dot_b == claimed_dot


def _verify_minor(values: list[Fraction], result: dict[str, Any], order: int) -> bool:
    witness = result.get("full_rank_witness")
    if not isinstance(witness, dict) or set(witness) != {"minor_rows", "determinant"}:
        return False
    rows = witness["minor_rows"]
    equations = len(values) - order
    if (not isinstance(rows, list) or len(rows) != order
            or any(type(index) is not int or not 0 <= index < equations for index in rows)
            or len(set(rows)) != order):
        return False
    try:
        claimed = _parse_result_fraction(witness["determinant"])
        matrix, _ = _system(values, order)
        minor = [[matrix[row][column] for column in range(order)] for row in rows]
        actual = _determinant(minor, _Budget())
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return actual != 0 and actual == claimed


def verify_recurrence(values, result) -> bool:
    """Independently verify the exact finite fit and its minimality certificates.

    Verification checks rational equations, lower-order left-null witnesses, and,
    for a unique fit, a nonzero determinant minor. It does not call the fitter.
    """
    try:
        parsed = _parse_values(values)
        if not isinstance(result, dict):
            return False
        required = {"format", "status", "order", "max_order", "search_limit", "sample_count",
                    "training_values", "training_digest", "coefficients", "particular", "nullvector",
                    "lower_order_witnesses", "full_rank_witness", "limits", "interpretation"}
        if result.get("status") == "underdetermined":
            required.add("reason")
        elif result.get("status") == "no_fit":
            required.add("reason")
        if set(result) != required or result.get("format") != FORMAT:
            return False
        training = result.get("training_values")
        if not isinstance(training, list) or training != _training_strings(parsed):
            return False
        if result.get("training_digest") != _canonical_digest(training):
            return False
        if (type(result.get("max_order")) is not int or not 1 <= result["max_order"] <= MAX_ORDER
                or result.get("sample_count") != len(parsed)
                or type(result.get("sample_count")) is not int):
            return False
        search_limit = min(result["max_order"], len(parsed) - 1)
        if type(result.get("search_limit")) is not int or result.get("search_limit") != search_limit:
            return False
        expected_limits = {"max_samples": MAX_SAMPLES, "max_order": MAX_ORDER,
                           "max_input_bits": MAX_INPUT_BITS,
                           "max_intermediate_bits": MAX_INTERMEDIATE_BITS,
                           "max_elimination_steps": MAX_STEPS,
                           "max_prediction_steps": MAX_PREDICTION_STEPS}
        if result.get("limits") != expected_limits or result.get("interpretation") != INTERPRETATION:
            return False
        witnesses = result.get("lower_order_witnesses")
        if not isinstance(witnesses, list):
            return False
        status = result["status"]
        if status == "underdetermined" and len(parsed) == 1:
            return (type(result["order"]) is int and result["order"] == 1 and search_limit == 0 and witnesses == []
                    and result.get("coefficients") is None and result.get("particular") == ["0"]
                    and result.get("nullvector") == ["1"] and result.get("full_rank_witness") is None)
        if status == "no_fit":
            if (result.get("order") is not None or result.get("coefficients") is not None
                    or result.get("particular") is not None or result.get("nullvector") is not None
                    or result.get("full_rank_witness") is not None
                    or len(witnesses) != search_limit):
                return False
            return all(_verify_witness(parsed, witness, order)
                       for order, witness in enumerate(witnesses, start=1))

        order = result.get("order")
        if type(order) is not int or not 1 <= order <= search_limit or len(witnesses) != order - 1:
            return False
        if not all(_verify_witness(parsed, witness, n)
                   for n, witness in enumerate(witnesses, start=1)):
            return False
        matrix, equation_count = _system(parsed, order)
        if status == "unique":
            coefficients = result.get("coefficients")
            if (not isinstance(coefficients, list) or len(coefficients) != order
                    or result.get("particular") is not None or result.get("nullvector") is not None):
                return False
            parsed_coefficients = [_parse_result_fraction(item) for item in coefficients]
            if any(sum((matrix[row][column] * parsed_coefficients[column]
                        for column in range(order)), Fraction(0)) != matrix[row][-1]
                   for row in range(equation_count)):
                return False
            return _verify_minor(parsed, result, order)
        if status == "underdetermined":
            if (result.get("coefficients") is not None or result.get("full_rank_witness") is not None
                    or not isinstance(result.get("particular"), list)
                    or not isinstance(result.get("nullvector"), list)
                    or len(result["particular"]) != order or len(result["nullvector"]) != order):
                return False
            particular = [_parse_result_fraction(item) for item in result["particular"]]
            nullvector = [_parse_result_fraction(item) for item in result["nullvector"]]
            if not any(nullvector):
                return False
            for row in range(equation_count):
                lhs_particular = sum((matrix[row][col] * particular[col] for col in range(order)), Fraction(0))
                lhs_null = sum((matrix[row][col] * nullvector[col] for col in range(order)), Fraction(0))
                if lhs_particular != matrix[row][-1] or lhs_null:
                    return False
            return True
        return False
    except (TypeError, ValueError, KeyError, ZeroDivisionError, OverflowError):
        return False


def predict_recurrence(result, prefix, steps=1) -> dict[str, Any]:
    """Conditionally forecast from any consistent exact prefix of a unique finite fit.

    The prefix need not equal the training data, but it must be an exact rational
    sequence satisfying the recurrence so far. A prediction is a model-conditional
    forecast, never evidence that the recurrence is an infinite law.
    """
    if not isinstance(result, dict) or result.get("status") != "unique":
        raise ValueError("prediction requires a unique recurrence fit")
    if not verify_recurrence(result.get("training_values"), result):
        raise ValueError("recurrence fit certificate is invalid")
    if type(steps) is not int or not 0 <= steps <= MAX_PREDICTION_STEPS:
        raise ValueError(f"steps must be an integer from 0 to {MAX_PREDICTION_STEPS}")
    sequence = _parse_values(prefix)
    order = result["order"]
    if len(sequence) < order:
        raise ValueError("prefix must contain at least order initial values")
    coefficients = [_parse_result_fraction(item) for item in result["coefficients"]]
    for index in range(len(sequence) - order):
        expected = sum((coefficients[j] * sequence[index + j] for j in range(order)), Fraction(0))
        if expected != sequence[index + order]:
            raise ValueError("prefix contradicts the fitted recurrence")
    predictions = []
    for _ in range(steps):
        value = sum((coefficients[j] * sequence[-order + j] for j in range(order)), Fraction(0))
        value = _check_fraction(value)
        predictions.append(_qstr(value))
        sequence.append(value)
    return {"values": predictions,
            "interpretation": "conditional extrapolation from this recurrence and supplied prefix; verify prospectively"}
