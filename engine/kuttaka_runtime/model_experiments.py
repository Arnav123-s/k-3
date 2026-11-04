"""Bounded conformance words for a learned finite Moore model.

The generated schedule characterizes the candidate table only. It cannot
establish completeness of an external world or infer a target-state count.
"""

from __future__ import annotations

import hashlib
import itertools
import math
from pathlib import Path

from .store import canonical, digest


MAX_EXTRA_DEPTH = 3
MAX_TESTS = 100_000
MAX_WORD_LENGTH = 32
MAX_WORK = 1_000_000


class _Limit(Exception):
    def __init__(self, reason):
        self.reason = reason


class _Work:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    def tick(self, amount=1, reason="work budget exhausted"):
        if amount < 0 or self.used + amount > self.limit:
            raise _Limit(reason)
        self.used += amount


def _shortlex(words, action_index):
    return sorted(words, key=lambda word: (len(word), tuple(action_index[a] for a in word)))


def _pair(a, b):
    return (a, b) if a < b else (b, a)


def _distinguishing_witnesses(model, work, max_word_length):
    """Find shortest output-distinguishing suffixes for all unordered pairs."""
    outputs, transitions, actions = model["outputs"], model["transitions"], model["actions"]
    count = len(outputs)
    pair_count = count * (count - 1) // 2
    work.tick(pair_count, "pair-analysis work budget exhausted")
    pairs = [(i, j) for i in range(count) for j in range(i+1, count)]
    pair_set = set(pairs)
    # Reverse graph: an edge (p --a--> q) means action a carries state pair p
    # to pair q. Diagonal transitions cannot distinguish their source pair.
    reverse = {item: [] for item in pairs}
    for source in pairs:
        left, right = source
        for action in actions:
            work.tick(reason="transition-pair work budget exhausted")
            target = _pair(transitions[left][action], transitions[right][action])
            if target[0] != target[1] and target in pair_set:
                reverse[target].append((source, action))

    # Distinct current outputs are distinguished by epsilon. Reverse BFS then
    # propagates shortest suffixes without materializing every candidate word.
    distance = {}
    queue = []
    for item in pairs:
        if outputs[item[0]] != outputs[item[1]]:
            distance[item] = 0
            queue.append(item)
    head = 0
    while head < len(queue):
        target = queue[head]
        head += 1
        for source, action in reverse[target]:
            work.tick(reason="witness-search work budget exhausted")
            if source in distance:
                continue
            distance[source] = distance[target] + 1
            queue.append(source)

    equivalent = [item for item in pairs if item not in distance]
    if any(distance[item] > max_word_length for item in distance):
        raise _Limit("shortest distinguishing suffix exceeds max_word_length")
    successor = {}
    for source in pairs:
        if distance.get(source, 0) == 0:
            continue
        left, right = source
        for action in actions:
            work.tick(reason="canonical-witness work budget exhausted")
            target = _pair(transitions[left][action], transitions[right][action])
            if target in distance and distance[target] + 1 == distance[source]:
                successor[source] = (action, target)
                break  # first declared action gives deterministic shortlex tie-breaking
    if len(successor) != sum(1 for item in pairs if distance.get(item, 0) > 0):
        raise AssertionError("distinguishing distance has no decreasing successor")
    witnesses = {}
    for item in pairs:
        if item not in distance:
            continue
        word, current = [], item
        while distance[current]:
            work.tick(reason="witness-materialization work budget exhausted")
            action, current = successor[current]
            word.append(action)
        witnesses[item] = tuple(word)
    return pair_count, witnesses, equivalent


