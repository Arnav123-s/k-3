import io
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.campaign import Ceiling, run_campaign
from kuttaka_runtime.demo import show
from kuttaka_runtime.demo_page import render
from kuttaka_runtime.runtime import Budget
from tests.test_kuttaka_campaign import STREAM, Scripted, local_executor

HOLDOUT = {'t1': [{'input': [12, 18], 'expected': 6}, {'input': [9, 6], 'expected': 3}],
           't2': [{'input': [21, 6], 'expected': 42}, {'input': [7, 3], 'expected': 21}]}


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.run = Path(tempfile.mkdtemp()) / 'KUTTAKA'
        run_campaign(STREAM, self.run, 'KUTTAKA', Ceiling(Scripted(), 50), budget=Budget(6, 12),
                     executor=local_executor)

    def demo(self, holdout=HOLDOUT):
        text = io.StringIO()
        return show(self.run, holdout, executor=local_executor, out=text), text.getvalue()

    def test_reuse_by_id_is_shown_from_executed_dependency_calls(self):
        summary, text = self.demo()
        self.assertEqual(summary['journals_intact'], 2)
        self.assertEqual(summary['holdout_solved'], 2)
        self.assertEqual(summary['reused_by_id'], 1)
        self.assertIn('a method the model wrote on t1', text)

    def test_html_page_shows_the_reuse_and_escapes_model_text(self):
        records = []
        show(self.run, HOLDOUT, executor=local_executor, out=io.StringIO(), records=records)
        records[1]['request'] = '<script>x</script>'
        page = render(records)
        self.assertIn('Reuse by id', page)
        self.assertIn('the method it wrote on <b>t1</b>', page)
        self.assertNotIn('<script>x</script>', page)

    def test_wrong_hidden_expectation_is_reported_unsupported(self):
        wrong = dict(HOLDOUT, t2=[{'input': [21, 6], 'expected': 41}])
        summary, text = self.demo(wrong)
        self.assertEqual(summary['unsupported'], 1)
        self.assertIn('UNSUPPORTED', text)

    def test_tampered_journal_is_detected(self):
        journal = self.run / 'runs' / 't1' / 'events.jsonl'
        lines = journal.read_text().splitlines(True)
        event = json.loads(lines[0])
        event['body']['request'] = 'changed'
        lines[0] = json.dumps(event) + '\n'
        journal.write_text(''.join(lines))
        summary, text = self.demo()
        self.assertEqual(summary['journals_intact'], 1)
        self.assertIn('FAILED integrity', text)

    def test_task_never_started_at_the_ceiling_is_not_an_integrity_failure(self):
        self.run = self.run.parent / 'capped'
        run_campaign(STREAM, self.run, 'KUTTAKA', Ceiling(Scripted(), 3), budget=Budget(6, 12),
                     executor=local_executor)
        summary, text = self.demo()
        self.assertEqual(summary['journals_intact'], summary['tasks'])
        self.assertNotIn('FAILED integrity', text)


if __name__ == '__main__':
    unittest.main()
