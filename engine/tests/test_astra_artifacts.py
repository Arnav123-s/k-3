"""Mechanism tests for persistent artifact memory; no code is executed."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zlib

from kuttaka_runtime.artifacts import ArtifactMemory, canonical


def payload(kind="method", body=None, dependencies=(), *, applicability=None):
    return {"kind": kind, "body": body if body is not None else {"steps": ["inspect", "compare"]},
            "dependencies": list(dependencies), "provenance": {"proposer": "fixed-model-v1"},
            "applicability": applicability if applicability is not None else {"description": "candidate scope"}}


class ArtifactMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory"

    def test_put_is_immutable_deduplicated_and_exactly_reversible(self):
        source = payload(body={"large_repeated_text": "abc123" * 2000})
        expected = json.loads(canonical(source))
        with ArtifactMemory(self.path) as memory:
            first = memory.put(source)
            source["body"]["large_repeated_text"] = "mutated by caller"
            second = memory.put(expected)
            self.assertEqual(first, hashlib.sha256(canonical(expected)).hexdigest())
            self.assertEqual(first, second)
            self.assertEqual(memory.get(first), expected)
            blobs = list((self.path / "blobs").glob("*.json.zlib"))
            self.assertEqual(len(blobs), 1)
            compressed = blobs[0].read_bytes()
            self.assertLess(len(compressed), len(canonical(expected)))
            self.assertEqual(zlib.decompress(compressed), canonical(expected))
            returned = memory.get(first)
            returned["body"]["large_repeated_text"] = "also mutated"
            self.assertEqual(memory.get(first), expected)

    def test_reopen_replays_qualification_binding_and_payload(self):
        with ArtifactMemory(self.path) as memory:
            aid = memory.put(payload())
            self.assertEqual(memory.status(aid, "task-family-A"), "proposed")
            qualification_hash = memory.qualify(aid, "task-family-A", "checker@sha256:abc", ["event:001", "event:002"])
            self.assertEqual(memory.status(aid, "task-family-A"), "qualified")
            self.assertEqual(memory.status(aid, "task-family-B"), "proposed")
            self.assertEqual(memory.get_qualified(aid, "task-family-A"), payload())
            events = [json.loads(line) for line in (self.path / "events.jsonl").read_text().splitlines()]
            q = events[-1]
            self.assertEqual(q["hash"], qualification_hash)
            self.assertEqual(q["kind"], "qualify")
            self.assertEqual(q["body"], {"id": aid, "scope": "task-family-A", "checker": "checker@sha256:abc",
                                           "evidence": ["event:001", "event:002"]})
        with ArtifactMemory(self.path) as memory:
            self.assertEqual(memory.get(aid), payload())
            self.assertEqual(memory.status(aid, "task-family-A"), "qualified")
            self.assertEqual(memory.status(aid, "task-family-B"), "proposed")
            self.assertEqual(memory.stats()["artifacts"], 1)

    def test_invalidation_cascades_and_preserves_history_and_unrelated_artifact(self):
        with ArtifactMemory(self.path) as memory:
            root = memory.put(payload("representation"))
            child = memory.put(payload("method", dependencies=[root]))
            grandchild = memory.put(payload("investigator", dependencies=[child]))
            unrelated = memory.put(payload("question"))
            for aid in (root, child, grandchild, unrelated):
                memory.qualify(aid, "scope-1", "checker-v1", ["evidence:pass"])
            memory.invalidate(root, "counterexample", ["evidence:counterexample"])
            for aid in (root, child, grandchild):
                self.assertEqual(memory.status(aid, "scope-1"), "invalidated")
                with self.assertRaises(PermissionError):
                    memory.get_qualified(aid, "scope-1")
                self.assertIsInstance(memory.get(aid), dict)  # suspect artifacts remain inspectable
            self.assertEqual(memory.status(unrelated, "scope-1"), "qualified")
            # Restoring the root alone cannot revive dependent qualifications.
            memory.qualify(root, "scope-1", "checker-v2", ["evidence:current-root"])
            self.assertEqual(memory.status(child, "scope-1"), "invalidated")
            with self.assertRaises(ValueError):
                memory.qualify(grandchild, "scope-1", "checker-v2", ["evidence:old"])
            memory.qualify(child, "scope-1", "checker-v2", ["evidence:current-child"])
            memory.qualify(grandchild, "scope-1", "checker-v2", ["evidence:current-grandchild"])
            self.assertEqual(memory.status(grandchild, "scope-1"), "qualified")
            self.assertEqual(memory.status(unrelated, "scope-1"), "qualified")
        with ArtifactMemory(self.path) as memory:
            self.assertEqual(memory.status(root, "scope-1"), "qualified")
            self.assertEqual(memory.status(child, "scope-1"), "qualified")
            self.assertEqual(memory.status(grandchild, "scope-1"), "qualified")

    def test_scoped_invalidation_preserves_other_scopes_and_replays_requalification(self):
        with ArtifactMemory(self.path) as memory:
            root = memory.put(payload("representation"))
            child = memory.put(payload("method", dependencies=[root]))
            for aid in (root, child):
                memory.qualify(aid, "narrow", "checker-v1", ["evidence:narrow"])
                memory.qualify(aid, "broad", "checker-v1", ["evidence:broad"])

            memory.invalidate(root, "counterexample only affects broad use", ["evidence:counterexample"],
                              scope="broad")
            self.assertEqual(memory.status(root, "narrow"), "qualified")
            self.assertEqual(memory.status(child, "narrow"), "qualified")
            self.assertEqual(memory.status(root, "broad"), "invalidated")
            self.assertEqual(memory.status(child, "broad"), "invalidated")
            # An affected scope is invalidated even when it had no prior qualification.
            memory.invalidate(root, "untested scope is also tainted", ["evidence:scope-review"],
                              scope="unqualified-scope")
            self.assertEqual(memory.status(child, "unqualified-scope"), "invalidated")

            memory.qualify(root, "broad", "checker-v2", ["evidence:root-recheck"])
            self.assertEqual(memory.status(root, "broad"), "qualified")
            self.assertEqual(memory.status(child, "broad"), "invalidated")
            memory.qualify(child, "broad", "checker-v2", ["evidence:child-recheck"])
            self.assertEqual(memory.status(child, "broad"), "qualified")

            memory.invalidate(root, "global counterexample", ["evidence:global"])
            self.assertEqual(memory.status(root, "narrow"), "invalidated")
            self.assertEqual(memory.status(child, "narrow"), "invalidated")
            self.assertEqual(memory.status(root, "broad"), "invalidated")
            events = [json.loads(line) for line in (self.path / "events.jsonl").read_text().splitlines()]
            global_event = events[-1]
            self.assertEqual(set(global_event["body"]), {"id", "affected", "reason", "evidence"})

            memory.qualify(root, "narrow", "checker-v3", ["evidence:narrow-current"])
            self.assertEqual(memory.status(root, "narrow"), "qualified")
            self.assertEqual(memory.status(child, "narrow"), "invalidated")
            memory.qualify(child, "narrow", "checker-v3", ["evidence:child-current"])

        with ArtifactMemory(self.path) as reopened:
            self.assertEqual(reopened.status(root, "narrow"), "qualified")
            self.assertEqual(reopened.status(child, "narrow"), "qualified")
            self.assertEqual(reopened.status(root, "broad"), "invalidated")
            self.assertEqual(reopened.status(child, "broad"), "invalidated")
            self.assertEqual(reopened.status(child, "unqualified-scope"), "invalidated")

    def test_scoped_invalidation_rejects_malformed_scope(self):
        with ArtifactMemory(self.path) as memory:
            aid = memory.put(payload())
            for scope in ("", "  ", 1, [], {}):
                with self.assertRaises(ValueError):
                    memory.invalidate(aid, "reason", ["evidence"], scope=scope)

    def test_missing_dependencies_are_rejected_and_no_execution_semantics_are_inferred(self):
        with ArtifactMemory(self.path) as memory:
            with self.assertRaises(KeyError):
                memory.put(payload(dependencies=["0" * 64]))
            aid = memory.put(payload(applicability={"requires": "state X is observable"}))
            # A named guard is inert metadata until a separate host qualification occurs.
            self.assertEqual(memory.status(aid, "state X is observable"), "proposed")
            with self.assertRaises(PermissionError):
                memory.get_qualified(aid, "state X is observable")
            for args in (("", "checker", ["e"]), ("scope", "", ["e"]), ("scope", "checker", [])):
                with self.assertRaises(ValueError):
                    memory.qualify(aid, *args)

    def test_tampered_blob_is_rejected_on_reopen(self):
        with ArtifactMemory(self.path) as memory:
            aid = memory.put(payload())
        blob = self.path / "blobs" / (aid + ".json.zlib")
        data = bytearray(blob.read_bytes())
        data[-1] ^= 1
        blob.write_bytes(data)
        with self.assertRaises(ValueError):
            ArtifactMemory(self.path)

    def test_qualified_read_checks_integrity_of_full_dependency_closure(self):
        with ArtifactMemory(self.path) as memory:
            root = memory.put(payload("representation"))
            child = memory.put(payload("method", dependencies=[root]))
            memory.qualify(root, "scope", "checker-v1", ["evidence:root"])
            memory.qualify(child, "scope", "checker-v1", ["evidence:child"])
            self.assertEqual(memory.get_qualified(child, "scope")["dependencies"], [root])
            root_blob = self.path / "blobs" / (root + ".json.zlib")
            corrupted = bytearray(root_blob.read_bytes())
            corrupted[-1] ^= 1
            root_blob.write_bytes(corrupted)
            # The child's own content and lifecycle remain intact, but a qualified
            # read cannot claim a usable dependency closure after root corruption.
            with self.assertRaises(ValueError):
                memory.get_qualified(child, "scope")

    def test_tampered_or_truncated_journal_is_rejected(self):
        with ArtifactMemory(self.path) as memory:
            memory.put(payload())
        log = self.path / "events.jsonl"
        event = json.loads(log.read_text().splitlines()[0])
        event["body"]["id"] = "f" * 64
        log.write_text(json.dumps(event) + "\n")
        with self.assertRaises(ValueError):
            ArtifactMemory(self.path)

    def test_writer_lock_is_exclusive_and_released_on_close(self):
        first = ArtifactMemory(self.path)
        try:
            with self.assertRaises(FileExistsError):
                ArtifactMemory(self.path)
        finally:
            first.close()
        with ArtifactMemory(self.path) as reopened:
            self.assertEqual(reopened.stats()["events"], 0)

    def test_catalog_is_bounded_deterministic_scoped_and_replays(self):
        with ArtifactMemory(self.path) as memory:
            entries = []
            for name in ("Zeta method", "Alpha method", "Middle method"):
                item = payload("method", body={"name": name, "description": "general procedure",
                                                "source": "hidden needle"})
                item["provenance"] = {"private_note": "hidden needle"}
                entries.append((name, memory.put(item)))
            representation = memory.put(payload("representation", body={"name": "Alpha representation"}))
            memory.qualify(entries[0][1], "catalog-scope", "checker-v1", ["evidence"])
            expected_ids = sorted(aid for _, aid in entries)
            first = memory.catalog(kind="method", scope="catalog-scope", limit=2)
            self.assertEqual([item["id"] for item in first], expected_ids[:2])
            self.assertEqual(first[0]["status"], "proposed")
            qualified = memory.catalog(kind="method", query="GENERAL", scope="catalog-scope", limit=10)
            status_by_id = {item["id"]: item["status"] for item in qualified}
            self.assertEqual(status_by_id[entries[0][1]], "qualified")
            self.assertEqual(status_by_id[entries[1][1]], "proposed")
            self.assertEqual(memory.catalog(kind="representation")[0]["id"], representation)
            self.assertEqual(memory.catalog(kind="method", query="hidden needle"), [])
            self.assertTrue(all(item["status"] == "unscoped" for item in memory.catalog(kind="method")))
        with ArtifactMemory(self.path) as reopened:
            self.assertEqual(reopened.catalog(kind="method", scope="catalog-scope", limit=2), first)

    def test_catalog_validates_arguments_and_fails_closed_on_corrupt_match(self):
        with ArtifactMemory(self.path) as memory:
            aid = memory.put(payload("method", body={"name": "Corruptible", "description": "target"}))
            for args in ({"kind": "unknown"}, {"query": 4}, {"scope": ""},
                         {"limit": True}, {"limit": 0}, {"limit": 101}):
                with self.assertRaises(ValueError):
                    memory.catalog(**args)
            blob = self.path / "blobs" / (aid + ".json.zlib")
            corrupted = bytearray(blob.read_bytes())
            corrupted[-1] ^= 1
            blob.write_bytes(corrupted)
            with self.assertRaises(ValueError):
                memory.catalog(query="target")

    def test_catalog_ranks_exact_then_lexical_overlap_and_keeps_scope_status(self):
        with ArtifactMemory(self.path) as memory:
            exact = memory.put(payload("method", body={
                "name": "remove repeated", "description": "exact match"}))
            lexical = memory.put(payload("method", body={
                "name": "dedupe first", "description": "Removes repeated values preserving first occurrence"}))
            lexical_peer = memory.put(payload("method", body={
                "name": "stable sequence", "description": "Repeated entries"}))
            memory.put(payload("method", body={
                "name": "calendar", "description": "schedule appointments"}))
            memory.qualify(lexical, "source-scope", "checker-v1", ["evidence"])

            matches = memory.catalog(kind="method", query="remove repeated",
                                     scope="other-scope", limit=3)
            ids = [entry["id"] for entry in matches]
            self.assertEqual(ids[0], exact)
            self.assertEqual(set(ids[1:]), {lexical, lexical_peer})
            self.assertEqual(ids[1:], sorted(ids[1:]))
            lexical_status = next(entry["status"] for entry in matches if entry["id"] == lexical)
            self.assertEqual(lexical_status, "proposed")
            self.assertEqual(len(memory.catalog(kind="method", query="remove repeated", limit=1)), 1)
            self.assertEqual(len(memory.catalog(kind="method", query="no shared terms")), 0)


if __name__ == "__main__":
    unittest.main()
