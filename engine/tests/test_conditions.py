"""Every condition the task states is used (THE_ORIGINALS.md, the real gap 1), and the analysis is kept with the method
(the real gap 2, bīja), behind Solver(conditions=True), off by default.

Nyāyasūtra 5.2.22-23 names the two errors of a judge: passing a fault, and condemning what is sound. A verify property
is shown correct outputs (it must accept them) and near misses (it must reject one-leaf changes); neither shows that it
uses each condition of the task. With the option, the planner states each condition with an input and the output that
meets the others and breaks it (Nyāyasūtra 1.1.37, the contrary example; BAU 3.9.28, "do not say 'from seed'").

Scenario: run(xs) returns the distinct elements of xs in ascending order. Two conditions: ascending, and no repeats.
  - a property that tests the order and the elements but not repeats accepts [1, 2, 2] for [2, 1, 2]: it supports no
    check, though every near miss of every check is rejected (control: one that also tests repeats keeps its support);
  - the conditions, with what the property said and what the method gave, are kept with the stored method and shown
    with it when a later plan sees it;
  - a method that keeps repeats passes checks that have none; it gives the tempting wrong output, which is reported,
    not refused (control: the right method differs from it);
  - off by default: the planner is not asked, and a reply's conditions are not read.
Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import CONDITIONS_NOTE, DERIVATION_UNVERIFIED, MAX_DERIVATION_TEXT, PLAN_INSTRUCTIONS, \
    Metered, Solver, Workspace, _conditions, _derivation, _probe_batches, settings, solve_task
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Distinct sorted: run(xs) returns the distinct elements of the list xs in ascending order.'
RIGHT = 'def run(xs):\n    return sorted(set(xs))\n'
KEEPS_REPEATS = 'def run(xs):\n    return sorted(xs)\n'
ORDER_ONLY = 'def verify(input, output):\n    return output == sorted(output) and set(output) == set(input)\n'
EVERY_CONDITION = ('def verify(input, output):\n    return (output == sorted(output) and set(output) == set(input)\n'
                   '            and len(output) == len(set(output)))\n')
CHECKS = [check([3, 1, 2], [1, 2, 3]), check([], []), check([5], [5]), check([2, 1], [1, 2]), check([4, 6], [4, 6])]
CONDITIONS = [{'condition': 'no element appears twice', 'input': [2, 1, 2], 'violating': [1, 2, 2]},
              {'condition': 'the elements are in ascending order', 'input': [9, 7, 8], 'violating': [9, 8, 7]}]
DERIVATION = ('A set has no element twice, so set(xs) meets the first condition; sorted() orders it ascending, '
              'which meets the second, and drops nothing, so every element of xs appears.')


def solve(verify, method=RIGHT, conditions=True, ws=None, seen=None, derivation=DERIVATION):
    def plan(context):
        if seen is not None:
            seen.append(context)
        return {'contract': 'run(xs) returns sorted(set(xs))', 'input': None, 'verify': verify,
                'checks': [dict(c) for c in CHECKS], 'conditions': [dict(c) for c in CONDITIONS],
                **({'derivation': derivation} if derivation is not None else {})}
    audit = lambda c: {'expected': [sorted(set(xs)) for xs in c['inputs']]}
    m = Metered(Model(plan, lambda r: store(method), audit=audit), {'used': 0, 'limit': 24})
    ws = ws or Workspace(Path(tempfile.mkdtemp()))
    solver = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                    say=lambda _: None, conditions=conditions)
    return solver.solve(TASK), ws


class StatedConditions(unittest.TestCase):
    def test_a_property_blind_to_a_stated_condition_supports_no_check(self):
        result, _ = solve(ORDER_ONLY)
        settled = events(result, 'solve_given_conditions')[0]
        tested = events(result, 'solve_condition_test')[0]
        self.assertEqual(tested['blind_to'], ['no element appears twice'])
        self.assertEqual(tested['property_rejects'], 1)
        self.assertNotIn('property', settled['support'].values(), 'a property that ignores a condition gave support')
        self.assertEqual(settled['property_blind'], len(CHECKS))
        self.assertEqual(result['status'], 'checked')   # the right method is still found and checked
        # Control: without the stated conditions the same property rejects every near miss and is a witness.
        off, _ = solve(ORDER_ONLY, conditions=False)
        self.assertEqual(set(events(off, 'solve_given_conditions')[0]['support'].values()), {'property'})

    def test_control_a_property_that_uses_every_condition_keeps_its_support(self):
        result, _ = solve(EVERY_CONDITION)
        settled = events(result, 'solve_given_conditions')[0]
        self.assertEqual(events(result, 'solve_condition_test')[0]['blind_to'], [])
        self.assertEqual(set(settled['support'].values()), {'property'})
        self.assertEqual(settled['property_blind'], 0)
        self.assertEqual([c['tested'] for c in result['conditions']], ['the property rejects its violating output'] * 2)
        self.assertEqual([c['method'] for c in result['conditions']], ['differs from the violating output'] * 2)

    def test_the_conditions_are_kept_with_the_method_and_shown_on_reuse(self):
        result, ws = solve(EVERY_CONDITION)
        row = ws.helpful_methods(TASK)[0]
        self.assertEqual(row['artifact'], result['artifact'])
        self.assertEqual([c['condition'] for c in row['conditions']], [c['condition'] for c in CONDITIONS])
        self.assertTrue(all(c['tested'] == 'the property rejects its violating output' for c in row['conditions']))
        seen = []
        solve(EVERY_CONDITION, ws=ws, seen=seen)
        shown = seen[0]['available_methods'][0]
        self.assertEqual([c['condition'] for c in shown['conditions']], [c['condition'] for c in CONDITIONS])
        # Control: without the option a later plan sees the method as before, with no conditions.
        seen_off = []
        solve(EVERY_CONDITION, ws=ws, seen=seen_off, conditions=False)
        self.assertNotIn('conditions', seen_off[0]['available_methods'][0])

    def test_a_method_giving_the_tempting_wrong_output_is_reported_not_refused(self):
        result, _ = solve(None, method=KEEPS_REPEATS)
        self.assertEqual(result['status'], 'checked')   # the checks hold no repeat: the method passes them
        by_condition = {c['condition']: c['method'] for c in result['conditions']}
        self.assertEqual(by_condition['no element appears twice'], 'gives the violating output')
        self.assertEqual(events(result, 'solve_condition_record')[0]['broken'], ['no element appears twice'])
        # Control: the right method differs from every violating output.
        right, _ = solve(None)
        self.assertEqual({c['method'] for c in right['conditions']}, {'differs from the violating output'})

    def test_off_by_default_the_planner_is_not_asked_and_a_reply_is_not_read(self):
        self.assertNotIn('violating', PLAN_INSTRUCTIONS)   # the registered instructions are unchanged
        seen = []
        result, ws = solve(ORDER_ONLY, conditions=False, seen=seen)
        self.assertNotIn('state_conditions', seen[0])
        self.assertEqual(events(result, 'solve_conditions_stated'), [])
        self.assertEqual(events(result, 'solve_condition_test'), [])
        self.assertNotIn('conditions', result)
        self.assertNotIn('conditions', ws.helpful_methods(TASK)[0])
        # Control: with the option the note is in the planner's context.
        seen_on = []
        solve(ORDER_ONLY, seen=seen_on)
        self.assertEqual(seen_on[0]['state_conditions'], CONDITIONS_NOTE)

    def test_the_conditions_are_read_strictly(self):
        checks = [check([3, 1, 2], [1, 2, 3])]
        stated, dropped = _conditions({'conditions': [
            {'condition': 'ascending', 'input': [3, 1, 2], 'violating': [1, 2, 3]},   # a check's expected value
            {'condition': 'distinct', 'input': [2, 2], 'violating': [2, 2]},
            {'condition': '', 'input': [1], 'violating': [1]},
            {'condition': 'x', 'input': [1]},
            'not an object'] + [{'condition': f'c{n}', 'input': [n], 'violating': [n, n]} for n in range(8)]}, checks)
        self.assertEqual([c['condition'] for c in stated], ['distinct', 'c0', 'c1', 'c2', 'c3', 'c4'])
        self.assertEqual(len(dropped), 7)
        self.assertIn('its violating output is the expected value of a check', dropped[0])
        self.assertEqual(_conditions({}, checks), ([], []))
        from kuttaka_runtime.solve import MAX_CONDITION_JSON
        big = {'condition': 'big', 'input': list(range(MAX_CONDITION_JSON)), 'violating': []}
        self.assertEqual(_conditions({'conditions': [big]}, checks)[0], [])
        # Control: a small one beside it is kept.
        self.assertEqual(len(_conditions({'conditions': [big, CONDITIONS[0]]}, checks)[0]), 1)
        self.assertEqual(_conditions({'conditions': 'all of them'}, checks), ([], ['conditions is not a list']))

    def test_the_option_reaches_the_solver_through_solve_task(self):
        def run(**overrides):
            plan = lambda context: {'contract': 'run(xs) returns sorted(set(xs))', 'input': None,
                                    'verify': ORDER_ONLY, 'checks': [dict(c) for c in CHECKS],
                                    'conditions': [dict(c) for c in CONDITIONS]}
            m = Model(plan, lambda r: store(RIGHT), audit=lambda c: {'expected': [sorted(set(xs)) for xs in c['inputs']]})
            return solve_task(TASK, settings=settings(max_calls=24, **overrides),
                              workspace=Workspace(Path(tempfile.mkdtemp())), models=(m, m, m, m, m),
                              executor=local_executor)
        self.assertEqual(events(run(conditions=True), 'solve_condition_test')[0]['blind_to'],
                         ['no element appears twice'])
        # Control: the same run without the option states and tests no condition.
        self.assertEqual(events(run(), 'solve_condition_test'), [])

    def test_the_written_derivation_is_kept_with_the_method_marked_unverified(self):
        result, ws = solve(EVERY_CONDITION)
        self.assertEqual(result['derivation'], {'text': DERIVATION, 'status': DERIVATION_UNVERIFIED})
        self.assertEqual(ws.helpful_methods(TASK)[0]['derivation']['text'], DERIVATION)
        self.assertEqual(events(result, 'solve_conditions_stated')[0]['derivation'], len(DERIVATION))
        seen = []
        solve(EVERY_CONDITION, ws=ws, seen=seen)
        self.assertEqual(seen[0]['available_methods'][0]['derivation']['status'], DERIVATION_UNVERIFIED)
        # It is words, not evidence: the same run without one has the same status and support.
        bare, _ = solve(EVERY_CONDITION, derivation=None)
        self.assertNotIn('derivation', bare)
        self.assertEqual((bare['status'], events(bare, 'solve_given_conditions')[0]['support']),
                         (result['status'], events(result, 'solve_given_conditions')[0]['support']))
        # `kuttaka library` shows it with its label, beside the conditions and what tested each.
        from kuttaka_runtime import shastra
        text = shastra.render(shastra.build(ws))
        self.assertIn(f'derivation ({DERIVATION_UNVERIFIED}): {DERIVATION}', text)
        self.assertIn('condition: no element appears twice (the property rejects its violating output', text)
        # Control: off by default, a reply's derivation is not read, stored or shown.
        off, ws_off = solve(EVERY_CONDITION, conditions=False)
        self.assertNotIn('derivation', off)
        self.assertNotIn('derivation', ws_off.helpful_methods(TASK)[0])
        self.assertNotIn('derivation (', shastra.render(shastra.build(ws_off)))

    def test_a_condition_test_that_cannot_run_is_said(self):
        # A property that crashes on the pairs gives no verdict: the event says so, and nothing is marked blind.
        crashing = 'def verify(input, output):\n    raise ValueError("no")\n'
        result, _ = solve(crashing)
        tested = events(result, 'solve_condition_test')
        self.assertEqual([e['outcome'] for e in tested], ['no verdict: the property did not run on them'])
        self.assertEqual(tested[0]['blind_to'], [])
        # Control: a property that runs says 'tested'.
        ran, _ = solve(EVERY_CONDITION)
        self.assertEqual([e['outcome'] for e in events(ran, 'solve_condition_test')], ['tested'])

    def test_what_the_method_did_is_said_as_it_was(self):
        from kuttaka_runtime.solve import _condition_outcome
        self.assertEqual(_condition_outcome({'value': [1, 2, 2]}, [1, 2, 2]), 'gives the violating output')
        self.assertEqual(_condition_outcome({'value': [1, 2]}, [1, 2, 2]), 'differs from the violating output')
        self.assertEqual(_condition_outcome({'error': 'ValueError'}, [1, 2, 2]), 'raises ValueError')
        self.assertEqual(_condition_outcome({'timeout': True, 'seconds': 0.0}, [1, 2, 2]), 'did not finish')
        # Control: no entry at all (the sandbox job did not run) is the only 'not run'.
        self.assertEqual(_condition_outcome(None, [1, 2, 2]), 'not run')

    def test_the_derivation_is_read_strictly(self):
        self.assertEqual(_derivation({}), (None, None))
        self.assertEqual(_derivation({'derivation': '  why  '}), ('why', None))
        self.assertEqual(_derivation({'derivation': ['why']}), (None, 'derivation is not a text'))
        self.assertEqual(_derivation({'derivation': ' '}), (None, 'derivation is not a text'))
        self.assertIsNone(_derivation({'derivation': 'x' * (MAX_DERIVATION_TEXT + 1)})[0])
        # Control: a text at the limit is kept.
        self.assertEqual(_derivation({'derivation': 'x' * MAX_DERIVATION_TEXT})[0], 'x' * MAX_DERIVATION_TEXT)

    def test_a_stated_condition_input_comes_first_among_the_probes(self):
        spec = {'checks': CHECKS, 'input': None, 'conditions': CONDITIONS}
        self.assertEqual(_probe_batches(spec)[0], [[2, 1, 2], [9, 7, 8]])
        # Control: without conditions the probes are as before.
        self.assertEqual(_probe_batches(dict(spec, conditions=None)), _probe_batches({'checks': CHECKS, 'input': None}))


if __name__ == '__main__':
    unittest.main()


class SuiteRunnerOption(unittest.TestCase):
    """experiments/solve_suite/run.py --conditions: the local runner can run the option, and its rows carry the
    conditions and the derivation (control: without the flag the rows are as before)."""

    def setUp(self):
        import importlib.util
        import json
        import shutil
        # Loaded under its own name from its path, so no other module called `run` can stand in for it.
        path = Path(__file__).resolve().parents[1] / 'experiments' / 'solve_suite' / 'run.py'
        spec = importlib.util.spec_from_file_location('solve_suite_run_for_conditions', path)
        self.run = run = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(run)
        suite = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, suite, True)
        (suite / 'tasks.json').write_text(json.dumps([{'id': 'd1', 'domain': 'd', 'level': 1, 'prompt': TASK}]))
        (suite / 'hidden.json').write_text(json.dumps({'d1': [{'input': [3, 3, 1], 'expected': [1, 3]}]}))
        saved = {name: getattr(run, name) for name in ('HERE', 'preflight', 'infrastructure_ready',
                                                       'PythonExecutor', 'build_models')}
        self.addCleanup(lambda: [setattr(run, k, v) for k, v in saved.items()])
        run.HERE, run.preflight = suite, lambda: {'ready': True}
        run.infrastructure_ready = lambda wait=0: True
        run.PythonExecutor = lambda **kw: local_executor

        class Scripted:
            identity = {'model': 'scripted'}
            last_usage = {'input_tokens': 10, 'output_tokens': 5}

            def propose(self, context):
                if 'request' in context:
                    return store(RIGHT)
                if 'inputs' in context:
                    return {'expected': [sorted(set(xs)) for xs in context['inputs']]}
                return {'contract': 'run(xs) returns sorted(set(xs))', 'input': None, 'verify': EVERY_CONDITION,
                        'checks': [dict(c) for c in CHECKS], 'conditions': [dict(c) for c in CONDITIONS],
                        'derivation': DERIVATION}

        run.build_models = lambda args, counter: tuple(Metered(Scripted(), counter) for _ in range(5))

    def rows(self, *flags):
        import json
        import shutil
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out, True)
        self.run.main(['--arm', 'fresh', '--out', str(out), *flags])
        return [json.loads(line) for line in (out / 'rows.jsonl').read_text().splitlines()]

    def test_the_rows_carry_the_conditions_and_the_derivation(self):
        row, = self.rows('--conditions')
        self.assertTrue(row['solved'])
        self.assertEqual([c['condition'] for c in row['conditions']], [c['condition'] for c in CONDITIONS])
        self.assertEqual(row['derivation'], {'text': DERIVATION, 'status': DERIVATION_UNVERIFIED})
        # Control: without the flag the planner's conditions and derivation are not read, and the row is as before.
        plain, = self.rows()
        self.assertTrue(plain['solved'])
        self.assertNotIn('conditions', plain)
        self.assertNotIn('derivation', plain)
