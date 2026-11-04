"""Bounded exact fits of first-order rational term ratios.

Fits Q(n) x[n+1] = P(n) x[n] with Q(0)=1 and bounded polynomial degrees.
These are finite-data model-family fits; neither a fit nor a forecast proves an
infinite law or identifies a historical author's method.
"""

from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import re
from typing import Any


FORMAT = "normalized-rational-ratio-v1"
INTERPRETATION = "A finite exact fit only; infinite validity is not inferred."
MAX_SAMPLES = 512
MAX_DEGREE = 4
MAX_INPUT_BITS = 512
MAX_INTERMEDIATE_BITS = 4096
MAX_INPUT_TEXT = 1024
MAX_RESULT_TEXT = 2500
MAX_STEPS = 2_000_000
MAX_PREDICTION_STEPS = 512
MAX_INDEX = 1_000_000
_RATIONAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:/(?:0|[1-9][0-9]*))?\Z")


class _Budget:
    def __init__(self):
        self.steps = 0

    def tick(self, n=1):
        self.steps += n
        if self.steps > MAX_STEPS:
            raise ValueError("exact elimination step budget exceeded")


def _qstr(x: Fraction) -> str:
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


def _parse(value: Any, *, result=False) -> Fraction:
    if type(value) is int:
        f = Fraction(value)
    elif isinstance(value, str) and len(value) <= (MAX_RESULT_TEXT if result else MAX_INPUT_TEXT) and _RATIONAL.fullmatch(value):
        try:
            f = Fraction(value)
        except (ValueError, ZeroDivisionError) as e:
            raise ValueError("invalid rational") from e
        if _qstr(f) != value:
            raise ValueError("rational strings must be canonical")
    else:
        raise ValueError("values must be integers or canonical rational strings; floats and booleans are rejected")
    limit = MAX_INTERMEDIATE_BITS if result else MAX_INPUT_BITS
    if f.numerator.bit_length() > limit or f.denominator.bit_length() > limit:
        raise ValueError("rational exceeds bit limit")
    return f


def _values(values):
    if not isinstance(values, list) or not values or len(values) > MAX_SAMPLES:
        raise ValueError(f"values must be a nonempty list of at most {MAX_SAMPLES} exact rationals")
    return [_parse(v) for v in values]


def _check(f):
    if f.numerator.bit_length() > MAX_INTERMEDIATE_BITS or f.denominator.bit_length() > MAX_INTERMEDIATE_BITS:
        raise ValueError("exact rational intermediate exceeds bit limit")
    return f


