"""Bounded reset-based learning of finite Moore-machine abstractions.

The learner constructs provisional state classes from action/observation
histories. It uses an L*-style observation table and bounded fresh challenges;
passing that schedule is evidence only for the tested words, never proof of the
complete environment or of the user's intended goal.
"""

from __future__ import annotations

from collections import deque
import hashlib
import itertools
from pathlib import Path
import uuid

from .store import canonical, digest


FORMAT = "active-moore-machine-v1"
MAX_ACTION_NAMES = 32
MAX_ACTION_NAME_CHARS = 64
MAX_OUTPUT_CHARS = 256
MAX_OUTPUT_BYTES = 1024
MAX_SUFFIXES = 4096
MAX_TABLE_CELLS = 100000


def _word(value, actions, *, max_length=32):
    if (not isinstance(value, (list, tuple)) or len(value) > max_length
            or any(not isinstance(action, str) or action not in actions for action in value)):
        raise ValueError("word must be a bounded sequence of declared actions")
    return tuple(value)


def _output(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_OUTPUT_CHARS
            or len(value.encode("utf-8")) > MAX_OUTPUT_BYTES):
        raise ValueError("observations must be nonempty bounded strings")
    return value


def _prefixes(word):
    return [word[:index] for index in range(len(word)+1)]


def validate_model(model) -> bool:
    """Return whether ``model`` is a well-formed bounded Moore table."""
    try:
        if not isinstance(model, dict) or set(model) != {
                "format", "actions", "initial", "outputs", "transitions", "access", "suffixes"}:
            return False
        if model["format"] != FORMAT:
            return False
        actions = model["actions"]
        if (not isinstance(actions, list) or not actions or len(actions) > MAX_ACTION_NAMES
                or any(not isinstance(a, str) or not a or len(a) > MAX_ACTION_NAME_CHARS for a in actions)
                or len(set(actions)) != len(actions)):
            return False
        outputs, transitions = model["outputs"], model["transitions"]
        if (not isinstance(outputs, list) or not 1 <= len(outputs) <= 128
                or not isinstance(transitions, list) or len(transitions) != len(outputs)
                or any(not _valid_output(o) for o in outputs)):
            return False
        for row in transitions:
            if not isinstance(row, dict) or set(row) != set(actions):
                return False
            if any(type(row[a]) is not int or not 0 <= row[a] < len(outputs) for a in actions):
                return False
        if type(model["initial"]) is not int or not 0 <= model["initial"] < len(outputs):
            return False
        for key in ("access", "suffixes"):
            seqs = model[key]
            if (not isinstance(seqs, list) or (key == "suffixes" and len(seqs) > MAX_SUFFIXES) or any(
                    not isinstance(seq, list) or len(seq) > 32
                    or any(not isinstance(a, str) or a not in actions for a in seq)
                    for seq in seqs)):
                return False
        if len(model["access"]) != len(outputs):
            return False
        for state, access in enumerate(model["access"]):
            reached = model["initial"]
            for action in access:
                reached = transitions[reached][action]
            if reached != state:
                return False
        return True
    except (TypeError, ValueError, UnicodeError):
        return False


def _valid_output(value):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_OUTPUT_CHARS
            and len(value.encode("utf-8")) <= MAX_OUTPUT_BYTES)


def predict_trace(model, word):
    """Return Moore outputs from reset through every action in ``word``."""
    if not validate_model(model):
        raise ValueError("invalid Moore model")
    actions = set(model["actions"])
    w = _word(word, actions)
    state = model["initial"]
    outputs = [model["outputs"][state]]
    for action in w:
        state = model["transitions"][state][action]
        outputs.append(model["outputs"][state])
    return outputs


def plan(model, goal_output):
    """Find a shortest action word reaching a state with ``goal_output``.

    The goal label is provisional and the plan is only relative to this learned
    finite table; no completeness or authorization claim is made here.
    """
    if not validate_model(model):
        raise ValueError("invalid Moore model")
    if not _valid_output(goal_output):
        raise ValueError("goal_output must be a bounded nonempty string")
    initial = model["initial"]
    queue = deque([initial])
    parent = {initial: None}
    while queue:
        state = queue.popleft()
        if model["outputs"][state] == goal_output:
            actions = []
            while parent[state] is not None:
                previous, action = parent[state]
                actions.append(action)
                state = previous
            return {"status": "plan", "actions": list(reversed(actions)),
                    "interpretation": "shortest path in the learned finite table only"}
        for action in model["actions"]:
            target = model["transitions"][state][action]
            if target not in parent:
                parent[target] = (state, action)
                queue.append(target)
    return {"status": "no_path", "scope": "learned finite table only"}


