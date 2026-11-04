"""Opt-in suffix counterexample refinement; all target machines are authored."""

from pathlib import Path
import tempfile
import unittest

from experiments.astra_model_experiments.run import _generated_world, exact_equivalence
from kuttaka_runtime.active_navigation import ActiveNavigation, _LearnStop
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.store import Journal


class SuffixRefinementTests(unittest.TestCase):
    def _learn(self, state_count, seed, *, partially_observed):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        world, _truth = _generated_world(state_count, seed,
            partially_observed=partially_observed)
        journal = Journal(root / "events.jsonl")
        memory = ArtifactMemory(root / "memory")
        self.addCleanup(journal.close)
        self.addCleanup(memory.close)
        learner = ActiveNavigation(memory, journal, world.reset, world.step,
            actions=list(world.actions), context=f"authored-{state_count}-{seed}-{partially_observed}")
        result = learner.learn(strategy="distribution", sampling={"seed": 809},
            counterexample_strategy="suffix")
        return world, learner, memory, journal, result

    def test_small_generated_worlds_refine_to_exact_finite_models(self):
        for state_count, seed, partial in ((4, 11, False), (6, 29, True)):
            with self.subTest(states=state_count, seed=seed, partial=partial):
                world, learner, memory, journal, result = self._learn(
                    state_count, seed, partially_observed=partial)
                self.assertEqual(result["status"], "conditional_tested")
                self.assertEqual(exact_equivalence(world,
                    memory.get(result["artifact"])["body"])["status"], "equivalent")
                refinements = [event for event in Journal.read(journal.path)
                    if event["kind"] == "active_navigation_counterexample_refinement"]
                if state_count == 4:
                    self.assertEqual(len(refinements), 0)
                else:
                    self.assertGreater(len(refinements), 0)
                for event in refinements:
                    self.assertEqual(event["body"]["strategy"], "suffix")
                    self.assertEqual(event["body"]["boundary"][1], event["body"]["boundary"][0]+1)
                    self.assertEqual(event["body"]["method"],
                        "Rivest-Schapire adjacent-boundary suffix refinement")

    def test_distribution_regression_generated_ten_seed_223_colors(self):
        world, learner, memory, journal, result = self._learn(
            10, 223, partially_observed=True)
        self.assertEqual(result["status"], "conditional_tested")
        self.assertEqual(exact_equivalence(world,
            memory.get(result["artifact"])["body"])["status"], "equivalent")
        refinements = [event for event in Journal.read(journal.path)
            if event["kind"] == "active_navigation_counterexample_refinement"]
        self.assertGreater(len(refinements), 0)
        self.assertGreaterEqual(result["counterexamples"], 1)
        self.assertEqual(memory.status(result["artifact"], learner.scope), "qualified")
        for event in refinements:
            body = event["body"]
            trimmed = body["trimmed_word"]
            self.assertEqual(body["source_word"][:len(trimmed)], trimmed)
            self.assertEqual(body["boundary_values"][0] == body["boundary_values"][1], False)
            self.assertIn(body["suffix"], body["added_suffix_closure"])

    def test_refinement_query_word_limit_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with Journal(root/"events.jsonl") as journal, ArtifactMemory(root/"memory") as memory:
                learner = ActiveNavigation(memory, journal, lambda: "x", lambda _a: "y",
                    actions=["a"], context="limit", max_word_length=1)
                model = {"format": "active-moore-machine-v1", "actions": ["a"],
                    "initial": 0, "outputs": ["x", "y"],
                    "transitions": [{"a": 1}, {"a": 1}],
                    "access": [[], ["a", "a"]], "suffixes": [[]]}
                with self.assertRaises(_LearnStop) as raised:
                    learner._refine_counterexample_suffix(["a"], model, "model-id",
                        observed_word=["a"], observed_trace=["x", "z"])
                self.assertEqual(raised.exception.outcome["status"], "word_budget_exhausted")
                self.assertFalse(any(event["kind"] == "active_navigation_schedule_completed"
                                     for event in Journal.read(journal.path)))

    def test_observer_failure_in_refinement_is_not_qualified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def fail_step(_action):
                raise RuntimeError("authored observer failure")
            with Journal(root/"events.jsonl") as journal, ArtifactMemory(root/"memory") as memory:
                learner = ActiveNavigation(memory, journal, lambda: "x", fail_step,
                    actions=["a"], context="observer")
                model = {"format": "active-moore-machine-v1", "actions": ["a"],
                    "initial": 0, "outputs": ["x", "y"],
                    "transitions": [{"a": 1}, {"a": 1}],
                    "access": [[], ["a"]], "suffixes": [[]]}
                with self.assertRaises(_LearnStop) as raised:
                    learner._refine_counterexample_suffix(["a"], model, "model-id",
                        observed_word=["a"], observed_trace=["x", "z"])
                self.assertEqual(raised.exception.outcome["status"], "observer_error")
                events = Journal.read(journal.path)
                self.assertFalse(any(event["kind"] == "active_navigation_schedule_completed"
                                     for event in events))
                self.assertTrue(any(event["kind"] == "active_navigation_query_attempt"
                                    for event in events))


if __name__ == "__main__":
    unittest.main()
