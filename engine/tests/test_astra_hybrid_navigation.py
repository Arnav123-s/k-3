"""The hybrid is a finite ordered union, not a merged or deduplicated test set."""

from pathlib import Path
import tempfile
import unittest

from experiments.astra_active_navigation.run import _make_long_trigger
from experiments.astra_model_experiments.run import exact_equivalence
from kuttaka_runtime.active_navigation import ActiveNavigation
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.store import Journal, digest


class HybridNavigationTests(unittest.TestCase):
    def _learner(self, root, world, *, max_queries=4096, max_actions=50000,
                 max_schedule_work=1_000_000, max_length=16):
        journal = Journal(root / "events.jsonl")
        memory = ArtifactMemory(root / "memory")
        learner = ActiveNavigation(memory, journal, world.reset, world.step,
            actions=list(world.actions), context="authored-hybrid-fixture",
            max_queries=max_queries, max_actions=max_actions)
        return learner, memory, journal, max_schedule_work, max_length

    def test_hybrid_hashes_and_order_bind_both_components_and_keep_duplicates(self):
        class ConstantWorld:
            actions = ["a"]
            def __init__(self):
                self.transitions = {"q": {"a": "q"}}
                self.outputs = {"q": "x"}
                self.initial = "q"
                self.reset_calls = self.step_calls = 0
            def reset(self):
                self.reset_calls += 1
                return "x"
            def step(self, action):
                self.step_calls += 1
                return "x"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            world = ConstantWorld()
            with Journal(root/"events.jsonl") as journal, ArtifactMemory(root/"memory") as memory:
                learner = ActiveNavigation(memory, journal, world.reset, world.step,
                    actions=world.actions, context="constant-hybrid")
                result = learner.learn(strategy="hybrid", sampling={
                    "seed": 809, "max_length": 0, "persistence": True,
                    "epsilon": [1, 20], "delta": [1, 20]})
                self.assertEqual(result["status"], "conditional_tested")
                challenge = result["challenge"]
                self.assertFalse(challenge["statistical_certificate"])
                self.assertEqual(challenge["word_order"],
                    "model_derived_then_all_sampled_draws_no_deduplication")
                self.assertEqual(challenge["deterministic_words"], 2)
                self.assertEqual(challenge["sampled_words"], 72)
                events = Journal.read(journal.path)
                component_events = [event["body"] for event in events
                    if event["kind"] == "active_navigation_hybrid_construction"]
                self.assertEqual([event["component"] for event in component_events],
                    ["model_derived", "sampled"])
                deterministic = component_events[0]["construction"]
                sampled = component_events[1]["construction"]
                self.assertEqual(challenge["component_hashes"], {
                    "model_derived": deterministic["schedule_hash"],
                    "sampled": sampled["schedule_hash"]})
                schedule_event = next(event["body"] for event in events
                    if event["kind"] == "active_navigation_schedule")
                challenge_words = [event["body"]["word"] for event in events
                    if event["kind"] == "active_navigation_challenge"]
                expected_words = deterministic["words"] + sampled["words"]
                self.assertEqual(challenge_words, expected_words)
                self.assertEqual(len(challenge_words), len(set(tuple(w) for w in challenge_words)) + 72)
                expected_hash = digest({
                    "format": "active-navigation-hybrid-schedule-v1",
                    "model": result["artifact"],
                    "model_digest": digest(memory.get(result["artifact"])["body"]),
                    "component_hashes": challenge["component_hashes"],
                    "word_order": challenge["word_order"],
                    "compact_prefixes": False,
                    "original_deterministic_words": len(deterministic["words"]),
                    "coverage_certificate_digest": digest([]),
                    "deterministic_words": len(deterministic["words"]),
                    "sampled_words": len(sampled["words"]),
                    "words": expected_words,
                    "controller_source_sha256": learner.source_sha256})
                self.assertEqual(schedule_event["schedule_hash"], expected_hash)
                self.assertEqual(challenge["schedule_hash"], expected_hash)

    def test_hybrid_compacts_only_deterministic_words(self):
        class ConstantWorld:
            actions = ["a"]
            def reset(self): return "x"
            def step(self, action): return "x"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            world = ConstantWorld()
            with Journal(root/"events.jsonl") as journal, ArtifactMemory(root/"memory") as memory:
                learner = ActiveNavigation(memory, journal, world.reset, world.step,
                    actions=world.actions, context="compact-hybrid")
                result = learner.learn(strategy="hybrid", hybrid_compact=True, sampling={
                    "seed": 809, "max_length": 0, "persistence": True,
                    "epsilon": [1, 20], "delta": [1, 20]})
                self.assertEqual(result["status"], "conditional_tested")
                challenge = result["challenge"]
                self.assertTrue(challenge["compact_prefixes"])
                self.assertEqual(challenge["original_deterministic_words"], 2)
                self.assertEqual(challenge["deterministic_words"], 1)
                self.assertEqual(challenge["sampled_words"], 72)
                events = Journal.read(journal.path)
                components = [event["body"] for event in events
                    if event["kind"] == "active_navigation_hybrid_construction"]
                deterministic, sampled = (item["construction"] for item in components)
                self.assertTrue(deterministic["compact_prefixes"])
                self.assertEqual(deterministic["original_word_count"], 2)
                self.assertEqual(deterministic["actual_tested_word_count"], 1)
                self.assertEqual(len(sampled["words"]), 72)
                challenge_words = [event["body"]["word"] for event in events
                    if event["kind"] == "active_navigation_challenge"]
                self.assertEqual(challenge_words,
                    deterministic["words"]+sampled["words"])
                self.assertEqual(challenge_words.count([]), 72)
                self.assertEqual(challenge["coverage_certificate_digest"],
                    digest(deterministic["coverage_certificate"]))

    def test_compaction_option_is_rejected_outside_hybrid(self):
        class ConstantWorld:
            actions = ["a"]
            def reset(self): return "x"
            def step(self, action): return "x"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with Journal(root/"events.jsonl") as journal, ArtifactMemory(root/"memory") as memory:
                learner = ActiveNavigation(memory, journal, ConstantWorld().reset,
                    ConstantWorld().step, actions=["a"], context="bad-compact")
                with self.assertRaises(ValueError):
                    learner.learn(strategy="shortlex", hybrid_compact=True)

    def test_hybrid_requires_sample_budget_after_deterministic_component(self):
        class ConstantWorld:
            actions = ["a"]
            def reset(self): return "x"
            def step(self, action): return "x"

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            world = ConstantWorld()
            with Journal(root/"events.jsonl") as journal, ArtifactMemory(root/"memory") as memory:
                learner = ActiveNavigation(memory, journal, world.reset, world.step,
                    actions=world.actions, context="sample-budget", max_queries=10)
                result = learner.learn(strategy="hybrid", sampling={
                    "seed": 809, "max_length": 0, "persistence": True,
                    "epsilon": [1, 20], "delta": [1, 20]})
                self.assertEqual(result["status"], "schedule_incomplete")
                self.assertEqual(result["detail"]["phase"], "sampled_component")
                events = Journal.read(journal.path)
                components = [event["body"] for event in events
                    if event["kind"] == "active_navigation_hybrid_construction"]
                self.assertEqual([event["component"] for event in components],
                    ["model_derived", "sampled"])
                self.assertEqual(components[1]["construction"]["status"], "construction_limit")
                self.assertFalse(any(event["kind"] == "active_navigation_schedule_completed"
                                     for event in events))
                self.assertFalse(any(event["kind"] == "active_navigation_challenge"
                                     for event in events))

    def test_action_budget_failure_never_qualifies_hybrid_schedule(self):
        world = _make_long_trigger(goal_after=3)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with Journal(root/"events.jsonl") as journal, ArtifactMemory(root/"memory") as memory:
                learner = ActiveNavigation(memory, journal, world.reset, world.step,
                    actions=list(world.actions), context="action-budget", max_actions=0)
                result = learner.learn(strategy="hybrid", sampling={
                    "seed": 809, "max_length": 3, "persistence": True,
                    "epsilon": [1, 20], "delta": [1, 20]})
                self.assertEqual(result["status"], "budget_exhausted")
                events = Journal.read(journal.path)
                self.assertFalse(any(event["kind"] == "active_navigation_schedule_completed"
                                     for event in events))

    def test_long_trigger_eight_seed_809_suffix_hybrid_adds_outcome_path(self):
        world = _make_long_trigger(goal_after=8)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with Journal(root/"events.jsonl") as journal, ArtifactMemory(root/"memory") as memory:
                learner = ActiveNavigation(memory, journal, world.reset, world.step,
                    actions=list(world.actions), context="long-trigger-hybrid")
                result = learner.learn(strategy="hybrid", counterexample_strategy="suffix",
                    hybrid_compact=True,
                    sampling={"seed": 809, "max_length": 16, "persistence": True,
                        "epsilon": [1, 20], "delta": [1, 20]})
                self.assertEqual(result["status"], "conditional_tested")
                exact = exact_equivalence(world, memory.get(result["artifact"])["body"])
                self.assertEqual(exact["status"], "equivalent")
                constructions = [event["body"]["construction"] for event in Journal.read(journal.path)
                    if event["kind"] == "active_navigation_hybrid_construction"
                    and event["body"]["component"] == "model_derived"]
                trigger_word = tuple(["b"]+["a"]*8)
                obligation = {"start_state": 0, "output": "G",
                    "word": ["a"]*8, "target_state": 8}
                found_covered_trigger = False
                for construction in constructions:
                    coverage = {tuple(item["original_word"]): tuple(item["retained_witness"])
                                for item in construction["coverage_certificate"]}
                    witness = coverage.get(trigger_word)
                    if (obligation in construction["outcome_path_obligations"] and witness is not None
                            and witness[:len(trigger_word)] == trigger_word):
                        found_covered_trigger = True
                self.assertTrue(found_covered_trigger)
                self.assertFalse(result["challenge"]["statistical_certificate"])


if __name__ == "__main__":
    unittest.main()
