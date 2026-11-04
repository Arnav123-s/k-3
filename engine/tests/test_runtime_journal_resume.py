"""Exclusive expected-head continuation for hash-chained journals."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.store import Journal


class JournalResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'events.jsonl'

    def tearDown(self):
        self.tmp.cleanup()

    def make_journal(self):
        journal = Journal(self.path)
        head = journal.append('first', {'value': 1})
        journal.close()
        return head

    def test_reopen_in_another_process_continues_verified_prefix(self):
        head = self.make_journal()
        script = (
            'import sys; from kuttaka_runtime.store import Journal; '
            'j=Journal.resume(sys.argv[1], expected_head=sys.argv[2]); '
            'print(j.append("second", {"value": 2})); j.close()'
        )
        proc = subprocess.run([sys.executable, '-c', script, str(self.path), head],
                              capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        events = Journal.read(self.path)
        self.assertEqual([event['kind'] for event in events], ['first', 'second'])
        self.assertEqual(events[1]['seq'], 1)
        self.assertEqual(events[1]['prev'], head)
        self.assertEqual(events[1]['hash'], proc.stdout.strip())

    def test_empty_journal_can_be_resumed_at_empty_head(self):
        with Journal(self.path):
            pass
        with Journal.resume(self.path, expected_head='') as journal:
            head = journal.append('first', {})
        self.assertEqual(Journal.read(self.path)[0]['hash'], head)

    def test_stale_head_rejects_without_changing_journal(self):
        actual = self.make_journal()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'expected_head'):
            Journal.resume(self.path, expected_head='0' * 64)
        self.assertEqual(self.path.read_bytes(), before)
        with Journal.resume(self.path, expected_head=actual):
            pass  # Failed validation released the lock.

    def test_corrupt_and_partial_journals_reject_without_repair(self):
        for contents in (b'{"bad":true}\n', b'{"partial":true}'):
            with self.subTest(contents=contents):
                self.path.write_bytes(contents)
                before = self.path.read_bytes()
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    Journal.resume(self.path, expected_head='')
                self.assertEqual(self.path.read_bytes(), before)
                # Rejection must release the lock.
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    Journal.resume(self.path, expected_head='')

    def test_missing_file_rejects_without_creating_a_journal(self):
        with self.assertRaises(FileNotFoundError):
            Journal.resume(self.path, expected_head='')
        self.assertFalse(self.path.exists())

    def test_existing_writer_blocks_resume_and_close_releases_lock(self):
        writer = Journal(self.path)
        head = writer.append('first', {})
        with self.assertRaisesRegex(RuntimeError, 'already open'):
            Journal.resume(self.path, expected_head=head)
        writer.close()
        writer.close()
        with Journal.resume(self.path, expected_head=head):
            pass

    def test_second_resume_blocks_while_first_resume_is_open(self):
        head = self.make_journal()
        first = Journal.resume(self.path, expected_head=head)
        try:
            with self.assertRaisesRegex(RuntimeError, 'already open'):
                Journal.resume(self.path, expected_head=head)
        finally:
            first.close()
        with Journal.resume(self.path, expected_head=head):
            pass

    def test_new_writer_cannot_overwrite_existing_journal(self):
        self.make_journal()
        with self.assertRaises(FileExistsError):
            Journal(self.path)
        self.assertEqual([event['kind'] for event in Journal.read(self.path)], ['first'])


if __name__ == '__main__':
    unittest.main()
