import json
import io
import re
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from kuttaka_runtime.solve import CallsExhausted, Metered, Solver, Workspace, stats
from tests.test_kuttaka_campaign import local_executor

SQUARE = 'def run(x):\n    return x * x\n'
TOTAL = 'def run(xs):\n    return sum(xs)\n'
PARENT = ('def run(xs):\n    sq, total = use("%s"), use("%s")\n'
          '    return total([sq(x) for x in xs])\n')


class Planner:
    identity = {'model': 'scripted-planner'}
    last_usage = {'input_tokens': 4, 'output_tokens': 3}

    def __init__(self):
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        if 'failure' in context:
            return {'subtasks': [
                {'request': 'Square a number.', 'checks': [{'input': 3, 'expected': 9}]},
                {'request': 'Add up a list.', 'checks': [{'input': [1, 2], 'expected': 3}]}]}
        task = context['task']
        if task.startswith('Square'):
            return {'contract': 'run(x) returns x*x', 'checks': [{'input': 2, 'expected': 4}], 'input': None}
        if task.startswith('Add'):
            return {'contract': 'run(xs) returns sum(xs)', 'checks': [{'input': [], 'expected': 0}], 'input': None}
        if task.startswith('Fix'):
            return {'contract': 'run(x) returns 2*x', 'input': None,
                    'checks': [{'input': 1, 'expected': 2}, {'input': 2, 'expected': 4}, {'input': 5, 'expected': 10}]}
        if task.startswith(('Stuck', 'Offset')):
            return {'contract': 'run(x) returns x+7', 'input': None,
                    'checks': [{'input': 0, 'expected': 7}, {'input': 1, 'expected': 8}]}
        if task.startswith('Root'):
            return {'contract': 'run(n) returns floor(sqrt(n))', 'input': 99,
                    'checks': [{'input': 4, 'expected': 2}, {'input': 10, 'expected': 3}, {'input': 17, 'expected': 5}],
                    'verify': 'def verify(n, r):\n    return r * r <= n < (r + 1) * (r + 1)\n'}
        if task.startswith('Double'):
            return {'contract': 'run(x) returns 2*x',
                    'checks': [{'input': 1, 'expected': 2}, {'input': 3, 'expected': 7}], 'input': None}
        if task.startswith('Nothing'):
            return {'contract': 'run(x) returns x', 'checks': [{'input': 2, 'expected': 2}], 'input': None}
        return {'contract': 'run(xs) returns the sum of the squares of xs',
                'checks': [{'input': [1, 2], 'expected': 5}, {'input': [], 'expected': 0}], 'input': [3, 4]}


class Actor:
    """Writes a method from the request; cannot solve the whole sum of squares in one step."""
    identity = {'model': 'scripted-actor'}
    last_usage = {'input_tokens': 10, 'output_tokens': 5}

    def propose(self, context):
        request, history = context['request'], context['history']
        if not history:
            return {'kind': 'experiment', 'tool': 'find_methods', 'input': {'query': 'm', 'limit': 5},
                    'predictions': [{'hypothesis': 'memory may already fit', 'outcome': 'a list'}]}
        fits = [m['id'] for m in history[-1]['observation'].get('methods', []) if m.get('fits_this_task')]
        if fits:
            return {'kind': 'answer', 'answer': {'artifact': fits[0]}}
        if 'artifact' in history[-1]['observation']:
            return {'kind': 'answer', 'answer': {'artifact': history[-1]['observation']['artifact']}}
        if request.startswith('Square'):
            body = {'source': SQUARE}
        elif request.startswith('Fix'):
            body = {'source': 'def run(x):\n    return 2 * x\n' if 'Best attempt so far' in request
                    else 'def run(x):\n    return x + 1\n'}
        elif request.startswith(('Stuck', 'Offset')):
            body = {'source': 'def run(x):\n    return x + 3\n'}
        elif request.startswith('Root'):
            body = {'source': 'import math\n\ndef run(n):\n    return math.isqrt(n)\n'}
        elif request.startswith('Double'):
            body = {'source': 'def run(x):\n    return 2 * x\n'}
        elif request.startswith('Add'):
            body = {'source': TOTAL}
        elif request.startswith('Half') and 'Stored methods' not in request:
            body = {'source': TOTAL}  # right on the empty list, wrong otherwise
        elif request.startswith(('Sum the squares', 'Half')) and 'Stored methods' in request:
            ids = re.findall(r'^([0-9a-f]{64}): ', request, re.M)
            body = {'source': PARENT % (ids[0], ids[1]), 'uses': ids}
        else:
            return {'kind': 'unresolved', 'reason': 'too hard in one step'}
        body.update(name='m', description='m')
        return {'kind': 'experiment', 'tool': 'remember_method', 'input': body,
                'predictions': [{'hypothesis': 'stored', 'outcome': 'artifact id'}]}


class SolveTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(tempfile.mkdtemp())
        self.counter = {'used': 0, 'limit': 40}
        self.planner = Planner()

    def solver(self, limit=40):
        self.counter['limit'] = limit
        return Solver(self.ws, Metered(self.planner, self.counter), Metered(Actor(), self.counter),
                      local_executor, say=lambda text: None)

    def test_direct_solve_is_checked_stored_and_deployed(self):
        result = self.solver().solve('Square a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['checks_by'], 'model')
        self.assertEqual(result['subtasks'], [])
        self.assertIn('x * x', result['source'])

    def test_failure_splits_into_subtasks_then_composes_them_by_id(self):
        result = self.solver().solve('Sum the squares of a list.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual([p['status'] for p in result['subtasks']], ['checked', 'checked'])
        self.assertEqual(set(result['reused']), {p['artifact'] for p in result['subtasks']})
        self.assertEqual(result['answer'], 25)  # deployed on the requested input [3, 4]

    def test_second_time_reuses_the_stored_method_and_costs_less(self):
        first = self.solver().solve('Square a number.')
        used = self.counter['used']
        self.counter['used'] = 0
        again = self.solver().solve('Square a number.')
        self.assertEqual(again['artifact'], first['artifact'])
        self.assertIn(first['artifact'], again['reused'])
        self.assertLessEqual(self.counter['used'], used)

    def test_failure_is_kept_as_a_lesson_and_shown_on_a_similar_task(self):
        result = self.solver(limit=40).solve('Nothing can do this task')
        self.assertNotEqual(result['status'], 'checked')
        self.solver().plan('Nothing can do this task again', [])
        self.assertTrue(self.planner.contexts[-1]['lessons'])

    def test_user_examples_are_kept_and_labelled(self):
        spec = self.solver().plan('Square a number.', [{'input': 5, 'expected': 25}])
        self.assertEqual(spec['checks'][0], {'input': 5, 'expected': 25})
        self.assertEqual(spec['checks_by'], 'user and model')

    def test_half_right_attempt_is_split_around_the_part_that_works(self):
        result = self.solver().solve('Half done: sum the squares of a list.')
        split = next(c for c in self.planner.contexts if 'failure' in c)
        self.assertEqual(split['partial_attempt']['passed_checks'], [{'input': [], 'expected': 0}])
        self.assertEqual(split['partial_attempt']['failed_checks'][0]['got'], 3)
        self.assertIn('sum(xs)', split['partial_attempt']['source'])
        self.assertEqual(result['status'], 'checked')

    def test_a_similar_task_reuses_the_whole_stored_method_without_splitting(self):
        first = self.solver().solve('Sum the squares of a list.')
        used = self.counter['used']
        self.counter['used'] = 0
        again = self.solver().solve('Sum the squares of a list of numbers.')
        self.assertEqual(again['status'], 'checked')
        self.assertEqual(again['subtasks'], [])
        self.assertIn(first['artifact'], again['reused'])
        self.assertLess(self.counter['used'], used)

    def test_a_breakdown_that_worked_is_used_when_the_model_gives_none(self):
        self.ws.remember_split('Sum the squares of a list.', [
            {'request': 'Square a number.', 'checks': [{'input': 3, 'expected': 9}]},
            {'request': 'Add up a list.', 'checks': [{'input': [1, 2], 'expected': 3}]}])

        class NoSplit:
            identity = {'model': 'scripted-splitter'}
            last_usage = {}
            contexts = []

            def propose(self, context):
                self.contexts.append(context)
                return {}
        splitter = NoSplit()
        solver = Solver(self.ws, Metered(self.planner, self.counter), Metered(Actor(), self.counter),
                        local_executor, splitter=Metered(splitter, self.counter), say=lambda text: None)
        result = solver.solve('Sum the squares of a list of numbers.')
        self.assertTrue(splitter.contexts[0]['similar_breakdowns'])
        self.assertTrue(result['split_by'].startswith('system'))
        self.assertEqual(result['status'], 'checked')

    def test_stored_methods_for_similar_tasks_are_offered_before_the_first_attempt(self):
        first = self.solver().solve('Square a number.')
        self.assertEqual(self.ws.helpful_methods('Square a number, please.')[0]['artifact'], first['artifact'])

    def audited(self, value):
        class Auditor:
            identity = {'model': 'scripted-auditor'}
            last_usage = {}
            seen = []

            def propose(self, context):
                self.seen.append(context)
                return {'expected': [value] * len(context['inputs'])}
        auditor = Auditor()
        solver = Solver(self.ws, Metered(self.planner, self.counter), Metered(Actor(), self.counter),
                        local_executor, auditor=Metered(auditor, self.counter), say=lambda text: None)
        return solver, auditor

    def test_a_wrong_model_check_is_corrected_by_blind_rederivation(self):
        solver, auditor = self.audited(6)
        result = solver.solve('Double a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertIn({'input': 3, 'expected': 6}, result['checks'])
        self.assertIn('corrected by blind re-derivation', result['checks_by'])
        self.assertNotIn('got', json.dumps(auditor.seen))  # the auditor never sees the method's output

    def test_an_auditor_that_repeats_the_check_changes_nothing(self):
        solver, _ = self.audited(7)
        result = solver.solve('Double a number.')
        self.assertNotEqual(result['status'], 'checked')
        self.assertIn({'input': 3, 'expected': 7}, result['checks'])

    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_user_examples_are_never_corrected(self):
        solver, auditor = self.audited(6)
        result = solver.solve('Double a number.', [{'input': 3, 'expected': 7}])
        self.assertIn({'input': 3, 'expected': 7}, result['checks'])
        self.assertEqual(auditor.seen, [])

    def test_a_cut_off_response_is_a_transport_failure(self):
        import http.client
        from kuttaka_runtime.runtime import TransportFailure

        class Cut:
            def propose(self, context):
                raise http.client.IncompleteRead(b'')
        with self.assertRaises(TransportFailure):
            Metered(Cut(), {'used': 0, 'limit': 5}).propose({})

    def custom(self, **models):
        wrapped = {k: Metered(v, self.counter) for k, v in models.items()}
        return Solver(self.ws, Metered(self.planner, self.counter), Metered(Actor(), self.counter),
                      local_executor, say=lambda text: None, **wrapped)

    def test_progress_rises_through_repair_and_never_falls(self):
        result = self.solver().solve('Fix a doubling method.')
        self.assertEqual(result['status'], 'checked')
        # Two of three checks fail, so reduction now precedes another whole-task repair. The fixture's
        # square/sum split cannot implement doubling; the bounded fallback repair still finishes it.
        self.assertEqual(result['progress'], [1, 1, 3])
        self.assertEqual(result['strategies'], ['split', 'repair'])

    def test_a_stuck_task_stops_instead_of_looping(self):
        result = self.solver(limit=200).solve('Stuck: add seven.')
        self.assertNotEqual(result['status'], 'checked')
        self.assertLess(self.counter['used'], 200)          # it stopped on its own, not at the limit
        self.assertEqual(max(result['progress']), 0)
        self.assertIn('replan', result['strategies'])

    def test_mati_fills_a_wrong_constant_by_search(self):
        class Sketcher:
            identity = {'model': 'scripted-sketcher'}
            last_usage = {}

            def propose(self, context):
                return {'source': 'def run(x):\n    return x + HOLE0\n', 'holes': {'HOLE0': list(range(10))}}
        result = self.custom(sketcher=Sketcher()).solve('Offset: add seven.')
        self.assertEqual(result['status'], 'checked')
        self.assertIn('mati', result['strategies'])
        # Filling preserves expression grouping; validate its behavior instead of spelling.
        checked = local_executor({'source': result['source'], 'input': 100})
        self.assertEqual(json.loads(checked['output'])['result'], 107)

    def test_the_verifier_corrects_a_wrong_check_by_substitution(self):
        result = self.solver().solve('Root: integer square root.')
        self.assertEqual(result['status'], 'checked')
        self.assertIn({'input': 17, 'expected': 4}, result['checks'])
        self.assertIn('corrected by verifier', result['checks_by'])
        self.assertEqual(result['answer'], 9)

    def test_call_limit_is_hard(self):
        metered = Metered(self.planner, {'used': 1, 'limit': 1})
        with self.assertRaises(CallsExhausted):
            metered.propose({'task': 'x'})

    def test_stats_reads_history(self):
        self.ws.record({'task': 't', 'status': 'checked', 'model_calls': 3, 'input_tokens': 1,
                        'output_tokens': 1, 'reused': [], 'subtasks': 0, 'unknown_usage_calls': 0})
        out = io.StringIO()
        stats(self.ws, out)
        self.assertIn('1/1 solved', out.getvalue())


if __name__ == '__main__':
    unittest.main()


class DeployExecutorTests(unittest.TestCase):
    def test_final_run_uses_the_deploy_executor_and_checks_do_not(self):
        seen = []

        def long_runner(job):
            seen.append(job['input'])
            return local_executor(job)

        ws = Workspace(tempfile.mkdtemp())
        counter = {'used': 0, 'limit': 40}
        solver = Solver(ws, Metered(Planner(), counter), Metered(Actor(), counter), local_executor,
                        deploy_executor=long_runner, say=lambda text: None)
        result = solver.solve('Sum the squares of a list.')
        self.assertEqual(result['answer'], 25)
        self.assertEqual(seen, [[3, 4]])  # only the requested input, never a check

    def test_long_run_limit_is_explicit(self):
        from kuttaka_runtime.execution import PythonExecutor
        with self.assertRaises(ValueError):
            PythonExecutor(timeout=3600)
        self.assertEqual(PythonExecutor(timeout=3600, long_run=True, memory='1g').memory, '1g')
        with self.assertRaises(ValueError):
            PythonExecutor(timeout=10, memory='64g')


ABS_REFERENCE = 'def reference(x):\n    return x if x >= 0 else -x\n'


class DiffPlanner:
    """Plans |x| tasks with checks that miss negative inputs, plus a slow reference."""
    identity = {'model': 'scripted-planner'}
    last_usage = {}

    def __init__(self, reference=ABS_REFERENCE):
        self.reference = reference

    def propose(self, context):
        if 'inputs' in context:  # the auditor, blind: it works out |x| from the task
            return {'expected': [abs(v) + (1 if context['task'].startswith('Abs plus') else 0)
                                 for v in context['inputs']]}
        task = context['task']
        if task.startswith('Identity'):
            return {'contract': 'run(x) returns abs(x)', 'checks': [{'input': 2, 'expected': 2}], 'input': None}
        plus = 1 if task.startswith('Abs plus') else 0
        return {'contract': 'run(x) returns abs(x)' + (' + 1' if plus else ''), 'input': None,
                'checks': [{'input': 1, 'expected': 1 + plus}, {'input': 5, 'expected': 5 + plus}],
                'reference': self.reference.replace('return x', 'return %d + x' % plus).replace('else -x', 'else %d - x' % plus),
                'small_inputs': [-3, 0, 4, -1]}


class DiffActor:
    """Writes x (wrong for negatives) until a failing check with a negative input appears."""
    identity = {'model': 'scripted-actor'}
    last_usage = {}

    def propose(self, context):
        request, history = context['request'], context['history']
        if history and 'artifact' in history[-1]['observation']:
            return {'kind': 'answer', 'answer': {'artifact': history[-1]['observation']['artifact']}}
        negative = bool(re.search(r'"input": -\d', request.split('Checks run(input) must pass:')[-1]))
        ids = re.findall(r'^([0-9a-f]{64}): ', request, re.M)
        if request.startswith('Abs plus') and ids and not negative:
            body = {'source': 'def run(x):\n    return use("%s")(x) + 1\n' % ids[0], 'uses': [ids[0]]}
        elif negative:
            body = {'source': 'def run(x):\n    return abs(x)%s\n' % (' + 1' if request.startswith('Abs plus') else '')}
        else:
            body = {'source': 'def run(x):\n    return x%s\n' % (' + 1' if request.startswith('Abs plus') else '')}
        body.update(name='m', description='m')
        return {'kind': 'experiment', 'tool': 'remember_method', 'input': body,
                'predictions': [{'hypothesis': 'stored', 'outcome': 'artifact id'}]}


class DifferentialTests(unittest.TestCase):
    def solver(self, planner, ws=None):
        counter = {'used': 0, 'limit': 40}
        return Solver(ws or Workspace(tempfile.mkdtemp()), Metered(planner, counter), Metered(DiffActor(), counter),
                      local_executor, say=lambda text: None)

    def test_reference_finds_a_missed_edge_case_and_the_loop_repairs_it(self):
        result = self.solver(DiffPlanner()).solve('Abs of a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertIn('abs(x)', result['source'])
        self.assertIn('differential', result['strategies'])
        self.assertIn('from differential testing', result['checks_by'])
        self.assertTrue(any(c['input'] < 0 for c in result['checks']))

    def test_a_wrong_reference_contradicted_by_the_auditor_is_ignored(self):
        wrong = 'def reference(x):\n    return x * 2\n'
        result = self.solver(DiffPlanner(reference=wrong)).solve('Abs of a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertNotIn('differential', result['strategies'])
        self.assertEqual(result['checks_by'], 'model')

    def test_an_inherited_error_in_a_reused_method_is_caught(self):
        ws = Workspace(tempfile.mkdtemp())
        first = self.solver(DiffPlanner(), ws).solve('Identity abs of a number.')  # stored, wrong for negatives
        self.assertEqual(len(ws.helpful_methods('Abs plus one of a number.')), 1)
        self.assertEqual(first['status'], 'checked')
        result = self.solver(DiffPlanner(), ws).solve('Abs plus one of a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertIn('differential', result['strategies'])
        attempts = [json.loads(l) for f in sorted(Path(ws.runs).glob('*/events.jsonl')) for l in f.read_text().splitlines()]
        self.assertTrue(any('use(' in json.dumps(e) for e in attempts))  # the first composite reused the stored method
        self.assertNotIn('use(', result['source'])
        self.assertIn('abs(x) + 1', result['source'])


GENERAL_KINEMATICS = ('def run(i):\n    x, v = 0.0, float(i["v0"])\n    for a, t in i["segments"]:\n'
                      '        x += v * t + 0.5 * a * t * t\n        v += a * t\n    return [round(x, 3), round(v, 3)]\n')
HARDCODED_KINEMATICS = ('def run(i):\n    if i["v0"] == 5 and i["segments"] == [[0, 2], [-1, 4]]:\n        return [6.0, 1.0]\n'
                        + GENERAL_KINEMATICS.split('\n', 1)[1].replace('def run(i):\n', ''))


class KinematicsPlanner:
    """Writes one wrong check (as the model did in SOLVE-SUITE-1 v3); the blind auditor gets it right."""
    identity = {'model': 'scripted-planner'}
    last_usage = {}

    def __init__(self, auditor_knows=True):
        self.auditor_knows = auditor_knows

    def propose(self, context):
        if 'inputs' in context:
            if not self.auditor_knows:
                return {}
            out = []
            for i in context['inputs']:
                ns = {}
                exec(GENERAL_KINEMATICS, ns)
                out.append(ns['run'](i))
            return {'expected': out}
        return {'contract': 'run(i) returns [final position, final velocity]', 'input': None, 'checks': [
            {'input': {'v0': 0, 'segments': [[2, 3]]}, 'expected': [9.0, 6.0]},
            {'input': {'v0': 5, 'segments': [[0, 2], [-1, 4]]}, 'expected': [6.0, 1.0]},  # wrong: [22.0, 1.0]
            {'input': {'v0': 0, 'segments': []}, 'expected': [0.0, 0.0]}]}


class GamingActor:
    """Writes the general method; told to repair, it hard-codes the failing check, until rejected."""
    identity = {'model': 'scripted-actor'}
    last_usage = {}

    def __init__(self, stubborn=False):
        self.stubborn = stubborn

    def propose(self, context):
        request, history = context['request'], context['history']
        if history and 'artifact' in history[-1]['observation']:
            return {'kind': 'answer', 'answer': {'artifact': history[-1]['observation']['artifact']}}
        repairing, rejected = 'Best attempt so far' in request, 'That is rejected' in request
        gaming = (repairing or rejected) if self.stubborn else (repairing and not rejected)
        source = HARDCODED_KINEMATICS if gaming else GENERAL_KINEMATICS
        return {'kind': 'experiment', 'tool': 'remember_method',
                'input': {'source': source, 'name': 'm', 'description': 'm'},
                'predictions': [{'hypothesis': 'stored', 'outcome': 'artifact id'}]}


class MemorizedCheckTests(unittest.TestCase):
    def solver(self, planner, actor):
        counter = {'used': 0, 'limit': 40}
        return Solver(Workspace(tempfile.mkdtemp()), Metered(planner, counter), Metered(actor, counter),
                      local_executor, say=lambda text: None)

    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_a_method_that_hard_codes_a_wrong_check_is_rejected_and_the_check_corrected(self):
        # The general method fails only the hand-worked check, so that check is questioned before any
        # repair (pramāṇa first): it is corrected, and the actor is never pushed to hard-code it.
        result = self.solver(KinematicsPlanner(), GamingActor()).solve('Cart kinematics.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['strategies'][0], 'audit')
        self.assertNotIn('unmemorize', result['strategies'])
        self.assertNotIn('[6.0, 1.0]', result['source'])
        fixed = {json.dumps(c['input'], sort_keys=True): c['expected'] for c in result['checks']}
        self.assertEqual(fixed[json.dumps({'v0': 5, 'segments': [[0, 2], [-1, 4]]}, sort_keys=True)], [22.0, 1.0])
        self.assertIn('corrected by blind re-derivation', result['checks_by'])

    def test_a_method_that_hard_codes_from_the_start_is_rejected_and_the_check_still_corrected(self):
        class EagerGamer(GamingActor):
            attempts = 0

            def propose(inner, context):   # hard-codes on its first attempt only
                request, history = context['request'], context['history']
                if history and 'artifact' in history[-1]['observation']:
                    return {'kind': 'answer', 'answer': {'artifact': history[-1]['observation']['artifact']}}
                inner.attempts += 1
                source = HARDCODED_KINEMATICS if inner.attempts == 1 else GENERAL_KINEMATICS
                return {'kind': 'experiment', 'tool': 'remember_method',
                        'input': {'source': source, 'name': 'm', 'description': 'm'},
                        'predictions': [{'hypothesis': 'stored', 'outcome': 'artifact id'}]}
        result = self.solver(KinematicsPlanner(), EagerGamer()).solve('Cart kinematics.')
        self.assertEqual(result['status'], 'checked')
        self.assertIn('unmemorize', result['strategies'])
        self.assertNotIn('[6.0, 1.0]', result['source'])
        fixed = {json.dumps(c['input'], sort_keys=True): c['expected'] for c in result['checks']}
        self.assertEqual(fixed[json.dumps({'v0': 5, 'segments': [[0, 2], [-1, 4]]}, sort_keys=True)], [22.0, 1.0])

    def test_a_method_that_keeps_memorizing_is_never_delivered(self):
        result = self.solver(KinematicsPlanner(auditor_knows=False), GamingActor(stubborn=True)).solve('Cart kinematics.')
        self.assertNotEqual(result['status'], 'checked')
        self.assertIn('literally', result['reason'])


class MemorizationDetectorTests(unittest.TestCase):
    def test_flags_literal_returns_guarded_by_the_check_input(self):
        from kuttaka_runtime.check_memorization import memorized_checks
        checks = [{'input': {'v0': 5, 'segments': [[0, 2], [-1, 4]]}, 'expected': [6.0, 1.0]}]
        self.assertEqual(memorized_checks(HARDCODED_KINEMATICS, checks), checks)
        self.assertEqual(memorized_checks(GENERAL_KINEMATICS, checks), [])

    def test_ordinary_edge_cases_are_not_flagged(self):
        from kuttaka_runtime.check_memorization import memorized_checks
        self.assertEqual(memorized_checks('def run(n):\n    if n == 0:\n        return 1\n    return n * run(n - 1)\n',
                                          [{'input': 0, 'expected': 1}]), [])
        self.assertEqual(memorized_checks('def run(i):\n    if not i["grid"]:\n        return -1\n    return 4\n',
                                          [{'input': {'grid': []}, 'expected': -1}]), [])
        self.assertEqual(memorized_checks('def run(x):\n    if x > 3:\n        return [x, x * 2]\n    return [0, 0]\n',
                                          [{'input': 5, 'expected': [5, 10]}]), [])
        self.assertEqual(memorized_checks('this is not python', [{'input': 1, 'expected': [1, 2]}]), [])
