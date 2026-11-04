import json
import unittest

from experiments.investigator_transfer.environments import (
    ALL_FAMILIES,
    TARGET_FAMILIES,
    make_task,
)


class InvestigatorEnvironmentTests(unittest.TestCase):
    def test_public_contract_is_separate_from_host_qualification_material(self):
        for family in ALL_FAMILIES:
            with self.subTest(family=family):
                task = make_task(family, 17)
                self.assertEqual(set(task.public), {"goal", "input_schema", "output_schema", "domain_description"})
                public_text = json.dumps(task.public, sort_keys=True).lower()
                for secret_marker in ("expected", "private_manifest", "coefficient", "formula"):
                    self.assertNotIn(secret_marker, public_text)
                self.assertTrue(task.checks)
                self.assertEqual(set(task.private_manifest), {"task_id", "family", "seed", "truth_sha256", "checks_sha256"})
                self.assertEqual(len(task.private_manifest["truth_sha256"]), 64)
                self.assertEqual(len(task.private_manifest["checks_sha256"]), 64)

    def test_instances_are_seeded_and_oracle_matches_private_checks(self):
        for family in ALL_FAMILIES:
            with self.subTest(family=family):
                first = make_task(family, 29)
                repeat = make_task(family, 29)
                other = make_task(family, 30)
                self.assertEqual(first.private_manifest, repeat.private_manifest)
                self.assertEqual(first.checks, repeat.checks)
                self.assertNotEqual(first.private_manifest["truth_sha256"], other.private_manifest["truth_sha256"])
                self.assertGreaterEqual(len(first.checks), 5)
                for check in first.checks:
                    self.assertEqual(first.observe(check["input"]), check["expected"])

    def test_target_tasks_have_independent_qualification_cases(self):
        for family in TARGET_FAMILIES:
            with self.subTest(family=family):
                task = make_task(family, 8)
                serialized_inputs = [json.dumps(row["input"], sort_keys=True) for row in task.checks]
                self.assertEqual(len(task.checks), 16 if family == "target_string" else 24)
                self.assertEqual(len(set(serialized_inputs)), len(serialized_inputs))
                # A constant answer must fail at least one informative target case.
                constant = {"text": ""} if family == "target_string" else {"displacement": 0}
                mismatches = sum(check["expected"] != constant for check in task.checks)
                self.assertGreaterEqual(mismatches, 1)

                other = make_task(family, 9)
                other_challenge_inputs = {
                    json.dumps(row["input"], sort_keys=True) for row in other.checks[8:]
                }
                challenge_inputs = set(serialized_inputs[8:])
                self.assertNotEqual(challenge_inputs, other_challenge_inputs)

                if family == "target_string":
                    challenge_texts = [row["input"]["text"] for row in task.checks[8:]]
                    self.assertTrue(any(len(text) >= 6 for text in challenge_texts))
                    self.assertTrue(any(len(set(text)) < len(text) for text in challenge_texts))
                    self.assertTrue(all(0 <= len(text) <= 12 and text.isupper() for text in challenge_texts))
                else:
                    for row in task.checks[8:]:
                        value = row["input"]
                        self.assertTrue(0 <= value["drive"] <= 12)
                        self.assertTrue(0 <= value["load"] <= 5)
                        self.assertIsInstance(value["latch"], bool)

    def test_source_check_sets_exhaust_the_integer_input_domain(self):
        for family in ("source_affine", "source_piecewise"):
            with self.subTest(family=family):
                task = make_task(family, 8)
                self.assertEqual([row["input"]["x"] for row in task.checks], list(range(-12, 13)))

    def test_observe_enforces_each_public_input_domain(self):
        self.assertEqual(
            make_task("source_affine", 2).observe({"x": -12}),
            make_task("source_affine", 2).checks[0]["expected"],
        )
        self.assertTrue(make_task("target_string", 2).observe({"text": "AZ"}))
        self.assertTrue(make_task("target_mechanical", 2).observe({"drive": 3, "load": 1, "latch": False}))

        invalid = [
            ("source_affine", {"x": True}),
            ("source_piecewise", {"x": 13}),
            ("target_string", {"text": "a"}),
            ("target_string", {"text": "ABCDEFGHIJKLM"}),
            ("target_mechanical", {"drive": 1, "load": 0, "latch": 0}),
            ("target_mechanical", {"drive": 1, "load": 6, "latch": False}),
        ]
        for family, value in invalid:
            with self.subTest(family=family, value=value), self.assertRaises(ValueError):
                make_task(family, 0).observe(value)

    def test_unknown_family_and_non_integer_seed_are_rejected(self):
        with self.assertRaises(ValueError):
            make_task("candidate_solver", 1)
        with self.assertRaises(ValueError):
            make_task("source_affine", True)
