"""Tests for bounded candidate-derived conformance schedules."""

import itertools
import unittest

from kuttaka_runtime.active_navigation import predict_trace, validate_model
from kuttaka_runtime.model_experiments import construct_experiments


def alias_model():
    return {"format": "active-moore-machine-v1", "actions": ["a", "b"],
        "initial": 0, "outputs": ["x", "x", "goal", "x"],
        "transitions": [
            {"a": 1, "b": 3},
            {"a": 3, "b": 2},
            {"a": 2, "b": 2},
            {"a": 3, "b": 3}],
        "access": [[], ["a"], ["a", "b"], ["b"]], "suffixes": [[]]}


def equivalent_model():
    return {"format": "active-moore-machine-v1", "actions": ["a", "b"],
        "initial": 0, "outputs": ["x", "x", "x"],
        "transitions": [
            {"a": 1, "b": 2},
            {"a": 2, "b": 0},
            {"a": 0, "b": 1}],
        "access": [[], ["a"], ["b"]], "suffixes": [[]]}


def _trace_from(model, state, word):
    trace = [model["outputs"][state]]
    for action in word:
        state = model["transitions"][state][action]
        trace.append(model["outputs"][state])
    return trace


def shortest_witness(model, left, right):
    """Independent forward product BFS, used as a small exact oracle."""
    queue = [(left, right, ())]
    seen = {(left, right)}
    cursor = 0
    while cursor < len(queue):
        lstate, rstate, word = queue[cursor]
        cursor += 1
        if model["outputs"][lstate] != model["outputs"][rstate]:
            return word
        for action in model["actions"]:
            pair = (model["transitions"][lstate][action],
                    model["transitions"][rstate][action])
            if pair not in seen:
                seen.add(pair)
                queue.append((pair[0], pair[1], word + (action,)))
    return None


def _shortlex(words, actions):
    rank = {action: i for i, action in enumerate(actions)}
    return sorted(words, key=lambda word: (len(word), tuple(rank[a] for a in word)))


