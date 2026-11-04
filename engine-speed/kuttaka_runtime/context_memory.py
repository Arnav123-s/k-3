"""Bounded model-facing views over an authoritative, host-owned history.

This module never summarizes, rewrites, or removes history. ``ContextMemory`` retains a
reference to the caller's full JSON history in host RAM; neither that storage nor the
deterministic literal search is bounded by ``max_bytes``. Only the serialized ``view``
and individual tool responses are bounded. Omitted context is represented by stable,
zero-based occurrence indices and content digests, and can be recovered with exact reads.
"""

from __future__ import annotations

import json
from typing import Any

from .store import canonical, digest


def _canonical(value: Any) -> bytes:
    # Match journal / broker accounting: canonical JSON is ASCII escaped.
    return canonical(value).encode("ascii")


def _digest(value: Any) -> str:
    return digest(value)


def _clone(value: Any) -> Any:
    return json.loads(canonical(value))


def _is_int(value: Any) -> bool:
    return type(value) is int


def _response_size(value: Any) -> int:
    return len(_canonical(value))


class ContextMemory:
    """A bounded chronological viewport plus deterministic exact recall tools.

    ``history`` remains authoritative and unabridged. Its size in host memory and the
    time needed to search it are not bounded by this class; only model-visible data is.
    """

    FORMAT = "kuttaka-context-memory-v1"
    SCOPE = "caller-bound JSON history; indices identify occurrences, including duplicates"

    def __init__(self, max_bytes: int = 6000, recent_records: int = 4, read_bytes: int = 4000):
        if not _is_int(max_bytes) or not 1200 <= max_bytes <= 1_000_000:
            raise ValueError("max_bytes must be an integer from 1200 to 1000000")
        if not _is_int(recent_records) or not 1 <= recent_records <= 10000:
            raise ValueError("recent_records must be an integer from 1 to 10000")
        if not _is_int(read_bytes) or not 512 <= read_bytes <= 7800:
            raise ValueError("read_bytes must be an integer from 512 to 7800")
        self._max_bytes = max_bytes
        self._recent_records = recent_records
        self._read_bytes = read_bytes
        self._history: list[Any] = []
        empty = self.view()
        if _response_size(empty) > self.max_bytes:
            raise ValueError("max_bytes is too small for the required view manifest")

    @property
    def configuration(self) -> dict[str, Any]:
        """Return a detached JSON-compatible snapshot of the immutable configuration."""
        return {"format": self.FORMAT, "max_bytes": self._max_bytes, "recent_records": self._recent_records,
                "read_bytes": self._read_bytes}

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def recent_records(self) -> int:
        return self._recent_records

    @property
    def read_bytes(self) -> int:
        return self._read_bytes

    def bind(self, history: list[Any]) -> None:
        """Bind the authoritative history list; later appends are visible immediately."""
        if not isinstance(history, list):
            raise TypeError("history must be a list")
        # Ensure all current records have a stable JSON representation, without copying or
        # mutating caller-owned records. New records are validated when accessed.
        for record in history:
            _canonical(record)
        self._history = history

    def _base_view(self) -> dict[str, Any]:
        total = len(self._history)
        return {
            "history": [],
            "memory": {
                "format": self.FORMAT,
                "scope": self.SCOPE,
                "interpretation": "not shown in this viewport does not mean absent; use exact recall/search",
                "total_records": total,
                "omitted_count": total,
                "included_count": 0,
                "referenced_count": 0,
                "partial": total > 0,
                "tools": {
                    "history_read": {
                        "input": {"index": "integer occurrence index", "path": "optional JSON Pointer",
                                  "offset": "optional page cursor", "limit": "optional response-byte cap"},
                        "output": "exact value, marked page with continuation, or explicit status/error object",
                    },
                    "history_search": {
                        "input": {"query": "nonempty literal string", "start": "optional index cursor",
                                  "limit": "optional maximum reference count"},
                        "output": "ordered {index,digest} references, next_cursor, and complete flag",
                        "semantics": "case-sensitive literal in string values/keys or scalar JSON; not relevance-ranked",
                    },
                },
            },
        }

    @staticmethod
    def _finalize_view(candidate: dict[str, Any], total: int) -> None:
        by_index = {row["index"]: row for row in candidate["history"]}
        full_indices = {index for index, row in by_index.items() if "record" in row}
        referenced_indices = set(by_index) - full_indices
        candidate["memory"].update({
            "omitted_count": total - len(full_indices),
            "included_count": len(full_indices),
            "referenced_count": len(referenced_indices),
            "partial": len(full_indices) != total,
        })

    def view(self) -> dict[str, Any]:
        """Return the newest bounded chronological window, using explicit refs as needed."""
        total = len(self._history)
        if not total:
            return self._base_view()

        start = max(0, total - self.recent_records)
        indices = list(range(start, total))
        latest_index = indices[-1]
        latest_record = self._history[latest_index]
        latest_entry = {"index": latest_index, "digest": _digest(latest_record),
                        "bytes": len(_canonical(latest_record)), "record": _clone(latest_record)}
        candidate = self._base_view()
        candidate["history"] = [latest_entry]
        self._finalize_view(candidate, total)
        if _response_size(candidate) > self.max_bytes:
            candidate = self._base_view()
            latest_ref = {"index": latest_index, "digest": _digest(latest_record),
                          "bytes": len(_canonical(latest_record)), "ref": True}
            candidate["history"] = [latest_ref]
            self._finalize_view(candidate, total)
            if _response_size(candidate) > self.max_bytes:
                raise ValueError("max_bytes cannot fit a reference to the latest record")

        # Add preceding recent records in reverse priority, then restore chronological order.
        for index in reversed(indices[:-1]):
            record = self._history[index]
            entry = {"index": index, "digest": _digest(record),
                     "bytes": len(_canonical(record)), "record": _clone(record)}
            attempt = dict(candidate)
            attempt["history"] = sorted([*candidate["history"], entry], key=lambda row: row["index"])
            self._finalize_view(attempt, total)
            if _response_size(attempt) <= self.max_bytes:
                candidate = attempt
                continue
            ref = {"index": index, "digest": _digest(record),
                   "bytes": len(_canonical(record)), "ref": True}
            attempt["history"] = sorted([*candidate["history"], ref], key=lambda row: row["index"])
            self._finalize_view(attempt, total)
            if _response_size(attempt) <= self.max_bytes:
                candidate = attempt

        if _response_size(candidate) > self.max_bytes:
            raise AssertionError("context view exceeded configured byte bound")
        return candidate

    def tools(self) -> dict[str, Any]:
        """Return the direct-investigator recall tools."""
        return {"history_read": self.read, "history_search": self.search}

    def _record(self, index: Any) -> tuple[Any, str]:
        if not _is_int(index):
            raise ValueError("index must be an integer")
        if index < 0 or index >= len(self._history):
            raise ValueError("history index out of range")
        record = self._history[index]
        try:
            digest = _digest(record)
        except (TypeError, ValueError) as error:
            raise ValueError("history record is not valid canonical JSON") from error
        return record, digest

    @staticmethod
    def _pointer(value: Any, path: Any) -> Any:
        if path is None:
            return value
        if not isinstance(path, str) or (path != "" and not path.startswith("/")):
            raise ValueError("path must be a JSON pointer string, empty string, or omitted")
        current = value
        if path == "":
            return current
        if len(path) > 64:
            raise ValueError("path exceeds the 64-character limit")
        for raw in path[1:].split("/"):
            cursor = 0
            while cursor < len(raw):
                if raw[cursor] == "~":
                    if cursor + 1 >= len(raw) or raw[cursor + 1] not in "01":
                        raise ValueError("invalid JSON pointer escape")
                    cursor += 2
                else:
                    cursor += 1
            token = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                if token not in current:
                    raise ValueError("path does not exist")
                current = current[token]
            elif isinstance(current, list):
                if not token.isdecimal() or (len(token) > 1 and token[0] == "0"):
                    raise ValueError("list JSON pointer token must be a canonical nonnegative index")
                index = int(token)
                if index >= len(current):
                    raise ValueError("path does not exist")
                current = current[index]
            else:
                raise ValueError("path traverses a scalar")
        return current

    def _check_limit(self, limit: Any) -> int:
        if limit is None:
            return self.read_bytes
        if not _is_int(limit) or limit < 256 or limit > self.read_bytes:
            raise ValueError("limit must be an integer from 256 through read_bytes")
        return limit

    def read(self, request: dict[str, Any]) -> dict[str, Any]:
        """Input: {index:int, path?:JSON-Pointer, offset?:int, limit?:int}.

        Output is an exact value, typed page with continuation, ``needs_path_or_paging``,
        or ``invalid_request``. ``limit`` bounds the canonical ASCII-escaped JSON response.
        """
        try:
            return self._read(request)
        except (TypeError, ValueError, KeyError) as error:
            return {"status": "invalid_request", "error": str(error)}

    def _read(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        allowed = {"index", "path", "offset", "limit"}
        if set(request) - allowed or "index" not in request:
            raise ValueError("request requires index and accepts only path, offset, limit")
        record, digest = self._record(request["index"])
        path = request.get("path")
        value = self._pointer(record, path)
        limit = self._check_limit(request.get("limit"))
        offset = request.get("offset")
        if offset is not None and (not _is_int(offset) or offset < 0):
            raise ValueError("offset must be a nonnegative integer")

        common = {"index": request["index"], "digest": digest, "path": path}
        raw = None
        if offset is None or not isinstance(value, (str, list)):
            try:
                raw = _canonical(value)
            except (TypeError, ValueError) as error:
                raise ValueError("history value is not valid canonical JSON") from error
        if offset is None:
            exact = {**common, "complete": True, "value": _clone(value)}
            if _response_size(exact) <= limit:
                return exact
            if not isinstance(value, (str, list)):
                return {**common, "complete": False, "status": "needs_path_or_paging",
                        "detail": "value exceeds response limit; select a smaller path or request offset 0"}

        # Strings page by Unicode code point, preserving valid text and exact continuation.
        if isinstance(value, str):
            point = offset or 0
            if point > len(value):
                raise ValueError("string offset out of range")
            end = point
            best = None
            while end < len(value):
                end += 1
                candidate = {**common, "complete": end == len(value), "kind": "string_page",
                             "offset": point, "text": value[point:end],
                             "next_offset": end if end < len(value) else None}
                if _response_size(candidate) > limit:
                    end -= 1
                    break
                best = candidate
            if end == point and point < len(value):
                return {**common, "complete": False, "status": "needs_path_or_paging",
                        "detail": "the next Unicode code point exceeds limit"}
            if best is not None:
                return best
            return {**common, "complete": True, "kind": "string_page", "offset": point,
                    "text": "", "next_offset": None}

        # Lists page by exact element boundaries; a large element can itself be read by path.
        if isinstance(value, list):
            point = offset or 0
            if point > len(value):
                raise ValueError("list offset out of range")
            page = []
            end = point
            while end < len(value):
                attempt = page + [value[end]]
                candidate = {**common, "complete": end + 1 == len(value), "kind": "list_page",
                             "offset": point, "value": _clone(attempt),
                             "next_offset": end + 1 if end + 1 < len(value) else None}
                if _response_size(candidate) > limit:
                    break
                page = attempt
                end += 1
            if end == point and point < len(value):
                return {**common, "complete": False, "status": "needs_path_or_paging",
                        "detail": "next list element exceeds limit; read its JSON pointer path"}
            return {**common, "complete": end == len(value), "kind": "list_page",
                    "offset": point, "value": _clone(page),
                    "next_offset": end if end < len(value) else None}

        # For objects and scalars, callers may page canonical ASCII JSON bytes explicitly.
        if offset is None:
            return {**common, "complete": False, "status": "needs_path_or_paging",
                    "detail": "value exceeds response limit; select a JSON pointer path or request offset 0"}
        point = offset or 0
        assert raw is not None
        if point > len(raw):
            raise ValueError("raw byte offset out of range")
        end = min(len(raw), point + limit)
        while end > point:
            try:
                text = raw[point:end].decode("ascii")
                candidate = {**common, "complete": end == len(raw), "kind": "canonical_json_page",
                             "offset": point, "text": text,
                             "next_offset": end if end < len(raw) else None,
                             "total_bytes": len(raw)}
                if _response_size(candidate) <= limit:
                    break
                end -= 1
            except UnicodeDecodeError:
                end -= 1
        else:
            return {**common, "complete": False, "status": "needs_path_or_paging",
                    "detail": "unable to fit a UTF-8 code point within limit"}
        return {**common, "complete": end == len(raw), "kind": "canonical_json_page",
                "offset": point, "text": text,
                "next_offset": end if end < len(raw) else None,
                "total_bytes": len(raw)}

    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        """Input: {query:str, start?:int, limit?:int}; output ordered refs and continuation.

        Matching is deterministic, case-sensitive literal search in string values/keys
        and canonical non-string scalars, not
        semantic or relevance search. Results contain only {index,digest} references.
        """
        try:
            return self._search(request)
        except (TypeError, ValueError, KeyError) as error:
            return {"status": "invalid_request", "error": str(error)}

    def _search(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        if set(request) - {"query", "start", "limit"} or "query" not in request:
            raise ValueError("request requires query and accepts only start and limit")
        query = request["query"]
        if not isinstance(query, str) or not query or len(query) > 512:
            raise ValueError("query must be a nonempty string of at most 512 characters")
        start = request.get("start", 0)
        if not _is_int(start) or start < 0 or start > len(self._history):
            raise ValueError("start must be a valid nonnegative history index")
        limit = request.get("limit", 20)
        if not _is_int(limit) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 to 100")

        empty_response = self._search_response(query, start, [], start)
        if _response_size(empty_response) > self.read_bytes:
            raise ValueError("query or cursor is too large for configured read_bytes")

        matches = []
        scanned_to = start
        for index in range(start, len(self._history)):
            record, digest = self._record(index)
            if self._contains_literal(record, query):
                ref = {"index": index, "digest": digest}
                attempt = matches + [ref]
                would_scan_to = index + 1
                response = self._search_response(query, start, attempt, would_scan_to)
                if len(attempt) > limit or _response_size(response) > self.read_bytes:
                    if not matches:
                        raise ValueError('query leaves no room for a result reference; shorten the query')
                    break
                matches = attempt
            scanned_to = index + 1
            if len(matches) >= limit:
                break
        complete = scanned_to >= len(self._history)
        response = self._search_response(query, start, matches, scanned_to)
        response["next_cursor"] = None if complete else scanned_to
        response["complete"] = complete
        return response

    @classmethod
    def _contains_literal(cls, value: Any, query: str) -> bool:
        if isinstance(value, str):
            return query in value
        if isinstance(value, dict):
            return any(query in key or cls._contains_literal(item, query)
                       for key, item in value.items())
        if isinstance(value, list):
            return any(cls._contains_literal(item, query) for item in value)
        return query in canonical(value)

    @staticmethod
    def _search_response(query: str, start: int, matches: list[dict[str, str]],
                         scanned_to: int) -> dict[str, Any]:
        return {"query": query, "matches": matches, "start": start,
                "next_cursor": scanned_to,
                "complete": False,
                "semantics": "case-sensitive literal in string values/keys or scalar JSON; not relevance-ranked"}
