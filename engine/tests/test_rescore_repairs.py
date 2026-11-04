"""Rescoring uses artifact dependencies and reports unavailable evidence separately.

All methods and scoring cases in this file are test-authored; no evaluation files are read.
"""

import json
import tempfile
import unittest
from pathlib import Path

from experiments.solve_suite import rescore
from kuttaka_runtime.artifacts import ArtifactMemory
from tests.test_kuttaka_campaign import local_executor


def method(source, dependencies=(), name='method'):
    return {'kind': 'method', 'body': {'name': name, 'description': name, 'source': source},
            'dependencies': list(dependencies), 'provenance': {'producer': 'test'}, 'applicability': {}}


class RescoreRepairs(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.out = Path(self.scratch.name)

    def score(self, rows, cases, executor=local_executor):
        (self.out / 'rows.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return rescore.rescore(self.out, cases, executor)

    def test_unverified_method_recovers_transitive_dependencies_without_solved_log(self):
        memory_dir = self.out / 'workspace' / 'memory'
        with ArtifactMemory(memory_dir) as memory:
            double = memory.put(method('def run(x):\n    return 2*x\n'))
            increment_source = f'def run(x):\n    return use("{double}")(x) + 1\n'
            increment = memory.put(method(increment_source, [double]))
            held_source = f'def run(x):\n    return use("{increment}")(x) * 3\n'
            memory.put(method(held_source, [increment]))
        before = (memory_dir / 'events.jsonl').read_bytes()
        report = self.score([{'task': 'held', 'status': 'incomplete', 'source': None,
                              'unverified_source': held_source, 'reused': [], 'subtasks': 0,
                              'hidden_passed': 0, 'solved': False}],
                            {'held': [{'input': 2, 'expected': 15}, {'input': 5, 'expected': 33}]})
        self.assertEqual(report['tasks'][0]['unverified_passed_rescored'], 2)
        self.assertTrue(report['summary']['complete'])
        self.assertEqual(report['summary']['unverified_rescored'], 1)
        self.assertEqual(report['summary']['solved_with_unverified_rescored'], 1)
        self.assertEqual((memory_dir / 'events.jsonl').read_bytes(), before)
        self.assertFalse((self.out / 'workspace' / 'solved.jsonl').exists())

    def test_fresh_workspace_is_also_indexed(self):
        with ArtifactMemory(self.out / 'workspaces' / 'fresh' / 'memory') as memory:
            identity = memory.put(method('def run(x):\n    return x\n'))
            source = f'def run(x):\n    return use("{identity}")(x)\n'
            memory.put(method(source, [identity]))
        libs, available = rescore.libraries_for(self.out, {'task': 'fresh'}, source)
        self.assertTrue(available)
        self.assertEqual(set(libs), {identity})

    def test_missing_libraries_never_execute_and_unknown_coverage_is_visible(self):
        source = 'def run(x):\n    return use("' + 'f' * 64 + '")(x)\n'
        calls = []

        def record(job):
            calls.append(job)
            return local_executor(job)

        # Cleared reused/subtasks fields reproduce the held-back-answer path.
        row = {'task': 'missing', 'status': 'checked', 'source': source, 'reused': [], 'subtasks': 0,
               'hidden_passed': 1, 'solved': True}
        report = self.score([row], {'missing': [{'input': 1, 'expected': 1}]}, record)
        self.assertEqual(calls, [])
        self.assertNotIn('passed_rescored', report['tasks'][0])
        self.assertNotIn('solved_rescored', report['tasks'][0])
        self.assertEqual(report['summary']['not_rescored'], 1)
        self.assertEqual(report['summary']['checked_not_rescored'], 1)
        self.assertEqual(report['summary']['solved_rescored'], 0)
        self.assertEqual(report['summary']['unsupported_rescored'], 0)
        self.assertFalse(report['summary']['complete'])

    def test_standalone_success_and_failure_are_both_observed(self):
        rows = [{'task': task, 'status': 'checked', 'source': source, 'hidden_passed': 0, 'solved': False}
                for task, source in [('right', 'def run(x):\n    return x * 2\n'),
                                     ('wrong', 'def run(x):\n    return x + 2\n')]]
        report = self.score(rows, {task: [{'input': 3, 'expected': 6}] for task in ('right', 'wrong')})
        summary = report['summary']
        self.assertEqual((summary['rescored'], summary['not_rescored']), (2, 0))
        self.assertEqual((summary['solved_rescored'], summary['unsupported_rescored']), (1, 1))
        self.assertTrue(summary['complete'])

    def test_ambiguous_source_with_different_closures_is_not_guessed(self):
        source = 'def run(x):\n    return x\n'
        with ArtifactMemory(self.out / 'workspace' / 'memory') as memory:
            unused = memory.put(method('def run(x):\n    return x*2\n'))
            memory.put(method(source, name='first'))
            memory.put(method(source, [unused], name='second'))
        self.assertEqual(rescore.libraries_for(self.out, {'task': 't'}, source), (None, False))

    def test_artifact_id_resolves_source_ambiguity_but_a_mismatch_is_not_scored(self):
        source = 'def run(x):\n    return x\n'
        with ArtifactMemory(self.out / 'workspace' / 'memory') as memory:
            other = memory.put(method('def run(x):\n    return x*2\n'))
            exact = memory.put(method(source, name='first'))
            memory.put(method(source, [other], name='second'))
        row = {'task': 'exact', 'status': 'checked', 'source': source, 'artifact': exact}
        cases = {'exact': [{'input': 4, 'expected': 4}]}
        report = self.score([row], cases)
        self.assertEqual(report['tasks'][0]['passed_rescored'], 1)
        row['artifact'] = other
        report = self.score([row], cases)
        self.assertNotIn('passed_rescored', report['tasks'][0])
        self.assertFalse(report['summary']['complete'])
        # A missing declared identity is not silently replaced by another source match.
        row['artifact'] = 'f' * 64
        self.assertFalse(self.score([row], cases)['summary']['complete'])

    def test_unverified_artifact_identity_is_checked_separately(self):
        with ArtifactMemory(self.out / 'workspace' / 'memory') as memory:
            wrong = memory.put(method('def run(x):\n    return 0\n'))
        row = {'task': 'held', 'status': 'incomplete', 'unverified_source': 'def run(x):\n    return x\n',
               'unverified_artifact': wrong}
        report = self.score([row], {'held': [{'input': 4, 'expected': 4}]})
        self.assertNotIn('unverified_passed_rescored', report['tasks'][0])
        self.assertEqual(report['summary']['unverified_not_rescored'], 1)
        self.assertFalse(report['summary']['complete'])

    def test_empty_cases_do_not_prove_a_delivered_method_solved(self):
        row = {'task': 'empty', 'status': 'checked', 'source': 'def run(x):\n    return x\n'}
        report = self.score([row], {'empty': []})
        self.assertNotIn('solved_rescored', report['tasks'][0])
        self.assertEqual(report['summary']['not_rescored'], 1)
        self.assertFalse(report['summary']['complete'])

    def test_legacy_limit_accounts_for_library_exemption(self):
        source = 'def run(x):\n    return len(x)\n'
        cases = [{'input': 'a' * 40000, 'expected': 40000}]
        self.assertEqual(rescore.over_legacy(source, cases), 1)
        self.assertEqual(rescore.over_legacy(source, cases, {'a' * 64: source}), 0)
        self.assertEqual(rescore.over_legacy(source, [{'input': '', 'expected': 0}]), 0)


if __name__ == '__main__':
    unittest.main()
