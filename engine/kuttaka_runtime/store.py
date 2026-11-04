"""Single-writer evidence journal; hash integrity does not establish truth."""

import hashlib
import fcntl
import json
import os
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Journal:
    """Exclusive append ownership. Existing journals are inspectable, never overwritten.

    A persistent sidecar lock file coordinates cooperating writers. The OS lock,
    rather than the sidecar's existence, represents ownership and is released on
    close or process exit.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._closed = False
        self._acquire_lock()
        try:
            self._stream = self.path.open('x', encoding='utf-8')
            self._events = []
        except BaseException:
            self._release_lock()
            raise

    @classmethod
    def resume(cls, path, *, expected_head):
        """Continue an existing intact journal only at the caller's expected tail.

        The exclusive nonblocking lock is acquired before checking the file. No
        repair or truncation is attempted; any validation failure leaves it intact.
        """
        self = cls.__new__(cls)
        self.path = Path(path)
        self._closed = False
        self._acquire_lock()
        try:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            if (not isinstance(expected_head, str)
                    or (expected_head and (len(expected_head) != 64
                        or any(ch not in '0123456789abcdef' for ch in expected_head)))):
                raise ValueError('expected_head must be empty or a lowercase SHA-256 hash')
            self._events = cls.read(self.path)
            actual_head = self._events[-1]['hash'] if self._events else ''
            if actual_head != expected_head:
                raise ValueError('journal head does not match expected_head')
            self._stream = self.path.open('a', encoding='utf-8', newline='\n')
            return self
        except BaseException:
            self._release_lock()
            raise

    def _acquire_lock(self):
        lock_path = Path(str(self.path) + '.lock')
        self._lock_stream = lock_path.open('a+', encoding='ascii')
        try:
            fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._lock_stream.close()
            self._lock_stream = None
            raise RuntimeError(f'journal is already open for writing: {self.path}') from error
        except BaseException:
            self._lock_stream.close()
            self._lock_stream = None
            raise

    def _release_lock(self):
        lock_stream = getattr(self, '_lock_stream', None)
        if lock_stream is not None:
            self._lock_stream = None
            try:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
            finally:
                lock_stream.close()

    def append(self, kind, body):
        # Copy through JSON: callers cannot subsequently change committed evidence.
        body = json.loads(canonical(body))
        event = dict(seq=len(self._events), kind=kind, body=body,
                     prev=self._events[-1]['hash'] if self._events else '')
        event['hash'] = digest(event)
        self._stream.write(canonical(event) + '\n')
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._events.append(event)
        return event['hash']

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.close()
        finally:
            self._release_lock()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def read(path):
        events = []
        with Path(path).open(encoding='utf-8') as stream:
            for line in stream:
                if not line.endswith('\n'):
                    raise ValueError('incomplete journal record')
                event = json.loads(line)
                if set(event) != {'seq', 'kind', 'body', 'prev', 'hash'}:
                    raise ValueError('invalid journal record')
                payload = {k: v for k, v in event.items() if k != 'hash'}
                if (type(event['seq']) is not int or event['seq'] != len(events)
                        or event['prev'] != (events[-1]['hash'] if events else '')
                        or event['hash'] != digest(payload)):
                    raise ValueError('journal integrity failure')
                events.append(event)
        return events
