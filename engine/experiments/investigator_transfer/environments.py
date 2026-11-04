"""Generated black-box tasks for the investigator-transfer experiment.

The public task contract deliberately contains no mechanism description beyond
the input and output schemas.  A caller can expose ``observe`` as an oracle and
keep ``checks`` and ``private_manifest`` on the host for qualification.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import random
from typing import Any, Callable


Family = str

SOURCE_FAMILIES = ("source_affine", "source_piecewise")
TARGET_FAMILIES = ("target_string", "target_mechanical")
ALL_FAMILIES = SOURCE_FAMILIES + TARGET_FAMILIES


@dataclass(frozen=True)
class InvestigatorTask:
    """A task with a public contract and host-only qualification material."""

    family: Family
    public: dict[str, Any]
    observe: Callable[[dict[str, Any]], dict[str, Any]]
    checks: tuple[dict[str, Any], ...]
    private_manifest: dict[str, Any]


def available_families() -> tuple[str, ...]:
    """Return the stable names accepted by :func:`make_task`."""

    return ALL_FAMILIES


def _rng(family: str, seed: int) -> random.Random:
    material = f"kuttaka-investigator-transfer-v1\0{family}\0{int(seed)}".encode()
    derived = int.from_bytes(hashlib.sha256(material).digest()[:16], "big")
    return random.Random(derived)


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _schema(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _validate_object(value: Any, *, required: tuple[str, ...], keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    if set(value) != set(keys):
        missing = sorted(set(required) - set(value))
        extra = sorted(set(value) - set(keys))
        raise ValueError(f"input keys do not match schema (missing={missing}, extra={extra})")
    return value


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _build(
    family: str,
    seed: int,
    public: dict[str, Any],
    oracle: Callable[[dict[str, Any]], dict[str, Any]],
    check_inputs: list[dict[str, Any]],
    truth: dict[str, Any],
) -> InvestigatorTask:
    checks = tuple({"input": item, "expected": oracle(item)} for item in check_inputs)
    return InvestigatorTask(
        family=family,
        public=public,
        observe=oracle,
        checks=checks,
        private_manifest={
            "task_id": f"{family}:{int(seed)}",
            "family": family,
            "seed": int(seed),
            "truth_sha256": _canonical_hash(truth),
            "checks_sha256": _canonical_hash(checks),
        },
    )


def _source_affine(seed: int) -> InvestigatorTask:
    rng = _rng("source_affine", seed)
    slope = rng.choice((-3, -2, 2, 3))
    intercept = rng.randint(-5, 5)

    def observe(value: dict[str, Any]) -> dict[str, Any]:
        value = _validate_object(value, required=("x",), keys=("x",))
        x = _integer(value["x"], "x", -12, 12)
        return {"y": slope * x + intercept}

    public = {
        "goal": "Determine the output of an unfamiliar numeric device for any valid input.",
        "input_schema": _schema({"x": {"type": "integer", "minimum": -12, "maximum": 12}}, ("x",)),
        "output_schema": _schema({"y": {"type": "integer"}}, ("y",)),
        "domain_description": "A deterministic device accepts one signed integer and reports one integer reading.",
    }
    qualification_inputs = [{"x": x} for x in range(-12, 13)]
    return _build("source_affine", seed, public, observe, qualification_inputs, {"slope": slope, "intercept": intercept})


def _source_piecewise(seed: int) -> InvestigatorTask:
    rng = _rng("source_piecewise", seed)
    split = rng.randint(-2, 2)
    left_slope = rng.choice((-3, -2, 1, 2, 3))
    right_slope = rng.choice((-3, -2, 1, 2, 3))
    if right_slope == left_slope:
        right_slope = 1 if left_slope != 1 else -2
    left_offset = rng.randint(-3, 3)
    right_offset = rng.randint(-3, 3)

    def observe(value: dict[str, Any]) -> dict[str, Any]:
        value = _validate_object(value, required=("x",), keys=("x",))
        x = _integer(value["x"], "x", -12, 12)
        slope, offset = (left_slope, left_offset) if x <= split else (right_slope, right_offset)
        return {"y": slope * x + offset}

    public = {
        "goal": "Determine the output of an unfamiliar numeric device for any valid input.",
        "input_schema": _schema({"x": {"type": "integer", "minimum": -12, "maximum": 12}}, ("x",)),
        "output_schema": _schema({"y": {"type": "integer"}}, ("y",)),
        "domain_description": "A deterministic device accepts one signed integer and reports one integer reading.",
    }
    qualification_inputs = [{"x": x} for x in range(-12, 13)]
    truth = {
        "split": split,
        "left_slope": left_slope,
        "left_offset": left_offset,
        "right_slope": right_slope,
        "right_offset": right_offset,
    }
    return _build("source_piecewise", seed, public, observe, qualification_inputs, truth)


def _target_string(seed: int) -> InvestigatorTask:
    rng = _rng("target_string", seed)
    shift = rng.randint(1, 9)
    drift = rng.randint(0, 4)
    reverse = bool(rng.randrange(2))
    rotate = rng.randrange(4)

    def observe(value: dict[str, Any]) -> dict[str, Any]:
        value = _validate_object(value, required=("text",), keys=("text",))
        text = value["text"]
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if len(text) > 12 or any(not ("A" <= char <= "Z") for char in text):
            raise ValueError("text must contain 0 to 12 uppercase A-Z characters")
        transformed = "".join(chr((ord(char) - ord("A") + shift + drift * index) % 26 + ord("A"))
                             for index, char in enumerate(text))
        if reverse:
            transformed = transformed[::-1]
        if transformed:
            amount = rotate % len(transformed)
            transformed = transformed[amount:] + transformed[:amount]
        return {"text": transformed}

    public = {
        "goal": "Determine the output of an unfamiliar text device for any valid input.",
        "input_schema": _schema({"text": {"type": "string", "pattern": "^[A-Z]{0,12}$"}}, ("text",)),
        "output_schema": _schema({"text": {"type": "string"}}, ("text",)),
        "domain_description": "A deterministic text device accepts a short string of uppercase English letters and returns a string.",
    }
    probes = [
        {"text": ""}, {"text": "A"}, {"text": "Z"}, {"text": "AB"},
        {"text": "BA"}, {"text": "ABCDE"}, {"text": "AZBYC"}, {"text": "ABCDEFGHIJKL"},
    ]
    check_rng = _rng("target_string-checks", seed)
    used = {item["text"] for item in probes}
    challenge_inputs = []
    while len(challenge_inputs) < 8:
        length = check_rng.randint(0, 12)
        candidate = "".join(chr(ord("A") + check_rng.randrange(26)) for _ in range(length))
        # The fixed set already covers the empty string and common short probes.
        if not candidate or candidate in used:
            continue
        challenge_inputs.append({"text": candidate})
        used.add(candidate)
    # Guarantee a repeated-symbol challenge independent of random chance.
    repeated = chr(ord("A") + check_rng.randrange(26))
    repeated_candidate = repeated * 6
    while repeated_candidate in used:
        repeated = chr(ord("A") + (ord(repeated) - ord("A") + 1) % 26)
        repeated_candidate = repeated * 6
    challenge_inputs[-1] = {"text": repeated_candidate}
    probes.extend(challenge_inputs)
    truth = {"shift": shift, "drift": drift, "reverse": reverse, "rotate": rotate}
    return _build("target_string", seed, public, observe, probes, truth)


def _target_mechanical(seed: int) -> InvestigatorTask:
    rng = _rng("target_mechanical", seed)
    spring = rng.randint(1, 3)
    load_ratio = rng.randint(1, 2)
    gain = rng.randint(1, 3)
    latch_mode = bool(rng.randrange(2))

    def observe(value: dict[str, Any]) -> dict[str, Any]:
        value = _validate_object(value, required=("drive", "load", "latch"), keys=("drive", "load", "latch"))
        drive = _integer(value["drive"], "drive", 0, 12)
        load = _integer(value["load"], "load", 0, 5)
        latch = value["latch"]
        if not isinstance(latch, bool):
            raise ValueError("latch must be a boolean")
        # A latched apparatus is blocked only in one generated configuration.
        blocked = latch and latch_mode
        displacement = 0 if blocked else max(0, drive - spring - load_ratio * load) * gain
        return {"displacement": displacement}

    public = {
        "goal": "Predict the displacement reported by an unfamiliar tabletop apparatus.",
        "input_schema": _schema({
            "drive": {"type": "integer", "minimum": 0, "maximum": 12},
            "load": {"type": "integer", "minimum": 0, "maximum": 5},
            "latch": {"type": "boolean"},
        }, ("drive", "load", "latch")),
        "output_schema": _schema({"displacement": {"type": "integer", "minimum": 0}}, ("displacement",)),
        "domain_description": "A deterministic tabletop apparatus accepts a drive setting, a load setting, and a latch state, then reports one displacement reading.",
    }
    probes = [
        {"drive": 0, "load": 0, "latch": False},
        {"drive": 12, "load": 0, "latch": False},
        {"drive": 6, "load": 0, "latch": False},
        {"drive": 12, "load": 3, "latch": False},
        {"drive": 12, "load": 5, "latch": False},
        {"drive": 12, "load": 0, "latch": True},
        {"drive": 4, "load": 2, "latch": False},
        {"drive": 7, "load": 4, "latch": True},
    ]
    check_rng = _rng("target_mechanical-checks", seed)
    used = {(row["drive"], row["load"], row["latch"]) for row in probes}
    while len(probes) < 24:
        row = {
            "drive": check_rng.randint(0, 12),
            "load": check_rng.randint(0, 5),
            "latch": bool(check_rng.randrange(2)),
        }
        key = (row["drive"], row["load"], row["latch"])
        if key in used:
            continue
        probes.append(row)
        used.add(key)
    truth = {"spring": spring, "load_ratio": load_ratio, "gain": gain, "latch_mode": latch_mode}
    return _build("target_mechanical", seed, public, observe, probes, truth)


def make_task(family: str, seed: int) -> InvestigatorTask:
    """Generate one deterministic task instance.

    The returned ``public`` object is the only task material intended for a
    live model. ``observe`` is the bounded oracle callback; the caller must keep
    ``checks`` and ``private_manifest`` on the scoring side of the boundary.
    """

    if family not in ALL_FAMILIES:
        raise ValueError(f"unknown family {family!r}; expected one of {ALL_FAMILIES}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    builders = {
        "source_affine": _source_affine,
        "source_piecewise": _source_piecewise,
        "target_string": _target_string,
        "target_mechanical": _target_mechanical,
    }
    return builders[family](seed)
