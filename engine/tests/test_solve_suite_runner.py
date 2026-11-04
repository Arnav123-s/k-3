"""The suite runner's --rounds: totals over rounds, and an outage resumes instead of dropping rounds.

A made-up task in a temporary folder stands in for the suite; no suite or hidden file is read.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.runtime import TransportFailure
from kuttaka_runtime.solve import Metered
from tests.test_kuttaka_campaign import local_executor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments' / 'solve_suite'))
import run  # noqa: E402


class RunnerRoundsTests(unittest.TestCase):
    def setUp(self):
        suite = Path(tempfile.mkdtemp())
        (suite / 'tasks.json').write_text(json.dumps([{'id': 't1', 'domain': 'd', 'level': 1,
                                                       'prompt': 'Triple a number.'}]))
        (suite / 'hidden.json').write_text(json.dumps({'t1': [{'input': 4, 'expected': 12}]}))
        saved = {name: getattr(run, name) for name in ('HERE', 'preflight', 'infrastructure_ready',
                                                       'PythonExecutor', 'build_models')}
        self.addCleanup(lambda: [setattr(run, k, v) for k, v in saved.items()])
        run.HERE, run.preflight = suite, lambda: {'ready': True}
        run.infrastructure_ready = lambda wait=0: True
        run.PythonExecutor = lambda **kw: local_executor
        self.state = state = {'phase': 'ok', 'build': 0, 'replied': 0}

        class Model:
            identity = {'model': 'scripted'}
            last_usage = {'input_tokens': 10, 'output_tokens': 5}

            def propose(self, context):
                if state['phase'] == 'outage' and state['build'] == 2:
                    raise TransportFailure('request_timeout')
                state['replied'] += 1
                if 'request' in context:
                    history = context['history']
                    if state['phase'] != 'ok':
                        return {'kind': 'unresolved', 'reason': 'not this time'}
                    if history and 'artifact' in history[-1]['observation']:
                        return {'kind': 'answer', 'answer': {'artifact': history[-1]['observation']['artifact']}}
                    return {'kind': 'experiment', 'tool': 'remember_method',
                            'input': {'name': 'm', 'description': 'm', 'source': 'def run(x):\n    return 3 * x\n'},
                            'predictions': [{'hypothesis': 'stored', 'outcome': 'id'}]}
                if 'failure' in context:
                    return {'subtasks': []}
                if 'inputs' in context:
                    return {'expected': None}
                return {'contract': 'run(x) returns 3*x', 'checks': [{'input': 2, 'expected': 6}], 'input': None}

        def build_models(args, counter):
            state['build'] += 1
            return tuple(Metered(Model(), counter) for _ in range(5))

        run.build_models = build_models
        self.out = Path(tempfile.mkdtemp()) / 'out'
        self.argv = ['--arm', 'fresh', '--out', str(self.out), '--rounds', '3', '--max-calls', '6']

    def test_an_outage_in_a_later_round_resumes_with_the_earlier_rounds_counted(self):
        self.state['phase'] = 'outage'   # round 1 gets replies (the actor gives up); round 2 gets none
        with self.assertRaises(SystemExit):
            run.main(self.argv)
        self.assertFalse((self.out / 'rows.jsonl').exists())
        pending = json.loads((self.out / 'rounds_state.json').read_text())['t1']
        self.assertEqual(pending['next_round'], 2)
        self.assertIsNotNone(pending['resume_contract'], 'a no-reply outage must not erase earlier planning')
        self.assertEqual(pending['resume_contract']['spec']['contract'], 'run(x) returns 3*x')
        self.state.update(phase='ok', build=0)
        run.main(self.argv)
        row = json.loads((self.out / 'rows.jsonl').read_text().splitlines()[0])
        self.assertEqual(row['status'], 'checked')
        # The scored source is pinned to the delivered artifact, so rescoring can recover its exact dependencies.
        from kuttaka_runtime.demo import load_method
        source, _ = load_method(self.out / 'workspaces' / row['task'] / 'memory', row['artifact'])
        self.assertEqual(source, row['source'])
        self.assertEqual(row['replies'], self.state['replied'])   # every replied call is in the row
        self.assertEqual(row['rounds'], 2)
        self.assertGreater(row['outage_calls'], 0)
        self.assertEqual(json.loads((self.out / 'rounds_state.json').read_text()), {})

    def test_rounds_must_be_positive(self):
        with self.assertRaises(SystemExit):
            run.main(self.argv[:-4] + ['--rounds', '0'])

    def test_later_invalid_plans_can_continue_the_previous_rounds_partial(self):
        """Real round wiring must not strand stored work behind a failed planner."""
        from unittest import mock
        from kuttaka_runtime.solve import Solver

        state = {'round': 0}

        class Model:
            identity = {'model': 'scripted-contract-continuity'}
            last_usage = {'input_tokens': 10, 'output_tokens': 5}

            def propose(self, context):
                if 'request' in context:
                    history = context['history']
                    if history:
                        artifact = history[-1]['observation'].get('artifact')
                        if state['round'] > 1 and artifact:
                            return {'kind': 'answer', 'answer': {'artifact': artifact}}
                        return {'kind': 'unresolved', 'reason': 'authored first-round limitation'}
                    source = 'def run(x):\n    return %d * x\n' % (2 if state['round'] == 1 else 3)
                    return {'kind': 'experiment', 'tool': 'remember_method',
                            'input': {'name': 'triple', 'description': 'authored fixture', 'source': source},
                            'predictions': [{'hypothesis': 'stored', 'outcome': 'id'}]}
                if 'failure' in context:
                    return {'subtasks': []}
                if 'inputs' in context:
                    return {'expected': [3 * value for value in context['inputs']]}
                if state['round'] > 1:
                    return {'contract': None, 'checks': 'authored malformed reply'}
                return {'contract': 'run(x) returns 3*x', 'input': None,
                        'checks': [{'input': value, 'expected': 3 * value} for value in (0, 2, 4)]}

        def build_models(args, counter):
            state['round'] += 1
            return tuple(Metered(Model(), counter) for _ in range(5))

        run.build_models = build_models
        args = ['--arm', 'fresh', '--out', str(self.out), '--rounds', '2', '--max-calls', '8']
        # Same scripted capabilities and budget, with only continuation disabled.
        with mock.patch.object(Solver, 'resume_contract', return_value=None):
            run.main(args)
        baseline = json.loads((self.out / 'rows.jsonl').read_text().splitlines()[0])
        self.assertEqual(baseline['status'], 'needs_examples')
        self.assertFalse(baseline['solved'])

        state['round'] = 0
        fixed_out = self.out.parent / 'with-contract'
        args[args.index('--out') + 1] = str(fixed_out)
        run.main(args)
        fixed = json.loads((fixed_out / 'rows.jsonl').read_text().splitlines()[0])
        self.assertEqual(fixed['status'], 'checked', fixed)
        self.assertTrue(fixed['solved'])
        self.assertEqual(fixed['rounds'], 2)
        self.assertLessEqual(fixed['model_calls'], 16)
        self.assertLessEqual(fixed['execution_attempts'], 512)


if __name__ == '__main__':
    unittest.main()


class ScoreOneTests(unittest.TestCase):
    def test_a_delivered_method_is_scored_on_the_hidden_cases(self):
        import score_one
        from kuttaka_runtime.solve import Solver, Workspace
        suite = Path(tempfile.mkdtemp())
        (suite / 'hard_hidden.json').write_text(json.dumps({'t1': [{'input': 4, 'expected': 12},
                                                                   {'input': 0, 'expected': 0}]}))
        saved = run.HERE
        self.addCleanup(setattr, run, 'HERE', saved)
        run.HERE = suite
        ws = Workspace(tempfile.mkdtemp())
        solver = Solver(ws, None, None, local_executor, say=lambda _: None)
        right = solver.admit('Triple.', [{'input': 1, 'expected': 3}], 'def run(x):\n    return 3 * x\n', 'm')
        wrong = solver.admit('Triple.', [{'input': 1, 'expected': 3}], 'def run(x):\n    return x + 2\n', 'm')
        self.assertTrue(score_one.score('hard', 't1', ws.memory, right['artifact'], local_executor)['solved'])
        low = score_one.score('hard', 't1', ws.memory, wrong['artifact'], local_executor)
        self.assertEqual((low['hidden_passed'], low['solved']), (0, False))


class WinsTests(unittest.TestCase):
    def test_every_task_is_classified_and_losses_are_kept(self):
        import wins
        base = Path(tempfile.mkdtemp())

        def arm(name, rows):
            folder = base / name
            folder.mkdir()
            (folder / 'rows.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
            return folder

        def row(task, status, passed, source='def run(x):\n    return x\n'):
            return {'task': task, 'level': 1, 'status': status, 'hidden_passed': passed, 'hidden_cases': 2,
                    'solved': passed == 2, 'model_calls': 3, 'source': source}
        with_dir = arm('with', [row('a', 'checked', 2), row('b', 'incomplete', 0, None), row('c', 'checked', 2),
                                row('d', 'checked', 1)])
        alone_dir = arm('alone', [row('a', 'delivered', 1), row('b', 'delivered', 2), row('c', 'delivered', 2),
                                  row('d', 'no_code', 0, None)])
        out = base / 'wins.md'
        wins.main(['--with', str(with_dir), '--alone', str(alone_dir), '--out', str(out)])
        report = json.loads(out.with_suffix('.json').read_text())
        self.assertEqual([t['outcome'] for t in report['tasks']], ['WIN', 'LOSS', 'BOTH', 'NEITHER'])
        self.assertEqual(report['totals']['wrong_delivered_with'], 1)    # d: checked but 1/2
        self.assertEqual(report['totals']['wrong_delivered_alone'], 1)   # a: delivered but 1/2
        self.assertIn('**LOSS**', out.read_text())