def _select_suffixes(model, witnesses, work, action_index):
    """Greedy deterministic set cover over distinguishable candidate pairs."""
    work.tick(len(witnesses), "witness-set work budget exhausted")
    candidate_set = set(witnesses.values())
    work.tick(len(candidate_set) * max(1, math.ceil(math.log2(max(2, len(candidate_set))))),
              "suffix-sort work budget exhausted")
    candidates = _shortlex(candidate_set, action_index)
    outputs, transitions = model["outputs"], model["transitions"]
    pairs = list(witnesses)
    coverage = {}
    for suffix in candidates:
        final_outputs = []
        for state in range(len(outputs)):
            current = state
            for action in suffix:
                work.tick(reason="suffix-coverage work budget exhausted")
                current = transitions[current][action]
            final_outputs.append(outputs[current])
        covered = set()
        for left, right in pairs:
            work.tick(reason="pair-coverage work budget exhausted")
            if final_outputs[left] != final_outputs[right]:
                covered.add((left, right))
        coverage[suffix] = covered

    universe = set(pairs)
    epsilon = ()
    if epsilon not in coverage:
        # A valid Moore table always has an epsilon witness for differing outputs,
        # but adding epsilon explicitly keeps the contract obvious for one-state
        # and all-equivalent models too.
        coverage[epsilon] = {(i, j) for i, j in pairs if outputs[i] != outputs[j]}
    selected = [epsilon]
    work.tick(len(universe), "initial-cover work budget exhausted")
    uncovered = universe - coverage[epsilon]
    remaining = [candidate for candidate in candidates if candidate]
    while uncovered:
        best, best_gain = None, 0
        for suffix in remaining:
            gain = 0
            for item in uncovered:
                work.tick(reason="greedy-cover work budget exhausted")
                if item in coverage[suffix]:
                    gain += 1
            # Compare gain/(suffix length + 1) without floating-point rounding.
            better_ratio = (best is None or
                gain * (len(best)+1) > best_gain * (len(suffix)+1))
            tied_ratio = (best is not None and
                gain * (len(best)+1) == best_gain * (len(suffix)+1))
            if gain and (better_ratio or (tied_ratio and
                    (len(suffix), tuple(action_index[a] for a in suffix)) <
                    (len(best), tuple(action_index[a] for a in best)))):
                best, best_gain = suffix, gain
        if best is None or best_gain == 0:
            raise AssertionError("shortest witness set failed to distinguish a known pair")
        selected.append(best)
        work.tick(len(coverage[best]), "cover-update work budget exhausted")
        uncovered -= coverage[best]
        remaining.remove(best)
    selected = set(selected)
    work.tick(len(selected) * max(1, math.ceil(math.log2(max(2, len(selected))))),
              "selected-suffix sort work budget exhausted")
    return _shortlex(selected, action_index)


def _prefixes(model, work, action_index):
    prefixes = set()
    for access in model["access"]:
        work.tick(len(access)+1, "access-prefix work budget exhausted")
        word = tuple(access)
        prefixes.add(word)
        for action in model["actions"]:
            work.tick(reason="access-extension work budget exhausted")
            prefixes.add(word + (action,))
    work.tick(len(prefixes) * max(1, math.ceil(math.log2(max(2, len(prefixes))))),
              "access-sort work budget exhausted")
    return _shortlex(prefixes, action_index)


