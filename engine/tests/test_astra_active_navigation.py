import tempfile
import itertools
import unittest
from pathlib import Path

from kuttaka_runtime.active_navigation import (
    ActiveNavigation,
    plan,
    predict_trace,
    validate_model,
)
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.store import Journal


class TinyAliasingMachine:
    """Four Moore states; A and B alias until the suffix a,b is tested."""
    def __init__(self):
        self.state = "A"
        self.transitions = {
            "A": {"a": "A", "b": "A", "c": "B"},
            "B": {"a": "C", "b": "B", "c": "B"},
            "C": {"a": "C", "b": "D", "c": "C"},
            "D": {"a": "D", "b": "D", "c": "D"},
        }
        self.outputs = {"A": "same", "B": "same", "C": "same", "D": "goal"}

    def reset(self):
        self.state = "A"
        return self.outputs[self.state]

    def step(self, action):
        self.state = self.transitions[self.state][action]
        return self.outputs[self.state]


class ActiveNavigationTests(unittest.TestCase):
    def _resources(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        journal = Journal(root / "events.jsonl")
        memory = ArtifactMemory(root / "memory")
        self.addCleanup(memory.close)
        self.addCleanup(journal.close)
        return journal, memory

    def test_aliasing_is_split_by_counterexample_and_model_plans(self):
        journal, memory = self._resources()
        machine = TinyAliasingMachine()
        learner = ActiveNavigation(memory, journal, machine.reset, machine.step,
            actions=["a", "b", "c"], context="tiny-alias", max_queries=4096,
            max_actions=50000, max_word_length=12, max_states=16)
        result = learner.learn(challenge_depth=3)
        self.assertEqual(result["status"], "conditional_tested")
        self.assertGreaterEqual(result["counterexamples"], 1)
        self.assertEqual(result["states"], 4)
        payload = memory.get(result["artifact"])
        model = payload["body"]
        self.assertTrue(validate_model(model))
        self.assertEqual(predict_trace(model, ["c", "a", "b"]),
                         ["same", "same", "same", "goal"])
        self.assertEqual(plan(model, "goal")["actions"], ["c", "a", "b"])
        self.assertEqual(memory.status(result["artifact"], result["scope"]), "qualified")
        self.assertEqual(result["challenge"]["words_completed"], 40)

    def test_prefix_cache_is_from_one_reset_trace_and_fresh_conflict_is_nonstationary(self):
        journal, memory = self._resources()
        counter = 0

        def reset():
            return "root"

        def step(_action):
            nonlocal counter
            counter += 1
            return f"out-{counter}"

        learner = ActiveNavigation(memory, journal, reset, step,
            actions=["a"], context="unstable", max_queries=20, max_actions=20)
        first = learner.query(["a"], fresh=True)
        self.assertEqual(first["status"], "observed")
        cached = learner.query([], fresh=False)
        self.assertEqual(cached["status"], "cached")
        self.assertEqual(cached["outputs"], ["root"])
        second = learner.query(["a", "a"], fresh=True)
        self.assertEqual(second["status"], "nonstationary")
        self.assertEqual(second["conflicting_prefix"], ["a"])
        # Drift is detected after the first action, so the second is never sent.
        self.assertEqual(counter, 2)
        self.assertEqual(learner.status, "nonstationary")
        events = Journal.read(journal.path)
        self.assertTrue(any(event["kind"] == "active_navigation_nonstationarity" for event in events))
        observations = [event for event in events if event["kind"] == "active_navigation_observation"]
        self.assertTrue(any(event["body"]["phase"] == "reset" for event in observations))
        self.assertEqual(sum(event["body"]["phase"] == "step" for event in observations), 2)

    def test_reset_prefix_conflict_stops_before_any_action(self):
        journal, memory = self._resources()
        reset_count = 0
        action_count = 0

        def reset():
            nonlocal reset_count
            reset_count += 1
            return "root" if reset_count == 1 else "changed-root"

        def step(_action):
            nonlocal action_count
            action_count += 1
            return "next"

        learner = ActiveNavigation(memory, journal, reset, step,
            actions=["a"], context="reset-drift", max_queries=10, max_actions=10)
        self.assertEqual(learner.query(["a"], fresh=True)["status"], "observed")
        conflict = learner.query(["a"], fresh=True)
        self.assertEqual(conflict["status"], "nonstationary")
        self.assertEqual(conflict["conflicting_prefix"], [])
        self.assertEqual(action_count, 1)

    def test_nonstationarity_invalidates_existing_conditional_support(self):
        journal, memory = self._resources()
        changed = False

        def reset():
            return "same"

        def step(_action):
            return "changed" if changed else "same"

        learner = ActiveNavigation(memory, journal, reset, step,
            actions=["a"], context="invalidate-on-drift", max_queries=32, max_actions=64)
        learned = learner.learn(challenge_depth=0)
        self.assertEqual(learned["status"], "conditional_tested")
        aid = learned["artifact"]
        self.assertEqual(memory.status(aid, learned["scope"]), "qualified")
        changed = True
        result = learner.query(["a"], fresh=True, model=aid)
        self.assertEqual(result["status"], "nonstationary")
        self.assertEqual(memory.status(aid, learned["scope"]), "invalidated")
        events = Journal.read(journal.path)
        challenge = next(event for event in events if event["kind"] == "active_navigation_challenge")
        reset_attempt = next(event for event in events if event["kind"] == "active_navigation_reset_attempt"
                             and event["seq"] > challenge["seq"])
        self.assertLess(challenge["seq"], reset_attempt["seq"])

    def test_no_reset_is_explicit_and_not_a_fake_single_state(self):
        journal, memory = self._resources()
        learner = ActiveNavigation(memory, journal, None, lambda _action: "x",
            actions=["act"], context="no-reset", max_queries=5, max_actions=5)
        result = learner.learn(challenge_depth=0)
        self.assertEqual(result["status"], "reset_unavailable")
        self.assertNotIn("artifact", result)

    def test_query_and_action_budgets_fail_closed(self):
        journal, memory = self._resources()
        machine = TinyAliasingMachine()
        by_query = ActiveNavigation(memory, journal, machine.reset, machine.step,
            actions=["a"], context="query-budget", max_queries=1, max_actions=10)
        result = by_query.learn(challenge_depth=0)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertNotEqual(result["status"], "conditional_tested")

        journal2, memory2 = self._resources()
        machine2 = TinyAliasingMachine()
        by_action = ActiveNavigation(memory2, journal2, machine2.reset, machine2.step,
            actions=["a"], context="action-budget", max_queries=20, max_actions=0)
        result2 = by_action.learn(challenge_depth=0)
        self.assertEqual(result2["status"], "budget_exhausted")

    def test_state_and_word_limits_are_incomplete_not_no_fit(self):
        journal, memory = self._resources()
        machine = TinyAliasingMachine()
        learner = ActiveNavigation(memory, journal, machine.reset, machine.step,
            actions=["a", "b", "c"], context="state-budget", max_queries=300,
            max_actions=500, max_word_length=6, max_states=1)
        result = learner.learn(challenge_depth=3)
        self.assertEqual(result["status"], "state_budget_exhausted")
        self.assertNotIn("artifact", result)

    def test_model_validation_predict_and_plan_reject_malformed_tables(self):
        model = {"format": "active-moore-machine-v1", "actions": ["a"], "initial": 0,
                 "outputs": ["x"], "transitions": [{"a": 0}], "access": [[]], "suffixes": [[]]}
        self.assertTrue(validate_model(model))
        self.assertEqual(predict_trace(model, ["a", "a"]), ["x", "x", "x"])
        self.assertEqual(plan(model, "x")["actions"], [])
        malformed = dict(model)
        malformed["transitions"] = [{"a": 1}]
        self.assertFalse(validate_model(malformed))
        with self.assertRaises(ValueError):
            predict_trace(malformed, ["a"])
        with self.assertRaises(ValueError):
            plan(model, " ")

    def test_observer_errors_are_counted_before_callbacks_and_preserved(self):
        journal, memory = self._resources()
        def fail_reset():
            raise RuntimeError("offline")
        learner = ActiveNavigation(memory, journal, fail_reset, lambda _action: "x",
            actions=["a"], context="observer-error", max_queries=5, max_actions=5)
        outcome = learner.query([], fresh=True)
        self.assertEqual(outcome["status"], "observer_error")
        self.assertEqual(learner.query_attempts, 1)
        self.assertEqual(learner.reset_attempts, 1)
        events = Journal.read(journal.path)
        self.assertTrue(any(event["kind"] == "active_navigation_reset_attempt" for event in events))
        self.assertTrue(any(event["kind"] == "active_navigation_query_result"
                             and event["body"].get("status") == "observer_error" for event in events))

    def test_shallower_challenge_cannot_forget_previously_observed_distinction(self):
        journal, memory = self._resources()
        machine = TinyAliasingMachine()
        learner = ActiveNavigation(memory, journal, machine.reset, machine.step,
            actions=['a', 'b', 'c'], context='all-history')
        learner.query(['c', 'a', 'b'])
        learned = learner.learn(challenge_depth=0)
        self.assertEqual(learned['status'], 'conditional_tested')
        self.assertGreater(learned['historical_counterexamples'], 0)
        self.assertEqual(learned['challenge']['words_completed'], 1)
        model = memory.get(learned['artifact'])['body']
        self.assertEqual(predict_trace(model, ['c', 'a', 'b'])[-1], 'goal')
        historical = [e for e in Journal.read(journal.path)
                      if e['kind'] == 'active_navigation_history_counterexample']
        self.assertTrue(all(not e['body']['fresh_validation_credit'] for e in historical))

    def test_first_predicted_mismatch_stops_before_remaining_actions(self):
        journal, memory = self._resources()
        machine = TinyAliasingMachine()
        calls = []

        def step(action):
            calls.append(action)
            return machine.step(action)

        learner = ActiveNavigation(memory, journal, machine.reset, step,
            actions=['a', 'b', 'c'], context='stop-on-prediction')
        learned = learner.learn(challenge_depth=0)
        calls.clear()
        outcome = learner.query(['c', 'a', 'b', 'a'], fresh=True, model=learned['artifact'])
        self.assertEqual(outcome['status'], 'counterexample')
        self.assertEqual(outcome['word'], ['c', 'a', 'b'])
        self.assertEqual(calls, ['c', 'a', 'b'])

    def test_nonstationarity_requires_new_context_even_if_old_behavior_returns(self):
        journal, memory = self._resources()
        changed = False
        learner = ActiveNavigation(memory, journal, lambda: 'root',
            lambda _: 'changed' if changed else 'root', actions=['a'], context='version-one')
        old = learner.learn(challenge_depth=1)
        changed = True
        learner.query(['a'], fresh=True)
        changed = False
        before = learner.query_attempts
        self.assertEqual(learner.learn(challenge_depth=0)['status'], 'nonstationary')
        self.assertEqual(learner.query(['a'])['status'], 'nonstationary')
        self.assertEqual(learner.query_attempts, before)
        self.assertEqual(memory.status(old['artifact'], old['scope']), 'invalidated')

    def test_all_two_state_binary_worlds_against_independent_product_equivalence(self):
        # All 16 transition tables x 4 output labellings, including unreachable
        # states and indistinguishable states. The learner never gets this bound.
        for edges in itertools.product(range(2), repeat=4):
            for labels in itertools.product(['x', 'y'], repeat=2):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp)
                    position = 0

                    def reset():
                        nonlocal position
                        position = 0
                        return labels[position]

                    def step(action):
                        nonlocal position
                        position = edges[2*position + (action == 'b')]
                        return labels[position]

                    with Journal(path/'events') as journal, ArtifactMemory(path/'memory') as memory:
                        learner = ActiveNavigation(memory, journal, reset, step,
                            actions=['a', 'b'], context='opaque-two-symbol-interface')
                        result = learner.learn(challenge_depth=2)
                        self.assertEqual(result['status'], 'conditional_tested')
                        model = memory.get(result['artifact'])['body']
                        frontier = [(0, model['initial'])]
                        seen = set()
                        # Exact finite product check explores all paired futures;
                        # this is evaluator-only evidence, never learner input.
                        while frontier:
                            actual, inferred = frontier.pop()
                            if (actual, inferred) in seen:
                                continue
                            seen.add((actual, inferred))
                            self.assertEqual(labels[actual], model['outputs'][inferred], (edges, labels))
                            for index, action in enumerate(['a', 'b']):
                                frontier.append((edges[2*actual+index],
                                                 model['transitions'][inferred][action]))

    def test_derived_schedule_is_explicit_and_can_miss_an_unknown_state(self):
        journal, memory = self._resources()
        machine = TinyAliasingMachine()
        learner = ActiveNavigation(memory, journal, machine.reset, machine.step,
            actions=['a', 'b', 'c'], context='hidden-trigger')
        result = learner.learn(strategy='model_derived', extra_depth=0)
        self.assertEqual(result['status'], 'conditional_tested')
        self.assertEqual(result['states'], 1)
        self.assertEqual(result['challenge']['strategy'], 'model_derived')
        self.assertEqual(result['challenge']['words_completed'], 4)
        model = memory.get(result['artifact'])['body']
        self.assertEqual(plan(model, 'goal')['status'], 'no_path')
        self.assertNotEqual(predict_trace(model, ['c', 'a', 'b'])[-1], 'goal')
        challenge = learner.query(['c', 'a', 'b'], fresh=True, model=result['artifact'])
        self.assertEqual(challenge['status'], 'counterexample')
        self.assertEqual(memory.status(result['artifact'], result['scope']), 'invalidated')

    def test_derived_schedule_can_reach_beyond_root_shortlex_horizon(self):
        journal, memory = self._resources()
        state = 0

        def reset():
            nonlocal state
            state = 0
            return '0'

        def step(_):
            nonlocal state
            state = (state+1) % 6
            return str(state)

        learner = ActiveNavigation(memory, journal, reset, step,
            actions=['a'], context='observable-cycle')
        result = learner.learn(strategy='model_derived', extra_depth=0)
        self.assertEqual(result['states'], 6)
        events = Journal.read(journal.path)
        construction = next(e['body']['construction'] for e in events
                            if e['kind'] == 'active_navigation_schedule_construction')
        self.assertGreater(max(map(len, construction['words'])), 4)
        completed = next(e['body'] for e in events if e['kind'] == 'active_navigation_schedule_completed')
        self.assertEqual(completed['schedule_hash'], construction['schedule_hash'])
        self.assertEqual(completed['words_completed'], len(construction['words']))

    def test_construction_limit_and_cumulative_work_are_not_validation(self):
        journal, memory = self._resources()
        learner = ActiveNavigation(memory, journal, lambda: 'x', lambda _: 'x',
            actions=['a'], context='construction-ceiling')
        stopped = learner.learn(strategy='model_derived', max_schedule_work=1)
        self.assertEqual(stopped['status'], 'schedule_incomplete')
        self.assertTrue(all(memory.status(aid, learner.scope) != 'qualified' for aid in learner._models))
        self.assertLessEqual(stopped['schedule_work'], 1)

        journal2, memory2 = self._resources()
        other = ActiveNavigation(memory2, journal2, lambda: 'x', lambda _: 'x',
            actions=['a'], context='cumulative-construction')
        learned = other.learn(strategy='model_derived')
        work, queries = learned['schedule_work'], learned['queries']
        self.assertGreater(work, 0)
        again = other.learn(strategy='model_derived', max_schedule_work=work)
        self.assertEqual(again['status'], 'schedule_incomplete')
        self.assertEqual(again['schedule_work'], work)
        self.assertEqual(again['queries'], queries)

    def test_adaptive_uses_cheaper_distinct_label_schedule_with_root_guard(self):
        journal, memory = self._resources()
        state = 0

        def reset():
            nonlocal state
            state = 0
            return 'x'

        def step(action):
            nonlocal state
            if action == 'a':
                state = 1-state
            return ['x', 'y'][state]

        learner = ActiveNavigation(memory, journal, reset, step,
            actions=['a', 'b'], context='two-visible-states')
        result = learner.learn(challenge_depth=4, strategy='adaptive')
        self.assertEqual(result['challenge']['selected_strategy'], 'model_derived')
        selection = result['challenge']['selection']
        self.assertLess(selection['candidate_cost'], selection['shortlex_cost'])
        events = Journal.read(journal.path)
        event = next(e for e in events if e['kind'] == 'active_navigation_schedule_selection')
        words = event['body']['words']
        self.assertEqual(len(words), result['challenge']['words_completed'])
        for length in range(3):
            self.assertTrue(all(list(w) in words for w in itertools.product(['a', 'b'], repeat=length)))
        description = dict(result['challenge'])
        description.pop('words_completed')
        schedule_hash = description.pop('schedule_hash')
        from kuttaka_runtime.store import digest
        self.assertEqual(schedule_hash, digest({'description': description,
            'controller_source_sha256': learner.source_sha256, 'words': words}))

    def test_adaptive_falls_back_for_aliased_candidate_outputs(self):
        journal, memory = self._resources()
        machine = TinyAliasingMachine()
        learner = ActiveNavigation(memory, journal, machine.reset, machine.step,
            actions=['a', 'b', 'c'], context='alias-fallback')
        result = learner.learn(challenge_depth=4, strategy='adaptive')
        self.assertEqual(result['states'], 4)
        self.assertEqual(result['challenge']['selected_strategy'], 'shortlex')
        self.assertEqual(result['challenge']['words_completed'], 121)

    def test_distinct_labels_in_hypothesis_do_not_prove_absence_of_hidden_aliases(self):
        journal, memory = self._resources()
        edges = [{'a': 1, 'b': 2, 'c': 0}, {'a': 1, 'b': 1, 'c': 1},
                 {'a': 1, 'b': 3, 'c': 0}, {'a': 4, 'b': 0, 'c': 0},
                 {'a': 4, 'b': 4, 'c': 4}]
        labels, state = ['x', 'G', 'x', 'x', 'Z'], 0

        def reset():
            nonlocal state
            state = 0
            return labels[state]

        def step(action):
            nonlocal state
            state = edges[state][action]
            return labels[state]

        learner = ActiveNavigation(memory, journal, reset, step,
            actions=['a', 'b', 'c'], context='concealed-alias')
        result = learner.learn(challenge_depth=4, strategy='adaptive')
        self.assertEqual(result['states'], 2)
        self.assertEqual(result['challenge']['selected_strategy'], 'model_derived')
        counterexample = learner.query(['b', 'b', 'a'], fresh=True, model=result['artifact'])
        self.assertEqual(counterexample['status'], 'counterexample')
        self.assertEqual(memory.status(result['artifact'], result['scope']), 'invalidated')


if __name__ == "__main__":
    unittest.main()
