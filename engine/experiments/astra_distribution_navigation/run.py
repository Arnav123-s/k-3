"""Authored study of seeded distribution probes for Moore navigation maps.

Every fixture and evaluator truth table is developer-authored. The learner sees
only reset/step callbacks. Seeded probe runs are reproducible finite tests, not
IID certificates; exact evaluator checks happen only after learning.
"""

from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random
import sys

from experiments.astra_active_navigation.run import ACTIONS, MooreWorld, _make_long_trigger
from experiments.astra_model_experiments.run import (
    AuthoredWorld, CONTEXT, LIMITS, REQUEST, _candidate_model, _generated_world,
    _hidden_alias_world, exact_equivalence,
)
from experiments.astra_distribution_navigation.evaluate import disagreement_mass
from kuttaka_runtime.active_navigation import ActiveNavigation
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.goals import GoalLedger
from kuttaka_runtime.navigation_tasks import execute_route, retain_route
from kuttaka_runtime.store import Journal, canonical


ROOT = Path(__file__).resolve().parents[2]
SHORTLEX_ARM = "shortlex-depth4"
SAMPLING_SEEDS = (701, 809, 907)
SAMPLING_CONTRACT = {"max_length": 16, "epsilon": [1, 20], "delta": [1, 20]}
LIMITS = {"max_queries": 4096, "max_actions": 50000,
          "max_word_length": 32, "max_states": 128}


def _kmp_step(matched, action, pattern):
    candidate = list(pattern[:matched]) + [action]
    for size in range(min(len(pattern), len(candidate)), 0, -1):
        if list(pattern[:size]) == candidate[-size:]:
            return size
    return 0


def _kmp_world(pattern, *, actions=ACTIONS):  # noqa: F811
    """Build the prefix-state Moore machine for a fixed substring trigger."""
    if not pattern or any(action not in actions for action in pattern):
        raise ValueError("nonempty pattern over declared alphabet required")
    transitions = {}
    for matched in range(len(pattern)):
        row = {}
        for action in actions:
            count = _kmp_step(matched, action, pattern)
            row[action] = "goal" if count == len(pattern) else f"p{count}"
        transitions[f"p{matched}"] = row
    transitions["goal"] = {action: "goal" for action in actions}
    outputs = {f"p{i}": "0" for i in range(len(pattern))}
    outputs["goal"] = "G"
    return AuthoredWorld(transitions, outputs, initial="p0", actions=actions)


def _sample_patterns():
    rng = random.Random(2029)
    return {length: [rng.choice(ACTIONS) for _ in range(length)] for length in (5, 7)}


def _exact_prefix_world(word):
    """Only the full listed word reaches G; every mismatch enters a sink."""
    states = [f"q{i}" for i in range(len(word)+1)] + ["sink"]
    transitions = {state: {a: "sink" for a in ACTIONS} for state in states}
    for i, action in enumerate(word):
        transitions[f"q{i}"][action] = f"q{i+1}"
    transitions[f"q{len(word)}"] = {a: f"q{len(word)}" for a in ACTIONS}
    transitions["sink"] = {a: "sink" for a in ACTIONS}
    outputs = {state: "0" for state in states}
    outputs[f"q{len(word)}"] = "G"
    return AuthoredWorld(transitions, outputs, initial="q0", actions=ACTIONS)