def _outcome_paths(model, work, max_word_length):
    """Find shortest paths from each state to every reachable output label."""
    outputs, transitions, actions = model["outputs"], model["transitions"], model["actions"]
    work.tick(len(outputs), "output-label scan work budget exhausted")
    labels = set(outputs)
    work.tick(len(labels) * max(1, math.ceil(math.log2(max(2, len(labels))))),
              "output-label sort work budget exhausted")
    ordered_labels = sorted(labels)
    obligations, unreachable, path_set = [], [], set()
    for start in range(len(outputs)):
        # BFS queue and action iteration preserve shortest-length, then declared
        # action-order tie breaking without consulting hidden environment data.
        work.tick(reason="outcome-path BFS initialization work budget exhausted")
        queue = [start]
        words = {start: ()}
        head = 0
        witness_by_label = {}
        while head < len(queue):
            state = queue[head]
            head += 1
            word = words[state]
            work.tick(reason="outcome-path state visit work budget exhausted")
            label = outputs[state]
            witness_by_label.setdefault(label, (word, state))
            for action in actions:
                work.tick(reason="outcome-path transition work budget exhausted")
                target = transitions[state][action]
                if target in words:
                    continue
                target_word = word + (action,)
                work.tick(len(target_word), "outcome-path materialization work budget exhausted")
                words[target] = target_word
                queue.append(target)
        for label in ordered_labels:
            witness = witness_by_label.get(label)
            if witness is None:
                work.tick(reason="unreachable-output obligation work budget exhausted")
                unreachable.append({"start_state": start, "output": label,
                    "interpretation": "output label is unreachable from this candidate state"})
                continue
            word, target_state = witness
            if len(word) > max_word_length:
                raise _Limit("shortest output-reaching path exceeds max_word_length")
            work.tick(len(word)+1, "outcome-path obligation materialization work budget exhausted")
            obligations.append({"start_state": start, "output": label,
                "word": list(word), "target_state": target_state})
            path_set.add(word)
    work.tick(len(path_set), "outcome-path suffix union work budget exhausted")
    return obligations, unreachable, path_set


def _compact_prefix_words(words, actions, work):
    """Retain prefix-maximal tests and certify every dropped prefix obligation."""
    root = {"children": {}, "terminal": False}
    for word in words:
        work.tick(len(word)+1, "prefix-trie insertion work budget exhausted")
        node = root
        for action in word:
            node = node["children"].setdefault(action, {"children": {}, "terminal": False})
        node["terminal"] = True

    leaves, certificate = [], []

    def visit(node, prefix):
        work.tick(reason="prefix-trie traversal work budget exhausted")
        if not node["children"]:
            if node["terminal"]:
                leaves.append(prefix)
                certificate.append((prefix, prefix))
                return prefix
            return None
        first_leaf = None
        for action in actions:
            work.tick(reason="prefix-trie edge scan work budget exhausted")
            child = node["children"].get(action)
            if child is None:
                continue
            work.tick(len(prefix)+1, "prefix-trie path materialization work budget exhausted")
            child_leaf = visit(child, prefix+(action,))
            if first_leaf is None:
                first_leaf = child_leaf
        if node["terminal"]:
            if first_leaf is None:
                raise AssertionError("terminal trie node lacks a maximal descendant")
            certificate.append((prefix, first_leaf))
        return first_leaf

    visit(root, ())
    work.tick(len(leaves) * max(1, math.ceil(math.log2(max(2, len(leaves))))),
              "prefix-maximal schedule sort work budget exhausted")
    action_index = {action: index for index, action in enumerate(actions)}
    compacted = _shortlex(leaves, action_index)
    work.tick(len(certificate) * max(1, math.ceil(math.log2(max(2, len(certificate))))),
              "prefix-coverage certificate sort work budget exhausted")
    certificate.sort(key=lambda pair: (len(pair[0]), tuple(action_index[a] for a in pair[0])))

    compacted_set = set(compacted)
    work.tick(len(words), "prefix-coverage obligation verification work budget exhausted")
    if {original for original, _ in certificate} != set(words):
        raise AssertionError("prefix coverage certificate omitted an original test")
    for original, witness in certificate:
        work.tick(len(original)+len(witness)+1,
                  "prefix-coverage witness verification work budget exhausted")
        if witness not in compacted_set or witness[:len(original)] != original:
            raise AssertionError("retained test does not cover an original prefix")
    for word in compacted:
        for size in range(len(word)):
            work.tick(reason="prefix-antichain verification work budget exhausted")
            if word[:size] in compacted_set:
                raise AssertionError("compacted tests are not prefix-maximal")

    serialized = [{"original_word": list(original), "retained_witness": list(witness)}
                  for original, witness in certificate]
    return compacted, serialized


def _middle_words(actions, depth):
    yield ()
    for length in range(1, depth+1):
        yield from itertools.product(actions, repeat=length)


