"""Local controls for the authored active-navigation experiment harness."""

import tempfile
import unittest
from pathlib import Path

from experiments.astra_active_navigation.run import (
    LIMITS,
    _make_long_trigger,
    _no_goal_world,
    _nonstationary_relearn,
    _partial_control,
    _positive_case,
    _query_trace,
    _reactive_policy_baseline,
    _run_branch_goal_change,
    _shortlex_baseline,
    fixtures,
)


class ActiveNavigationExperimentTests(unittest.TestCase):
    def _root(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def test_opaque_fixtures_have_evaluator_only_target_words_and_equal_limits(self):
        worlds = fixtures()
        self.assertEqual(set(worlds), {"aliased_rooms", "protocol_handshake",
            "delayed_actuator", "locked_workflow", "renamed_handshake",
            "three_phase_cycle", "branching_targets"})
        self.assertEqual(LIMITS, {"max_queries": 4096, "max_actions": 50000,
            "max_word_length": 32, "max_states": 128, "challenge_depth": 4})
        for label, (world, goal, word, count, challenge) in worlds.items():
            self.assertEqual(world.actions, ["a", "b", "c"])
            if word is None:
                continue
            self.assertLessEqual(len(word), 4)
            output = world.reset()
            for action in word:
                output = world.step(action)
            self.assertEqual(output, goal, label)
            self.assertGreaterEqual(count, 4)
            self.assertLessEqual(len(challenge), len(word))

    def test_complete_learn_route_freshcheck_reload_and_reuse_by_exact_id(self):
        root = self._root()
        world, goal, target, count, cex = fixtures()["aliased_rooms"]
        result = _positive_case(root, "aliased", world, goal, target, count, cex)
        self.assertEqual(result["status"], "conditional_tested")
        self.assertEqual(result["route_actions"], list(target))
        self.assertEqual(result["first_fresh_execution"]["status"], "goal_observed")
        self.assertEqual(result["reloaded_execution_same_route_id"]["status"], "goal_observed")
        self.assertTrue(result["goal_restored"])
        self.assertEqual(result["learn"]["challenge"]["words_completed"], 121)
        self.assertLessEqual(result["learn"]["action_attempts"], 50000)

    def test_branching_world_keeps_goal_ambiguous_then_reuses_map_for_new_authorized_goal(self):
        result = _run_branch_goal_change(self._root())
        self.assertEqual(result["unresolved_before_user_clarification"]["status"], "goal_unresolved")
        self.assertEqual(result["route_A_actions"], ["a", "b"])
        self.assertEqual(result["route_B_actions"], ["c", "a"])
        self.assertEqual(result["route_A_initial_execution"]["status"], "goal_observed")
        self.assertEqual(result["route_A_reload_execution"]["status"], "goal_observed")
        self.assertEqual(result["old_route_after_goal_change"]["status"], "goal_binding_changed")
        self.assertEqual(result["route_B_execution"]["status"], "goal_observed")
        self.assertTrue(result["goal_B_restored_and_active"])

    def test_horizon_control_is_indistinguishable_and_shortlex_baseline_cost_is_reported(self):
        root = self._root()
        idle = _partial_control(root, "idle", _no_goal_world, "G", 2,
                                 context="horizon-control")
        trigger = _partial_control(root, "trigger", _make_long_trigger, "G", 2,
                                   context="horizon-control")
        self.assertEqual(idle["learn"]["status"], "conditional_tested")
        self.assertEqual(trigger["learn"]["status"], "conditional_tested")
        self.assertEqual(idle["route"]["status"], "no_route_in_hypothesis")
        self.assertEqual(trigger["route"]["status"], "no_route_in_hypothesis")
        self.assertEqual(_query_trace(idle["events"]), _query_trace(trigger["events"]))

        world = fixtures()["protocol_handshake"][0]
        reactive = _reactive_policy_baseline(world, "G")
        enumeration = _shortlex_baseline(fixtures()["protocol_handshake"][0], "G", max_depth=4)
        self.assertEqual(reactive["status"], "not_solved")
        self.assertEqual(enumeration["word"], list("abc"))
        self.assertLess(enumeration["step_calls"] + enumeration["reset_calls"], 50000)

    def test_no_reset_and_tiny_query_budget_stop_without_candidate_artifact(self):
        root = self._root()
        no_reset = _partial_control(root, "no-reset", lambda: fixtures()["protocol_handshake"][0],
                                    "G", 2, reset_available=False)
        tiny = _partial_control(root, "tiny", lambda: fixtures()["protocol_handshake"][0],
                                 "G", 2, limits={"max_queries": 1, "max_actions": 10})
        self.assertEqual(no_reset["learn"]["status"], "reset_unavailable")
        self.assertNotIn("artifact", no_reset["learn"])
        self.assertEqual(tiny["learn"]["status"], "budget_exhausted")
        self.assertNotIn("artifact", tiny["learn"])

    def test_nonstationary_route_invalidates_then_new_context_learns_and_executes(self):
        result = _nonstationary_relearn(self._root())
        self.assertEqual(result["fresh_counterexample"]["status"], "counterexample")
        self.assertEqual(result["old_model_status"], "invalidated")
        self.assertEqual(result["old_route_status"], "invalidated")
        self.assertEqual(result["new_route_actions"], ["c", "a"])
        self.assertEqual(result["new_context_execution"]["status"], "goal_observed")
        self.assertNotEqual(result["learn_v1"]["scope"], result["learn_v2"]["scope"])


if __name__ == "__main__":
    unittest.main()
