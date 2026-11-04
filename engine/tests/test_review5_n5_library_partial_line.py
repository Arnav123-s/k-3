"""N5. R8 says the library "can be read while a solve is writing": shastra reads the memory's event log
tolerating a line still being written, but it reads solved.jsonl, refuted.jsonl, lessons.jsonl and
history.jsonl through Workspace._read, which json-parses every line and raises on a partial one. A solve
appends large rows there (a solved row carries its evidence, keyed by every check's input and expected
value). (a) deterministic: a solved.jsonl whose last row is still being written makes build() raise;
(b) a real writer process appending rows while build() runs: GET /library / the MCP tool fail."""
import json
import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path

from kuttaka_runtime.shastra import build
from kuttaka_runtime.solve import Workspace


def writer(root, rows, stop):
    ws = Workspace(Path(root))
    big = {'checks': {json.dumps([i, 'x' * 2000]): 'two derivations' for i in range(60)}}   # ~130 KB evidence
    for n in range(rows):
        if stop.is_set():
            break
        ws.remember_solved('Task %d.' % n, 'contract', '%064x' % n, evidence=big)


class N5LibraryReadsPartialRows(unittest.TestCase):
    def test_a_row_being_written_makes_the_library_raise(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        ws.remember_solved('Square a number.', 'run(x) returns x * x', 'a' * 64)
        with ws.solved_path.open('a') as handle:
            handle.write('{"artifact":"' + 'b' * 64 + '","contract":"run(x) ret')   # an append in progress
        try:
            build(ws)
        except ValueError as error:
            self.fail('the library cannot be read while a solve is writing: %r' % error)

    def test_a_concurrent_writer_breaks_the_library_read(self):
        root = tempfile.mkdtemp()
        stop = multiprocessing.Event()
        process = multiprocessing.Process(target=writer, args=(root, 400, stop))
        process.start()
        errors, reads, deadline = [], 0, time.time() + 20
        ws = Workspace(Path(root))
        try:
            while process.is_alive() and time.time() < deadline and not errors:
                try:
                    build(ws)
                    reads += 1
                except ValueError as error:
                    errors.append(repr(error)[:120])
        finally:
            stop.set()
            process.join(30)
        print('\nlibrary reads:', reads, '| failed reads:', errors[:1])
        self.assertFalse(errors, 'a library read during a solve failed')


if __name__ == '__main__':
    unittest.main()