class ModelExperimentTests(unittest.TestCase):
    def test_optional_outcome_paths_match_independent_bfs_for_every_state_and_label(self):
        model = {"format": "active-moore-machine-v1", "actions": ["a", "b"],
            "initial": 0, "outputs": ["x"]*8+["G"],
            "transitions": [{"a": i+1, "b": 0} for i in range(8)] +
                [{"a": 8, "b": 8}],
            "access": [["a"]*i for i in range(9)], "suffixes": [[]]}
        self.assertTrue(validate_model(model))
        result = construct_experiments(model, extra_depth=0, include_outcome_paths=True)
        self.assertEqual(result["status"], "constructed")
        self.assertTrue(result["include_outcome_paths"])
        actual = {(item["start_state"], item["output"]):
                  (tuple(item["word"]), item["target_state"])
                  for item in result["outcome_path_obligations"]}
        labels = sorted(set(model["outputs"]))
        expected_unreachable = []
        for start in range(len(model["outputs"])):
            queue = [(start, ())]
            seen = {start}
            first_by_label = {}
            cursor = 0
            while cursor < len(queue):
                state, word = queue[cursor]
                cursor += 1
                first_by_label.setdefault(model["outputs"][state], (word, state))
                for action in model["actions"]:
                    target = model["transitions"][state][action]
                    if target not in seen:
                        seen.add(target)
                        queue.append((target, word+(action,)))
            for label in labels:
                if label in first_by_label:
                    self.assertEqual(actual[(start, label)], first_by_label[label])
                else:
                    expected_unreachable.append((start, label))
        self.assertEqual([(item["start_state"], item["output"])
                          for item in result["unreachable_output_labels"]], expected_unreachable)
        self.assertIn(["a"]*8, result["outcome_path_suffixes"])
        self.assertIn(["b"]+["a"]*8, result["words"])
        self.assertLessEqual(max(map(len, result["characteristic_suffixes"])), 7)

    def test_outcome_paths_default_preserves_schedule(self):
        model = alias_model()
        implicit = construct_experiments(model, extra_depth=1)
        explicit = construct_experiments(model, extra_depth=1, include_outcome_paths=False)
        self.assertEqual(implicit["words"], explicit["words"])
        self.assertEqual(implicit["suffixes"], explicit["suffixes"])
        self.assertNotIn("outcome_path_obligations", implicit)
        self.assertNotIn("outcome_path_obligations", explicit)

    def test_prefix_compaction_preserves_every_reset_trace_prefix_obligation(self):
        model = alias_model()
        full = construct_experiments(model, extra_depth=1)
        compact = construct_experiments(model, extra_depth=1, compact_prefixes=True)
        original = {tuple(word) for word in full["words"]}
        retained = {tuple(word) for word in compact["words"]}
        self.assertEqual(compact["original_word_count"], len(original))
        self.assertEqual(compact["actual_tested_word_count"], len(retained))
        self.assertLess(len(retained), len(original))
        certificate = {tuple(item["original_word"]): tuple(item["retained_witness"])
                       for item in compact["coverage_certificate"]}
        self.assertEqual(set(certificate), original)
        for word, witness in certificate.items():
            self.assertIn(witness, retained)
            self.assertEqual(witness[:len(word)], word)
        for left in retained:
            for right in retained:
                if len(left) < len(right):
                    self.assertNotEqual(right[:len(left)], left)
        self.assertEqual(construct_experiments(model, extra_depth=1,
            compact_prefixes=True)["schedule_hash"], compact["schedule_hash"])
        self.assertNotEqual(full["schedule_hash"], compact["schedule_hash"])

    def test_prefix_compaction_limits_fail_closed_without_partial_words(self):
        complete = construct_experiments(alias_model(), extra_depth=1,
            compact_prefixes=True)
        result = construct_experiments(alias_model(), extra_depth=1,
            compact_prefixes=True, max_work=complete["work"]-1)
        self.assertEqual(result["status"], "construction_limit")
        self.assertEqual(result["words"], [])

    def test_outcome_path_word_test_and_work_limits_fail_closed(self):
        model = {"format": "active-moore-machine-v1", "actions": ["a", "b"],
            "initial": 0, "outputs": ["x"]*8+["G"],
            "transitions": [{"a": i+1, "b": 0} for i in range(8)] +
                [{"a": 8, "b": 8}],
            "access": [["a"]*i for i in range(9)], "suffixes": [[]]}
        for kwargs in ({"max_word_length": 7}, {"max_work": 2}, {"max_tests": 1}):
            with self.subTest(kwargs=kwargs):
                result = construct_experiments(model, extra_depth=0,
                    include_outcome_paths=True, **kwargs)
                self.assertEqual(result["status"], "construction_limit")
                self.assertEqual(result["words"], [])

    def test_outcome_obligations_do_not_invent_hidden_output_labels(self):
        model = {"format": "active-moore-machine-v1", "actions": ["a", "b"],
            "initial": 0, "outputs": ["x"],
            "transitions": [{"a": 0, "b": 0}], "access": [[]], "suffixes": [[]]}
        result = construct_experiments(model, extra_depth=0, include_outcome_paths=True)
        self.assertEqual(result["status"], "constructed")
        self.assertEqual({item["output"] for item in result["outcome_path_obligations"]}, {"x"})
        self.assertNotIn("G", {item["output"] for item in result["outcome_path_obligations"]})
        self.assertIn("finite tests only", result["interpretation"])

    def test_reverse_bfs_witnesses_match_independent_product_search(self):
        model = alias_model()
        self.assertTrue(validate_model(model))
        result = construct_experiments(model, extra_depth=1)
        self.assertEqual(result["status"], "constructed")
        actual = {tuple(item["states"]): tuple(item["shortest_suffix"])
                  for item in result["distinguished_pairs"]}
        for left in range(len(model["outputs"])):
            for right in range(left+1, len(model["outputs"])):
                self.assertEqual(actual[(left, right)], shortest_witness(model, left, right))
        self.assertEqual(actual[(0, 1)], ("b",))
        self.assertEqual(actual[(0, 3)], ("a", "b"))

    def test_greedy_suffixes_cover_pairs_and_schedule_is_exact_product(self):
        model = alias_model()
        result = construct_experiments(model, extra_depth=1)
        self.assertEqual(result["status"], "constructed")
        suffixes = [tuple(word) for word in result["suffixes"]]
        self.assertIn((), suffixes)
        witnessed = {tuple(item["states"]) for item in result["distinguished_pairs"]}
        for left, right in witnessed:
            self.assertTrue(any(_trace_from(model, left, suffix)[-1] !=
                                _trace_from(model, right, suffix)[-1]
                                for suffix in suffixes))
        prefixes = {tuple(access) for access in model["access"]}
        for access in model["access"]:
            prefixes.update(tuple(access) + (action,) for action in model["actions"])
        middles = {word for length in range(2)
                   for word in itertools.product(model["actions"], repeat=length)}
        expected = {prefix + middle + suffix for prefix in prefixes
                    for middle in middles for suffix in suffixes}
        self.assertEqual([tuple(word) for word in result["words"]],
                         _shortlex(expected, model["actions"]))
        self.assertEqual(result["endpoint_outputs"], ["goal", "x"])
        self.assertTrue(result["schedule_complete_for_distinguishable_pairs"])

    def test_equivalent_states_are_reported_without_false_characterization_claim(self):
        model = equivalent_model()
        result = construct_experiments(model)
        self.assertEqual(result["status"], "equivalent_states")
        self.assertEqual(result["pair_count"], 3)
        self.assertEqual(len(result["equivalent_pairs"]), 3)
        self.assertEqual(result["distinguished_pairs"], [])
        self.assertEqual(result["suffixes"], [[]])
        self.assertTrue(result["schedule_complete_for_distinguishable_pairs"])
        self.assertTrue(result["state_pair_equivalence_warning"])
        self.assertIn([], result["words"])

    def test_constructed_hash_and_order_are_deterministic(self):
        first = construct_experiments(alias_model(), extra_depth=2)
        second = construct_experiments(alias_model(), extra_depth=2)
        self.assertEqual(first["schedule_hash"], second["schedule_hash"])
        self.assertEqual(first["words"], second["words"])
        self.assertEqual(first["work"], second["work"])
        self.assertEqual(first["words"], [list(w) for w in _shortlex(
            {tuple(w) for w in first["words"]}, alias_model()["actions"])])
        changed_limits = construct_experiments(alias_model(), extra_depth=2, max_tests=4095)
        self.assertNotEqual(first["schedule_hash"], changed_limits["schedule_hash"])

    def test_work_test_and_word_limits_fail_closed_without_partial_schedule(self):
        model = alias_model()
        for kwargs in ({"max_work": 5}, {"max_tests": 1}, {"max_word_length": 1},
                       {"max_tests": 0}, {"max_work": 0}):
            with self.subTest(kwargs=kwargs):
                result = construct_experiments(model, **kwargs)
                self.assertEqual(result["status"], "construction_limit")
                self.assertEqual(result["words"], [])
                self.assertLessEqual(result["work"], kwargs.get("max_work", 1_000_000))

    def test_invalid_models_and_nonliteral_or_out_of_range_limits_reject(self):
        malformed = alias_model()
        malformed["transitions"][0]["a"] = 99
        with self.assertRaises(ValueError):
            construct_experiments(malformed)
        for kwargs in ({"extra_depth": True}, {"extra_depth": 4},
                       {"max_tests": True}, {"max_word_length": 33},
                       {"max_work": 1_000_001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                construct_experiments(alias_model(), **kwargs)


if __name__ == "__main__":
    unittest.main()
