import json
import unittest

from kuttaka_runtime.context_memory import ContextMemory, _canonical


class ContextMemoryTests(unittest.TestCase):
    def test_late_contradiction_is_searchable_and_exactly_recallable(self):
        history = [{"kind": "observation", "text": f"early {i}"} for i in range(8)]
        history.append({"kind": "observation", "text": "contradiction: prior claim is false"})
        memory = ContextMemory(max_bytes=1400, recent_records=2, read_bytes=512)
        memory.bind(history)

        view = memory.view()
        self.assertLessEqual(len(_canonical(view)), 1400)
        self.assertTrue(view["memory"]["partial"])
        self.assertEqual(view["memory"]["omitted_count"], 7)
        found = memory.tools()["history_search"]({"query": "contradiction"})
        self.assertTrue(found["complete"])
        self.assertEqual(found["matches"][0]["index"], 8)
        recalled = memory.tools()["history_read"]({"index": 8})
        self.assertEqual(recalled["value"], history[8])
        self.assertEqual(recalled["digest"], found["matches"][0]["digest"])

    def test_duplicate_payload_occurrences_have_distinct_indices(self):
        duplicate = {"kind": "note", "text": "same"}
        memory = ContextMemory(recent_records=1)
        memory.bind([duplicate, duplicate])
        refs = memory.search({"query": "same", "limit": 1})
        self.assertFalse(refs["complete"])
        self.assertEqual(refs["matches"][0]["index"], 0)
        continuation = memory.search({"query": "same", "start": refs["next_cursor"]})
        self.assertEqual(continuation["matches"][0]["index"], 1)
        self.assertEqual(refs["matches"][0]["digest"], continuation["matches"][0]["digest"])

    def test_huge_object_requires_explicit_path_or_raw_page(self):
        huge = {"payload": "x" * 3000, "kind": "large"}
        memory = ContextMemory(read_bytes=512)
        memory.bind([huge])
        initial = memory.read({"index": 0})
        self.assertEqual(initial["status"], "needs_path_or_paging")

        offset = 0
        parts = []
        while True:
            page = memory.read({"index": 0, "path": "/payload", "offset": offset, "limit": 512})
            self.assertIn(page.get("kind"), ("string_page",))
            self.assertLessEqual(len(_canonical(page)), 512)
            parts.append(page["text"])
            if page["complete"]:
                break
            offset = page["next_offset"]
        self.assertEqual("".join(parts), huge["payload"])

        offset = 0
        raw_parts = []
        while True:
            page = memory.read({"index": 0, "offset": offset, "limit": 512})
            self.assertEqual(page.get("kind"), "canonical_json_page")
            self.assertLessEqual(len(_canonical(page)), 512)
            raw_parts.append(page["text"])
            if page["complete"]:
                break
            offset = page["next_offset"]
        self.assertEqual("".join(raw_parts).encode("utf-8"), _canonical(huge))

    def test_large_scalar_has_explicit_raw_paging(self):
        value = int("9" * 2000)
        memory = ContextMemory(read_bytes=512)
        memory.bind([value])
        self.assertEqual(memory.read({"index": 0})["status"], "needs_path_or_paging")
        offset = 0
        parts = []
        while True:
            page = memory.read({"index": 0, "offset": offset})
            self.assertEqual(page["kind"], "canonical_json_page")
            self.assertLessEqual(len(_canonical(page)), 512)
            parts.append(page["text"])
            if page["complete"]:
                break
            offset = page["next_offset"]
        self.assertEqual("".join(parts), str(value))

    def test_unicode_string_pages_are_valid_and_complete(self):
        text = "雪🙂é" * 400
        memory = ContextMemory(read_bytes=512)
        memory.bind([{"text": text}])
        offset = 0
        parts = []
        while True:
            page = memory.read({"index": 0, "path": "/text", "offset": offset, "limit": 512})
            self.assertEqual(page["kind"], "string_page")
            self.assertLessEqual(len(_canonical(page)), 512)
            parts.append(page["text"])
            if page["complete"]:
                break
            offset = page["next_offset"]
        self.assertEqual("".join(parts), text)

    def test_read_path_and_index_errors_are_json_responses(self):
        memory = ContextMemory()
        memory.bind([{"a": [1]}])
        self.assertEqual(memory.read({"index": 0, "path": "/missing"})["status"], "invalid_request")
        self.assertEqual(memory.read({"index": 0, "path": "/a/00"})["status"], "invalid_request")
        self.assertEqual(memory.read({"index": 1})["status"], "invalid_request")
        self.assertEqual(memory.search({"query": "", "limit": 1})["status"], "invalid_request")
        self.assertEqual(memory.read({"index": 0, "limit": 128})["status"], "invalid_request")

    def test_json_pointer_escaped_tilde_and_slash(self):
        memory = ContextMemory()
        memory.bind([{"a~b": 1, "a/b": 2}])
        self.assertEqual(memory.read({"index": 0, "path": "/a~0b"})["value"], 1)
        self.assertEqual(memory.read({"index": 0, "path": "/a~1b"})["value"], 2)
        self.assertEqual(memory.read({"index": 0, "path": "/a~2b"})["status"], "invalid_request")

    def test_literal_search_handles_unicode_text(self):
        memory = ContextMemory()
        memory.bind([{"note": "contradiction 雪🙂"}])
        result = memory.search({"query": "雪🙂"})
        self.assertEqual(result["matches"][0]["index"], 0)

    def test_config_validation_and_snapshot_are_immutable(self):
        for kwargs in (
            {"max_bytes": True}, {"max_bytes": 1199}, {"recent_records": False},
            {"recent_records": 0}, {"read_bytes": True}, {"read_bytes": 511},
            {"read_bytes": 7801},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ContextMemory(**kwargs)
        memory = ContextMemory()
        config = memory.configuration
        self.assertEqual(config["format"], ContextMemory.FORMAT)
        config["max_bytes"] = 1
        self.assertEqual(memory.configuration["max_bytes"], 6000)
        with self.assertRaises(AttributeError):
            memory.max_bytes = 1

    def test_binding_is_live_and_never_mutates_authoritative_records(self):
        record = {"kind": "observation", "nested": [1, 2]}
        history = [record]
        original = json.loads(json.dumps(history))
        memory = ContextMemory()
        memory.bind(history)
        history.append({"kind": "later", "text": "visible"})
        self.assertEqual(memory.view()["memory"]["total_records"], 2)
        self.assertEqual(memory.search({"query": "visible"})["matches"][0]["index"], 1)
        memory.read({"index": 0, "path": "/nested", "offset": 0, "limit": 128})
        self.assertEqual([record], original)
        self.assertIs(memory._history, history)
        viewed = memory.view()
        viewed["history"][0]["record"]["nested"].append(3)
        read = memory.read({"index": 0})
        read["value"]["nested"].append(4)
        self.assertEqual(history, original + [{"kind": "later", "text": "visible"}])

    def test_view_uses_stable_refs_for_records_that_do_not_fit(self):
        history = [{"text": "x" * 10000}, {"text": "latest"}]
        memory = ContextMemory(max_bytes=1200, recent_records=2)
        memory.bind(history)
        view = memory.view()
        self.assertLessEqual(len(_canonical(view)), 1200)
        self.assertEqual([row["index"] for row in view["history"]], [0, 1])
        self.assertTrue(view["history"][0]["ref"])
        self.assertEqual(view["history"][1]["record"], history[1])
        self.assertEqual(view["memory"]["referenced_count"], 1)
        self.assertTrue(view["memory"]["partial"])

    def test_minimum_view_bound_with_large_total_count(self):
        history = [{"n": i} for i in range(10000)]
        memory = ContextMemory(max_bytes=1200, recent_records=1)
        memory.bind(history)
        view = memory.view()
        self.assertLessEqual(len(_canonical(view)), 1200)
        self.assertEqual(view["memory"]["total_records"], 10000)
        self.assertEqual(view["history"][-1]["index"], 9999)

    def test_view_packing_measures_finalized_counts_and_partial_flag(self):
        memory = ContextMemory(max_bytes=1449, recent_records=10, read_bytes=512)
        memory.bind([{"text": "x" * 201, "i": i} for i in range(2)])
        view = memory.view()
        self.assertLessEqual(len(_canonical(view)), 1449)
        self.assertEqual(view["memory"]["partial"], view["memory"]["omitted_count"] > 0)
        self.assertEqual(view["memory"]["included_count"] + view["memory"]["omitted_count"], 2)

        # Exercise fit transitions where integer digit widths and final manifest fields grow.
        for size in range(0, 500, 17):
            records = [{"text": "雪" * size, "i": i} for i in range(3)]
            candidate = ContextMemory(max_bytes=1500 + size, recent_records=3)
            candidate.bind(records)
            packed = candidate.view()
            self.assertLessEqual(len(_canonical(packed)), candidate.max_bytes)

    def test_all_recall_responses_fit_configured_ascii_canonical_budget(self):
        memory = ContextMemory(read_bytes=512)
        memory.bind([{"key": "雪🙂" * 100, "nested": {"value": "x" * 1000}}])
        offset = 0
        while True:
            page = memory.read({"index": 0, "path": "/key", "offset": offset})
            self.assertLessEqual(len(_canonical(page)), 512)
            self.assertEqual(page["kind"], "string_page")
            if page["complete"]:
                break
            offset = page["next_offset"]
        result = memory.search({"query": "key"})
        self.assertLessEqual(len(_canonical(result)), 512)


if __name__ == "__main__":
    unittest.main()
