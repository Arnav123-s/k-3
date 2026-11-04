"""Review 15 regressions using authored programs and scripted models only."""
import json
import tempfile
import unittest
from unittest import mock

from kuttaka_runtime.demo import load_method
from kuttaka_runtime.execution import INPUT_LIMIT
from kuttaka_runtime.solve import MAX_PROBES, Workspace, _probe_batches, settings, solve_task
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, make_solver, store


TRI = lambda n: n * (n + 1) // 2
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'
WRONG = 'def run(n):\n    return n * (n + 1) // 2 if n < 4 or n == 5 else n * n\n'
PROPERTY = 'def verify(n, y):\n    return y == n * (n + 1) // 2\n'
PICKY = 'def verify(n, y):\n    return y == n * (n + 1) // 2 and n != 5\n'
SHARED_ERROR = 'def verify(n, y):\n    return y == n * (n + 1) // 2 + (1 if n >= 5 else 0)\n'


def triangular_run(prop, sources, audit, reference=None):
    writes = []

    def act(request):
        writes.append(request)
        return store(sources[min(len(writes) - 1, len(sources) - 1)])

    plan = lambda _: {'contract': 'run(n) returns the n-th triangular number', 'input': None,
                      'checks': [check(1, 1), check(2, 3), check(3, 6)],
                      'verify': prop, 'reference': reference}
    solver, ws = make_solver(Model(plan, act, audit=audit), max_calls=30)
    return solver.solve('The n-th triangular number.'), ws, writes


class ProbeCoverage(unittest.TestCase):
    def test_discarded_small_probe_does_not_dedupe_away_the_requested_input(self):
        spec = {'checks': [check(0, 0)], 'small_inputs': list(range(1, MAX_PROBES + 5)),
                'input': MAX_PROBES + 2}
        with mock.patch('kuttaka_runtime.pramana.near_inputs', return_value=[]), \
                mock.patch('kuttaka_runtime.pramana.varied_inputs', return_value=[]):
            batches = _probe_batches(spec)
        self.assertEqual(len(batches[0]), MAX_PROBES)
        self.assertEqual(batches[-1], [spec['input']])
        self.assertEqual(sum(batch.count(spec['input']) for batch in batches), 1)

    def test_a_field_preserves_grammar_and_digit_challenges_within_the_probe_cap(self):
        original = {'text': 'a*'}
        values = [value for batch in _probe_batches({'checks': [check(original, 0)], 'input': None})
                  for value in batch]
        self.assertIn({'text': 'a**'}, values)
        self.assertIn({'text': 'a0*'}, values)
        self.assertNotIn(original, values)
        self.assertTrue(all(set(value) == {'text'} for value in values))

    def test_requested_input_is_audited_before_six_out_of_domain_disputes(self):
        words = ['abc', 'banana', 'kayak', 'lemon', 'piano', 'tiger']
        asked = {'s': 'ab' * 16}
        right = ('def run(i):\n    s = i["s"]\n'
                 '    if any(a == b for a, b in zip(s, s[1:])):\n        return -1\n'
                 '    return len(s)\n')
        wrong = right.replace('    return len(s)\n', '    return len(s) + (1 if len(s) > 30 else 0)\n')

        def audit(context):
            return {'expected': [len(i['s']) if i['s'].islower() and i['s'].isalpha()
                                 and all(a != b for a, b in zip(i['s'], i['s'][1:])) else None
                                 for i in context['inputs']]}

        for oracle, source in [('reference', 'def reference(i):\n    return len(i["s"])\n'),
                               ('verify', 'def verify(i, y):\n    return y == len(i["s"])\n')]:
            with self.subTest(oracle=oracle):
                writes = []

                def act(request):
                    writes.append(request)
                    return store(wrong if len(writes) == 1 else right)

                plan = lambda _: {'contract': 'run(i) returns the length of lower-case s with no doubled letter',
                                  'input': asked, 'checks': [check({'s': w}, len(w)) for w in words], oracle: source}
                solver, _ = make_solver(Model(plan, act, audit=audit), max_calls=30)
                result = solver.solve('Return the length of a word with no doubled letters.')
                self.assertEqual((result['status'], result['answer']), ('checked', 32))
                self.assertEqual(len(writes), 2)
                kind = 'solve_differential' if oracle == 'reference' else 'solve_property_test'
                disagreements = [e for e in events(result, kind) if e.get('outcome') == 'disagree']
                self.assertEqual(disagreements[0]['disputed_inputs'][0], asked)


