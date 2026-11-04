"""A revision snapshots closed memory without racing a live writer."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from experiments.astra_live import revise
from kuttaka_runtime.artifacts import ArtifactMemory


class RevisionSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.campaign = Path(self.tmp.name)
        acquired = self.campaign / 'stable-unique'
        self.memory = acquired / 'memory'
        (acquired / 'acquire').mkdir(parents=True)
        (acquired / 'acquire/result.json').write_text(json.dumps({
            'status': 'checked', 'answer': {'artifact': 'a' * 64}}))
        with ArtifactMemory(self.memory):
            pass
        self.output = self.campaign / 'revision'

    def test_closed_memory_can_be_copied_and_lock_is_not_copied(self):
        # A missing source tree stops run immediately after the memory snapshot,
        # before any revision subprocess or model could start.
        self.assertTrue((self.memory / '.writer.lock').exists())
        with self.assertRaises(FileNotFoundError):
            revise.run(self.campaign, self.output)
        snapshot = self.output / 'memory'
        self.assertEqual((snapshot / 'events.jsonl').read_bytes(),
                         (self.memory / 'events.jsonl').read_bytes())
        self.assertFalse((snapshot / '.writer.lock').exists())
        with ArtifactMemory(self.memory), ArtifactMemory(snapshot):
            pass

    def test_live_writer_is_refused_without_creating_output(self):
        with ArtifactMemory(self.memory):
            with self.assertRaisesRegex(ValueError, 'source memory writer'):
                revise.run(self.campaign, self.output)
        self.assertFalse(self.output.exists())

    def test_writer_is_refused_during_copy_and_allowed_after(self):
        copytree = shutil.copytree
        tested = []

        def copying(source, destination, *args, **kwargs):
            if Path(source) == self.memory:
                with self.assertRaises(FileExistsError):
                    ArtifactMemory(self.memory)
                tested.append(True)
            return copytree(source, destination, *args, **kwargs)

        with mock.patch.object(revise.shutil, 'copytree', side_effect=copying):
            with self.assertRaises(FileNotFoundError):
                revise.run(self.campaign, self.output)
        self.assertEqual(tested, [True])
        with ArtifactMemory(self.memory):
            pass

    def test_copy_failure_releases_source_lock(self):
        with mock.patch.object(revise.shutil, 'copytree', side_effect=OSError('copy failed')):
            with self.assertRaisesRegex(OSError, 'copy failed'):
                revise.run(self.campaign, self.output)
        self.assertTrue((self.memory / '.writer.lock').exists())
        with ArtifactMemory(self.memory):
            pass


if __name__ == '__main__':
    unittest.main()
