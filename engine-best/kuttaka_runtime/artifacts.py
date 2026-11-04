"""Persistent, content-addressed memory for inspectable JSON artifacts.

Artifacts are data only. This module never executes an artifact or treats its
applicability description as an executable guard or as evidence of truth.
Qualification is a host-supplied attestation whose opaque evidence identifiers
are recorded, not authenticated or checked for freshness here. Hash chains
detect inconsistent edits, not an untrusted writer or removal of a valid tail.
The writer lock is an OS lock (flock) on a sidecar file, held while the memory is open
and released when it closes or its process ends, so an unclean shutdown leaves no lock
behind (until 2026-11-01 the file's existence was the lock, and a killed writer left the
workspace refusing every later open). The hash-chained log is replayed on every open, so a
write torn by the shutdown fails loudly rather than being trusted.
"""

from __future__ import annotations

import copy
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
import zlib


KINDS = frozenset({"representation", "method", "investigator", "question"})


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_copy(value):
    return json.loads(canonical(value))


class ArtifactMemory:
    """Single-writer persistent memory with immutable compressed blobs and an event log.

    Payloads have exactly these fields: ``kind``, ``body``, ``dependencies``,
    ``provenance`` and ``applicability``. Applicability is descriptive JSON.
    Dependency references must already exist, which makes ordinary cycles
    impossible and pins composition to exact content identities.

    Evidence identifiers are opaque strings. Requalification does not infer
    their age or freshness; callers must supply and assess current evidence.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.blobs = self.path / "blobs"
        self.path.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(exist_ok=True)
        self.log_path = self.path / "events.jsonl"
        self.lock_path = self.path / ".writer.lock"
        self._lock_stream = None
        self._closed = False
        self._records = {}
        self._events = []
        try:
            self._acquire_lock()
            if not self.log_path.exists():
                with self.log_path.open("x", encoding="utf-8"):
                    pass
            self._replay()
            self._stream = self.log_path.open("a", encoding="utf-8", newline="\n")
        except BaseException:
            self._release_lock()
            raise

    def _acquire_lock(self):
        """One writer at a time: a second open, in this process or another, raises FileExistsError."""
        stream = self.lock_path.open("a+", encoding="ascii")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            raise FileExistsError(errno.EEXIST, "artifact memory is open for writing elsewhere",
                                  str(self.lock_path)) from None
        except BaseException:
            stream.close()
            raise
        self._lock_stream = stream

    def _release_lock(self):
        # The sidecar file stays: removing it while another opener holds it open could give two writers.
        stream, self._lock_stream = self._lock_stream, None
        if stream is not None:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            stream.close()

    @staticmethod
    def _validate_payload(payload):
        if not isinstance(payload, dict) or set(payload) != {
                "kind", "body", "dependencies", "provenance", "applicability"}:
            raise ValueError("payload must contain kind, body, dependencies, provenance, and applicability")
        if not isinstance(payload["kind"], str) or payload["kind"] not in KINDS:
            raise ValueError("unknown artifact kind")
        deps = payload["dependencies"]
        if (not isinstance(deps, list) or any(not isinstance(dep, str) or len(dep) != 64
                                               or any(c not in "0123456789abcdef" for c in dep)
                                               for dep in deps)
                or len(set(deps)) != len(deps)):
            raise ValueError("dependencies must be unique artifact ids")
        # Enforce JSON compatibility and detach caller-owned structures.
        return _json_copy(payload)

    def _blob_path(self, artifact_id):
        return self.blobs / (artifact_id + ".json.zlib")

    def _read_blob(self, artifact_id):
        compressed = self._blob_path(artifact_id).read_bytes()
        try:
            raw = zlib.decompress(compressed)
            payload = json.loads(raw)
        except (zlib.error, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"corrupt artifact blob: {artifact_id}") from error
        if _digest(raw) != artifact_id or canonical(payload) != raw:
            raise ValueError(f"artifact blob identity or canonical encoding mismatch: {artifact_id}")
        return self._validate_payload(payload)

    def _install_blob(self, artifact_id, raw):
        destination = self._blob_path(artifact_id)
        compressed = zlib.compress(raw, level=9)
        temporary = self.blobs / ("." + artifact_id + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(compressed)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if self._read_blob(artifact_id) != json.loads(raw):
                    raise ValueError("existing artifact blob differs from its content identity")
            dir_fd = os.open(self.blobs, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _append(self, kind, body):
        body = _json_copy(body)
        event = {"seq": len(self._events), "kind": kind, "body": body,
                 "prev": self._events[-1]["hash"] if self._events else ""}
        event["hash"] = _digest(canonical(event))
        self._stream.write(canonical(event).decode("utf-8") + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._apply(event)
        self._events.append(event)
        return event["hash"]

    def _replay(self):
        events = []
        with self.log_path.open("r", encoding="utf-8", newline="") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    raise ValueError("incomplete artifact journal record")
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError("invalid artifact journal JSON") from error
                if (not isinstance(event, dict) or set(event) != {"seq", "kind", "body", "prev", "hash"}
                        or type(event["seq"]) is not int or event["seq"] != len(events)
                        or event["prev"] != (events[-1]["hash"] if events else "")):
                    raise ValueError("artifact journal sequence or shape failure")
                expected = _digest(canonical({k: v for k, v in event.items() if k != "hash"}))
                if event["hash"] != expected:
                    raise ValueError("artifact journal integrity failure")
                self._apply(event)
                events.append(event)
        self._events = events

    def _apply(self, event):
        kind, body = event["kind"], event["body"]
        if kind == "put":
            aid = body.get("id")
            if (not isinstance(aid, str) or len(aid) != 64
                    or any(c not in "0123456789abcdef" for c in aid)):
                raise ValueError("invalid artifact id in journal")
            payload = self._read_blob(aid)
            if body.get("dependencies") != payload["dependencies"]:
                raise ValueError("artifact journal dependency mismatch")
            missing = [dep for dep in payload["dependencies"] if dep not in self._records]
            if missing:
                raise ValueError("artifact journal refers to missing dependencies")
            self._records.setdefault(aid, {"payload": payload, "qualifications": {},
                                           "qualification_history": [], "invalidated": False,
                                           "invalidated_scopes": set(), "requalified_scopes": set(),
                                           "invalidation_history": []})
        elif kind == "qualify":
            aid, scope = body.get("id"), body.get("scope")
            rec = self._records.get(aid)
            if rec is None:
                raise ValueError("qualification refers to missing artifact")
            if any(self.status(dep, scope) != "qualified" for dep in rec["payload"]["dependencies"]):
                raise ValueError("qualification dependencies are not current for scope")
            qualification = {"scope": scope, "checker": body["checker"],
                             "evidence": list(body["evidence"]), "event_hash": event["hash"],
                             "active": True}
            rec["qualifications"][scope] = qualification
            rec["invalidated_scopes"].discard(scope)
            rec["requalified_scopes"].add(scope)
            rec["qualification_history"].append(copy.deepcopy(qualification))
        elif kind == "invalidate":
            aid = body.get("id")
            if aid not in self._records:
                raise ValueError("invalidation refers to missing artifact")
            if set(body) == {"id", "affected", "reason", "evidence"}:
                scope = None  # Legacy global event shape.
            elif set(body) == {"id", "affected", "reason", "evidence", "scope"}:
                scope = body["scope"]
                if not isinstance(scope, str) or not scope.strip():
                    raise ValueError("invalid scope in artifact invalidation")
            else:
                raise ValueError("invalid artifact invalidation event shape")
            for affected_id in body.get("affected", []):
                rec = self._records.get(affected_id)
                if rec is None:
                    raise ValueError("invalidation dependency is missing")
                if scope is None:
                    rec["invalidated"] = True
                    rec["requalified_scopes"].clear()
                    for qualification in rec["qualifications"].values():
                        qualification["active"] = False
                else:
                    rec["invalidated_scopes"].add(scope)
                    rec["requalified_scopes"].discard(scope)
                    qualification = rec["qualifications"].get(scope)
                    if qualification is not None:
                        qualification["active"] = False
                rec["invalidation_history"].append({"reason": body["reason"],
                    "evidence": list(body["evidence"]), "event_hash": event["hash"],
                    "root": aid, "scope": scope})
        else:
            raise ValueError("unknown artifact journal event")

    def _require_open(self):
        if self._closed:
            raise ValueError("artifact memory is closed")

    def put(self, payload):
        self._require_open()
        payload = self._validate_payload(payload)
        raw = canonical(payload)
        aid = _digest(raw)
        missing = [dep for dep in payload["dependencies"] if dep not in self._records]
        if missing:
            raise KeyError(f"missing artifact dependencies: {', '.join(missing)}")
        self._install_blob(aid, raw)
        self._append("put", {"id": aid, "dependencies": payload["dependencies"]})
        return aid

    def get(self, artifact_id):
        """Return a defensive copy even when the artifact is suspect or invalidated."""
        self._require_open()
        if artifact_id not in self._records:
            raise KeyError(artifact_id)
        return copy.deepcopy(self._read_blob(artifact_id))

    def get_qualified(self, artifact_id, scope):
        """Qualified read; inspection remains possible through ``get``."""
        self._require_open()
        self._verify_qualified_closure(artifact_id, scope, set())
        return self.get(artifact_id)

    def catalog(self, *, kind="method", query="", scope=None, limit=20):
        """Return a deterministic, bounded model-facing artifact index.

        Search is limited to the caller-visible body ``name`` and
        ``description`` strings. Exact case-insensitive substring matches rank
        first; remaining candidates rank by shared query-token count, then by
        artifact ID. An empty query lists artifacts by ID. Provenance and
        arbitrary payload fields are excluded. A catalog entry is metadata,
        not endorsement; its status is scoped only when the caller supplies a
        scope.
        """
        self._require_open()
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("unknown artifact kind")
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        if scope is not None and (not isinstance(scope, str) or not scope.strip()):
            raise ValueError("scope must be a nonempty string when supplied")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 through 100")
        needle = query.casefold().strip()
        query_terms = set(re.findall(r"[^\W_]+", needle, flags=re.UNICODE))
        found = []
        for artifact_id in sorted(self._records):
            payload = self._records[artifact_id]["payload"]
            if payload["kind"] != kind:
                continue
            body = payload["body"]
            name = body.get("name") if isinstance(body, dict) else None
            description = body.get("description") if isinstance(body, dict) else None
            name = name if isinstance(name, str) else ""
            description = description if isinstance(description, str) else ""
            searchable = (name + " " + description).casefold()
            exact = bool(needle and needle in searchable)
            terms = set(re.findall(r"[^\W_]+", searchable, flags=re.UNICODE))
            overlap = len(query_terms & terms)
            if needle and not exact and overlap == 0:
                continue
            label = name if isinstance(body, dict) and isinstance(body.get("name"), str) else artifact_id
            found.append((not exact, -overlap, artifact_id, label))
        found.sort()
        entries = []
        for _, _, artifact_id, label in found[:limit]:
            # Re-read selected blobs so post-open corruption fails closed.
            payload = self.get(artifact_id)
            entries.append({"id": artifact_id, "kind": payload["kind"], "label": label,
                            "status": self.status(artifact_id, scope) if scope is not None else "unscoped",
                            "qualified_scopes": self.qualified_scope_count(artifact_id)})
        return entries

    def ids(self, kind=None):
        """Every artifact id in first-stored order (optionally one kind); no limit, no ranking."""
        self._require_open()
        if kind is not None and (not isinstance(kind, str) or kind not in KINDS):
            raise ValueError("unknown artifact kind")
        return [artifact_id for artifact_id, record in self._records.items()
                if kind is None or record["payload"]["kind"] == kind]

    def qualified_scope_count(self, artifact_id):
        """Number of scopes in which the artifact currently holds a qualification."""
        self._require_open()
        return sum(1 for scope in self._records[artifact_id]["qualifications"]
                   if self.status(artifact_id, scope) == "qualified")

    def qualifications(self, artifact_id):
        """Return host attestations with checker identities; roles are not inferred.

        A component attestation is not an independent check of that component's
        own outputs. Consumers must preserve the checker/scope distinction.
        """
        self._require_open()
        return copy.deepcopy(list(self._records[artifact_id]['qualifications'].values()))

    def _verify_qualified_closure(self, artifact_id, scope, visiting):
        if artifact_id in visiting:
            raise ValueError("artifact dependency cycle detected")
        if self.status(artifact_id, scope) != "qualified":
            raise PermissionError("artifact or dependency is not currently qualified for this scope")
        payload = self._read_blob(artifact_id)
        visiting.add(artifact_id)
        try:
            for dependency in payload["dependencies"]:
                self._verify_qualified_closure(dependency, scope, visiting)
        finally:
            visiting.remove(artifact_id)

    def qualify(self, artifact_id, scope, checker, evidence):
        self._require_open()
        if artifact_id not in self._records:
            raise KeyError(artifact_id)
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("an explicit nonempty qualification scope is required")
        if not isinstance(checker, str) or not checker.strip():
            raise ValueError("an explicit checker identity is required")
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(ref, str) or not ref.strip() for ref in evidence)
                or len(set(evidence)) != len(evidence)):
            raise ValueError("one or more unique evidence identifiers are required")
        rec = self._records[artifact_id]
        if any(self.status(dep, scope) != "qualified" for dep in rec["payload"]["dependencies"]):
            raise ValueError("every dependency must be currently qualified for this scope")
        return self._append("qualify", {"id": artifact_id, "scope": scope,
                            "checker": checker, "evidence": evidence})

    def invalidate(self, artifact_id, reason, evidence, *, scope=None):
        """Invalidate a dependency closure globally, or only in the named scope.

        A scoped counterexample preserves qualifications in other scopes. Scope
        relationships are opaque: callers must explicitly invalidate any other
        affected scopes. Requalification never automatically restores dependents.
        """
        self._require_open()
        if artifact_id not in self._records:
            raise KeyError(artifact_id)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("an invalidation reason is required")
        if scope is not None and (not isinstance(scope, str) or not scope.strip()):
            raise ValueError("scope must be a nonempty string when supplied")
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(ref, str) or not ref.strip() for ref in evidence)
                or len(set(evidence)) != len(evidence)):
            raise ValueError("one or more unique counterevidence identifiers are required")
        affected = [artifact_id]
        seen = {artifact_id}
        index = 0
        while index < len(affected):
            current = affected[index]
            index += 1
            for candidate in sorted(self._records):
                if candidate in seen:
                    continue
                if current in self._records[candidate]["payload"]["dependencies"]:
                    seen.add(candidate)
                    affected.append(candidate)
        body = {"id": artifact_id, "affected": affected, "reason": reason, "evidence": evidence}
        if scope is not None:
            body["scope"] = scope
        return self._append("invalidate", body)

    def status(self, artifact_id, scope):
        """Return qualified, invalidated, or proposed; applicability is not evaluated."""
        self._require_open()
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("an explicit nonempty scope is required")
        if artifact_id not in self._records:
            raise KeyError(artifact_id)
        rec = self._records[artifact_id]
        qualification = rec["qualifications"].get(scope)
        if qualification and qualification["active"]:
            return "qualified"
        if (scope in rec["invalidated_scopes"]
                or (rec["invalidated"] and scope not in rec["requalified_scopes"])):
            return "invalidated"
        return "proposed"

    def stats(self):
        self._require_open()
        return {"artifacts": len(self._records), "events": len(self._events),
                "canonical_unique_blob_bytes": sum(len(canonical(self._read_blob(aid)))
                                                   for aid in self._records),
                "compressed_blob_bytes": sum(p.stat().st_size for p in self.blobs.glob("*.json.zlib")),
                "journal_bytes": self.log_path.stat().st_size}

    def close(self):
        if not self._closed:
            self._stream.close()
            self._closed = True
            self._release_lock()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