def construct_experiments(model, *, extra_depth=1, max_tests=4096,
                          max_word_length=32, max_work=1_000_000,
                          include_outcome_paths=False, compact_prefixes=False) -> dict:
    """Build a bounded reset/step conformance schedule for ``model``.

    The schedule is the deduplicated shortlex union of
    ``P · A^<=extra_depth · E``. ``P`` contains each state access word and its
    one-action extensions. ``E`` is a greedy suffix cover of all distinguishable
    state pairs; epsilon is always retained. If candidate states are equivalent,
    status is ``equivalent_states`` and the pair list is explicit. That status
    still includes a schedule for the distinguishable pairs; it does not claim
    every row is a distinct real-world state.

    When ``include_outcome_paths`` is true, shortest paths from every candidate
    state to every reachable output label are also included. These finite
    candidate-graph obligations do not establish hidden-world reachability.
    When ``compact_prefixes`` is true, only prefix-maximal reset words are
    tested; a certificate maps every original word to a retained extension.
    This preserves trace obligations under stationary deterministic resets.
    """
    from .active_navigation import validate_model

    if (type(extra_depth) is not int or not 0 <= extra_depth <= MAX_EXTRA_DEPTH
            or type(max_tests) is not int or not 0 <= max_tests <= MAX_TESTS
            or type(max_word_length) is not int or not 0 <= max_word_length <= MAX_WORD_LENGTH
            or type(max_work) is not int or not 0 <= max_work <= MAX_WORK
            or type(include_outcome_paths) is not bool
            or type(compact_prefixes) is not bool):
        raise ValueError("experiment limits outside bounded contract")
    if not validate_model(model):
        raise ValueError("invalid active Moore model")

    actions = model["actions"]
    action_index = {action: index for index, action in enumerate(actions)}
    model_digest = digest(model)
    source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    limit_record = {"extra_depth": extra_depth, "max_tests": max_tests,
                    "max_word_length": max_word_length, "max_work": max_work}
    if include_outcome_paths:
        limit_record["include_outcome_paths"] = True
    if compact_prefixes:
        limit_record["compact_prefixes"] = True
    strategy = "model_derived"
    construction_method = "reverse-pair-BFS; greedy-shortest-witness-set-cover; deduplicated P.A<=d.E"
    if include_outcome_paths:
        construction_method += "; shortest candidate paths to each reachable output label"
    work = _Work(max_work)
    base = {"model_digest": model_digest, "source_sha256": source_sha256,
            "limits": limit_record, "pair_count": 0, "distinguished_pairs": [],
            "equivalent_pairs": [], "suffixes": [], "words": [], "endpoint_outputs": [],
            "work": 0, "schedule_hash": None,
            "interpretation": "finite tests only; no complete-world or target-state-bound claim"}
    try:
        pair_count, witnesses, equivalent = _distinguishing_witnesses(
            model, work, max_word_length)
        base["pair_count"] = pair_count
        work.tick(len(witnesses) * max(1, math.ceil(math.log2(max(2, len(witnesses))))),
                  "distinguished-pair sort work budget exhausted")
        base["distinguished_pairs"] = [
            {"states": [left, right], "shortest_suffix": list(witnesses[(left, right)])}
            for left, right in sorted(witnesses)]
        base["equivalent_pairs"] = [list(item) for item in equivalent]
        suffixes = _select_suffixes(model, witnesses, work, action_index)
        outcome_obligations, unreachable_outputs, outcome_suffixes = [], [], set()
        characteristic_suffixes = list(suffixes)
        if include_outcome_paths:
            outcome_obligations, unreachable_outputs, outcome_suffixes = _outcome_paths(
                model, work, max_word_length)
            work.tick(len(suffixes)+len(outcome_suffixes),
                      "combined suffix set work budget exhausted")
            suffixes = _shortlex(set(suffixes) | outcome_suffixes, action_index)
            work.tick(len(suffixes) * max(1, math.ceil(math.log2(max(2, len(suffixes))))),
                      "combined suffix sort work budget exhausted")
        access = _prefixes(model, work, action_index)
        max_prefix = max((len(word) for word in access), default=0)
        max_suffix = max((len(word) for word in suffixes), default=0)
        if max_prefix + extra_depth + max_suffix > max_word_length:
            raise _Limit("schedule word exceeds max_word_length")
        middle_count = sum(len(actions) ** length for length in range(extra_depth+1))
        raw_count = len(access) * middle_count * len(suffixes)
        # Avoid spending unbounded time deduplicating a Cartesian product. This
        # conservative preflight may stop a schedule whose deduplicated union
        # would be smaller, but never silently omits requested combinations.
        if raw_count > max_work - work.used:
            raise _Limit("schedule product exceeds remaining work budget")
        words = set()
        for prefix in access:
            for middle in _middle_words(actions, extra_depth):
                for suffix in suffixes:
                    work.tick(reason="schedule-construction work budget exhausted")
                    word = prefix + tuple(middle) + suffix
                    if len(word) > max_word_length:
                        raise _Limit("schedule word exceeds max_word_length")
                    if word not in words:
                        if len(words) >= max_tests:
                            raise _Limit("unique schedule exceeds max_tests")
                        words.add(word)
        # Keep epsilon even if malformed future extensions change P/E construction.
        if () not in words:
            if len(words) >= max_tests:
                raise _Limit("epsilon exceeds max_tests")
            words.add(())
        sort_cost = len(words) * max(1, math.ceil(math.log2(max(2, len(words)))))
        work.tick(sort_cost, "schedule-sort work budget exhausted")
        sorted_words = _shortlex(words, action_index)
        original_word_count = len(sorted_words)
        coverage_certificate = None
        if compact_prefixes:
            sorted_words, coverage_certificate = _compact_prefix_words(
                sorted_words, actions, work)
            work.tick(sum(len(word)+1 for word in sorted_words),
                      "compacted schedule serialization work budget exhausted")
        outputs = sorted(set(model["outputs"]))
        if include_outcome_paths:
            work.tick(sum(len(word)+1 for word in outcome_suffixes),
                      "outcome-path suffix serialization work budget exhausted")
        base.update({"status": "equivalent_states" if equivalent else "constructed",
            "strategy": strategy, "construction_method": construction_method,
            "suffixes": [list(word) for word in suffixes],
            "words": [list(word) for word in sorted_words],
            "endpoint_outputs": outputs,
            "schedule_complete_for_distinguishable_pairs": True,
            "state_pair_equivalence_warning": bool(equivalent)})
        hash_payload = {"source_sha256": source_sha256,
            "model_digest": model_digest, "limits": limit_record,
            "strategy": strategy, "construction_method": construction_method,
            "words": base["words"], "suffixes": base["suffixes"]}
        if include_outcome_paths:
            base.update({"include_outcome_paths": True,
                "characteristic_suffixes": [list(word) for word in characteristic_suffixes],
                "outcome_path_suffixes": [list(word) for word in _shortlex(
                    outcome_suffixes, action_index)],
                "outcome_path_obligations": outcome_obligations,
                "unreachable_output_labels": unreachable_outputs})
            hash_payload.update({"outcome_path_obligations": outcome_obligations,
                "unreachable_output_labels": unreachable_outputs})
        if compact_prefixes:
            actual_tested_word_count = len(sorted_words)
            base.update({"compact_prefixes": True,
                "original_word_count": original_word_count,
                "actual_tested_word_count": actual_tested_word_count,
                "coverage_certificate": coverage_certificate,
                "coverage_scope": "exact reset-trace prefix coverage under stationary deterministic behavior"})
            hash_payload.update({"compact_prefixes": True,
                "original_word_count": original_word_count,
                "actual_tested_word_count": actual_tested_word_count,
                "coverage_certificate": coverage_certificate})
        base["schedule_hash"] = digest(hash_payload)
    except _Limit as stopped:
        base.update({"status": "construction_limit", "reason": stopped.reason,
                     "schedule_complete_for_distinguishable_pairs": False})
    base["work"] = work.used
    return base