class PropertyEvidence(unittest.TestCase):
    def test_a_discredited_property_cannot_refute_or_poison_memory(self):
        result, ws, _ = triangular_run(SHARED_ERROR, [RIGHT],
            lambda c: {'expected': [TRI(n) + (1 if n == 7 else 0) for n in c['inputs']]})
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(load_method(ws.memory, result['unverified']['artifact'])[0], RIGHT)
        self.assertEqual(result['disputed_property']['hypotheses'],
                         [{'input': 7, 'method_value': 28, 'auditor_value': 29}])
        self.assertFalse(ws.refuted())
        self.assertFalse(ws.confirmed('The n-th triangular number.'))
        self.assertFalse(ws._read(ws.solved_path))
        self.assertEqual(result['checks'], [check(1, 1), check(2, 3), check(3, 6)])

    def test_control_a_reliable_property_still_refutes_and_repairs(self):
        result, ws, writes = triangular_run(PROPERTY, [WRONG, RIGHT],
                                            lambda c: {'expected': [TRI(n) for n in c['inputs']]})
        self.assertEqual((result['status'], result['source']), ('checked', RIGHT))
        self.assertEqual(len(writes), 2)
        self.assertTrue(ws.refuted())
        self.assertTrue(ws.confirmed('The n-th triangular number.'))

    def test_control_a_method_confirmed_everywhere_overrules_the_property(self):
        result, ws, _ = triangular_run(PICKY, [RIGHT],
                                       lambda c: {'expected': [TRI(n) for n in c['inputs']]})
        self.assertEqual((result['status'], result['source']), ('checked', RIGHT))
        self.assertFalse(ws.refuted())
        self.assertNotIn('disputed_property', result)
        self.assertNotIn('property', result['evidence']['tested_beyond_checks'])

    def test_an_independent_reference_can_repair_without_a_stale_property_veto(self):
        result, ws, writes = triangular_run(PICKY, [WRONG, RIGHT],
            lambda c: {'expected': [TRI(n) for n in c['inputs']]},
            reference='def reference(n):\n    return n * (n + 1) // 2\n')
        self.assertEqual((result['status'], result['source']), ('checked', RIGHT))
        self.assertEqual(len(writes), 2)
        self.assertTrue(ws.refuted())
        self.assertNotIn('disputed_property', result)

    def test_an_unrelated_repair_cannot_clear_disputes_by_changing_artifact_id(self):
        partial_fix = ('def run(n):\n'
                       '    return n * (n + 1) // 2 if n < 4 or n == 5 or n > 50 else n * n\n')
        reference = ('def reference(n):\n    if n > 50: return n * (n + 1) // 2\n'
                     '    raise ValueError("outside reference domain")\n')
        writes = []

        def act(request):
            writes.append(request)
            return store(WRONG if len(writes) == 1 else partial_fix)

        plan = lambda _: {'contract': 'run(n) returns the n-th triangular number', 'input': None,
                          'checks': [check(1, 1), check(2, 3), check(3, 6)], 'verify': PICKY,
                          'reference': reference, 'small_inputs': [100]}
        solver, ws = make_solver(Model(plan, act,
            audit=lambda c: {'expected': [TRI(n) for n in c['inputs']]}), max_calls=30)
        result = solver.solve('The n-th triangular number.')
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(len(writes), 2)
        self.assertIn(check(100, TRI(100)), result['checks'])
        self.assertEqual([h['input'] for h in result['disputed_property']['hypotheses']], [4, 7])
        self.assertEqual(load_method(ws.memory, result['unverified']['artifact'])[0], partial_fix)
        self.assertFalse(ws._read(ws.solved_path))

    def test_rounds_preserve_uncertainty_but_allow_a_genuinely_repaired_method(self):
        for repair in (False, True):
            with self.subTest(repair=repair):
                plans, writes = [], []

                def plan(context):
                    plans.append(context)
                    return {'contract': 'run(n) returns the n-th triangular number', 'input': None,
                            'checks': [check(1, 1), check(2, 3), check(3, 6)],
                            'verify': PICKY if len(plans) == 1 else None}

                def act(request):
                    writes.append(request)
                    value = store(RIGHT if repair and len(writes) > 1 else WRONG)
                    value['input']['name'] = 'attempt_' + str(len(writes))  # a fresh ID even for identical source
                    return value

                model = Model(plan, act, audit=lambda c: {'expected': [TRI(n) for n in c['inputs']]})
                ws = Workspace(tempfile.mkdtemp())
                result = solve_task('The n-th triangular number.', settings=settings(max_calls=30, rounds=2),
                                    workspace=ws, models=(model,) * 5, executor=local_executor)
                self.assertEqual(len(result['rounds']), 2)
                self.assertEqual(result['rounds'][0]['status'], 'incomplete')
                self.assertEqual(result['status'], 'checked' if repair else 'incomplete')
                self.assertTrue(ws._read(ws.property_disputes_path))
                self.assertFalse(ws.confirmed('The n-th triangular number.'))
                if repair:
                    self.assertEqual(result['source'], RIGHT)
                else:
                    self.assertEqual([h['input'] for h in result['disputed_property']['hypotheses']], [4, 7])
                    self.assertFalse(ws._read(ws.solved_path))

    def test_restart_reloads_uncertainty_and_failed_replay_stays_unsettled(self):
        plan = lambda _: {'contract': 'run(n) returns the n-th triangular number', 'input': None,
                          'checks': [check(1, 1), check(2, 3), check(3, 6)], 'verify': PICKY}
        model = Model(plan, lambda _: store(WRONG),
                      audit=lambda c: {'expected': [TRI(n) for n in c['inputs']]})
        solver, ws = make_solver(model)
        result = solver.solve('The n-th triangular number.')
        reloaded = Workspace(ws.root)
        solver.ws = reloaded
        solver.max_executions = solver._execution_used
        before = solver._execution_used
        dispute = solver._property_dispute_gate('The n-th triangular number.', {'reference': None},
                                                result['unverified']['artifact'])
        self.assertTrue(dispute)
        self.assertEqual([h['input'] for h in dispute['hypotheses']], [4, 7])
        self.assertEqual(solver._execution_used, before)
        self.assertEqual(reloaded.property_disputes('The n-th triangular number.', 'different given conditions'), [])

    def test_failed_later_property_batch_preserves_earlier_evidence(self):
        def fail_late(job):
            if job['input'] and isinstance(job['input'], list) and job['input'][0] == [9, 81]:
                return {'status': 'timeout', 'exit_code': 1}
            return local_executor(job)

        with mock.patch('kuttaka_runtime.solve._probe_batches', return_value=[[8], [9]]), \
                mock.patch('tests.test_solve_review_regressions.local_executor', fail_late):
            result, ws, writes = triangular_run(PROPERTY, [WRONG, RIGHT],
                lambda c: {'expected': [TRI(n) for n in c['inputs']]})
        self.assertEqual((result['status'], result['source']), ('checked', RIGHT))
        self.assertEqual(len(writes), 2)
        self.assertTrue(ws.refuted())
        disputed = [e for e in events(result, 'solve_property_test') if e.get('outcome') == 'disagree']
        self.assertEqual(disputed[0]['confirmed'], [check(8, TRI(8))])

    def test_property_verifies_large_probe_batches_separately(self):
        def sized(job):
            if len(json.dumps(job, allow_nan=False, separators=(',', ':')).encode()) > INPUT_LIMIT:
                raise ValueError('program input and libraries exceed 256 KiB')
            return local_executor(job)

        # Lengths differ by one unit so the expected counts differ: checks that all expect one value are
        # refused as pinning nothing (DEGENERATE_CHECKS). Each text stays about 12 KB, as the batching needs.
        words = [('a' + unit * 3000)[:12000 - 4 * k] for k, unit in enumerate(('bcda', 'xyza', 'mnoa'))]
        right = 'def run(s):\n    return s.lower().count("a")\n'
        wrong = 'def run(s):\n    return s.count("a")\n'
        writes = []

        def act(request):
            writes.append(request)
            return store(wrong if len(writes) == 1 else right)

        plan = lambda _: {'contract': 'run(s) counts a or A in s', 'input': None,
                          'checks': [check(w, w.count('a')) for w in words],
                          'verify': 'def verify(s, y):\n    return y == s.lower().count("a")\n'}
        with mock.patch('tests.test_solve_review_regressions.local_executor', sized):
            solver, ws = make_solver(Model(plan, act,
                audit=lambda c: {'expected': [s.lower().count('a') for s in c['inputs']]}), max_calls=30)
            result = solver.solve('Count letters a, of either case.')
        self.assertEqual((result['status'], result['source']), ('checked', right))
        self.assertEqual(len(writes), 2)
        self.assertTrue(ws.refuted())


if __name__ == '__main__':
    unittest.main()