def _fixed_cases():
    result = []
    for state_count in (6, 10):
        for seed in (211, 223):
            for partially_observed in (False, True):
                variant = "colors" if partially_observed else "visible"
                name = f"generated_{state_count}_{seed}_{variant}"
                result.append({"case": name, "origin": "authored-seeded-generator",
                    "factory": lambda n=state_count, s=seed, p=partially_observed:
                        _generated_world(n, s, partially_observed=p)[0],
                    "truth_factory": lambda n=state_count, s=seed, p=partially_observed:
                        _generated_world(n, s, partially_observed=p)[1]})
    for delay in (2, 3):
        result.append({"case": f"concealed_alias_{delay}", "origin": "authored-concealed-alias",
            "factory": lambda d=delay: _hidden_alias_world(d)[0],
            "truth_factory": lambda d=delay: _hidden_alias_world(d)[1]})
    for length in (6, 8):
        result.append({"case": f"long_trigger_{length}", "origin": "authored-long-trigger",
            "factory": lambda n=length: _make_long_trigger(goal_after=n),
            "truth_factory": lambda n=length: {"trigger_length_evaluator_only": n,
                "transitions_evaluator_only": _make_long_trigger(goal_after=n).transitions,
                "outputs_evaluator_only": _make_long_trigger(goal_after=n).outputs,
                "target_word_evaluator_only": ["a"]*n}})
    patterns = _sample_patterns()
    for length, pattern in patterns.items():
        result.append({"case": f"kmp_substring_{length}", "origin": "authored-kmp-substring",
            "factory": lambda p=pattern: _kmp_world(p),
            "truth_factory": lambda p=pattern: {"pattern_evaluator_only": p,
                "transitions_evaluator_only": _kmp_world(p).transitions,
                "outputs_evaluator_only": _kmp_world(p).outputs,
                "state_count_evaluator_only": len(_kmp_world(p).outputs)}})
    result.append({"case": "long_trigger_17_beyond_support", "origin": "authored-beyond-probe-support",
        "factory": lambda: _make_long_trigger(goal_after=17),
        "truth_factory": lambda: {"trigger_length_evaluator_only": 17,
            "transitions_evaluator_only": _make_long_trigger(goal_after=17).transitions,
            "outputs_evaluator_only": _make_long_trigger(goal_after=17).outputs,
            "target_word_evaluator_only": ["a"]*17}})
    rng = random.Random(2039)
    for length in (14, 16):
        word = [rng.choice(ACTIONS) for _ in range(length)]
        result.append({"case": f"exact_prefix_sink_{length}", "origin": "authored-rare-exact-prefix",
            "factory": lambda w=word: _exact_prefix_world(w),
            "truth_factory": lambda w=word: {"word_evaluator_only": w,
                "transitions_evaluator_only": _exact_prefix_world(w).transitions,
                "outputs_evaluator_only": _exact_prefix_world(w).outputs,
                "state_count_evaluator_only": len(_exact_prefix_world(w).outputs)}})
    return result


def _arm_specs():
    result = [(SHORTLEX_ARM, "shortlex", None)]
    for persistence, mode in ((False, "uniform"), (True, "persistent")):
        for seed in SAMPLING_SEEDS:
            label = f"distribution-{mode}-{seed}"
            result.append((label, "distribution", {**SAMPLING_CONTRACT,
                "seed": seed, "persistence": persistence}))
    return result


def _candidate_model(memory, artifact_id):
    return memory.get(artifact_id)["body"] if artifact_id else None


