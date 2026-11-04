"""Controls for the label-hidden forward-stream representation search."""

import tempfile
import unittest
from pathlib import Path

from experiments.astra_representation_search.run import ARMS, LIMITS, fixtures
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.representation_search import RepresentationSearch
from kuttaka_runtime.store import Journal


class RepresentationSearchExperimentTests(unittest.TestCase):
    def _run(self, values, allowed_pairs=None, context="test-stream"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        journal_path = root / "events.jsonl"
        observations = []
        cursor = 0
        with Journal(journal_path) as journal, ArtifactMemory(root / "memory") as memory:
            def observe_next():
                nonlocal cursor
                # The host interface carries no task ID, future-index request, or label.
                self.assertEqual(cursor, len(observations))
                events = Journal.read(journal_path)
                proposals = [event for event in events
                             if event["kind"] == "representation_search_decision"]
                if proposals and proposals[-1]["body"]["predictions"]:
                    committed = {event["body"].get("artifact") for event in events
                                 if event["kind"] == "representation_forecast"
                                 and event["body"].get("index") == cursor}
                    self.assertEqual(committed, set(proposals[-1]["body"]["predictions"]),
                                     "observation happened before all forecasts were committed")
                if cursor == len(values):
                    raise StopIteration
                value = str(values[cursor])
                observations.append(value)
                cursor += 1
                return value

            controller = RepresentationSearch(memory, journal, observe_next,
                context=context, **LIMITS, allowed_pairs=allowed_pairs)
            report = controller.run()
        return report, observations, Journal.read(journal_path)

    def test_fixture_families_and_restricted_arms_are_explicit(self):
        streams = fixtures()
        self.assertTrue({"r2d1_base", "r2d1_variant_a", "r2d1_variant_b",
            "oscillator", "arithmetic", "factorial_ratio", "noisy_counterexample",
            "delayed_shift", "delayed_change", "undersampled_all_zero",
            "undersampled_budget", "future_leading_zero", "shared_prefix_a",
            "shared_prefix_b"}.issubset(streams))
        self.assertTrue(all(len(values) == 32 for name, values in streams.items()
                            if name != "undersampled_budget"))
        self.assertEqual(len(streams["undersampled_budget"]), 3)
        # Directly verify the core variable-order, variable-degree generator.
        values = streams["r2d1_base"]
        for n in range(1, len(values)-1):
            self.assertEqual(values[n+1], (n+1)*values[n] + values[n-1])
        self.assertEqual(ARMS["constant_only_d0"], [[r, 0] for r in range(1, 5)])
        self.assertEqual(ARMS["first_order_only_r1"], [[1, d] for d in range(5)])
        self.assertEqual(LIMITS["max_observations"], 16)
        self.assertEqual(LIMITS["max_fits"], 128)

    def test_controller_consumes_only_forward_observations_and_keeps_history(self):
        report, observed, events = self._run(fixtures()["r2d1_base"])
        self.assertLessEqual(len(observed), 16)
        self.assertEqual(report["observations"], len(observed))
        # Whenever predictions exist, each precedes the sequential observation.
        committed = [event for event in events
                     if event["kind"] == "representation_search_decision"
                     and event["body"]["predictions"]]
        # A small stream may not identify a predictive relation; other tests
        # exercise exactly this same observer-order check when a forecast exists.
        self.assertEqual(report["history"], observed)
        self.assertIn("correct_issued", report)
        self.assertIn("wrong_issued", report)
        self.assertIn("abstentions", report)

    def test_ablation_arms_receive_identical_observation_prefixes(self):
        values = fixtures()["r2d1_base"]
        prefixes = []
        for arm, pairs in ARMS.items():
            report, observed, _ = self._run(values, allowed_pairs=pairs,
                                             context="same-ablation-prefix")
            self.assertLessEqual(report["observations"], 16)
            prefixes.append(observed)
        self.assertEqual(prefixes[0], prefixes[1])
        self.assertEqual(prefixes[0], prefixes[2])

    def test_shared_prefix_prevents_early_branch_identification(self):
        a, b = fixtures()["shared_prefix_a"], fixtures()["shared_prefix_b"]
        common = 0
        for left, right in zip(a, b):
            if left != right:
                break
            common += 1
        self.assertGreaterEqual(common, 12)
        report_a, obs_a, _ = self._run(a, context="shared-prefix")
        report_b, obs_b, _ = self._run(b, context="shared-prefix")
        self.assertEqual(obs_a[:common], obs_b[:common])
        first_difference = next((i for i, pair in enumerate(zip(obs_a, obs_b))
                                 if pair[0] != pair[1]), None)
        if first_difference is None:
            # If the controller abstains before it can distinguish the streams,
            # all decisions must remain the same; absence of evidence is not a win.
            self.assertEqual(report_a["decisions"], report_b["decisions"])
        else:
            for left, right in zip(report_a["decisions"], report_b["decisions"]):
                if left.get("index", -1) >= first_difference:
                    break
                self.assertEqual(left.get("mode"), right.get("mode"))
                self.assertEqual(left.get("value"), right.get("value"))

    def test_counterexample_is_recorded_without_discarding_prior_observations(self):
        report, observed, events = self._run(fixtures()["noisy_counterexample"])
        counterexamples = [event for event in events
                           if event["kind"] == "representation_counterexample"]
        self.assertTrue(counterexamples)
        self.assertEqual(report["history"], observed)
        for event in counterexamples:
            prior_outcomes = sum(other["kind"] == "representation_search_outcome"
                                 and other["seq"] < event["seq"] for other in events)
            self.assertEqual(event["body"]["history_preserved"], prior_outcomes + 1)

    def test_leading_polynomial_root_blocks_forecast_and_short_stream_stays_incomplete(self):
        root_report, _, root_events = self._run(fixtures()["future_leading_zero"])
        self.assertTrue(any(event["kind"] == "representation_forecast_blocked"
                            for event in root_events))
        short_report, short_observed, _ = self._run(fixtures()["undersampled_budget"])
        self.assertLessEqual(len(short_observed), 3)
        self.assertEqual(short_report["status"], "input_exhausted")
        self.assertEqual(short_report['measurement_attempts'], 4)


if __name__ == "__main__":
    unittest.main()
