"""Opt-in response feedback preserves strict defaults and replay semantics."""

import tempfile
from pathlib import Path
import unittest

from kuttaka_runtime import Budget, Journal, investigate
from kuttaka_runtime.models import ReplayModel


INVALID_TRUNCATED = {"kind": "invalid", "raw_reply": '{"kind":"answer"'}
VALID_ANSWER = {"kind": "answer", "answer": 42}


class Replies:
    identity = {"model": "scripted"}

    def __init__(self, *actions):
        self.actions = iter(actions)
        self.contexts = []
        self.last_usage = {"input_tokens": 3, "output_tokens": 2, "finish_reason": "length"}
        self.last_response = {"status": 200}

    def propose(self, context):
        self.contexts.append(context)
        return next(self.actions)


class ResponseFeedbackRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def _run(self, name, model, **options):
        path = Path(self.temp.name) / name
        with Journal(path) as journal:
            result = investigate("Find the answer", model, {}, lambda _request, answer: answer == 42,
                                 journal, Budget(2, 2), **options)
        return result, Journal.read(path), path

    def test_default_false_has_identical_legacy_journal_shape(self):
        _, omitted_events, _ = self._run("omitted.jsonl", Replies(INVALID_TRUNCATED, VALID_ANSWER))
        _, false_events, _ = self._run("false.jsonl", Replies(INVALID_TRUNCATED, VALID_ANSWER),
                                      detailed_response_feedback=False)
        self.assertEqual(omitted_events, false_events)
        self.assertNotIn("runtime_options", omitted_events[0]["body"])
        rejection = next(event for event in omitted_events if event["kind"] == "action_rejected")
        self.assertEqual(rejection["body"]["feedback"],
                         "Use kind experiment, answer, or unresolved and follow that action schema.")

    def test_opt_in_feedback_reaches_next_context_without_raw_reply(self):
        model = Replies(INVALID_TRUNCATED, VALID_ANSWER)
        result, events, _ = self._run("opt-in.jsonl", model, detailed_response_feedback=True)
        feedback = model.contexts[1]["history"][-1]["observation"]["feedback"]
        self.assertIn("ended at the token limit without valid JSON", feedback)
        self.assertNotIn(INVALID_TRUNCATED["raw_reply"], feedback)
        self.assertEqual((result.status, result.model_calls, result.tool_calls), ("checked", 2, 1))
        self.assertEqual(sum(event["kind"] == "model_attempt" for event in events), 2)
        self.assertEqual(sum(event["kind"] == "tool_attempt" for event in events), 1)
        start = events[0]["body"]
        self.assertTrue(start["runtime_options"]["detailed_response_feedback"])
        self.assertEqual(next(event for event in events if event["kind"] == "action_rejected")["body"]["feedback"],
                         feedback)

    def test_valid_action_is_unchanged_by_opt_in(self):
        _, default_events, _ = self._run("valid-default.jsonl", Replies(VALID_ANSWER))
        _, opted_events, _ = self._run("valid-opted.jsonl", Replies(VALID_ANSWER),
                                       detailed_response_feedback=True)
        for kind in ("model_reply", "proposal", "tool_attempt", "observation", "finish"):
            self.assertEqual([e["body"] for e in default_events if e["kind"] == kind],
                             [e["body"] for e in opted_events if e["kind"] == kind])

    def test_opt_in_replays_identically_and_refuses_option_mismatch(self):
        original, events, path = self._run("recorded.jsonl", Replies(INVALID_TRUNCATED, VALID_ANSWER),
                                            detailed_response_feedback=True)
        replay_path = Path(self.temp.name) / "replayed.jsonl"
        with Journal(replay_path) as journal:
            replayed = investigate("Find the answer", ReplayModel(path), {},
                                   lambda _request, answer: answer == 42, journal,
                                   Budget(2, 2), detailed_response_feedback=True)
        self.assertEqual(replayed, original)
        self.assertEqual(Journal.read(replay_path), events)

        mismatch_path = Path(self.temp.name) / "mismatch.jsonl"
        with Journal(mismatch_path) as journal:
            with self.assertRaisesRegex(ValueError, "runtime options do not match"):
                investigate("Find the answer", ReplayModel(path), {}, lambda *_: True,
                            journal, Budget(2, 2), detailed_response_feedback=False)
        self.assertEqual(Journal.read(mismatch_path), [])

    def test_option_requires_exact_bool(self):
        with Journal(Path(self.temp.name) / "bad-bool.jsonl") as journal:
            with self.assertRaisesRegex(ValueError, "detailed_response_feedback must be bool"):
                investigate("Find the answer", Replies(VALID_ANSWER), {}, lambda *_: True,
                            journal, Budget(1, 1), detailed_response_feedback=1)
        self.assertEqual(Journal.read(Path(self.temp.name) / "bad-bool.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
