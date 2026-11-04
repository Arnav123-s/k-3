"""Recorded end-to-end tests of bounded reset/step state construction.

All worlds here are authored deterministic fixtures. The learner receives only
the opaque actions ``a/b/c`` and short observations from reset/step. World names,
state IDs, transition tables, target words and baselines stay in this evaluator.
This measures an engineered L*-style learner, not model-invented navigation.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from kuttaka_runtime.active_navigation import ActiveNavigation, plan
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.goals import GoalLedger
from kuttaka_runtime.navigation_tasks import execute_route, retain_route
from kuttaka_runtime.store import Journal, canonical


ROOT = Path(__file__).resolve().parents[2]
ACTIONS = ["a", "b", "c"]
LIMITS = {"max_queries": 4096, "max_actions": 50000,
          "max_word_length": 32, "max_states": 128, "challenge_depth": 4}
REQUEST = "Reach the state whose observable marker is G."


class MooreWorld:
    """Evaluator-only finite machine; the callbacks expose no state IDs."""
    def __init__(self, transitions, outputs, initial="q0", *, actions=ACTIONS):
        self.transitions = transitions
        self.outputs = outputs
        self.initial = initial
        self.actions = list(actions)
        self.state = initial
        self.reset_calls = 0
        self.step_calls = 0

    def reset(self):
        self.reset_calls += 1
        self.state = self.initial
        return self.outputs[self.state]

    def step(self, action):
        self.step_calls += 1
        self.state = self.transitions[self.state][action]
        return self.outputs[self.state]


def _sequence_world(word, *, outputs=None, label="0"):
    """Only the exact word reaches G; every unlisted edge reaches a sink."""
    states = [f"q{i}" for i in range(len(word)+1)] + ["sink"]
    transitions = {s: {a: "sink" for a in ACTIONS} for s in states}
    outputs_map = {s: label for s in states}
    outputs_map["sink"] = label
    outputs_map[states[-2]] = "G"
    for index, action in enumerate(word):
        transitions[states[index]][action] = states[index+1]
    # Successful state is absorbing. It is terminal by output, not hidden metadata.
    transitions[states[-2]] = {a: states[-2] for a in ACTIONS}
    if outputs:
        outputs_map.update({states[i]: value for i, value in outputs.items()})
    return MooreWorld(transitions, outputs_map)


def fixtures():
    """Return authored worlds plus evaluator-only benchmark facts."""
    # Alias case: q0 and q1 have identical observations, but distinct futures.
    alias = _sequence_world("ab", label="room")
    # Make sink and all non-goal stages look the same, and ensure q0/q1 alias.
    alias.outputs.update({"q0": "room", "q1": "room", "sink": "room"})

    handshake = _sequence_world("abc", label="idle", outputs={2: "ready"})
    delayed = _sequence_world("abbc", label="off", outputs={3: "on"})
    workflow = _sequence_world("bacb", label="step")
    renamed = _sequence_world("cab", label="idle", outputs={2: "ready"})

    cycle_transitions = {
        "q0": {"a": "q1", "b": "sink", "c": "sink"},
        "q1": {"a": "q2", "b": "sink", "c": "sink"},
        "q2": {"a": "q0", "b": "goal", "c": "sink"},
        "goal": {a: "goal" for a in ACTIONS},
        "sink": {a: "sink" for a in ACTIONS},
    }
    cycle = MooreWorld(cycle_transitions,
        {"q0": "0", "q1": "0", "q2": "0", "goal": "G", "sink": "0"})

    # Reusable branching map: user chooses A or B only after the map is learned.
    branch_transitions = {
        "q0": {"a": "qA", "b": "sink", "c": "qB"},
        "qA": {"a": "sink", "b": "goalA", "c": "sink"},
        "qB": {"a": "goalB", "b": "sink", "c": "sink"},
        "goalA": {a: "goalA" for a in ACTIONS},
        "goalB": {a: "goalB" for a in ACTIONS},
        "sink": {a: "sink" for a in ACTIONS},
    }
    branching = MooreWorld(branch_transitions,
        {"q0": "0", "qA": "0", "qB": "0", "goalA": "A", "goalB": "B", "sink": "0"})

    return {
        "aliased_rooms": (alias, "G", "ab", 4, "ab"),
        "protocol_handshake": (handshake, "G", "abc", 5, "ab"),
        "delayed_actuator": (delayed, "G", "abbc", 6, "abb"),
        "locked_workflow": (workflow, "G", "bacb", 6, "bacb"),
        "renamed_handshake": (renamed, "G", "cab", 5, "ca"),
        "three_phase_cycle": (cycle, "G", "aab", 5, "aab"),
        "branching_targets": (branching, None, None, 6, None),
    }


def _events_by_kind(events, kind):
    return [event for event in events if event["kind"] == kind]


def _goal_request(goals, output, *, parent=None, user_signal=None):
    evidence = user_signal if user_signal is not None else goals.request_evidence
    description = (f"Reach observable marker {output}." if user_signal is not None
                   else "Reach the state whose observable marker is G.")
    proposed = goals.propose({"description": description,
        "success_criteria": [f"reset/step observation equals {output}"],
        "unknowns": [], "tradeoffs": [], "parent": parent, "based_on": [evidence]})
    goals.authorize(proposed["goal"], user_evidence=evidence)
    return proposed["goal"]


def _learner(memory, journal, world, *, context="opaque-world-context-v1", limits=None):
    merged = {**LIMITS, **(limits or {})}
    challenge_depth = merged.pop("challenge_depth")
    learner = ActiveNavigation(memory, journal, world.reset, world.step,
        actions=ACTIONS, context=context, **merged)
    return learner, challenge_depth


def _positive_case(root, label, world, goal_output, target_word, hidden_states, coarse_challenge):
    store_dir = root / "stores" / label
    journal_path = root / "journals" / f"{label}.jsonl"
    store_dir.parent.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    learn_result = route_result = first_execution = None
    model_id = route_id = scope = None
    training_reset_calls = training_step_calls = 0
    original_request = REQUEST
    with Journal(journal_path) as journal, ArtifactMemory(store_dir) as memory:
        goals = GoalLedger(original_request, journal)
        goal_id = _goal_request(goals, goal_output)
        learner, challenge_depth = _learner(memory, journal, world)
        learn_result = learner.learn(challenge_depth=challenge_depth)
        training_reset_calls = world.reset_calls
        training_step_calls = world.step_calls
        if learn_result.get("status") != "conditional_tested":
            return {"fixture": label, "status": "learn_failed", "learn": learn_result,
                    "hidden_state_count_evaluator_only": hidden_states,
                    "target_word_evaluator_only": target_word}
        model_id, scope = learn_result["artifact"], learn_result["scope"]
        route_result = retain_route(memory, journal, goals, model_id,
            scope=scope, context=learner.context, goal_output=goal_output)
        if route_result.get("status") != "route_proposed":
            return {"fixture": label, "status": "route_failed", "learn": learn_result,
                    "route": route_result, "hidden_state_count_evaluator_only": hidden_states,
                    "target_word_evaluator_only": target_word}
        route_id = route_result["artifact"]
        first_execution = execute_route(memory, journal, goals, route_id,
            scope=scope, context=learner.context, goal_output=goal_output, reset=world.reset,
            step=world.step, max_actions=128)
    # Reopen both stores at the validated journal head and replay the exact IDs.
    events = Journal.read(journal_path)
    head = events[-1]["hash"]
    with Journal.resume(journal_path, expected_head=head) as journal, ArtifactMemory(store_dir) as memory:
        goals = GoalLedger.restore(original_request, journal, events)
        second_execution = execute_route(memory, journal, goals, route_id,
            scope=scope, context="opaque-world-context-v1", goal_output=goal_output, reset=world.reset,
            step=world.step, max_actions=128)
        route_payload = memory.get(route_id)
        route_status = memory.status(route_id, scope)
        model_bytes = len(canonical(memory.get(model_id)['body']).encode())
        route_bytes = len(canonical(route_payload['body']).encode())
        second_execution_reset_calls = second_execution.get("reset_attempts", 0)
        second_execution_step_calls = second_execution.get("action_attempts", 0)
    return {"fixture": label, "status": learn_result["status"], "model": model_id,
        "route": route_id, "scope": scope, "authorized_goal": goal_id,
        "learn": learn_result, "route_proposal": route_result,
        "first_fresh_execution": first_execution,
        "reloaded_execution_same_route_id": second_execution,
        "route_actions": route_payload["body"]["actions"],
        "route_status_after_reload": route_status,
        "memory_bytes": {"model_body_json": model_bytes, "route_body_json": route_bytes,
            "journal_after_reopen": journal_path.stat().st_size,
            "store_files_after_reopen": sum(p.stat().st_size for p in store_dir.rglob('*') if p.is_file()),
            "interpretation": "deployment representation size; evidence remains retained separately"},
        "costs": {"learning_queries": learn_result["queries"],
            "learning_reset_attempts": learner.reset_attempts,
            "learning_reset_calls_observed": training_reset_calls,
            "learning_step_calls_observed": training_step_calls,
            "route_word_length": len(route_payload["body"]["actions"]),
            "first_execution_reset_calls": first_execution.get("reset_attempts", 0),
            "first_execution_step_calls": first_execution.get("action_attempts", 0),
            "reload_execution_reset_calls": second_execution_reset_calls,
            "reload_execution_step_calls": second_execution_step_calls,
            "reload_steps_per_learning_step": (second_execution_step_calls / training_step_calls
                if training_step_calls else None)},
        "goal_restored": goals.status({})["active"] == goal_id,
        "hidden_state_count_evaluator_only": hidden_states,
        "target_word_evaluator_only": target_word,
        "coarse_model_counterexample_depth_evaluator_only": len(coarse_challenge)}


def _run_branch_goal_change(root):
    label = "branching_goal_binding"
    store_dir = root / "stores" / label
    journal_path = root / "journals" / f"{label}.jsonl"
    store_dir.parent.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    world = fixtures()["branching_targets"][0]
    original_request = "Reach the target."  # Which of A/B is intentionally unresolved.
    with Journal(journal_path) as journal, ArtifactMemory(store_dir) as memory:
        goals = GoalLedger(original_request, journal)
        learner, depth = _learner(memory, journal, world)
        learned = learner.learn(challenge_depth=depth)
        model_id, scope = learned["artifact"], learned["scope"]
        unresolved = retain_route(memory, journal, goals, model_id,
            scope=scope, context=learner.context, goal_output="A")
        signal_a = goals.record_signal("Please reach marker A.", source="user")
        goal_a = _goal_request(goals, "A", user_signal=signal_a)
        route_a = retain_route(memory, journal, goals, model_id,
            scope=scope, context=learner.context, goal_output="A")
        run_a = execute_route(memory, journal, goals, route_a["artifact"],
            scope=scope, context=learner.context, goal_output="A", reset=world.reset, step=world.step)
    events = Journal.read(journal_path)
    head = events[-1]["hash"]
    with Journal.resume(journal_path, expected_head=head) as journal, ArtifactMemory(store_dir) as memory:
        goals = GoalLedger.restore(original_request, journal, events)
        reuse_a = execute_route(memory, journal, goals, route_a["artifact"],
            scope=scope, context="opaque-world-context-v1", goal_output="A", reset=world.reset, step=world.step)
        signal_b = goals.record_signal("Please reach marker B.", source="user")
        goal_b = _goal_request(goals, "B", parent=goal_a, user_signal=signal_b)
        old_refusal = execute_route(memory, journal, goals, route_a["artifact"],
            scope=scope, context="opaque-world-context-v1", goal_output="B", reset=world.reset, step=world.step)
        route_b = retain_route(memory, journal, goals, model_id,
            scope=scope, context="opaque-world-context-v1", goal_output="B")
        run_b = execute_route(memory, journal, goals, route_b["artifact"],
            scope=scope, context="opaque-world-context-v1", goal_output="B", reset=world.reset, step=world.step)
        return {"fixture": label, "learn": learned, "goal_A": goal_a,
            "goal_B": goal_b, "unresolved_before_user_clarification": unresolved,
            "route_A": route_a["artifact"], "route_A_actions": route_a["actions"],
            "route_A_initial_execution": run_a, "route_A_reload_execution": reuse_a,
            "old_route_after_goal_change": old_refusal,
            "route_B": route_b["artifact"], "route_B_actions": route_b["actions"],
            "route_B_execution": run_b,
            "goal_B_restored_and_active": goals.status({})["active"] == goal_b}


def _shortlex_baseline(world, goal, max_depth=4):
    trials = resets = steps = 0
    for length in range(1, max_depth+1):
        for word in itertools.product(ACTIONS, repeat=length):
            trials += 1
            resets += 1
            output = world.reset()
            if output == goal:
                return {"status": "found", "word": [], "trials": trials,
                        "reset_calls": resets, "step_calls": steps}
            for index, action in enumerate(word):
                steps += 1
                output = world.step(action)
                if output == goal:
                    return {"status": "found", "word": list(word[:index+1]),
                            "trials": trials, "reset_calls": resets, "step_calls": steps}
    return {"status": "not_found_within_horizon", "trials": trials,
            "reset_calls": resets, "step_calls": steps}


def _reactive_policy_baseline(world, goal, max_steps=64):
    # Enumerate all deterministic stationary observation->action policies; this
    # diagnostic has no internal state/history, unlike the active learner.
    observations = sorted(set(world.outputs.values()) - {goal})
    tested = 0
    for selected in itertools.product(ACTIONS, repeat=len(observations)):
        tested += 1
        policy = dict(zip(observations, selected))
        output = world.reset()
        for _ in range(max_steps):
            if output == goal:
                return {"status": "found", "policies_tested": tested}
            if output not in policy:
                break
            output = world.step(policy[output])
    return {"status": "not_solved", "policies_tested": tested}


def _partial_control(root, label, world_factory, goal_output, depth, *, context="opaque-control-v1",
                     reset_available=True, limits=None):
    store_dir = root / "stores" / label
    journal_path = root / "journals" / f"{label}.jsonl"
    store_dir.parent.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    world = world_factory()
    with Journal(journal_path) as journal, ArtifactMemory(store_dir) as memory:
        original = REQUEST if goal_output == "G" else "Reach the target."
        goals = GoalLedger(original, journal)
        if goal_output is not None:
            _goal_request(goals, goal_output)
        learner, _ = _learner(memory, journal, world, context=context, limits=limits)
        if not reset_available:
            learner.reset_callback = None
        learned = learner.learn(challenge_depth=depth)
        route = None
        if learned.get("artifact") and goal_output is not None:
            route = retain_route(memory, journal, goals, learned["artifact"],
                scope=learned["scope"], context=context, goal_output=goal_output)
        return {"fixture": label, "learn": learned, "route": route,
                "events": Journal.read(journal_path), "store": str(store_dir),
                "journal": str(journal_path)}


def _query_trace(events):
    attempts = {event["hash"]: event["body"] for event in events
                if event["kind"] == "active_navigation_query_attempt"}
    trace = []
    for event in events:
        if event["kind"] not in {"active_navigation_query_result", "active_navigation_query_cached"}:
            continue
        attempt = attempts.get(event["body"].get("attempt"), {})
        trace.append({"word": attempt.get("word"), "fresh": attempt.get("fresh"),
                      "outputs": event["body"].get("outputs"),
                      "status": event["body"].get("status", "cached")})
    return trace


def _make_long_trigger(goal_after=6):
    # Same as all-idle on every word shorter than goal_after; hidden goal only
    # follows a repeated-a trigger that the depth-2 schedule never reaches.
    word = "a" * goal_after
    return _sequence_world(word, label="0")


def _no_goal_world():
    transitions = {"q0": {a: "sink" for a in ACTIONS},
                   "sink": {a: "sink" for a in ACTIONS}}
    return MooreWorld(transitions, {"q0": "0", "sink": "0"})


class VersionedAliasWorld(MooreWorld):
    def __init__(self):
        transitions = {
            "q0": {"a": "q1", "b": "sink", "c": "sink"},
            "q1": {"a": "sink", "b": "goal", "c": "sink"},
            "q2": {"a": "goal", "b": "sink", "c": "sink"},
            "goal": {a: "goal" for a in ACTIONS},
            "sink": {a: "sink" for a in ACTIONS},
        }
        super().__init__(transitions, {"q0": "0", "q1": "0", "q2": "0",
            "goal": "G", "sink": "0"})
        self.changed = False

    def step(self, action):
        if self.changed and self.state == "q0":
            self.step_calls += 1
            self.state = "q2" if action == "c" else "sink"
        else:
            super().step(action)
        return self.outputs[self.state]


def _nonstationary_relearn(root):
    label = "nonstationary_then_versioned_relearn"
    store_dir = root / "stores" / label
    journal_path = root / "journals" / f"{label}.jsonl"
    store_dir.parent.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    world = VersionedAliasWorld()
    with Journal(journal_path) as journal, ArtifactMemory(store_dir) as memory:
        goals = GoalLedger(REQUEST, journal)
        _goal_request(goals, "G")
        learner_v1, depth = _learner(memory, journal, world, context="authored-map-v1")
        learned_v1 = learner_v1.learn(challenge_depth=depth)
        old_model = learned_v1["artifact"]
        old_route = retain_route(memory, journal, goals, old_model,
            scope=learned_v1["scope"], context=learner_v1.context, goal_output="G")
        world.changed = True
        mismatch = execute_route(memory, journal, goals, old_route["artifact"],
            scope=learned_v1["scope"], context=learner_v1.context,
            goal_output="G", reset=world.reset, step=world.step)
        old_model_status = memory.status(old_model, learned_v1["scope"])
        old_route_status = memory.status(old_route["artifact"], learned_v1["scope"])

        learner_v2, depth = _learner(memory, journal, world, context="authored-map-v2")
        learned_v2 = learner_v2.learn(challenge_depth=depth)
        new_route = retain_route(memory, journal, goals, learned_v2["artifact"],
            scope=learned_v2["scope"], context=learner_v2.context, goal_output="G")
        execution_v2 = execute_route(memory, journal, goals, new_route["artifact"],
            scope=learned_v2["scope"], context=learner_v2.context,
            goal_output="G", reset=world.reset, step=world.step)
        return {"fixture": label, "learn_v1": learned_v1,
            "old_route": old_route["artifact"], "old_route_actions": old_route["actions"],
            "fresh_counterexample": mismatch, "old_model_status": old_model_status,
            "old_route_status": old_route_status, "learn_v2": learned_v2,
            "new_route": new_route["artifact"], "new_route_actions": new_route["actions"],
            "new_context_execution": execution_v2,
            "journal_event_count": len(Journal.read(journal_path))}


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "stores").mkdir()
    (output / "journals").mkdir()
    positive, baselines = [], []
    for label, (world, goal, target, state_count, cex) in fixtures().items():
        if goal is None:
            continue
        positive.append(_positive_case(output, label, world, goal, target, state_count, cex))
        baseline_world = fixtures()[label][0]
        baselines.append({"fixture": label,
            "observation_only_stationary": _reactive_policy_baseline(baseline_world, goal),
            "shortlex_word_search": _shortlex_baseline(fixtures()[label][0], goal, max_depth=4),
            "scope": "authored deterministic fixtures; baselines are host code"})
    branch_world = fixtures()["branching_targets"][0]
    baselines.append({"fixture": "branching_targets",
        "observation_only_stationary_A": _reactive_policy_baseline(branch_world, "A"),
        "observation_only_stationary_B": _reactive_policy_baseline(branch_world, "B"),
        "shortlex_A": _shortlex_baseline(fixtures()["branching_targets"][0], "A", max_depth=4),
        "shortlex_B": _shortlex_baseline(fixtures()["branching_targets"][0], "B", max_depth=4),
        "scope": "host baseline on authored branching map"})

    # The ambiguous branch fixture first remains blocked, then demonstrates
    # goal-specific plan reuse after a fresh user signal and process-style reload.
    branch = _run_branch_goal_change(output)

    # Long-trigger and all-idle worlds are indistinguishable under the same
    # depth-2 schedule. The target remains outside the observed horizon.
    idle = _partial_control(output, "horizon_all_idle", _no_goal_world,
                            "G", 2, context="same-horizon-context")
    trigger = _partial_control(output, "horizon_long_trigger", _make_long_trigger,
                               "G", 2, context="same-horizon-context")
    horizon_control = {"idle_learn": idle["learn"], "trigger_learn": trigger["learn"],
        "idle_route": idle["route"], "trigger_route": trigger["route"],
        "same_query_word_output_trace": _query_trace(idle["events"]) == _query_trace(trigger["events"]),
        "query_trace": _query_trace(idle["events"]),
        "interpretation": "no route in the learned hypothesis; hidden long trigger is not ruled out"}

    no_reset = _partial_control(output, "no_reset", lambda: _sequence_world("abc"), "G", 2,
                                reset_available=False)
    tiny = _partial_control(output, "tiny_budget", lambda: _sequence_world("abc"), "G", 2,
                            limits={"max_queries": 1, "max_actions": 10})
    shortlex_max = max((sum(value["step_calls"] + value["reset_calls"]
                            for key, value in row.items()
                            if key.startswith("shortlex") and isinstance(value, dict))
                        for row in baselines), default=0)

    nonstationary = _nonstationary_relearn(output)
    source_paths = [Path(__file__), ROOT / "kuttaka_runtime/active_navigation.py",
                    ROOT / "kuttaka_runtime/navigation_tasks.py",
                    ROOT / "kuttaka_runtime/goals.py", ROOT / "kuttaka_runtime/artifacts.py",
                    ROOT / "kuttaka_runtime/store.py"]
    manifest = {"schema": "astra-active-navigation-fixtures-v1",
        "attribution": "engineered bounded Moore learner and authored worlds; no model endpoint calls",
        "endpoint_calls": 0, "actions": ACTIONS, "limits": LIMITS,
        "code_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in source_paths},
        "positive_cases": positive, "baselines": baselines,
        "branch_goal_binding_case": branch, "finite_horizon_control": horizon_control,
        "no_reset_control": {"learn": no_reset["learn"], "route": no_reset["route"]},
        "tiny_budget_control": {"learn": tiny["learn"], "route": tiny["route"]},
        "nonstationary_relearn_control": nonstationary,
        "max_shortlex_baseline_resets_plus_steps": shortlex_max,
        "claim_boundary": "finite authored transition systems; no field map or open-world discovery claim"}
    (output / "summary.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
    return manifest


def replay(output):
    """Run retained routes in a fresh interpreter without constructing a learner."""
    output = Path(output)
    manifest = json.loads((output/'summary.json').read_text())
    for name, expected in manifest['code_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != expected:
            raise ValueError('source changed before fresh-process reuse')
    results = []
    for case in manifest['positive_cases']:
        label = case['fixture']
        world = fixtures()[label][0]
        journal_path = output/'journals'/f'{label}.jsonl'
        events = Journal.read(journal_path)
        with Journal.resume(journal_path, expected_head=events[-1]['hash']) as journal, \
                ArtifactMemory(output/'stores'/label) as memory:
            goals = GoalLedger.restore(REQUEST, journal, events)
            result = execute_route(memory, journal, goals, case['route'],
                scope=case['scope'], context='opaque-world-context-v1', goal_output='G',
                reset=world.reset, step=world.step)
            results.append({'fixture': label, 'route': case['route'], 'result': result,
                'new_learning_queries': 0, 'world_resets': world.reset_calls,
                'world_steps': world.step_calls})
    report = {'fresh_interpreter': True, 'endpoint_calls': 0, 'results': results,
              'all_reached_goal': all(r['result']['status'] == 'goal_observed' for r in results)}
    (output/'fresh-process-replay.json').write_text(canonical(report)+'\n')
    if not report['all_reached_goal']:
        raise RuntimeError('fresh-process route reuse failed')
    return report


def record(output):
    """Retain full journals, compressed artifact stores and exact source snapshots."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temp:
        full = Path(temp)/'evidence'
        summary = run(full)
        subprocess.run([sys.executable, '-m', 'experiments.astra_active_navigation.run',
                        '--replay', str(full)], cwd=ROOT, check=True, timeout=60)
        for name in summary['code_sha256']:
            target = full/'sources'/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)
        journals = {str(p.relative_to(full)): {'events': len(events), 'head': events[-1]['hash']}
            for p in sorted((full/'journals').glob('*.jsonl')) if (events := Journal.read(p))}
        stores = {}
        for path in sorted((full/'stores').iterdir()):
            with ArtifactMemory(path) as memory:
                stores[str(path.relative_to(full))] = memory.stats()
        files = {str(p.relative_to(full)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(full.rglob('*')) if p.is_file() and not p.name.endswith('.lock')}
        verification = {'journals': journals, 'reopened_stores': stores,
                        'files_sha256': files, 'source_sha256': summary['code_sha256']}
        (full/'verification.json').write_text(canonical(verification)+'\n')
        for name in ('summary.json', 'fresh-process-replay.json', 'verification.json'):
            shutil.copyfile(full/name, output/name)
        archive = output/'evidence.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for path in sorted(full.rglob('*')):
                if path.is_file() and not path.name.endswith('.lock'):
                    tar.add(path, arcname=str(path.relative_to(full)), recursive=False)
        receipt = {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                   'archive_bytes': archive.stat().st_size, 'files': len(files)+1,
                   'journal_events': sum(v['events'] for v in journals.values()),
                   'stores': len(stores)}
        (output/'archive.json').write_text(canonical(receipt)+'\n')
        return receipt


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", help="new output directory with full evidence")
    mode.add_argument("--replay", help="reopen an existing full-evidence directory")
    mode.add_argument("--record", help="new directory for audited compressed evidence")
    args = parser.parse_args()
    if args.replay:
        replay(args.replay)
    elif args.record:
        record(args.record)
    else:
        run(args.output)


if __name__ == "__main__":
    main()