def _digest(strings):
    return hashlib.sha256(json.dumps(strings, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def _system(xs, degree):
    # Parameters are p0..pd, q1..qd; q0 is fixed to one.
    cols = 2 * degree + 1
    matrix = []
    for n in range(len(xs) - 1):
        row = [xs[n] * (n ** j) for j in range(degree + 1)]
        row += [-xs[n + 1] * (n ** j) for j in range(1, degree + 1)]
        matrix.append(row + [xs[n + 1]])
    return matrix, cols


def _rref(matrix, columns, budget):
    row_ids = list(range(len(matrix)))
    width = len(matrix[0]) if matrix else columns
    pivots = []
    prow = 0
    for col in range(columns):
        pick = next((r for r in range(prow, len(matrix)) if matrix[r][col]), None)
        if pick is None:
            continue
        matrix[prow], matrix[pick] = matrix[pick], matrix[prow]
        row_ids[prow], row_ids[pick] = row_ids[pick], row_ids[prow]
        pivot = matrix[prow][col]
        for j in range(col, width):
            budget.tick()
            matrix[prow][j] = _check(matrix[prow][j] / pivot)
        for r in range(len(matrix)):
            if r == prow:
                continue
            factor = matrix[r][col]
            if factor:
                for j in range(col, width):
                    budget.tick()
                    matrix[r][j] = _check(matrix[r][j] - factor * matrix[prow][j])
        pivots.append((prow, col))
        prow += 1
        if prow == len(matrix):
            break
    return matrix, row_ids, pivots


def _det(matrix, budget):
    n = len(matrix)
    if any(len(row) != n for row in matrix):
        raise ValueError("minor must be square")
    a = [[Fraction(x) for x in row] for row in matrix]
    out, sign = Fraction(1), 1
    for col in range(n):
        pick = next((r for r in range(col, n) if a[r][col]), None)
        if pick is None:
            return Fraction(0)
        if pick != col:
            a[pick], a[col] = a[col], a[pick]
            sign = -sign
        pivot = a[col][col]
        out = _check(out * pivot)
        for r in range(col + 1, n):
            if not a[r][col]:
                continue
            factor = a[r][col] / pivot
            for j in range(col + 1, n):
                budget.tick()
                a[r][j] = _check(a[r][j] - factor * a[col][j])
    return _check(out * sign)


def _lambda_witness(xs, degree, budget):
    matrix, cols = _system(xs, degree)
    m = len(matrix)
    trans = [[matrix[r][c] for r in range(m)] for c in range(cols)]
    rr, _, pivots = _rref(trans, m, budget)
    pivotcols = {c for _, c in pivots}
    for free in range(m):
        if free in pivotcols:
            continue
        lam = [Fraction(0) for _ in range(m)]
        lam[free] = Fraction(1)
        for row, col in pivots:
            lam[col] = -rr[row][free]
        dot = sum((lam[r] * matrix[r][-1] for r in range(m)), Fraction(0))
        budget.tick(m)
        if dot:
            return {"degree": degree, "lambda": [_qstr(v) for v in lam], "dot_b": _qstr(dot)}
    raise AssertionError("inconsistent system had no witness")


def _denominator(coefficients, degree, n):
    return Fraction(1) + sum((coefficients[degree + j] * (n ** j)
                              for j in range(1, degree + 1)), Fraction(0))


def _bad_denominator(coefficients, degree, count):
    return [n for n in range(count) if not _denominator(coefficients, degree, n)]


def _rank_witness(matrix, cols, row_ids, pivots, budget):
    pivot_cols = [col for _, col in pivots if col < cols]
    rows = [row_ids[row] for row, col in pivots if col < cols]
    minor = [[matrix[r][c] for c in pivot_cols] for r in rows]
    return {"rows": rows, "columns": pivot_cols, "determinant": _qstr(_det(minor, budget))}


def _base(xs, max_degree, search_limit):
    ss = [_qstr(v) for v in xs]
    return {"format": FORMAT, "status": None, "degree": None, "max_degree": max_degree,
            "search_limit": search_limit, "sample_count": len(xs), "training_values": ss,
            "coefficient_layout": "p0..pd,q1..qd; Q(n)=1+sum(qj*n^j)",
            "training_digest": _digest(ss), "coefficients": None, "particular": None,
            "nullvector": None, "degree_rejections": [], "full_rank_witness": None,
            "limits": {"max_samples": MAX_SAMPLES, "max_degree": MAX_DEGREE,
                       "max_input_bits": MAX_INPUT_BITS, "max_intermediate_bits": MAX_INTERMEDIATE_BITS,
                       "max_elimination_steps": MAX_STEPS, "max_prediction_steps": MAX_PREDICTION_STEPS,
                       "max_prediction_index": MAX_INDEX},
            "interpretation": INTERPRETATION}


def fit_ratio(values, max_degree=4) -> dict[str, Any]:
    """Fit the lowest admissible degree in Q(n)x[n+1]=P(n)x[n], Q(0)=1.

    Coefficients are ordered p0..pd,q1..qd. No degree-zero constant Q term
    other than one is fitted. A candidate whose Q vanishes at an observed
    transition index is rejected as inadmissible.
    """
    xs = _values(values)
    if type(max_degree) is not int or not 0 <= max_degree <= MAX_DEGREE:
        raise ValueError(f"max_degree must be an integer from 0 to {MAX_DEGREE}")
    limit = min(max_degree, max(0, len(xs) - 1))
    result, budget, rejections = _base(xs, max_degree, limit), _Budget(), []
    for degree in range(limit + 1):
        matrix, cols = _system(xs, degree)
        rr, row_ids, pivots = _rref([row[:] for row in matrix], cols, budget)
        inconsistent = any(not any(row[:cols]) and row[cols] for row in rr)
        if inconsistent:
            rejections.append({"kind": "inconsistent", **_lambda_witness(xs, degree, budget)})
            continue
        pivotmap = {col: row for row, col in pivots if col < cols}
        particular = [Fraction(0) for _ in range(cols)]
        for col, row in pivotmap.items():
            particular[col] = rr[row][cols]
        freecols = [c for c in range(cols) if c not in pivotmap]
        null = None
        if freecols:
            null_basis = []
            for free in freecols:
                direction = [Fraction(0) for _ in range(cols)]
                direction[free] = Fraction(1)
                for col, row in pivotmap.items():
                    direction[col] = -rr[row][free]
                null_basis.append(direction)
            # A polynomial curve through the affine solution space avoids the
            # finite union of denominator-zero hyperplanes unless one is forced.
            transitions = len(xs) - 1
            candidate = None
            for t in range(transitions * len(null_basis) + 1):
                trial = [particular[i] + sum((Fraction(t ** (k + 1)) * null_basis[k][i]
                                              for k in range(len(null_basis))), Fraction(0))
                         for i in range(cols)]
                if not _bad_denominator(trial, degree, transitions):
                    candidate = trial
                    break
            if candidate is not None:
                particular = candidate
            else:
                forced = []
                for n in range(transitions):
                    if _denominator(particular, degree, n):
                        continue
                    if all(sum((direction[degree + j] * (n ** j)
                                for j in range(1, degree + 1)), Fraction(0)) == 0
                           for direction in null_basis):
                        forced.append(n)
                rejections.append({"kind": "forced_denominator_zero", "degree": degree,
                                   "particular": [_qstr(v) for v in particular],
                                   "null_basis": [[_qstr(v) for v in direction] for direction in null_basis],
                                   "indices": forced,
                                   "rank_witness": _rank_witness(matrix, cols, row_ids, pivots, budget)})
                continue
            null = null_basis[0]
        bad = _bad_denominator(particular, degree, len(xs) - 1)
        if bad:
            minor_rows = [row_ids[row] for row, col in pivots if col < cols]
            det = _det([[matrix[r][c] for c in range(cols)] for r in minor_rows], budget)
            rejections.append({"kind": "denominator_zero", "degree": degree,
                               "coefficients": [_qstr(v) for v in particular], "indices": bad,
                               "full_rank_witness": {"minor_rows": minor_rows, "determinant": _qstr(det)}})
            continue
        result["degree_rejections"] = rejections
        result["degree"] = degree
        if freecols:
            result.update({"status": "underdetermined", "particular": [_qstr(v) for v in particular],
                           "nullvector": [_qstr(v) for v in null],
                           "reason": "minimal admissible degree has non-unique coefficients"})
            return result
        minor_rows = [row_ids[row] for row, col in pivots if col < cols]
        det = _det([[matrix[r][c] for c in range(cols)] for r in minor_rows], budget)
        if not det:
            raise AssertionError("selected rows do not certify full rank")
        result.update({"status": "unique", "coefficients": [_qstr(v) for v in particular],
                       "full_rank_witness": {"minor_rows": minor_rows, "determinant": _qstr(det)}})
        return result
    result.update({"status": "no_fit", "degree_rejections": rejections,
                   "reason": "no admissible exact rational ratio in the searched degree range"})
    return result


def _verify_witness(xs, witness, degree):
    if not isinstance(witness, dict) or set(witness) != {"kind", "degree", "lambda", "dot_b"}:
        return False
    if witness.get("kind") != "inconsistent" or type(witness.get("degree")) is not int or witness["degree"] != degree:
        return False
    matrix, cols = _system(xs, degree)
    m = len(matrix)
    if not isinstance(witness["lambda"], list) or len(witness["lambda"]) != m:
        return False
    try:
        lam = [_parse(x, result=True) for x in witness["lambda"]]
        dot = _parse(witness["dot_b"], result=True)
    except (TypeError, ValueError):
        return False
    if not any(lam):
        return False
    if any(sum((lam[r] * matrix[r][c] for r in range(m)), Fraction(0)) for c in range(cols)):
        return False
    actual = sum((lam[r] * matrix[r][-1] for r in range(m)), Fraction(0))
    return actual != 0 and actual == dot


def _verify_minor(xs, degree, witness):
    if not isinstance(witness, dict) or set(witness) != {"minor_rows", "determinant"}:
        return False
    matrix, cols = _system(xs, degree)
    rows = witness["minor_rows"]
    if (not isinstance(rows, list) or len(rows) != cols or len(set(rows)) != cols
            or any(type(r) is not int or not 0 <= r < len(matrix) for r in rows)):
        return False
    try:
        claimed = _parse(witness["determinant"], result=True)
        actual = _det([[matrix[r][c] for c in range(cols)] for r in rows], _Budget())
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return actual != 0 and actual == claimed


def _verify_denominator_rejection(xs, witness, degree):
    if not isinstance(witness, dict) or set(witness) != {
            "kind", "degree", "coefficients", "indices", "full_rank_witness"}:
        return False
    if witness.get("kind") != "denominator_zero" or type(witness.get("degree")) is not int or witness["degree"] != degree:
        return False
    matrix, cols = _system(xs, degree)
    coeffs = witness.get("coefficients")
    if not isinstance(coeffs, list) or len(coeffs) != cols:
        return False
    try:
        c = [_parse(v, result=True) for v in coeffs]
    except (TypeError, ValueError):
        return False
    if any(sum((row[j] * c[j] for j in range(cols)), Fraction(0)) != row[-1] for row in matrix):
        return False
    indices = _bad_denominator(c, degree, len(xs) - 1)
    return bool(indices) and witness.get("indices") == indices and _verify_minor(
        xs, degree, witness.get("full_rank_witness"))


def _verify_forced_denominator_rejection(xs, witness, degree):
    if not isinstance(witness, dict) or set(witness) != {
            "kind", "degree", "particular", "null_basis", "indices", "rank_witness"}:
        return False
    if witness.get("kind") != "forced_denominator_zero" or type(witness.get("degree")) is not int or witness["degree"] != degree:
        return False
    matrix, cols = _system(xs, degree)
    try:
        p = [_parse(v, result=True) for v in witness["particular"]]
        basis = [[_parse(v, result=True) for v in vec] for vec in witness["null_basis"]]
    except (TypeError, ValueError):
        return False
    if len(p) != cols or not basis or any(len(v) != cols for v in basis):
        return False
    if any(sum((row[j] * p[j] for j in range(cols)), Fraction(0)) != row[-1] for row in matrix):
        return False
    if any(any(sum((row[j] * v[j] for j in range(cols)), Fraction(0)) for row in matrix) for v in basis):
        return False
    transposed = [[v[j] for v in basis] for j in range(cols)]
    reduced, _, pivots = _rref(transposed, len(basis), _Budget())
    if len(pivots) != len(basis):
        return False
    if not _verify_rank(xs, degree, witness.get("rank_witness")):
        return False
    forced = []
    for n in range(len(xs) - 1):
        if _denominator(p, degree, n):
            continue
        if all(sum((v[degree + j] * (n ** j) for j in range(1, degree + 1)), Fraction(0)) == 0
               for v in basis):
            forced.append(n)
    return bool(forced) and witness.get("indices") == forced and len(basis) == cols - len(witness["rank_witness"]["columns"])


def _verify_rank(xs, degree, witness):
    if not isinstance(witness, dict) or set(witness) != {"rows", "columns", "determinant"}:
        return False
    matrix, cols = _system(xs, degree)
    rows, columns = witness["rows"], witness["columns"]
    if (not isinstance(rows, list) or not isinstance(columns, list) or len(rows) != len(columns)
            or len(set(rows)) != len(rows) or len(set(columns)) != len(columns)
            or any(type(r) is not int or not 0 <= r < len(matrix) for r in rows)
            or any(type(c) is not int or not 0 <= c < cols for c in columns)):
        return False
    try:
        claimed = _parse(witness["determinant"], result=True)
        actual = _det([[matrix[r][c] for c in columns] for r in rows], _Budget())
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return actual != 0 and actual == claimed


def verify_ratio(values, result) -> bool:
    """Check fit equations and minimality/full-rank certificates independently."""
    try:
        xs = _values(values)
        if not isinstance(result, dict):
            return False
        required = {"format", "status", "degree", "max_degree", "search_limit", "sample_count", "coefficient_layout",
                    "training_values", "training_digest", "coefficients", "particular", "nullvector",
                    "degree_rejections", "full_rank_witness", "limits", "interpretation"}
        if result.get("status") in ("underdetermined", "no_fit"):
            required.add("reason")
        if set(result) != required or result.get("format") != FORMAT:
            return False
        ss = [_qstr(x) for x in xs]
        if result.get("training_values") != ss or result.get("training_digest") != _digest(ss):
            return False
        md = result.get("max_degree")
        if type(md) is not int or not 0 <= md <= MAX_DEGREE or result.get("sample_count") != len(xs):
            return False
        limit = min(md, max(0, len(xs) - 1))
        if type(result.get("search_limit")) is not int or result["search_limit"] != limit:
            return False
        limits = {"max_samples": MAX_SAMPLES, "max_degree": MAX_DEGREE,
                  "max_input_bits": MAX_INPUT_BITS, "max_intermediate_bits": MAX_INTERMEDIATE_BITS,
                  "max_elimination_steps": MAX_STEPS, "max_prediction_steps": MAX_PREDICTION_STEPS,
                  "max_prediction_index": MAX_INDEX}
        if (result.get("limits") != limits or result.get("interpretation") != INTERPRETATION
                or result.get("coefficient_layout") != "p0..pd,q1..qd; Q(n)=1+sum(qj*n^j)"):
            return False
        if type(result.get("sample_count")) is not int:
            return False
        rejs = result.get("degree_rejections")
        if not isinstance(rejs, list):
            return False
        status = result["status"]
        if status == "no_fit":
            if (result.get("degree") is not None or result.get("coefficients") is not None
                    or result.get("particular") is not None or result.get("nullvector") is not None
                    or result.get("full_rank_witness") is not None or len(rejs) != limit + 1):
                return False
            for d, witness in enumerate(rejs):
                if witness.get("kind") == "inconsistent":
                    if not _verify_witness(xs, witness, d):
                        return False
                elif witness.get("kind") == "denominator_zero":
                    if not _verify_denominator_rejection(xs, witness, d):
                        return False
                elif witness.get("kind") == "forced_denominator_zero":
                    if not _verify_forced_denominator_rejection(xs, witness, d):
                        return False
                else:
                    return False
            return True
        degree = result.get("degree")
        if type(degree) is not int or not 0 <= degree <= limit or len(rejs) != degree:
            return False
        for d, witness in enumerate(rejs):
            if witness.get("kind") == "inconsistent":
                if not _verify_witness(xs, witness, d):
                    return False
            elif witness.get("kind") == "denominator_zero":
                if not _verify_denominator_rejection(xs, witness, d):
                    return False
            elif witness.get("kind") == "forced_denominator_zero":
                if not _verify_forced_denominator_rejection(xs, witness, d):
                    return False
            else:
                return False
        matrix, cols = _system(xs, degree)
        coeffs = result.get("coefficients") if status == "unique" else result.get("particular")
        if not isinstance(coeffs, list) or len(coeffs) != cols:
            return False
        c = [_parse(v, result=True) for v in coeffs]
        if any(sum((row[j] * c[j] for j in range(cols)), Fraction(0)) != row[-1] for row in matrix):
            return False
        if _bad_denominator(c, degree, len(xs) - 1):
            return False
        if status == "unique":
            return (result.get("particular") is None and result.get("nullvector") is None
                    and _verify_minor(xs, degree, result.get("full_rank_witness")))
        if status == "underdetermined":
            if result.get("coefficients") is not None or result.get("full_rank_witness") is not None:
                return False
            nv = result.get("nullvector")
            if not isinstance(nv, list) or len(nv) != cols:
                return False
            v = [_parse(item, result=True) for item in nv]
            return any(v) and all(sum((row[j] * v[j] for j in range(cols)), Fraction(0)) == 0 for row in matrix)
        return False
    except (TypeError, ValueError, KeyError, ZeroDivisionError, OverflowError, AttributeError):
        return False


def predict_ratio(result, start_index, value, steps=1) -> dict[str, Any]:
    """Forecast from an explicitly indexed seed under a verified unique fit."""
    if not isinstance(result, dict) or result.get("status") != "unique":
        raise ValueError("prediction requires a unique rational-ratio fit")
    if not verify_ratio(result.get("training_values"), result):
        raise ValueError("rational-ratio certificate is invalid")
    if type(start_index) is not int or not 0 <= start_index <= MAX_INDEX:
        raise ValueError(f"start_index must be an integer from 0 to {MAX_INDEX}")
    if type(steps) is not int or not 0 <= steps <= MAX_PREDICTION_STEPS:
        raise ValueError(f"steps must be an integer from 0 to {MAX_PREDICTION_STEPS}")
    if start_index + steps > MAX_INDEX:
        raise ValueError("prediction exceeds maximum index")
    x = _parse(value)
    if start_index < len(result["training_values"]):
        trained = _parse(result["training_values"][start_index], result=True)
        if x != trained:
            raise ValueError("seed contradicts the fitted training value at start_index")
    degree = result["degree"]
    coeffs = [_parse(c, result=True) for c in result["coefficients"]]
    out = []
    for offset in range(steps):
        n = start_index + offset
        q = _denominator(coeffs, degree, n)
        if not q:
            raise ValueError(f"fitted denominator is zero at prediction index {n}")
        p = sum((coeffs[j] * (n ** j) for j in range(degree + 1)), Fraction(0))
        x = _check(x * p / q)
        target_index = n + 1
        if target_index < len(result["training_values"]):
            observed = _parse(result["training_values"][target_index], result=True)
            if x != observed:
                raise ValueError("forecast contradicts the fitted training value")
        out.append(_qstr(x))
    return {"values": out,
            "interpretation": "conditional extrapolation from this finite rational-ratio fit and supplied indexed seed; verify prospectively"}