class ActiveNavigation:
    """Learn a bounded Moore abstraction from reset/action/output traces.

    ``reset`` and ``step`` are trusted host callbacks. A successful observation
    is an exact nonempty string. Unknown actions cannot be executed by this
    class; extending the action vocabulary requires constructing a new learner.
    """

    def __init__(self, memory, journal, reset, step, *, actions, context,
                 max_queries=4096, max_actions=50000, max_word_length=32, max_states=128):
        if (reset is not None and not callable(reset)) or not callable(step):
            raise ValueError("step callback and optional reset callback are required")
        if (not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTION_NAMES
                or any(not isinstance(a, str) or not a or len(a) > MAX_ACTION_NAME_CHARS for a in actions)
                or len(set(actions)) != len(actions)):
            raise ValueError("actions must be a unique bounded list of nonempty names")
        if not isinstance(context, str) or not context or len(context) > 1024:
            raise ValueError("context must be a bounded nonempty string")
        limits = ((max_queries, 1, 4096), (max_actions, 0, 50000),
                  (max_word_length, 1, 32), (max_states, 1, 128))
        if any(type(value) is not int or not low <= value <= high for value, low, high in limits):
            raise ValueError("navigation bounds outside the supported contract")
        self.memory, self.journal, self.reset_callback, self.step_callback = memory, journal, reset, step
        self.actions = tuple(actions)
        self.action_set = set(actions)
        self.context = context
        self.max_queries, self.max_actions = max_queries, max_actions
        self.max_word_length, self.max_states = max_word_length, max_states
        self.query_attempts = self.cache_hits = self.table_cells = 0
        self.action_attempts = self.reset_attempts = 0
        self.schedule_work = 0
        self._probe_stream = None
        self._table_cell_cache = {}
        self.counterexamples = 0
        self.historical_counterexamples = 0
        self._prefix_cache = {}
        self._prefix_evidence = {}
        self._traces = []
        self._models = []
        self._active_artifact = None
        self._table_s, self._table_e = {()}, {()}
        self._last_model = None
        self.status = "ready"
        self.run_id = uuid.uuid4().hex
        self.scope = "active-navigation:" + digest({"context": context, "run_id": self.run_id})
        self.source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.start_evidence = journal.append("active_navigation_start", {
            "format": FORMAT, "actions": list(self.actions), "context": context,
            "run_id": self.run_id, "scope": self.scope, "limits": self.limits(),
            "source_sha256": self.source_sha256,
            "assumptions": ["trusted reset and step callbacks", "deterministic stationary behavior is a testable hypothesis",
                            "only declared actions are available", "outputs are exact strings"],
            "claim_boundary": "bounded tested traces only; no complete-environment or user-intent claim"})

    def limits(self):
        return {"max_queries": self.max_queries, "max_actions": self.max_actions,
                "max_word_length": self.max_word_length, "max_states": self.max_states,
                "max_suffixes": MAX_SUFFIXES,
                "max_table_cells": MAX_TABLE_CELLS,
                "max_actions_in_alphabet": MAX_ACTION_NAMES,
                "max_action_name_chars": MAX_ACTION_NAME_CHARS,
                "max_output_chars": MAX_OUTPUT_CHARS, "max_output_bytes": MAX_OUTPUT_BYTES}

    def _invalidate_models(self, evidence, reason):
        for aid in self._models:
            try:
                if self.memory.status(aid, self.scope) != "invalidated":
                    self.memory.invalidate(aid, reason, [evidence], scope=self.scope)
            except (KeyError, ValueError):
                pass
        self._active_artifact = None

    def _model_body(self, model):
        if isinstance(model, str):
            if model not in self._models:
                raise ValueError("model artifact was not created by this learner")
            payload = self.memory.get(model)
            if (payload.get("kind") != "representation"
                    or payload.get("applicability") != {"context": self.context, "scope": self.scope}):
                raise ValueError("model artifact context mismatch")
            body = payload.get("body")
        else:
            body = model
        if not validate_model(body):
            raise ValueError("invalid Moore model")
        if body["actions"] != list(self.actions):
            raise ValueError("model action vocabulary mismatch")
        return body

    def query(self, word, fresh=False, model=None):
        """Query one reset history; fresh mode checks stability and can challenge a model.

        Returns reset output followed by each completed step output. Cached
        answers come only from actually observed reset/action prefixes, including
        prefixes completed before a later callback failed.
        """
        w = _word(word, self.action_set, max_length=self.max_word_length)
        if type(fresh) is not bool:
            raise ValueError("fresh must be a literal bool")
        if model is not None and not fresh:
            raise ValueError("model challenges must be fresh")
        if self.status == "nonstationary":
            return {"status": "nonstationary", "requires": "new explicitly versioned context"}
        model_id = model if isinstance(model, str) else None
        expected = None
        if model is not None:
            expected = predict_trace(self._model_body(model), w)
        if not fresh and w in self._prefix_cache:
            self.cache_hits += 1
            trace = list(self._prefix_cache[w])
            eid = self.journal.append("active_navigation_query_cached", {
                "word": list(w), "outputs": trace,
                "source_evidence": self._prefix_evidence.get(w), "cache_hit": self.cache_hits})
            return self._query_outcome("cached", w, trace, eid, expected, model_id)
        if self.query_attempts >= self.max_queries:
            self.journal.append("active_navigation_query_blocked", {
                "word": list(w), "reason": "query budget exhausted", "queries": self.query_attempts})
            return {"status": "budget_exhausted", "word": list(w)}
        self.query_attempts += 1
        challenge_ref = None
        if model is not None:
            challenge_ref = self.journal.append("active_navigation_challenge", {
                "model": model_id, "word": list(w), "expected": expected,
                "query_number": self.query_attempts,
                "interpretation": "committed conditional prediction before fresh execution"})
        attempt = self.journal.append("active_navigation_query_attempt", {
            "word": list(w), "fresh": fresh, "number": self.query_attempts,
            "challenge": challenge_ref})
        if self.reset_attempts >= self.max_queries:
            eid = self.journal.append("active_navigation_query_result", {
                "attempt": attempt, "status": "budget_exhausted", "phase": "reset_budget",
                "word": list(w)})
            return {"status": "budget_exhausted", "word": list(w), "evidence": eid}
        self.reset_attempts += 1
        reset_event = self.journal.append("active_navigation_reset_attempt", {
            "query": attempt, "number": self.reset_attempts})
        try:
            initial = None if self.reset_callback is None else self.reset_callback()
        except Exception as error:
            eid = self.journal.append("active_navigation_query_result", {
                "attempt": attempt, "reset_attempt": reset_event, "status": "observer_error",
                "phase": "reset", "error_type": type(error).__name__})
            return {"status": "observer_error", "error_type": type(error).__name__, "evidence": eid}
        if initial is None:
            eid = self.journal.append("active_navigation_query_result", {
                "attempt": attempt, "reset_attempt": reset_event, "status": "reset_unavailable"})
            return {"status": "reset_unavailable", "evidence": eid}
        try:
            trace = [_output(initial)]
        except (ValueError, UnicodeError) as error:
            eid = self.journal.append("active_navigation_query_result", {
                "attempt": attempt, "reset_attempt": reset_event, "status": "observer_error",
                "phase": "reset_output", "error_type": type(error).__name__})
            return {"status": "observer_error", "error_type": type(error).__name__, "evidence": eid}

        # Persist and compare each prefix as soon as its output arrives. Besides
        # preserving evidence at action boundaries, this lets
        # a fresh observation stop on drift before any later action is issued.
        observation = self._record_observation(attempt, (), trace, "reset")
        conflict = self._prefix_conflict((), trace)
        if conflict:
            return self._nonstationary(attempt, w, (), trace, observation, challenge_ref)
        self._cache_prefix((), trace, observation)
        if expected is not None and trace != expected[:1]:
            return self._query_outcome("observed", (), trace, observation, expected[:1], model_id)

        for index, action in enumerate(w):
            if self.action_attempts >= self.max_actions:
                eid = self.journal.append("active_navigation_query_result", {
                    "attempt": attempt, "status": "budget_exhausted", "phase": "step_budget",
                    "completed_outputs": trace, "completed_actions": list(w[:index])})
                return {"status": "budget_exhausted", "evidence": eid,
                        "completed_outputs": trace, "completed_actions": list(w[:index])}
            self.action_attempts += 1
            action_event = self.journal.append("active_navigation_step_attempt", {
                "query": attempt, "index": index, "action": action,
                "number": self.action_attempts})
            try:
                value = self.step_callback(action)
                trace.append(_output(value))
            except Exception as error:
                eid = self.journal.append("active_navigation_query_result", {
                    "attempt": attempt, "action_attempt": action_event, "status": "observer_error",
                    "phase": "step", "error_type": type(error).__name__,
                    "completed_outputs": trace, "completed_actions": list(w[:index])})
                return {"status": "observer_error", "error_type": type(error).__name__,
                        "evidence": eid, "completed_outputs": trace}

            prefix = w[:index+1]
            observation = self._record_observation(attempt, prefix, trace, "step", action_event)
            conflict = self._prefix_conflict(prefix, trace)
            if conflict:
                return self._nonstationary(attempt, w, prefix, trace, observation, challenge_ref)
            self._cache_prefix(prefix, trace, observation)
            if expected is not None and trace != expected[:len(trace)]:
                return self._query_outcome("observed", prefix, trace, observation,
                                           expected[:len(trace)], model_id)

        eid = self.journal.append("active_navigation_query_result", {
            "attempt": attempt, "status": "observed", "word": list(w), "outputs": trace,
            "fresh": fresh, "challenge": challenge_ref})
        self._traces.append({"word": w, "outputs": tuple(trace), "evidence": eid, "fresh": fresh})
        return self._query_outcome("observed", w, trace, eid, expected, model_id)

    def _record_observation(self, attempt, prefix, trace, phase, action_attempt=None):
        return self.journal.append("active_navigation_observation", {
            "query": attempt, "phase": phase, "action_attempt": action_attempt,
            "prefix": list(prefix), "outputs": list(trace),
            "interpretation": "host callback observation at this reset/action boundary"})

    def _prefix_conflict(self, prefix, trace):
        previous = self._prefix_cache.get(prefix)
        return previous is not None and list(previous) != list(trace)

    def _cache_prefix(self, prefix, trace, evidence):
        self._prefix_cache.setdefault(prefix, tuple(trace))
        self._prefix_evidence.setdefault(prefix, evidence)

    def _nonstationary(self, attempt, word, prefix, trace, observation, challenge_ref):
        observed = list(trace)
        previous = list(self._prefix_cache[prefix])
        result_evidence = self.journal.append("active_navigation_query_result", {
            "attempt": attempt, "status": "nonstationary", "word": list(word),
            "conflicting_prefix": list(prefix), "previous": previous,
            "fresh": observed, "observation": observation, "challenge": challenge_ref})
        self.journal.append("active_navigation_nonstationarity", {
            "word": list(word), "conflicting_prefix": list(prefix),
            "previous": previous, "fresh": observed,
            "evidence": result_evidence,
            "interpretation": "repeatable behavior changed; no model truth claim"})
        self._invalidate_models(result_evidence, "fresh repeat contradicted cached reset history")
        self.status = "nonstationary"
        return {"status": "nonstationary", "word": list(word),
                "outputs": observed, "conflicting_prefix": list(prefix),
                "evidence": result_evidence}

    def _query_outcome(self, base_status, word, trace, evidence, expected, model_id):
        if expected is None:
            return {"status": base_status, "word": list(word), "outputs": list(trace), "evidence": evidence}
        if list(trace) == expected:
            return {"status": "challenge_match", "word": list(word), "outputs": list(trace),
                    "expected": expected, "evidence": evidence, "model": model_id}
        self.counterexamples += 1
        self.journal.append("active_navigation_counterexample", {
            "model": model_id, "word": list(word), "expected": expected, "observed": list(trace),
            "evidence": evidence, "interpretation": "finite counterexample; refine the hypothesis"})
        if model_id is not None:
            self._invalidate_artifact(model_id, evidence, "fresh conformance word contradicted model")
        return {"status": "counterexample", "word": list(word), "outputs": list(trace),
                "expected": expected, "evidence": evidence, "model": model_id}

    def _invalidate_artifact(self, aid, evidence, reason):
        try:
            if self.memory.status(aid, self.scope) != "invalidated":
                self.memory.invalidate(aid, reason, [evidence], scope=self.scope)
        except (KeyError, ValueError):
            pass
        if aid == self._active_artifact:
            self._active_artifact = None

    def _table_row(self, prefix):
        values = []
        for suffix in sorted(self._table_e, key=lambda x: (len(x), x)):
            word = prefix + suffix
            if len(word) > self.max_word_length:
                raise _LearnStop({"status": "word_budget_exhausted"})
            key = (prefix, suffix)
            if key in self._table_cell_cache:
                values.append(self._table_cell_cache[key])
                continue
            if self.table_cells >= MAX_TABLE_CELLS:
                raise _LearnStop({"status": "table_budget_exhausted"})
            self.table_cells += 1
            if word in self._prefix_cache:
                trace = self._prefix_cache[word]
            else:
                result = self.query(word)
                if result["status"] in {"budget_exhausted", "observer_error", "reset_unavailable", "nonstationary"}:
                    raise _LearnStop(result)
                trace = result["outputs"]
            cell = tuple(trace[len(prefix):])
            self._table_cell_cache[key] = cell
            values.append(cell)
        return tuple(values)

    def _refine_counterexample(self, word):
        for prefix in _prefixes(word):
            if len(prefix) <= self.max_word_length:
                self._table_s.add(prefix)
        self._table_cell_cache.clear()
        # The complete suffix closure gives the table a finite distinguishing
        # vocabulary grounded in the observed counterexample.
        for start in range(len(word)+1):
            self._table_e.add(tuple(word[start:]))

    def _counterexample_target_endpoint(self, word, *, phase):
        if len(word) > self.max_word_length:
            raise _LearnStop({"status": "word_budget_exhausted", "phase": phase,
                              "word_length": len(word), "limit": self.max_word_length})
        result = self.query(word)
        if result.get("status") in {"budget_exhausted", "observer_error", "reset_unavailable",
                                    "nonstationary"}:
            raise _LearnStop({**result, "phase": phase})
        if result.get("status") not in {"observed", "cached"}:
            raise _LearnStop({"status": "counterexample_refinement_error", "phase": phase,
                              "query_status": result.get("status")})
        return result["outputs"][-1], result.get("evidence")

    def _refine_counterexample_suffix(self, source_word, model, model_id, *,
                                      observed_word=None, observed_trace=None):
        """Use an adjacent target/model boundary to add one diagnostic suffix.

        This is an engineered Rivest–Schapire-style counterexample transform.
        It queries only through ``self.query``; these cached or new membership
        observations receive no fresh challenge credit.
        """
        source_word = tuple(source_word)
        query_start = self.query_attempts
        action_start = self.action_attempts
        reset_start = self.reset_attempts
        cache_start = self.cache_hits
        target_word = source_word if observed_word is None else tuple(observed_word)
        observed = (self._prefix_cache.get(target_word) if observed_trace is None
                    else tuple(observed_trace))
        if observed is None:
            result = self.query(target_word)
            if result.get("status") not in {"observed", "cached"}:
                raise _LearnStop({**result, "phase": "counterexample_source"})
            observed = tuple(result["outputs"])
        predicted = predict_trace(model, target_word)
        mismatch = next((i for i, (a, b) in enumerate(zip(predicted, observed)) if a != b), None)
        if mismatch is None:
            raise _LearnStop({"status": "counterexample_not_reproduced",
                              "source_word": list(source_word)})
        if mismatch == 0:
            raise _LearnStop({"status": "suffix_refinement_unavailable",
                              "reason": "reset output differs before any action"})
        word = target_word[:mismatch]
        length = len(word)
        states = [model["initial"]]
        for action in word:
            states.append(model["transitions"][states[-1]][action])
        access = model["access"]
        evidence = []

        def target_value(index, phase):
            state = states[index]
            query_word = tuple(access[state]) + word[index:]
            value, receipt = self._counterexample_target_endpoint(query_word, phase=phase)
            if receipt is not None:
                evidence.append(receipt)
            return value

        left, right = 0, length
        left_value = target_value(left, "counterexample_boundary_left")
        right_value = target_value(right, "counterexample_boundary_right")
        if left_value == right_value:
            raise _LearnStop({"status": "counterexample_not_bracketed",
                              "source_word": list(source_word), "trimmed_word": list(word),
                              "endpoint_values": [left_value, right_value]})
        while right - left > 1:
            middle = (left + right) // 2
            middle_value = target_value(middle, "counterexample_boundary_search")
            if middle_value == left_value:
                left = middle
            else:
                right, right_value = middle, middle_value

        suffix = tuple(word[left+1:])
        if suffix in self._table_e:
            raise _LearnStop({"status": "suffix_refinement_stalled",
                              "source_word": list(source_word), "trimmed_word": list(word),
                              "boundary": [left, right], "suffix": list(suffix),
                              "reason": "counterexample produced an already-present suffix"})
        added = [suffix[index:] for index in range(len(suffix)+1)]
        self._table_e.update(added)
        self._table_cell_cache.clear()
        self.journal.append("active_navigation_counterexample_refinement", {
            "artifact": model_id, "strategy": "suffix",
            "method": "Rivest-Schapire adjacent-boundary suffix refinement",
            "source_word": list(source_word), "trimmed_word": list(word),
            "boundary": [left, right], "suffix": list(suffix),
            "boundary_values": [left_value, right_value],
            "added_suffix_closure": [list(item) for item in added],
            "query_evidence": evidence,
            "query_attempts": self.query_attempts-query_start,
            "cache_hits": self.cache_hits-cache_start,
            "reset_attempts": self.reset_attempts-reset_start,
            "action_attempts": self.action_attempts-action_start,
            "transformation_attribution": "engineered implementation of a known automata-learning method; finite observations only"})

    def _learn_table(self):
        while True:
            if len(self._table_e) > MAX_SUFFIXES:
                return None, {"status": "suffix_budget_exhausted"}
            self._table_cell_cache.clear()
            rows = {s: self._table_row(s) for s in sorted(self._table_s, key=lambda x: (len(x), x))}
            if len(set(rows.values())) > self.max_states:
                return None, {"status": "state_budget_exhausted"}
            row_to_s = {row: s for s, row in rows.items()}
            expanded = False
            for s in sorted(self._table_s, key=lambda x: (len(x), x)):
                for action in self.actions:
                    sa = s + (action,)
                    if len(sa) > self.max_word_length:
                        return None, {"status": "word_budget_exhausted"}
                    row = self._table_row(sa)
                    if row not in row_to_s:
                        self._table_s.add(sa)
                        expanded = True
                        break
                if expanded:
                    break
            if expanded:
                continue

            # Consistency: equal rows must have equal successor rows. Add a
            # distinguishing suffix a·e whenever the table separates them.
            states = sorted(self._table_s, key=lambda x: (len(x), x))
            inconsistent_suffix = None
            for i, left in enumerate(states):
                for right in states[i+1:]:
                    if rows[left] != rows[right]:
                        continue
                    for action in self.actions:
                        lrow, rrow = self._table_row(left+(action,)), self._table_row(right+(action,))
                        if lrow == rrow:
                            continue
                        differing = next((idx for idx, pair in enumerate(zip(lrow, rrow))
                                          if pair[0] != pair[1]), None)
                        if differing is None:
                            continue
                        suffix = sorted(self._table_e, key=lambda x: (len(x), x))[differing]
                        inconsistent_suffix = (action,) + suffix
                        break
                    if inconsistent_suffix is not None:
                        break
                if inconsistent_suffix is not None:
                    break
            if inconsistent_suffix is not None:
                if len(inconsistent_suffix) > self.max_word_length:
                    return None, {"status": "word_budget_exhausted"}
                for start in range(len(inconsistent_suffix)+1):
                    self._table_e.add(inconsistent_suffix[start:])
                continue
            return self._build_model(rows), None

    def _build_model(self, rows):
        unique_rows = sorted(set(rows.values()), key=canonical)
        row_id = {row: i for i, row in enumerate(unique_rows)}
        outputs, transitions, access = [], [], []
        suffixes = sorted(self._table_e, key=lambda x: (len(x), x))
        empty_index = suffixes.index(())
        for row in unique_rows:
            representatives = [prefix for prefix, candidate in rows.items() if candidate == row]
            prefix = min(representatives, key=lambda x: (len(x), x))
            outputs.append(row[empty_index][0])
            access.append(list(prefix))
            edge_row = {}
            for action in self.actions:
                successor = self._table_row(prefix+(action,))
                if successor not in row_id:
                    raise AssertionError("closed table lacks a successor state")
                edge_row[action] = row_id[successor]
            transitions.append(edge_row)
        initial = row_id[rows[()]]
        model = {"format": FORMAT, "actions": list(self.actions), "initial": initial,
                 "outputs": outputs, "transitions": transitions, "access": access,
                 "suffixes": [list(word) for word in suffixes]}
        if not validate_model(model):
            raise AssertionError("constructed Moore model failed structural validation")
        return model

    def _persist_model(self, model, iteration):
        payload = {"kind": "representation", "body": model, "dependencies": [],
                   "provenance": {"learner": "bounded L*-style Moore observation table",
                                  "source_sha256": self.source_sha256,
                                  "iteration": iteration,
                                  "membership_queries": self.query_attempts},
                   "applicability": {"context": self.context, "scope": self.scope}}
        aid = self.memory.put(payload)
        self._models.append(aid)
        self._active_artifact = aid
        self.journal.append("active_navigation_model_proposed", {
            "artifact": aid, "iteration": iteration, "states": len(model["outputs"]),
            "queries": self.query_attempts, "access": model["access"], "suffixes": model["suffixes"],
            "claim": "table-consistent candidate; not yet challenged"})
        return aid

    def _challenge_words(self, depth):
        yield ()
        for length in range(1, depth+1):
            for word in itertools.product(self.actions, repeat=length):
                yield word

    def learn(self, challenge_depth=5, *, strategy='shortlex', extra_depth=1,
              max_schedule_work=1000000, sampling=None,
              counterexample_strategy='prefixes', hybrid_compact=False):
        """Fit, persist, and freshly challenge a declared finite test schedule.

        Shortlex remains the default. The opt-in model-derived strategy tests
        access/transition words followed by constructed distinguishing suffixes.
        Distribution probes can test longer histories outside the candidate's
        state distinctions. Their seeded runs are not statistical certificates.
        Counterexample refinement defaults to all prefixes; the opt-in suffix
        strategy applies a bounded adjacent-boundary automata-learning method.
        No schedule proves equivalence to an unbounded unknown world.
        """
        if type(challenge_depth) is not int or not 0 <= challenge_depth <= self.max_word_length:
            raise ValueError("challenge_depth must be an integer within max_word_length")
        if strategy not in ('shortlex', 'model_derived', 'adaptive', 'distribution', 'hybrid'):
            raise ValueError('unknown challenge strategy')
        if counterexample_strategy not in ('prefixes', 'suffix'):
            raise ValueError('unknown counterexample refinement strategy')
        if type(hybrid_compact) is not bool:
            raise ValueError('hybrid_compact must be a literal bool')
        if hybrid_compact and strategy != 'hybrid':
            raise ValueError('hybrid_compact is supported only by hybrid strategy')
        if counterexample_strategy == 'suffix':
            self.journal.append('active_navigation_counterexample_strategy', {
                'strategy': 'suffix',
                'method': 'Rivest-Schapire adjacent-boundary suffix refinement',
                'attribution': 'engineered implementation of a known automata-learning method',
                'interpretation': 'finite counterexample refinement; not a general discovery claim'})
        if type(extra_depth) is not int or not 0 <= extra_depth <= 3:
            raise ValueError('extra_depth must be an integer in 0..3')
        if type(max_schedule_work) is not int or not 1 <= max_schedule_work <= 1000000:
            raise ValueError('schedule work ceiling must be an integer in 1..1000000')
        if strategy in ('distribution', 'hybrid'):
            from .distribution_probes import ProbeStream
            if sampling is not None and (type(sampling) is not dict or set(sampling) - {
                    'seed', 'max_length', 'persistence', 'epsilon', 'delta'}):
                raise ValueError('invalid host sampling configuration')
            config = {'seed': 0, 'max_length': min(16, self.max_word_length), **(sampling or {})}
            candidate_stream = ProbeStream(self.actions, **config)
            if candidate_stream.contract['max_length'] > self.max_word_length:
                raise ValueError('probe length exceeds learner word ceiling')
            if self._probe_stream is None:
                self._probe_stream = candidate_stream
            elif self._probe_stream.contract != candidate_stream.contract:
                raise ValueError('sampling contract cannot change within one learner')
        elif sampling is not None:
            raise ValueError('sampling configuration requires distribution or hybrid strategy')
        if self.status == "nonstationary":
            return self._stopped("nonstationary", {"requires": "new explicitly versioned context"})
        iteration = 0
        while True:
            iteration += 1
            try:
                model, stop = self._learn_table()
            except _LearnStop as stopped:
                result = self._stopped(stopped.outcome["status"], stopped.outcome)
                return result
            if stop:
                return self._stopped(stop["status"], stop)
            aid = self._persist_model(model, iteration)
            # Membership queries or an earlier, deeper challenge can contain
            # distinctions absent from S/E. Never qualify a candidate already
            # contradicted by any retained observation. This is retrospective
            # consistency, and earns no fresh challenge credit.
            contradiction = next((word for word in sorted(self._prefix_cache, key=lambda w: (len(w), w))
                if predict_trace(model, word) != list(self._prefix_cache[word])), None)
            if contradiction is not None:
                self.historical_counterexamples += 1
                receipt = self.journal.append('active_navigation_history_counterexample', {
                    'artifact': aid, 'word': list(contradiction),
                    'expected': predict_trace(model, contradiction),
                    'observed': list(self._prefix_cache[contradiction]),
                    'observation_evidence': self._prefix_evidence[contradiction],
                    'fresh_validation_credit': False})
                self._invalidate_artifact(aid, receipt, 'contradicted by retained observations')
                try:
                    if counterexample_strategy == 'suffix':
                        self._refine_counterexample_suffix(contradiction, model, aid)
                    else:
                        self._refine_counterexample(contradiction)
                except _LearnStop as stopped:
                    return self._stopped(stopped.outcome['status'], stopped.outcome)
                continue
            built, selected_strategy, selection = None, strategy, None
            if strategy == 'adaptive':
                # This is a heuristic about the current hypothesis, not evidence
                # that observation labels identify every latent world state.
                selected_strategy = 'shortlex'
                baseline_cost = sum((i+1)*len(self.actions)**i for i in range(challenge_depth+1))
                selection = {'model': aid, 'requested_strategy': strategy,
                    'selected_strategy': selected_strategy, 'shortlex_cost': baseline_cost,
                    'candidate_cost': None, 'root_guard_depth': min(2, challenge_depth),
                    'reason': 'candidate outputs do not distinguish at least two states',
                    'words': None,
                    'assumption': 'distinct candidate labels may still hide unmodeled aliases'}
                if len(model['outputs']) > 1 and len(set(model['outputs'])) == len(model['outputs']):
                    from .model_experiments import construct_experiments
                    remaining_work = max(0, max_schedule_work-self.schedule_work)
                    built = construct_experiments(model, extra_depth=0,
                        max_tests=self.max_queries, max_word_length=self.max_word_length,
                        max_work=remaining_work)
                    self.schedule_work += built['work']
                    self.journal.append('active_navigation_schedule_construction',
                        {'model': aid, 'construction': built})
                    selection['reason'] = 'optional construction incomplete; use shortlex'
                    if built['status'] == 'constructed':
                        union = {tuple(w) for w in built['words']}
                        union.update(self._challenge_words(min(2, challenge_depth)))
                        rank = {a: i for i, a in enumerate(self.actions)}
                        candidates = sorted(union, key=lambda w: (len(w), tuple(rank[a] for a in w)))
                        projected = sum(1+len(w) for w in candidates)
                        selection['candidate_cost'] = projected
                        selection['reason'] = 'derived schedule is not cheaper within test ceiling'
                        if projected < baseline_cost and len(candidates) <= self.max_queries:
                            selected_strategy = 'model_derived'
                            selection.update({'selected_strategy': selected_strategy,
                                'reason': 'distinct candidate labels and lower projected reset/action cost',
                                'words': [list(w) for w in candidates]})
                            # Preserve the original construction hash; bind the
                            # guard-augmented schedule separately below.
                            built = {**built, 'construction_schedule_hash': built['schedule_hash'],
                                     'words': selection['words']}
            if selected_strategy == 'hybrid':
                from .model_experiments import construct_experiments
                remaining_work = max(0, max_schedule_work-self.schedule_work)
                derived = construct_experiments(model, extra_depth=0,
                    max_tests=max(0, self.max_queries-self.query_attempts),
                    max_word_length=self.max_word_length, max_work=remaining_work,
                    include_outcome_paths=True, compact_prefixes=hybrid_compact)
                self.schedule_work += derived['work']
                self.journal.append('active_navigation_hybrid_construction', {
                    'model': aid, 'component': 'model_derived', 'construction': derived})
                if derived['status'] not in ('constructed', 'equivalent_states'):
                    return self._stopped('schedule_incomplete', {
                        'phase': 'model_derived_component', 'construction': derived})
                deterministic_words = derived['words']
                remaining_tests = max(0, self.max_queries-self.query_attempts-len(deterministic_words))
                remaining_work = max(0, max_schedule_work-self.schedule_work)
                sampled = self._probe_stream.construct(model,
                    max_tests=remaining_tests, max_work=remaining_work)
                self.schedule_work += sampled['work']
                self.journal.append('active_navigation_hybrid_construction', {
                    'model': aid, 'component': 'sampled', 'construction': sampled})
                if sampled['status'] != 'constructed':
                    return self._stopped('schedule_incomplete', {
                        'phase': 'sampled_component', 'construction': sampled})
                sampled_words = sampled['words']
                # Preserve the component schedules in order and retain every
                # sampled draw, including words already tested deterministically.
                words = [*deterministic_words, *sampled_words]
                component_hashes = {
                    'model_derived': derived['schedule_hash'],
                    'sampled': sampled['schedule_hash']}
                model_digest = digest(model)
                coverage_certificate_digest = digest(derived.get('coverage_certificate', []))
                combined_payload = {'format': 'active-navigation-hybrid-schedule-v1',
                    'model': aid, 'model_digest': model_digest,
                    'component_hashes': component_hashes,
                    'word_order': 'model_derived_then_all_sampled_draws_no_deduplication',
                    'compact_prefixes': hybrid_compact,
                    'original_deterministic_words': derived.get('original_word_count', len(deterministic_words)),
                    'coverage_certificate_digest': coverage_certificate_digest,
                    'deterministic_words': len(deterministic_words),
                    'sampled_words': len(sampled_words), 'words': words,
                    'controller_source_sha256': self.source_sha256}
                description = {'strategy': 'hybrid', 'selected_strategy': 'hybrid',
                    'model': aid, 'model_digest': model_digest,
                    'component_hashes': component_hashes,
                    'word_order': combined_payload['word_order'],
                    'compact_prefixes': hybrid_compact,
                    'original_deterministic_words': combined_payload['original_deterministic_words'],
                    'coverage_certificate_digest': coverage_certificate_digest,
                    'deterministic_words': len(deterministic_words),
                    'sampled_words': len(sampled_words), 'expected_words': len(words),
                    'schedule': 'complete model-derived component followed by every seeded sampled draw',
                    'sampling_contract': sampled['contract'],
                    'sampling_round': sampled['round'],
                    'sampling_work': sampled['work'],
                    'statistical_certificate': False,
                    'interpretation': 'finite combined test only; any IID risk statement applies only conditionally to the ideal independent sampled component; fixed PRNG is not certified IID'}
                description['schedule_hash'] = digest(combined_payload)
            elif selected_strategy == 'distribution':
                built = self._probe_stream.construct(model,
                    max_tests=max(0, self.max_queries-self.query_attempts),
                    max_work=max(0, max_schedule_work-self.schedule_work))
                self.schedule_work += built['work']
                self.journal.append('active_navigation_distribution_construction',
                    {'model': aid, 'construction': built})
                if built['status'] != 'constructed':
                    return self._stopped('schedule_incomplete', built)
                words = built['words']
                description = {key: value for key, value in built.items() if key != 'words'}
                description.update({'strategy': strategy, 'model': aid,
                    'schedule': 'seeded reset-word distribution; full-trace comparison'})
            elif selected_strategy == 'shortlex':
                description = {'strategy': strategy, 'depth': challenge_depth,
                    'schedule': 'all reset-based action words in shortlex order through depth',
                    'expected_words': sum(len(self.actions)**i for i in range(challenge_depth+1)),
                    'model': aid, 'actions': list(self.actions)}
                words = self._challenge_words(challenge_depth)
            else:
                from .model_experiments import construct_experiments
                if built is None:
                    remaining_work = max_schedule_work - self.schedule_work
                    if remaining_work <= 0:
                        return self._stopped('schedule_incomplete', {'reason': 'cumulative schedule work exhausted'})
                    built = construct_experiments(model, extra_depth=extra_depth,
                        max_tests=self.max_queries, max_word_length=self.max_word_length,
                        max_work=remaining_work)
                    self.schedule_work += built['work']
                    self.journal.append('active_navigation_schedule_construction',
                        {'model': aid, 'construction': built})
                if built['status'] != 'constructed':
                    return self._stopped('schedule_incomplete', built)
                words = built['words']
                description = {key: value for key, value in built.items() if key != 'words'}
                description.update({'strategy': strategy, 'model': aid,
                    'expected_words': len(words), 'extra_depth': 0 if strategy == 'adaptive' else extra_depth,
                    'schedule': 'candidate access/transition cover, finite stress words, distinguishing suffixes'})
            description['selected_strategy'] = selected_strategy
            if selection is not None:
                description['selection'] = {k: v for k, v in selection.items() if k != 'words'}
                prior_hash = description.pop('schedule_hash', None)
                if prior_hash is not None:
                    description.setdefault('construction_schedule_hash', prior_hash)
                description['schedule_hash'] = digest({'description': description,
                    'controller_source_sha256': self.source_sha256, 'words': selection['words']})
                selection['schedule_hash'] = description['schedule_hash']
                self.journal.append('active_navigation_schedule_selection', selection)
            elif strategy == 'shortlex':
                description['schedule_hash'] = digest(description)
            schedule_evidence = self.journal.append('active_navigation_schedule', description)
            evidence = []
            for word in words:
                outcome = self.query(word, fresh=True, model=aid)
                if outcome["status"] == "challenge_match":
                    evidence.append(outcome["evidence"])
                    continue
                if outcome["status"] == "counterexample":
                    try:
                        if counterexample_strategy == 'suffix':
                            self._refine_counterexample_suffix(tuple(word), model, aid,
                                observed_word=tuple(outcome['word']),
                                observed_trace=tuple(outcome['outputs']))
                        else:
                            self._refine_counterexample(tuple(outcome['word']))
                    except _LearnStop as stopped:
                        return self._stopped(stopped.outcome['status'], stopped.outcome)
                    break
                if outcome["status"] == "nonstationary":
                    return self._stopped("nonstationary", outcome)
                return self._stopped(outcome["status"], outcome)
            else:
                # Every fresh word in the finite schedule passed. This scope is
                # an attestation of that schedule only.
                receipt = self.journal.append('active_navigation_schedule_completed', {
                    'model': aid, 'schedule_evidence': schedule_evidence,
                    'schedule_hash': description['schedule_hash'],
                    'words_completed': len(evidence), 'strategy': strategy,
                    'claim': 'fresh agreement on this finite schedule only'})
                self.memory.qualify(aid, self.scope, 'finite-navigation-schedule:'+strategy,
                                    [receipt]+evidence)
                self._active_artifact = aid
                self.status = "conditional_tested"
                return {"status": self.status, "artifact": aid, "scope": self.scope,
                        "states": len(model["outputs"]), "queries": self.query_attempts,
                        "cache_hits": self.cache_hits, "table_cells": self.table_cells,
                        "action_attempts": self.action_attempts,
                        "reset_attempts": self.reset_attempts,
                        "schedule_work": self.schedule_work,
                        "counterexamples": self.counterexamples,
                        "historical_counterexamples": self.historical_counterexamples,
                        "challenge": {**description, 'words_completed': len(evidence)},
                        "interpretation": "conditional agreement on the finite challenge schedule; no complete-environment claim"}
            if self.query_attempts >= self.max_queries or self.action_attempts >= self.max_actions:
                return self._stopped("budget_exhausted", {"phase": "refinement_after_counterexample"})

    def _stopped(self, status, detail=None):
        self.status = status
        body = {"status": status, "detail": detail or {}, "queries": self.query_attempts,
                "cache_hits": self.cache_hits, "table_cells": self.table_cells,
                "action_attempts": self.action_attempts, "counterexamples": self.counterexamples}
        body['reset_attempts'] = self.reset_attempts
        body['schedule_work'] = self.schedule_work
        body['historical_counterexamples'] = self.historical_counterexamples
        self.journal.append("active_navigation_stopped", body)
        return body


class _LearnStop(Exception):
    def __init__(self, outcome):
        self.outcome = outcome
