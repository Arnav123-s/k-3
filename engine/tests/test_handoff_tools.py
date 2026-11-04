"""wins.py --rescored and read_journals.py, on folders this test writes (no results branch, no hidden case)."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'experiments' / 'solve_suite'))
import climb  # noqa: E402
import read_journals  # noqa: E402
import wins  # noqa: E402


def arm(rows, rescored=None):
    folder = Path(tempfile.mkdtemp())
    (folder / 'rows.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    if rescored is not None:
        (folder / 'rescored.json').write_text(json.dumps({'tasks': rescored}))
    return folder


def row(task, solved, passed, cases, status='checked'):
    return {'task': task, 'level': 7, 'status': status, 'solved': solved, 'hidden_passed': passed,
            'hidden_cases': cases, 'source': 'def run(x):\n    return x\n', 'model_calls': 1}


class WinsRescored(unittest.TestCase):
    def test_rescored_counts_replace_as_run_ones_and_the_control_keeps_them(self):
        with_dir = arm([row('t', False, 5, 7)], [{'task': 't', 'passed_rescored': 7, 'solved_rescored': True}])
        alone_dir = arm([row('t', False, 5, 7, 'delivered')], [{'task': 't', 'passed_rescored': 5, 'solved_rescored': False}])
        out = Path(tempfile.mkdtemp()) / 'w.md'
        with redirect_stdout(io.StringIO()):
            wins.main(['--with', str(with_dir), '--alone', str(alone_dir), '--out', str(out), '--rescored'])
        self.assertEqual(json.loads(out.with_suffix('.json').read_text())['totals']['wins'], 1)
        self.assertIn('rescored.json', out.read_text())
        with redirect_stdout(io.StringIO()):
            wins.main(['--with', str(with_dir), '--alone', str(alone_dir), '--out', str(out)])   # control: as run
        self.assertEqual(json.loads(out.with_suffix('.json').read_text())['totals']['neither'], 1)

    def test_a_missing_rescored_file_stops(self):
        with self.assertRaises(SystemExit):
            wins.load(arm([row('t', True, 1, 1)]), rescored=True)


class ReadJournals(unittest.TestCase):
    def test_events_and_model_calls_are_listed_without_executions(self):
        folder = arm([row('t', True, 1, 1)])
        run = folder / 'workspace' / 'runs' / '2026-11-01T010203Z-abc-def'
        solve = folder / 'workspace' / 'runs' / 'solve-1'
        run.mkdir(parents=True)
        solve.mkdir(parents=True)
        events = [{'kind': 'solve_started', 'body': {'request': 'Square a number.'}},
                  {'kind': 'solve_execution_intent', 'body': {'number': 1}},
                  {'kind': 'solve_attempt_finished', 'body': {'run': 'results/x/workspace/runs/' + run.name}}]
        (solve / 'solve.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
        (run / 'events.jsonl').write_text(json.dumps({'kind': 'model_reply', 'body': {
            'usage': {'output_tokens': 16384, 'finish_reason': 'length'}, 'action': {'kind': 'invalid'}}}) + '\n')
        text = io.StringIO()
        with redirect_stdout(text):
            read_journals.main([str(folder)])
        printed = text.getvalue()
        self.assertIn('Square a number.', printed)
        self.assertNotIn('solve_execution_intent', printed)
        self.assertIn('16384 length invalid', printed)


def journal_task(folder, n, request, attempts):
    """A solve journal and its attempt journals: attempts = [(status, artifact, methods written, refuted)]."""
    events = [{'kind': 'solve_started', 'body': {'request': request}}]
    for i, (status, artifact, methods, refuted) in enumerate(attempts):
        name = f'2026-11-01T0{n}{i}000Z-a-b'
        run = folder / 'workspace' / 'runs' / name
        run.mkdir(parents=True)
        replies = [{'kind': 'model_reply', 'body': {'action': {'kind': 'experiment', 'tool': 'remember_method'}}}] * methods
        (run / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in replies))
        events.append({'kind': 'solve_attempt_finished',
                       'body': {'status': status, 'artifact': artifact, 'run': 'results/x/workspace/runs/' + name}})
        if refuted:
            events.append({'kind': 'solve_refuted', 'body': {'artifact': artifact}})
    solve = folder / 'workspace' / 'runs' / f'solve-{n}'
    solve.mkdir(parents=True)
    (solve / 'solve.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))


class Climb(unittest.TestCase):
    def test_each_kind_of_climb_is_told_apart(self):
        rows = [row('first', True, 1, 1), row('climbed', True, 1, 1), row('false', False, 0, 1),
                row('within', True, 1, 1), row('failed', False, 0, 1, 'incomplete')]
        folder = arm(rows)
        journal_task(folder, 1, 'first', [('checked', 'a1', 1, False)])
        journal_task(folder, 2, 'climbed', [('incomplete', None, 1, False), ('checked', 'a2', 1, False)])
        journal_task(folder, 3, 'false', [('checked', 'a3', 1, False)])
        journal_task(folder, 4, 'within', [('checked', 'a4', 3, False)])
        journal_task(folder, 5, 'failed', [('checked', 'a5', 1, True), ('incomplete', None, 2, False)])
        result = climb.report(folder)
        self.assertEqual([t['class'] for t in result['tasks']],
                         ['FIRST_TRY', 'CLIMBED', 'FALSE_STOP', 'CLIMBED_IN_ATTEMPT', 'CLIMB_FAILED'])
        self.assertEqual(result['tasks'][4]['first'], 'refuted')
        self.assertEqual(result['tasks'][4]['methods'], 3)


if __name__ == '__main__':
    unittest.main()
