"""Compare finite shortlex and model-derived conformance schedules.

All worlds are developer-authored deterministic fixtures. The learner receives
only reset/step callbacks and the declared opaque alphabet; evaluator truth is
consulted only after learning and is saved separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys

from experiments.astra_active_navigation.run import (
    ACTIONS,
    MooreWorld,
    _make_long_trigger,
    _sequence_world,
    fixtures as legacy_fixtures,
)
from kuttaka_runtime.active_navigation import ActiveNavigation, predict_trace
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.goals import GoalLedger
from kuttaka_runtime.model_experiments import construct_experiments
from kuttaka_runtime.navigation_tasks import execute_route, retain_route
from kuttaka_runtime.store import Journal, canonical, digest


ROOT = Path(__file__).resolve().parents[2]
LIMITS = {"max_queries": 4096, "max_actions": 50000,
          "max_word_length": 32, "max_states": 128}
ARMS = (("shortlex", 0), ("model_derived", 0), ("model_derived", 1))
ARMS_V2 = (("shortlex", 0), ("model_derived", 0), ("model_derived", 1), ("adaptive", 0))
SEEDS = (11, 29, 47)
STATE_COUNTS = (4, 8, 12)
SEEDS_V2 = (101, 131, 167)
STATE_COUNTS_V2 = (6, 10, 16)
REQUEST = "Reach the observable G marker."
CONTEXT = "opaque-reset-action-interface-v1"


class AuthoredWorld(MooreWorld):
    """Small explicit Moore fixture with callback-visible strings only."""


def _generated_world(state_count, seed, *, partially_observed):
    """Build a reachable 3-action graph; action a cycles through every state."""
    rng = random.Random(seed)
    names = [f"q{i}" for i in range(state_count)]
    transitions = {}
    for i, state in enumerate(names):
        transitions[state] = {
            "a": names[(i + 1) % state_count],
            "b": names[rng.randrange(state_count)],
            "c": names[rng.randrange(state_count)],
        }
    target = (seed + state_count - 1) % state_count
    outputs = {}
    for i, state in enumerate(names):
        if i == target:
            outputs[state] = "G"
        elif partially_observed:
            outputs[state] = ("dull", "bright")[(i + seed) % 2]
        else:
            outputs[state] = f"observation-{i}"
    world = AuthoredWorld(transitions, outputs, actions=ACTIONS)
    truth = {"state_count_evaluator_only": state_count,
             "seed_evaluator_only": seed,
             "target_state_evaluator_only": names[target],
             "transitions_evaluator_only": transitions,
             "outputs_evaluator_only": outputs,
             "target_word_evaluator_only": ["a"] * target,
             "partially_observed": partially_observed}
    return world, truth


def cases():
    """Return the fixed experiment matrix before any arm is run."""
    result = []
    for name in ("aliased_rooms", "protocol_handshake", "delayed_actuator",
                 "locked_workflow", "renamed_handshake", "three_phase_cycle"):
        result.append({"case": name, "goal_output": "G", "origin": "existing-authored-fixture",
            "factory": lambda name=name: legacy_fixtures()[name][0],
            "truth_factory": lambda name=name: {
                "source_fixture_evaluator_only": name,
                "state_count_evaluator_only": len(legacy_fixtures()[name][0].outputs),
                "transitions_evaluator_only": legacy_fixtures()[name][0].transitions,
                "outputs_evaluator_only": legacy_fixtures()[name][0].outputs,
                "target_word_evaluator_only": list(legacy_fixtures()[name][2]),
            }})

    for state_count in STATE_COUNTS:
        for seed in SEEDS:
            for partially_observed in (False, True):
                suffix = "colors" if partially_observed else "visible"
                name = f"generated_{state_count}_{seed}_{suffix}"
                result.append({"case": name, "goal_output": "G", "origin": "authored-seeded-generator",
                    "factory": lambda n=state_count, s=seed, p=partially_observed:
                        _generated_world(n, s, partially_observed=p)[0],
                    "truth_factory": lambda n=state_count, s=seed, p=partially_observed:
                        _generated_world(n, s, partially_observed=p)[1]})

    for trigger_length in (6, 8):
        name = f"long_trigger_{trigger_length}"
        result.append({"case": name, "goal_output": "G", "origin": "authored-hidden-trigger-control",
            "factory": lambda n=trigger_length: _make_long_trigger(goal_after=n),
            "truth_factory": lambda n=trigger_length: {
                "trigger_length_evaluator_only": n,
                "transitions_evaluator_only": _make_long_trigger(goal_after=n).transitions,
                "outputs_evaluator_only": _make_long_trigger(goal_after=n).outputs,
                "target_word_evaluator_only": ["a"] * n,
            }})
    return result


def _hidden_alias_world(delay):
    """Reachable x-output alias chain splits only after repeated b then a."""
    if delay not in (2, 3):
        raise ValueError("the preregistered hidden-alias delays are 2 or 3")
    chain = [f"r{i}" for i in range(1, delay+1)]
    states = ["q0", "q1", *chain, "z"]
    transitions = {state: {action: "q0" for action in ACTIONS} for state in states}
    transitions["q0"] = {"a": "q1", "b": chain[0], "c": "q0"}
    transitions["q1"] = {action: "q1" for action in ACTIONS}
    for index, state in enumerate(chain):
        transitions[state] = {
            "a": "z" if index == delay-1 else "q1",
            "b": chain[index+1] if index < delay-1 else "q0",
            "c": "q0",
        }
    transitions["z"] = {"a": "q1", "b": chain[0], "c": "q0"}
    outputs = {state: "x" for state in ["q0", *chain]}
    outputs.update({"q1": "G", "z": "Z"})
    world = AuthoredWorld(transitions, outputs, actions=ACTIONS)
    return world, {"state_count_evaluator_only": len(states),
        "delay_b_count_evaluator_only": delay,
        "transitions_evaluator_only": transitions, "outputs_evaluator_only": outputs,
        "target_word_evaluator_only": ["a"],
        "shortest_hidden_alias_counterexample_evaluator_only": ["b"]*delay+["a"]}


def cases_v2():
    """Fixed V2 matrix; no outcome-dependent fixture selection."""
    result = []
    for name in ("aliased_rooms", "protocol_handshake", "delayed_actuator",
                 "locked_workflow", "renamed_handshake", "three_phase_cycle"):
        result.append({"case": name, "goal_output": "G", "origin": "existing-authored-fixture",
            "factory": lambda name=name: legacy_fixtures()[name][0],
            "truth_factory": lambda name=name: {
                "source_fixture_evaluator_only": name,
                "state_count_evaluator_only": len(legacy_fixtures()[name][0].outputs),
                "transitions_evaluator_only": legacy_fixtures()[name][0].transitions,
                "outputs_evaluator_only": legacy_fixtures()[name][0].outputs,
                "target_word_evaluator_only": list(legacy_fixtures()[name][2]),
            }})
    for state_count in STATE_COUNTS_V2:
        for seed in SEEDS_V2:
            for partially_observed in (False, True):
                variant = "colors" if partially_observed else "visible"
                name = f"generated_{state_count}_{seed}_{variant}"
                result.append({"case": name, "goal_output": "G", "origin": "authored-seeded-generator-v2",
                    "factory": lambda n=state_count, s=seed, p=partially_observed:
                        _generated_world(n, s, partially_observed=p)[0],
                    "truth_factory": lambda n=state_count, s=seed, p=partially_observed:
                        _generated_world(n, s, partially_observed=p)[1]})
    for delay in (2, 3):
        label = f"concealed_alias_delay_{delay}"
        result.append({"case": label, "goal_output": "G", "origin": "authored-concealed-alias-control",
            "factory": lambda d=delay: _hidden_alias_world(d)[0],
            "truth_factory": lambda d=delay: _hidden_alias_world(d)[1]})
    for trigger_length in (6, 8):
        name = f"long_trigger_{trigger_length}"
        result.append({"case": name, "goal_output": "G", "origin": "authored-hidden-trigger-control",
            "factory": lambda n=trigger_length: _make_long_trigger(goal_after=n),
            "truth_factory": lambda n=trigger_length: {
                "trigger_length_evaluator_only": n,
                "transitions_evaluator_only": _make_long_trigger(goal_after=n).transitions,
                "outputs_evaluator_only": _make_long_trigger(goal_after=n).outputs,
                "target_word_evaluator_only": ["a"] * n,
            }})
    return result


def exact_equivalence(world, model):
    """Independent product-BFS oracle; evaluator-only and never fed to learning."""
    if model is None:
        return {"status": "not_checked", "reason": "no model artifact"}
    if list(world.actions) != model.get("actions"):
        return {"status": "not_checked", "reason": "action alphabet mismatch"}
    start = (world.initial, model["initial"])
    queue = [(start, ())]
    seen = {start}
    head = 0
    while head < len(queue):
        (actual_state, inferred_state), word = queue[head]
        head += 1
        actual_output = world.outputs[actual_state]
        inferred_output = model["outputs"][inferred_state]
        if actual_output != inferred_output:
            expected = predict_trace(model, word)
            observed = _trace_world(world, word)
            return {"status": "not_equivalent", "product_pairs_checked": len(seen),
                    "shortest_counterexample": list(word),
                    "candidate_outputs": expected, "world_outputs_evaluator_only": observed}
        for action in world.actions:
            target = (world.transitions[actual_state][action],
                      model["transitions"][inferred_state][action])
            if target not in seen:
                seen.add(target)
                queue.append((target, word + (action,)))
    return {"status": "equivalent", "product_pairs_checked": len(seen),
            "interpretation": "exact equivalence for this authored finite deterministic fixture only"}


def _trace_world(world, word):
    state = world.initial
    trace = [world.outputs[state]]
    for action in word:
        state = world.transitions[state][action]
        trace.append(world.outputs[state])
    return trace


def _candidate_model(memory, artifact):
    if not artifact:
        return None
    return memory.get(artifact)["body"]


def _schedule_records(events, final_artifact):
    schedules = [e for e in events if e["kind"] == "active_navigation_schedule"]
    constructions = [e for e in events if e["kind"] == "active_navigation_schedule_construction"]
    selections = [e for e in events if e["kind"] == "active_navigation_schedule_selection"]
    result = []
    for index, event in enumerate(schedules):
        body = event["body"]
        if body.get("model") != final_artifact:
            continue
        end_seq = schedules[index + 1]["seq"] if index + 1 < len(schedules) else float("inf")
        challenges = [e for e in events if e["kind"] == "active_navigation_challenge"
                      and e["seq"] > event["seq"] and e["seq"] < end_seq
                      and e["body"].get("model") == final_artifact]
        construction = next((e["body"]["construction"] for e in reversed(constructions)
                             if e["seq"] < event["seq"] and e["body"].get("model") == final_artifact), None)
        selection_event = next((e for e in reversed(selections)
            if e["seq"] < event["seq"] and e["body"].get("model") == final_artifact
            and e["body"].get("schedule_hash") == body.get("schedule_hash")), None)
        selection = selection_event["body"] if selection_event else None
        constructed_words = construction.get("words") if isinstance(construction, dict) else None
        if selection and selection.get("words") is not None:
            constructed_words = selection["words"]
        result.append({"sequence": len(result), "model": final_artifact,
            "schedule_hash": body.get("schedule_hash"), "strategy": body.get("strategy"),
            "selected_strategy": body.get("selected_strategy", body.get("strategy")),
            "construction_schedule_hash": body.get("construction_schedule_hash"),
            "selection": selection,
            "expected_words": body.get("expected_words"),
            "attempted_words": [e["body"]["word"] for e in challenges],
            "constructed_words": constructed_words,
            "work": construction.get("work") if isinstance(construction, dict) else None,
            "status": "completed" if any(e["kind"] == "active_navigation_schedule_completed"
                and e["body"].get("schedule_evidence") == event["hash"] for e in events) else "incomplete"})
    scheduled_models = {item["model"] for item in result}
    for event in constructions:
        body = event["body"]
        construction = body.get("construction", {})
        if body.get("model") != final_artifact or body.get("model") in scheduled_models:
            continue
        result.append({"sequence": len(result), "model": final_artifact,
            "schedule_hash": construction.get("schedule_hash"),
            "strategy": "model_derived", "expected_words": None,
            "word_limit": construction.get("limits", {}).get("max_tests"),
            "attempted_words": [], "constructed_words": construction.get("words"),
            "constructed_word_count": len(construction.get("words", [])),
            "work": construction.get("work"), "construction_status": construction.get("status"),
            "status": "construction_incomplete"})
    return result


def _run_arm(root, case, arm, *, challenge_depth=4):
    strategy, extra_depth = arm
    label = case["case"]
    arm_label = f"{strategy}-{extra_depth}"
    world = case["factory"]()
    store_dir = root / "stores" / label / arm_label
    journal_path = root / "journals" / f"{label}--{arm_label}.jsonl"
    store_dir.parent.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    learn_result = route = execution = None
    model_id = model = None
    route_id = None
    learning_resets = learning_actions = 0
    try:
        with Journal(journal_path) as journal, ArtifactMemory(store_dir) as memory:
            goals = GoalLedger(REQUEST, journal)
            proposed = goals.propose({"description": "Reach marker G.",
                "success_criteria": ["reset/step observation equals G"],
                "unknowns": [], "tradeoffs": [], "parent": None,
                "based_on": [goals.request_evidence]})
            goals.authorize(proposed["goal"], user_evidence=goals.request_evidence)
            learner = ActiveNavigation(memory, journal, world.reset, world.step,
                actions=list(ACTIONS), context=CONTEXT, **LIMITS)
            learn_result = learner.learn(challenge_depth=challenge_depth, strategy=strategy,
                extra_depth=extra_depth, max_schedule_work=1_000_000)
            learning_resets = world.reset_calls
            learning_actions = world.step_calls
            model_id = learn_result.get("artifact")
            model = _candidate_model(memory, model_id)
            exact = exact_equivalence(world, model)
            if learn_result.get("status") == "conditional_tested" and model_id:
                route = retain_route(memory, journal, goals, model_id,
                    scope=learner.scope, context=CONTEXT, goal_output=case["goal_output"])
                route_id = route.get("artifact")
                if route.get("status") == "route_proposed":
                    execution = execute_route(memory, journal, goals, route_id,
                        scope=learner.scope, context=CONTEXT, goal_output=case["goal_output"],
                        reset=world.reset, step=world.step, max_actions=128)
                else:
                    execution = {"status": route.get("status")}
            events = Journal.read(journal_path)
            schedule_records = _schedule_records(events, model_id)
            selection_events = [event["body"] for event in events
                if event["kind"] == "active_navigation_schedule_selection"
                and event["body"].get("model") == model_id]
            challenge_meta = learn_result.get("challenge") or {}
            selected_strategy = challenge_meta.get("selected_strategy")
            if selected_strategy is None and schedule_records:
                selected_strategy = schedule_records[-1].get("selected_strategy")
            if selected_strategy is None:
                selected_strategy = strategy
            return {"case": label, "arm": arm_label, "strategy": strategy,
                "selected_strategy": selected_strategy,
                "selection_events": selection_events,
                "extra_depth": extra_depth, "status": learn_result.get("status"),
                "learn": learn_result, "model_id": model_id,
                "learned_state_count": learn_result.get("states"),
                "exact_equivalence_evaluator_only": exact,
                "route": route, "route_id": route_id, "execution": execution,
                "schedule_records": schedule_records,
                "schedule_hashes": [record["schedule_hash"] for record in schedule_records],
                "schedule_words": [record["attempted_words"] for record in schedule_records],
                "schedule_work": [record["work"] for record in schedule_records],
                "actual_costs": {"reset_calls": world.reset_calls,
                    "challenge_word_attempts": sum(e['kind'] == 'active_navigation_challenge' for e in events),
                    "action_calls": world.step_calls,
                    "learning_reset_calls": learning_resets,
                    "learning_action_calls": learning_actions,
                    "route_reset_calls": (execution or {}).get("reset_attempts", 0),
                    "route_action_calls": (execution or {}).get("action_attempts", 0),
                    "learner_queries": learn_result.get("queries"),
                    "learner_resets": learn_result.get("reset_attempts"),
                    "learner_actions": learn_result.get("action_attempts"),
                    "challenge_words_expected": (learn_result.get("challenge") or {}).get("expected_words"),
                    "challenge_words_completed": (learn_result.get("challenge") or {}).get("words_completed")},
                "journal": str(journal_path.relative_to(root)),
                "store": str(store_dir.relative_to(root))}
    except Exception as error:
        return {"case": label, "arm": arm_label, "strategy": strategy,
            "extra_depth": extra_depth, "status": "harness_error",
            "error_type": type(error).__name__, "error": str(error),
            "reset_calls": world.reset_calls, "action_calls": world.step_calls,
            "journal": str(journal_path.relative_to(root)),
            "store": str(store_dir.relative_to(root))}


def _source_snapshot(output):
    paths = [Path(__file__), ROOT / "experiments/astra_active_navigation/run.py",
             ROOT / "kuttaka_runtime/active_navigation.py",
             ROOT / "kuttaka_runtime/model_experiments.py",
             ROOT / "kuttaka_runtime/navigation_tasks.py",
             ROOT / "kuttaka_runtime/artifacts.py", ROOT / "kuttaka_runtime/goals.py",
             ROOT / "kuttaka_runtime/store.py"]
    hashes = {}
    for source in paths:
        relative = str(source.relative_to(ROOT))
        raw = source.read_bytes()
        hashes[relative] = hashlib.sha256(raw).hexdigest()
        destination = output / "sources" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    return hashes


def _write_truth(output, fixed_cases):
    truth = {case["case"]: {"origin": case["origin"], "goal_output": case["goal_output"],
                           **case["truth_factory"]()} for case in fixed_cases}
    (output / "evaluator-truth.json").write_text(canonical(truth) + "\n", encoding="utf-8")
    return hashlib.sha256(canonical(truth).encode()).hexdigest()


def _v2_comparisons(arms):
    aggregates = {}
    for arm in ARMS_V2:
        label = f"{arm[0]}-{arm[1]}"
        rows = [row for row in arms if row.get("arm") == label]
        exact = [row.get("exact_equivalence_evaluator_only", {}).get("status") for row in rows]
        aggregates[label] = {"cases": len(rows),
            "conditional_tested": sum(row.get("status") == "conditional_tested" for row in rows),
            "exact_equivalent": exact.count("equivalent"),
            "conditional_but_non_equivalent": exact.count("not_equivalent"),
            "incomplete_or_error": sum(row.get("status") != "conditional_tested" for row in rows),
            "learning_resets": sum((row.get("actual_costs") or {}).get("learning_reset_calls", 0) or 0 for row in rows),
            "learning_actions": sum((row.get("actual_costs") or {}).get("learning_action_calls", 0) or 0 for row in rows),
            "full_resets_including_route": sum((row.get("actual_costs") or {}).get("reset_calls", 0) or 0 for row in rows),
            "full_actions_including_route": sum((row.get("actual_costs") or {}).get("action_calls", 0) or 0 for row in rows),
            # Final-model schedules omit failed earlier candidates and optional
            # constructions rejected by the selector. Count all attempted work.
            "challenged_words": (None if any((row.get('actual_costs') or {}).get('challenge_word_attempts') is None
                                               for row in rows) else sum(row['actual_costs']['challenge_word_attempts'] for row in rows)),
            "construction_work": sum((row.get('learn') or {}).get('schedule_work', 0) for row in rows)}
    pairwise = {}
    pairs = [("shortlex-0", "model_derived-0"),
             ("shortlex-0", "model_derived-1"),
             ("shortlex-0", "adaptive-0")]
    for left, right in pairs:
        left_by = {row["case"]: row for row in arms if row.get("arm") == left}
        right_by = {row["case"]: row for row in arms if row.get("arm") == right}
        matched = []
        for case in sorted(left_by.keys() & right_by.keys()):
            lrow, rrow = left_by[case], right_by[case]
            if (lrow.get("exact_equivalence_evaluator_only", {}).get("status") == "equivalent"
                    and rrow.get("exact_equivalence_evaluator_only", {}).get("status") == "equivalent"):
                lc, rc = lrow.get("actual_costs", {}), rrow.get("actual_costs", {})
                matched.append({"case": case,
                    "left_learning_resets": lc.get("learning_reset_calls"),
                    "right_learning_resets": rc.get("learning_reset_calls"),
                    "left_learning_actions": lc.get("learning_action_calls"),
                    "right_learning_actions": rc.get("learning_action_calls")})
        pairwise[f"{left}__vs__{right}"] = {"both_exact_cases": matched,
            "count": len(matched),
            "interpretation": "matched cost comparison restricted to cases exactly equivalent in both arms"}
    return {"by_arm": aggregates, "matched_exact_costs": pairwise}


def run(output, study="v1"):
    """Run the fixed V1 or V2 matrix; no outcome-dependent case selection."""
    if study not in ("v1", "v2"):
        raise ValueError("study must be v1 or v2")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "journals").mkdir()
    (output / "stores").mkdir()
    source_hashes = _source_snapshot(output)
    fixed_cases = cases() if study == "v1" else cases_v2()
    truth_sha256 = _write_truth(output, fixed_cases)
    arms_spec = ARMS if study == "v1" else ARMS_V2
    design = {"schema": f"astra-model-derived-schedule-study-{study}",
        "attribution": "engineered schedule comparison on developer-authored deterministic fixtures",
        "endpoint_calls": 0, "actions": list(ACTIONS), "limits": LIMITS,
        "arms": [{"strategy": s, "extra_depth": d} for s, d in arms_spec],
        "shortlex_depth": 4,
        "generated_state_counts": list(STATE_COUNTS if study == "v1" else STATE_COUNTS_V2),
        "generated_seeds": list(SEEDS if study == "v1" else SEEDS_V2),
        "output_variants": ["visible", "two-color partial observation"],
        "long_trigger_lengths": [6, 8], "truth_file_sha256": truth_sha256,
        "claim_boundary": "schedule cost and false-pass behavior for these fixtures only; no target-state bound or equivalence claim"}
    if study == "v2":
        design["concealed_alias_delays"] = [2, 3]
        initial_goal_cases = []
        for case in fixed_cases:
            outputs = case["truth_factory"]().get("outputs_evaluator_only", {})
            if outputs.get("q0") == case["goal_output"]:
                initial_goal_cases.append(case["case"])
        design["initial_goal_cases_evaluator_only"] = initial_goal_cases
        design["initial_goal_case_count"] = len(initial_goal_cases)
    (output / "design.json").write_text(canonical(design) + "\n", encoding="utf-8")

    arms = []
    for case in fixed_cases:
        for arm in arms_spec:
            arms.append(_run_arm(output, case, arm))
    summary = {**design, "source_sha256": source_hashes, "arms_run": len(arms),
        "cases_run": len(fixed_cases), "arms": arms,
        "counts": {
            "conditional_tested": sum(row.get("status") == "conditional_tested" for row in arms),
            "equivalent": sum((row.get("exact_equivalence_evaluator_only") or {}).get("status") == "equivalent" for row in arms),
            "non_equivalent_conditional": sum(row.get("status") == "conditional_tested" and
                (row.get("exact_equivalence_evaluator_only") or {}).get("status") == "not_equivalent" for row in arms),
            "incomplete_or_error": sum(row.get("status") != "conditional_tested" for row in arms)},
        "interpretation": "Different finite schedules were tested and are not equal-coverage guarantees; exact checker is evaluator-only."}
    if study == "v2":
        summary["comparisons"] = _v2_comparisons(arms)
    (output / "summary.json").write_text(canonical(summary) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="new directory for raw journals/stores and summary")
    parser.add_argument("--study", choices=("v1", "v2"), default="v1")
    args = parser.parse_args()
    result = run(args.output, study=args.study)
    print(json.dumps({"cases": result["cases_run"], "arms": result["arms_run"],
                      "counts": result["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
