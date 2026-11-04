"""A killed writer leaves no lock behind (found from W2-b's h-r4-collide, 2026-11-01).

The runner's process was killed during a task; on resume every open of the workspace's memory raised
FileExistsError, because the lock was the existence of `.writer.lock`, which only a clean close removed.
The lock is now an OS lock on that file, released when its process ends. A live writer still excludes
every other (the control). Only test-written code runs here (a child Python that opens the memory).
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from kuttaka_runtime.artifacts import ArtifactMemory

ROOT = Path(__file__).resolve().parents[1]

CHILD = '''import os, sys, time
sys.path.insert(0, sys.argv[1])
from kuttaka_runtime.artifacts import ArtifactMemory
memory = ArtifactMemory(sys.argv[2])
print("open", flush=True)
if sys.argv[3] == "die":
    os._exit(9)          # no close, no __exit__: as a killed process
time.sleep(30)           # a live writer, holding the memory
'''


def child(path, mode):
    process = subprocess.Popen([sys.executable, '-I', '-c', CHILD, str(ROOT), str(path), mode],
                               stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == 'open'
    return process


class KilledWriter(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / 'memory'

    def test_the_memory_opens_after_its_writer_was_killed(self):
        process = child(self.path, 'die')
        process.wait(timeout=30)
        self.assertNotEqual(process.returncode, 0)
        self.assertTrue((self.path / '.writer.lock').exists())   # the file stays; the lock does not
        with ArtifactMemory(self.path) as memory:
            self.assertEqual(memory.stats()['events'], 0)

    def test_control_a_live_writer_still_excludes_another(self):
        process = child(self.path, 'live')
        try:
            with self.assertRaises(FileExistsError):
                ArtifactMemory(self.path)
        finally:
            process.kill()
            process.wait(timeout=30)
        deadline = time.monotonic() + 10
        while True:   # once that process is gone, its lock is gone with it
            try:
                ArtifactMemory(self.path).close()
                break
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def test_a_second_open_in_the_same_process_is_refused_and_close_releases(self):
        first = ArtifactMemory(self.path)
        try:
            with self.assertRaises(FileExistsError):
                ArtifactMemory(self.path)
        finally:
            first.close()
        ArtifactMemory(self.path).close()

    def test_a_stale_lock_file_from_the_old_rule_is_no_lock(self):
        # A workspace left by a writer killed before this change: the file exists, nobody holds it.
        self.path.mkdir(parents=True)
        (self.path / '.writer.lock').write_text('0123456789abcdef', encoding='ascii')
        with ArtifactMemory(self.path) as memory:
            self.assertEqual(memory.stats()['events'], 0)


if __name__ == '__main__':
    unittest.main()
