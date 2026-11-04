"""Opt-in, content-safe feedback for invalid model action sentinels.

This module has no model, network, journal, or runtime dependencies. Callers may
use the returned fixed feedback only after ordinary action validation fails.
"""

from __future__ import annotations

import json


_EMPTY_FINAL = "empty_final_content"
_EMPTY_TRUNCATED = "empty_final_content_truncated"
_INVALID_TRUNCATED = "invalid_json_after_truncation"
INVALID_JSON = "invalid_json"
TRUNCATED = frozenset({_EMPTY_TRUNCATED, _INVALID_TRUNCATED})
_BRIEF = (" Keep the reasoning short and give the action first. If the checks seem impossible for a method"
          " that follows the task, say so: reply with kind unresolved, naming the check and why.")

_FEEDBACK = {
    _EMPTY_FINAL: "No visible JSON action was received. Return one JSON object that follows the documented action schema.",
    _EMPTY_TRUNCATED: "The response ended at the token limit before a visible JSON action. Return one complete JSON object that follows the documented action schema." + _BRIEF,
    _INVALID_TRUNCATED: "The response ended at the token limit without valid JSON. Return one complete JSON object that follows the documented action schema." + _BRIEF,
    INVALID_JSON: "The reply is not valid JSON. Return exactly one complete JSON object with balanced brackets, "
                  "double-quoted strings, and no prose or Markdown. For an experiment use exactly kind, tool, "
                  "input, and predictions. Keep predictions short: each needs a hypothesis and an outcome; "
                  "a short string outcome is sufficient instead of a nested guessed tool result. Follow the "
                  "tool's documented input types. No tool has run for this rejected reply.",
}


def classify_invalid_action(action, usage):
    """Return fixed feedback for recognized empty, truncated, or malformed replies.

    Only the parser's exact invalid-action sentinel and a known finish reason are
    considered. Raw reply text and other usage metadata are never returned. Any
    malformed or ambiguous input fails safe with ``None``.
    """
    if type(action) is not dict or set(action) != {"kind", "raw_reply"}:
        return None
    if action.get("kind") != "invalid" or type(action.get("raw_reply")) is not str:
        return None
    if type(usage) is not dict:
        return None
    finish_reason = usage.get("finish_reason")
    if type(finish_reason) is not str or finish_reason not in {"stop", "length"}:
        return None

    raw_reply = action["raw_reply"]
    # parse_action retains only the first 2000 characters. A full prefix may
    # belong to valid non-object JSON, so it cannot establish a syntax failure.
    # Keep the existing provider-token-limit classification separate.
    if finish_reason == "stop" and len(raw_reply) >= 2000:
        return None
    if not raw_reply.strip():
        category = _EMPTY_TRUNCATED if finish_reason == "length" else _EMPTY_FINAL
    else:
        try:
            json.loads(raw_reply)
        except (ValueError, TypeError):
            category = _INVALID_TRUNCATED if finish_reason == "length" else INVALID_JSON
        else:
            # A valid non-object JSON reply is outside this helper's scope.
            return None

    return {"category": category, "feedback": _FEEDBACK[category]}