def _run_arm(output, case, arm, *, counterexample_strategy='prefixes', hybrid_compact=False):
    label, strategy, sampling = arm
    world = case["factory"]()
    store_dir = output / "stores" / case["case"] / label
    journal_path = output / "journals" / f"{case['case']}--{label}.jsonl"
    store_dir.parent.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    learn_result = route = execution = model_id = model = None
    learn_resets = learn_actions = 0
    learning_finished = False
    error = None
    try:
        with Journal(journal_path) as journal, ArtifactMemory(store_dir) as memory:
            goals = GoalLedger(REQUEST, journal)
            proposal = goals.propose({"description": "Reach observable G.",
                "success_criteria": ["reset/step observation equals G"], "unknowns": [],
                "tradeoffs": [], "parent": None, "based_on": [goals.request_evidence]})
            goals.authorize(proposal["goal"], user_evidence=goals.request_evidence)
            learner = ActiveNavigation(memory, journal, world.reset, world.step,
                actions=list(ACTIONS), context=CONTEXT, **LIMITS)
            refinement = ({'counterexample_strategy': counterexample_strategy}
                          if counterexample_strategy != 'prefixes' else {})
            if hybrid_compact:
                refinement['hybrid_compact'] = True
            learn_result = learner.learn(challenge_depth=4, strategy=strategy,
                max_schedule_work=1_000_000, sampling=sampling, **refinement)
            learn_resets, learn_actions = world.reset_calls, world.step_calls
            learning_finished = True
            model_id = learn_result.get("artifact")
            model = _candidate_model(memory, model_id)
            model_evaluations = []
            for issued_id in learner._models:
                issued_model = _candidate_model(memory, issued_id)
                model_evaluations.append({"model_id": issued_id,
                    "exact_equivalence_evaluator_only": exact_equivalence(world, issued_model),
                    "disagreement_mass_evaluator_only": {
                        "uniform": disagreement_mass(world, issued_model, max_length=16, persistence=False),
                        "persistent": disagreement_mass(world, issued_model, max_length=16, persistence=True)}})
            final_evaluation = next((item for item in model_evaluations
                if item["model_id"] == model_id), None)
            exact = final_evaluation["exact_equivalence_evaluator_only"] if final_evaluation else {
                "status": "not_checked", "reason": "no issued model artifact"}
            masses = final_evaluation["disagreement_mass_evaluator_only"] if final_evaluation else None
            if learn_result.get("status") == "conditional_tested" and model_id:
                route = retain_route(memory, journal, goals, model_id,
                    scope=learner.scope, context=CONTEXT, goal_output="G")
                if route.get("status") == "route_proposed":
                    execution = execute_route(memory, journal, goals, route["artifact"],
                        scope=learner.scope, context=CONTEXT, goal_output="G",
                        reset=world.reset, step=world.step, max_actions=128)
                else:
                    execution = {"status": route.get("status")}
            events = Journal.read(journal_path)
            challenge_count = sum(event["kind"] == "active_navigation_challenge" for event in events)
            constructions = [event["body"].get("construction", {}) for event in events
                if event["kind"] == "active_navigation_distribution_construction"
                or (event['kind'] == 'active_navigation_hybrid_construction'
                    and event['body']['component'] == 'sampled')]
            derived_constructions = [event['body'].get('construction', {}) for event in events
                if event['kind'] == 'active_navigation_schedule_construction'
                or (event['kind'] == 'active_navigation_hybrid_construction'
                    and event['body']['component'] == 'model_derived')]
            all_constructions = constructions + derived_constructions
            construction_work = sum(int(item.get("work", 0)) for item in all_constructions)
            challenge_events = [event for event in events if event["kind"] == "active_navigation_challenge"]
            probes = [event["body"] for event in challenge_events]
            outcome = {"case": case["case"], "arm": label, "strategy": strategy,
                "sampling": sampling, "status": learn_result.get("status"),
                "learn": learn_result, "model_id": model_id,
                "learned_state_count": learn_result.get("states"),
                "exact_equivalence_evaluator_only": exact,
                "disagreement_mass_evaluator_only": masses,
                "issued_model_evaluations_evaluator_only": model_evaluations,
                "route": route, "execution": execution,
                "probe_constructions": constructions,
                "derived_constructions": derived_constructions,
                "actual_costs": {"learning_resets": learn_resets,
                    "learning_actions": learn_actions,
                    "route_resets": (execution or {}).get("reset_attempts", 0),
                    "route_actions": (execution or {}).get("action_attempts", 0),
                    "total_resets": world.reset_calls, "total_actions": world.step_calls,
                    "challenge_attempts_all_candidates": challenge_count,
                    "construction_work_all_candidates": construction_work,
                    "construction_attempts": len(all_constructions),
                    "probe_words_attempted": len(probes)},
                "journal": str(journal_path.relative_to(output)),
                "store": str(store_dir.relative_to(output)), "harness_error": None}
        # Reopen and validate every written chain/blob after the writer closes.
        reopened = Journal.read(journal_path)
        with ArtifactMemory(store_dir) as check_memory:
            if model_id:
                check_memory.get(model_id)
            if route and route.get("artifact"):
                check_memory.get(route["artifact"])
        outcome["integrity"] = {"journal_events": len(reopened),
            "journal_tail": reopened[-1]["hash"] if reopened else "",
            "reopened_model": bool(model_id), "reopened_route": bool(route and route.get("artifact"))}
        return outcome
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        if not learning_finished:
            learn_resets, learn_actions = world.reset_calls, world.step_calls
        try:
            events = Journal.read(journal_path) if journal_path.exists() else []
            constructions = [event["body"].get("construction", {}) for event in events
                if event["kind"] in {'active_navigation_distribution_construction',
                                     'active_navigation_schedule_construction',
                                     'active_navigation_hybrid_construction'}]
            challenge_count = sum(event["kind"] == "active_navigation_challenge" for event in events)
            construction_work = sum(int(item.get("work", 0)) for item in constructions)
        except Exception:
            events, constructions, challenge_count, construction_work = [], None, None, None
        return {"case": case["case"], "arm": label, "strategy": strategy,
            "sampling": sampling, "status": "harness_error", "harness_error": error,
            "model_id": model_id,
            "issued_model_evaluations_evaluator_only": locals().get("model_evaluations", []),
            "actual_costs": {"learning_resets": learn_resets,
                "learning_actions": learn_actions,
                "route_resets": max(0, world.reset_calls-learn_resets) if learning_finished else 0,
                "route_actions": max(0, world.step_calls-learn_actions) if learning_finished else 0,
                "total_resets": world.reset_calls, "total_actions": world.step_calls,
                "challenge_attempts_all_candidates": challenge_count,
                "construction_work_all_candidates": construction_work,
                "construction_attempts": len(constructions) if constructions is not None else None},
            "journal": str(journal_path.relative_to(output)),
            "store": str(store_dir.relative_to(output)),
            "integrity": {"journal_events": len(events) if challenge_count is not None else None,
                "journal_read": challenge_count is not None,
                "journal_reference": str(journal_path.relative_to(output))}}


