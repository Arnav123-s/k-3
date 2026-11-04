import unittest

from kuttaka_runtime.models import parse_action
from kuttaka_runtime.response_feedback import classify_invalid_action


class InvalidActionFeedbackTests(unittest.TestCase):
    def test_empty_and_whitespace_final_content_get_fixed_feedback(self):
        for text in ("", " \t\n"):
            with self.subTest(text=repr(text)):
                result = classify_invalid_action(parse_action(text), {"finish_reason": "stop"})
                self.assertEqual(result["category"], "empty_final_content")
                if text:
                    self.assertNotIn(text, result["feedback"])

    def test_empty_content_after_length_has_distinct_category(self):
        result = classify_invalid_action(parse_action(""), {"finish_reason": "length"})
        self.assertEqual(result["category"], "empty_final_content_truncated")

    def test_malformed_json_after_length_gets_fixed_feedback_without_echo(self):
        secretish_reply = '{"kind":"experiment","private":"do not echo"'
        result = classify_invalid_action(parse_action(secretish_reply), {"finish_reason": "length"})
        self.assertEqual(result["category"], "invalid_json_after_truncation")
        self.assertNotIn("private", result["feedback"])
        self.assertNotIn(secretish_reply, result["feedback"])
        self.assertEqual(set(result), {"category", "feedback"})

    def test_valid_action_with_length_finish_is_untouched(self):
        action = {"kind": "answer", "answer": {"safe": True}}
        self.assertIsNone(classify_invalid_action(action, {"finish_reason": "length"}))

    def test_nontruncated_nonempty_invalid_reply_gets_syntax_feedback_without_echo(self):
        raw = '{"kind":"experiment","private":"do not echo"'
        result = classify_invalid_action(parse_action(raw), {"finish_reason": "stop"})
        self.assertEqual(result['category'], 'invalid_json')
        self.assertIn('not valid JSON', result['feedback'])
        self.assertNotIn(raw, result['feedback'])
        self.assertNotIn('private', result['feedback'])

    def test_valid_nonobject_json_after_length_gets_no_override(self):
        self.assertIsNone(classify_invalid_action(parse_action("[]"), {"finish_reason": "length"}))

    def test_malformed_or_unknown_metadata_fails_safe(self):
        action = parse_action("")
        for usage in (None, [], {}, {"finish_reason": 7}, {"finish_reason": "vendor-private-reason"}):
            with self.subTest(usage=type(usage).__name__):
                self.assertIsNone(classify_invalid_action(action, usage))

    def test_helper_never_invents_action_or_predictions(self):
        result = classify_invalid_action(parse_action(""), {"finish_reason": "stop"})
        self.assertEqual(set(result), {"category", "feedback"})
        self.assertNotIn("action", result)
        self.assertNotIn("predictions", result)


if __name__ == "__main__":
    unittest.main()