def _summary(rows, arms):
    by_arm = {}
    for label, _, _ in arms:
        selected = [row for row in rows if row.get("arm") == label]
        exact = [row.get("exact_equivalence_evaluator_only", {}).get("status") for row in selected]
        status_counts = Counter(row.get("status") for row in selected)
        mass_counts = {kind: sum(
            Fraction(item["disagreement_mass_evaluator_only"][kind]["numerator"],
                     item["disagreement_mass_evaluator_only"][kind]["denominator"]) >= Fraction(1, 20)
            for row in selected for item in row.get("issued_model_evaluations_evaluator_only", [])
            if kind in item.get("disagreement_mass_evaluator_only", {}))
            for kind in ("uniform", "persistent")}
        qualified_mass_counts = {kind: sum(
            Fraction(row["disagreement_mass_evaluator_only"][kind]["numerator"],
                     row["disagreement_mass_evaluator_only"][kind]["denominator"]) >= Fraction(1, 20)
            for row in selected if row.get("status") == "conditional_tested"
            and row.get("disagreement_mass_evaluator_only")
            and kind in row["disagreement_mass_evaluator_only"])
            for kind in ("uniform", "persistent")}
        issued_exact = [item.get("exact_equivalence_evaluator_only", {}).get("status")
            for row in selected for item in row.get("issued_model_evaluations_evaluator_only", [])]
        by_arm[label] = {"cases": len(selected), "status_counts": dict(status_counts),
            "conditional_tested": status_counts.get("conditional_tested", 0),
            "exact_maps": exact.count("equivalent"),
            "final_case_maps_exact_equivalent": exact.count("equivalent"),
            "issued_models_exact_equivalent": issued_exact.count("equivalent"),
            "issued_models_not_equivalent": issued_exact.count("not_equivalent"),
            "conditional_but_wrong_maps": sum(row.get("status") == "conditional_tested"
                and row.get("exact_equivalence_evaluator_only", {}).get("status") == "not_equivalent"
                for row in selected),
            "incomplete_or_error": sum(row.get("status") != "conditional_tested" for row in selected),
            "learning_resets": sum((row.get("actual_costs") or {}).get("learning_resets", 0) for row in selected),
            "learning_actions": sum((row.get("actual_costs") or {}).get("learning_actions", 0) for row in selected),
            "challenge_attempts_all_candidates": _sum_known(selected, "challenge_attempts_all_candidates"),
            "construction_work_all_attempts": _sum_known(selected, "construction_work_all_candidates"),
            "issued_model_mass_at_least_1_20": mass_counts,
            "qualified_final_model_mass_at_least_1_20": qualified_mass_counts,
            "issued_models_evaluated": sum(len(row.get("issued_model_evaluations_evaluator_only", []))
                for row in selected)}
    return {"by_arm": by_arm,
        "claim_boundary": "descriptive results on fixed authored finite fixtures; seeded schedules are not IID certificates; evaluator disagreement masses are bounded-distribution diagnostics, not unbounded equivalence"}


def _sum_known(rows, key):
    values = [(row.get("actual_costs") or {}).get(key) for row in rows]
    return None if any(value is None for value in values) else sum(values)


def _source_snapshot(output):
    paths = sorted((ROOT / "kuttaka_runtime").glob("*.py")) + [
        ROOT / "experiments/astra_active_navigation/run.py",
        ROOT / "experiments/astra_model_experiments/run.py",
        Path(__file__), ROOT / "experiments/astra_distribution_navigation/evaluate.py",
        ROOT / "experiments/astra_distribution_navigation/PROTOCOL.md"]
    hashes = {}
    for path in paths:
        relative = str(path.relative_to(ROOT))
        raw = path.read_bytes()
        hashes[relative] = hashlib.sha256(raw).hexdigest()
        dest = output / "sources" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
    return hashes


def _truth(cases):
    return {case["case"]: {"origin": case["origin"], "goal_output": "G",
        **case["truth_factory"]()} for case in cases}


def _write_design_and_truth(output, cases, arms, source_hashes):
    truth = _truth(cases)
    truth_bytes = canonical(truth).encode()
    (output / "evaluator-truth.json").write_bytes(truth_bytes + b"\n")
    design = {"schema": "astra-distribution-navigation-study-v1",
        "attribution": "engineered learner and developer-authored deterministic fixtures",
        "endpoint_calls": 0, "actions": list(ACTIONS), "limits": LIMITS,
        "case_names": [case["case"] for case in cases], "case_count": len(cases),
        "arms": [{"label": label, "strategy": strategy, "sampling": sampling}
                 for label, strategy, sampling in arms],
        "arm_count": len(arms), "run_count": len(cases)*len(arms),
        "challenge_depth_shortlex": 4, "common_mass_distributions": [
            {"persistence": False, "max_length": 16}, {"persistence": True, "max_length": 16}],
        "seeded_sampling_contract": SAMPLING_CONTRACT,
        "fixed_pattern_words_evaluator_only": {str(n): p for n, p in _sample_patterns().items()},
        "exact_prefix_words_evaluator_only": {str(n): _exact_prefix_word_for_truth(n) for n in (14, 16)},
        "source_sha256": source_hashes,
        "truth_sha256": hashlib.sha256(truth_bytes).hexdigest(),
        "python_version": sys.version,
        "initial_goal_cases_evaluator_only": [case["case"] for case in cases
            if case["truth_factory"]().get("outputs_evaluator_only", {}).get("q0") == "G"],
        "claim_boundary": "finite authored fixtures and bounded evaluator distributions; no IID certification from fixed seeds; no unbounded environment equivalence claim"}
    (output / "design.json").write_text(canonical(design)+"\n", encoding="utf-8")


def _exact_prefix_word_for_truth(length):
    rng = random.Random(2039)
    words = {}
    for size in (14, 16):
        words[size] = [rng.choice(ACTIONS) for _ in range(size)]
    return words[length]


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "journals").mkdir()
    (output / "stores").mkdir()
    source_hashes = _source_snapshot(output)
    fixed_cases, arms = _fixed_cases(), _arm_specs()
    if len(fixed_cases) != 17 or len(arms) != 7:
        raise AssertionError("preregistered study matrix size changed")
    _write_design_and_truth(output, fixed_cases, arms, source_hashes)
    rows = []
    for case in fixed_cases:
        for arm in arms:
            rows.append(_run_arm(output, case, arm))
            (output / "partial-results.json").write_text(canonical(rows)+"\n", encoding="utf-8")
    if len(rows) != 119:
        raise AssertionError("every preregistered case/arm pair must have a retained row")
    summary = _summary(rows, arms)
    (output / "results.json").write_text(canonical({"expected_run_count": 119,
        "retained_run_count": len(rows), "rows": rows, "summary": summary})+"\n", encoding="utf-8")
    (output / "partial-results.json").unlink(missing_ok=True)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
