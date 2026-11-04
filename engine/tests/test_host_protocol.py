"""Host steps instead of model calls, and the scale test; no model service is called.

Retrieval before the first call and validation after each stored method are host work
(Solver.host_protocol, runtime.investigate's prelude and auto_validate). In the recorded hard runs these
two tools took most actor replies (W4-k Glimmer: 17 find_methods and 17 validate_method of 68 replies;
Llama 90B: 196 find_methods of 334, with 8 methods written). The scale test asks again for a checked method
that times out on a valid input at the top of the stated range (the planner's large_inputs).

Every source below is an authored test fixture run through test executors only. Each test has a
control: the same fixture under the model-driven protocol, or without large_inputs.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.execution import RUNNER, PythonExecutor
from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_evidence_integration import ScriptedModel, SequenceModel, find_action, remember_action

REQUEST = 'Construct a general increment procedure: run(x) returns x+1.'
CHECKS = [{'input': 2, 'expected': 3}, {'input': 7, 'expected': 8}]
SOURCE = 'def run(x):\n    return x + 1\n'
WRONG = 'def run(x):\n    return x + 2\n'


def validate_action():
    return {'kind': 'experiment', 'tool': 'validate_method', 'input': {'artifact': ''},
            'predictions': [{'hypothesis': 'it passes', 'outcome': 'passed'}]}


class HostSolver:
    def __init__(self, case, host=True):
        self.case, self.host = case, host

    def __enter__(self):
        self.old = Solver.host_protocol
        Solver.host_protocol = self.host
        return self

    def __exit__(self, *exc):
        Solver.host_protocol = self.old


class HostProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.number = 0

    def solver(self, actor, calls=8, workspace=None):
        self.number += 1
        counter = {'used': 0, 'limit': calls}
        metered = Metered(actor, counter)
        return Solver(workspace or Workspace(self.root / str(self.number)), metered, metered, local_executor,
                      say=lambda _: None)

    def attempt(self, solver):
        result = solver.attempt(REQUEST, CHECKS)
        return result, Journal.read(Path(result['run']) / 'events.jsonl')

    def test_one_model_call_writes_and_delivers_a_method(self):
        actor = ScriptedModel(remember_action(SOURCE, 'increment', 'x+1'))
        with HostSolver(self):
            result, events = self.attempt(self.solver(actor))
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(actor.calls, 1)
        first = actor.contexts[0]
        self.assertNotIn('find_methods', first['tools'])
        self.assertIn('the host validates', first['tools']['remember_method'])
        self.assertEqual(first['history'][0]['action']['by'], 'host')
        self.assertEqual(first['history'][0]['action']['tool'], 'find_methods')
        self.assertEqual(first['history'][0]['observation']['retrieval'], 'empty')
        hosted = [e['body']['reason'] for e in events if e['kind'] == 'host_experiment']
        self.assertEqual(hosted, ['prelude', 'auto_validate'])
        start = next(e['body'] for e in events if e['kind'] == 'start')
        self.assertEqual(start['runtime_options']['auto_validate'], ['remember_method', 'validate_method'])

    def test_control_the_model_driven_protocol_needs_three_calls(self):
        actor = SequenceModel([find_action(), remember_action(SOURCE, 'increment', 'x+1'), validate_action()])

        def fill(context):   # validate the artifact the history names, as a model would
            reply = SequenceModel.propose(actor_ref, context)
            if reply.get('tool') == 'validate_method':
                reply = dict(reply, input={'artifact': context['history'][-1]['observation']['artifact']})
            return reply
        actor_ref = actor
        actor.propose = fill
        with HostSolver(self, host=False):
            result, _ = self.attempt(self.solver(actor))
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(actor.calls, 3)
        self.assertIn('find_methods', actor.contexts[0]['tools'])

    def test_a_failed_validation_is_fed_back_without_a_model_call(self):
        def reply(context):
            last = context['history'][-1]['observation'] if context['history'] else {}
            fixed = isinstance(last, dict) and last.get('status') == 'failed'
            return remember_action(SOURCE if fixed else WRONG, 'increment', 'x+1')
        actor = ScriptedModel(reply)
        with HostSolver(self):
            result, events = self.attempt(self.solver(actor))
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(actor.calls, 2)
        feedback = actor.contexts[1]['history'][-1]
        self.assertEqual(feedback['action']['reason'], 'auto_validate')
        self.assertEqual(feedback['observation']['status'], 'failed')
        self.assertEqual(len(feedback['observation']['failed_checks']), 2)

    def test_a_stored_method_is_listed_before_the_first_call(self):
        workspace = Workspace(self.root / 'shared')
        with HostSolver(self):
            seeded, _ = self.attempt(self.solver(ScriptedModel(remember_action(SOURCE, 'increment', 'x+1')),
                                                 workspace=workspace))
            self.assertEqual(seeded['status'], 'checked')

            def reuse(context):
                listed = context['history'][0]['observation']['methods']
                return {'kind': 'answer', 'answer': {'artifact': listed[0]['id']}}
            actor = ScriptedModel(reuse)
            result, _ = self.attempt(self.solver(actor, workspace=workspace))
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['artifact'], seeded['artifact'])
        self.assertEqual(actor.calls, 1)
        self.assertTrue(actor.contexts[0]['history'][0]['observation']['methods'][0]['fits_this_task'])


SLOW = 'def run(x):\n    n = 0\n    for _ in range(x):\n        n += 1\n    return n\n'
FAST = 'def run(x):\n    return x\n'
COUNT = 'run(x) receives an integer x with 0 <= x <= 10^12 and returns how many integers k satisfy 0 <= k < x.'
COUNT_CHECKS = [{'input': 1, 'expected': 1}, {'input': 5, 'expected': 5}, {'input': 12, 'expected': 12}]


def timed_executor(job, timeout=3):
    """Test-only executor with a short limit; a timeout is reported as the sandbox reports one."""
    try:
        proc = subprocess.run([sys.executable, '-I', '-c', RUNNER], input=json.dumps(job).encode(),
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'exit_code': None, 'output': '', 'diagnostics': '', 'dependency_calls': []}
    output, calls = proc.stdout.decode(), []
    if proc.returncode == 0:
        output, calls = PythonExecutor._parse_runner_output(output, libraries_present='libraries' in job)
    return {'status': 'completed', 'exit_code': proc.returncode, 'output': output,
            'diagnostics': proc.stderr.decode(), 'dependency_calls': calls}


class ScaleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def solve(self, large):
        plan = {'contract': COUNT, 'checks': COUNT_CHECKS, 'input': None, 'verify': None, 'reference': None,
                'small_inputs': None, 'large_inputs': large, 'decomposition': None}

        def act(context):
            fast = 'ran out of time or memory' in context['request']
            return remember_action(FAST if fast else SLOW, 'count', 'count below x')

        def audit(context):
            return {'expected': [v if isinstance(v, int) else None for v in context.get('inputs', [])]}
        counter = {'used': 0, 'limit': 30}
        planner, actor, auditor = (Metered(ScriptedModel(r), counter) for r in (plan, act, audit))
        old = Solver.host_protocol
        Solver.host_protocol = True
        try:
            solver = Solver(Workspace(self.root / str(bool(large))), planner, actor, timed_executor,
                            auditor=auditor, say=lambda _: None)
            result = solver.solve(COUNT)
        finally:
            Solver.host_protocol = old
        events = Journal.read(Path(result['solve_journal']))
        return result, [e['body'] for e in events if e['kind'] == 'solve_scale_test']

    def test_a_method_that_times_out_at_the_top_of_the_range_is_asked_for_again(self):
        result, scale = self.solve('[10**12]')
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['source'], FAST)
        self.assertIn('scale', result['strategies'])
        self.assertEqual(scale[0]['timed_out'], 1)
        self.assertEqual(scale[-1]['timed_out'], 0)

    def test_control_without_large_inputs_the_slow_method_is_delivered(self):
        result, scale = self.solve(None)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['source'], SLOW)
        self.assertEqual(scale, [])


if __name__ == '__main__':
    unittest.main()


class ParallelRunnerTests(unittest.TestCase):
    """run.py --jobs: independent tasks side by side give the rows one at a time gives, in less wall time."""

    def setUp(self):
        import importlib
        import threading
        import time
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments' / 'solve_suite'))
        self.run = run = importlib.import_module('run')
        suite = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(suite, ignore_errors=True))
        tasks = [{'id': f't{k}', 'domain': 'd', 'level': 1, 'prompt': f'Multiply a number by {k}.'} for k in (2, 3, 4, 5)]
        (suite / 'tasks.json').write_text(json.dumps(tasks))
        (suite / 'hidden.json').write_text(json.dumps({t['id']: [{'input': 4, 'expected': 4 * k}]
                                                       for t, k in zip(tasks, (2, 3, 4, 5))}))
        saved = {name: getattr(run, name) for name in ('HERE', 'preflight', 'infrastructure_ready',
                                                       'PythonExecutor', 'build_models')}
        self.addCleanup(lambda: [setattr(run, k, v) for k, v in saved.items()])
        run.HERE, run.preflight = suite, lambda: {'ready': True}
        run.infrastructure_ready = lambda wait=0: True
        run.PythonExecutor = lambda **kw: local_executor
        self.live = {'now': 0, 'most': 0}
        lock = threading.Lock()
        live = self.live

        class Model:
            identity = {'model': 'scripted'}
            last_usage = {'input_tokens': 10, 'output_tokens': 5}

            def propose(self, context):
                with lock:
                    live['now'] += 1
                    live['most'] = max(live['most'], live['now'])
                time.sleep(0.3)   # a model call's latency
                with lock:
                    live['now'] -= 1
                text = json.dumps(context)
                k = next(k for k in (2, 3, 4, 5) if f'by {k}' in text)
                if 'request' in context:
                    return {'kind': 'experiment', 'tool': 'remember_method',
                            'input': {'name': 'm', 'description': 'm', 'source': f'def run(x):\n    return {k} * x\n'},
                            'predictions': [{'hypothesis': 'stored', 'outcome': 'id'}]}
                if 'inputs' in context:
                    return {'expected': [k * v for v in context['inputs']]}
                return {'contract': f'run(x) returns {k}*x', 'checks': [{'input': 1, 'expected': k},
                        {'input': 2, 'expected': 2 * k}, {'input': 3, 'expected': 3 * k}], 'input': None}

        run.build_models = lambda args, counter: tuple(Metered(Model(), counter) for _ in range(5))

    def arm(self, jobs):
        out = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(out, ignore_errors=True))
        old = Solver.host_protocol
        Solver.host_protocol = True
        try:
            self.run.main(['--arm', 'fresh', '--out', str(out), '--jobs', str(jobs)])
        finally:
            Solver.host_protocol = old
        rows = sorted((json.loads(line) for line in (out / 'rows.jsonl').read_text().splitlines()),
                      key=lambda r: r['task'])
        return [(r['task'], r['status'], r['hidden_passed'], r['model_calls']) for r in rows]

    def test_parallel_rows_match_sequential_rows(self):
        sequential = self.arm(1)
        self.assertEqual(self.live['most'], 1)
        parallel = self.arm(4)
        self.assertEqual(parallel, sequential)
        self.assertGreater(self.live['most'], 1)
        self.assertTrue(all(status == 'checked' and passed == 1 for _, status, passed, _ in sequential), sequential)

    def test_the_kuttaka_arm_stays_one_at_a_time(self):
        with self.assertRaises(SystemExit):
            self.run.main(['--arm', 'kuttaka', '--out', tempfile.mkdtemp(), '--jobs', '2'])


class LargeInputsTests(unittest.TestCase):
    """The planner gives an expression for its large inputs, and the sandbox builds them.

    W4-l (901439f): asked for the inputs themselves, the planner wrote Python inside its JSON
    ({"s": "a" * 100000}) and h-c2-substrings' plans failed to parse twice per round (needs_examples)."""

    setUp = ScaleTests.setUp

    def test_the_plan_field_is_a_string_expression(self):
        from kuttaka_runtime.solve import PLAN_INSTRUCTIONS, _large_inputs
        self.assertEqual(_large_inputs({'large_inputs': " [{'s': 'ab' * 50000}] "}), "[{'s': 'ab' * 50000}]")
        self.assertIsNone(_large_inputs({'large_inputs': [{'n': 10 ** 18}]}))   # the old literal form
        self.assertIsNone(_large_inputs({'large_inputs': 'x' * 2001}))
        self.assertIn('one Python expression, given as a JSON string', PLAN_INSTRUCTIONS)

    def test_a_long_text_built_in_the_sandbox_finds_a_quadratic_method(self):
        quadratic = ('def run(input):\n    s = input["s"]\n    return len({s[i:j] for i in range(len(s)) '
                     'for j in range(i + 1, len(s) + 1)})\n')
        linear = ('def run(input):\n    s = input["s"]\n    if len(set(s)) == 1:\n        return len(s)\n'
                  '    return len({s[i:j] for i in range(len(s)) for j in range(i + 1, len(s) + 1)})\n')
        checks = [{'input': {'s': 'a'}, 'expected': 1}, {'input': {'s': 'aa'}, 'expected': 2},
                  {'input': {'s': 'ab'}, 'expected': 3}]
        task = 'run(input) receives {"s": str} with up to 100000 letters and returns the number of distinct substrings.'

        def solve(expression):
            plan = {'contract': task, 'checks': checks, 'input': None, 'verify': None, 'reference': None,
                    'small_inputs': None, 'large_inputs': expression, 'decomposition': None}

            def act(context):
                fast = 'ran out of time or memory' in context['request']
                return remember_action(linear if fast else quadratic, 'count', 'distinct substrings')

            def audit(context):
                return {'expected': [None for _ in context.get('inputs', [])]}
            counter = {'used': 0, 'limit': 30}
            planner, actor, auditor = (Metered(ScriptedModel(r), counter) for r in (plan, act, audit))
            old = Solver.host_protocol
            Solver.host_protocol = True
            try:
                solver = Solver(Workspace(self.root / str(expression)[:8].replace("'", '')), planner, actor,
                                timed_executor, auditor=auditor, say=lambda _: None)
                result = solver.solve(task)
            finally:
                Solver.host_protocol = old
            events = Journal.read(Path(result['solve_journal']))
            return result, [e['body'] for e in events if e['kind'] == 'solve_large_inputs']

        result, built = solve("[{'s': 'a' * 100000}]")
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['source'], linear)
        self.assertEqual(built[0]['built'], 1)
        self.assertGreater(built[0]['sizes'][0], 100000)
        control, built = solve(None)
        self.assertEqual(control['source'], quadratic)
        self.assertEqual(built, [])


class ScaleExhaustionTests(unittest.TestCase):
    """Running out of memory counts as a scale failure; a deliberate refusal does not."""

    def verdict(self, response):
        from unittest import mock
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), None, None, lambda job: response, say=lambda _: None)
        spec = {'check_scope': '0' * 64, 'large_inputs': '[1]', 'large_values': [1]}
        with mock.patch('kuttaka_runtime.demo.load_method', return_value=('def run(x):\n    return x\n', {})):
            return solver.scale_test(spec, 'a' * 64)

    def test_memory_exhaustion_counts(self):
        self.assertEqual(self.verdict({'status': 'completed', 'exit_code': 137, 'output': '', 'diagnostics': ''}), [1])
        self.assertEqual(self.verdict({'status': 'completed', 'exit_code': 1, 'output': '',
                                       'diagnostics': 'Traceback ...\nMemoryError\n'}), [1])

    def test_a_success_that_mentions_memory_error_does_not_count(self):
        # The runner's review of 974bd05: a run that exits 0 and prints the word was counted as running out.
        self.assertEqual(self.verdict({'status': 'completed', 'exit_code': 0, 'output': '{"result": 1}',
                                       'diagnostics': 'MemoryError was caught; recovered successfully'}), [])
        self.assertEqual(self.verdict({'status': 'completed', 'exit_code': 1, 'output': '',
                                       'diagnostics': 'Traceback ...\nValueError: MemoryError mentioned\n'}), [])

    def test_no_built_input_reads_as_not_run(self):
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), None, None, lambda job: {}, say=lambda _: None)
        spec = {'check_scope': '0' * 64, 'large_inputs': '[1]', 'large_values': []}
        from unittest import mock
        with mock.patch('kuttaka_runtime.demo.load_method', return_value=('def run(x):\n    return x\n', {})):
            self.assertEqual(solver.scale_test(spec, 'a' * 64), [])
        self.assertEqual(solver._mechanical, {'scale_not_run': 1})

    def test_control_a_refusal_does_not_count(self):
        self.assertEqual(self.verdict({'status': 'completed', 'exit_code': 1, 'output': '',
                                       'diagnostics': 'Traceback ...\nValueError: n out of range\n'}), [])


class InvalidPlanReplyTests(unittest.TestCase):
    """A plan reply that is not JSON is named as such to the second try, not as a missing contract."""

    def test_the_retry_is_told_the_reply_was_not_json(self):
        good = {'contract': 'run(x) returns x+1', 'checks': CHECKS, 'input': None, 'verify': None,
                'reference': None, 'small_inputs': None, 'large_inputs': None, 'decomposition': None}
        planner = SequenceModel([{'kind': 'invalid', 'raw_reply': '{"contract": "x", "large_inputs": [{"s": "a" * 9}]}'},
                                 good])
        counter = {'used': 0, 'limit': 8}
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), Metered(planner, counter), Metered(planner, counter),
                        local_executor, say=lambda _: None)
        spec = solver.plan(REQUEST, [])
        self.assertEqual(spec['contract'], 'run(x) returns x+1')
        told = planner.contexts[1]['previous_reply_rejected']
        self.assertIn('not valid JSON', told)
        self.assertNotIn('contract must be', told)

    def test_control_a_reply_without_a_contract_is_still_named_so(self):
        planner = SequenceModel([{'checks': CHECKS}, {'checks': CHECKS}])
        counter = {'used': 0, 'limit': 8}
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), Metered(planner, counter), Metered(planner, counter),
                        local_executor, say=lambda _: None)
        self.assertIsNone(solver.plan(REQUEST, []))
        self.assertIn('contract must be', planner.contexts[1]['previous_reply_rejected'])


class DegenerateChecksTests(unittest.TestCase):
    """Checks that all expect one value pin nothing: the plan is asked for again (ERDŐS-979-c: three checks,
    all {"n": null, "representations": []}; a constant passed them and was reported checked with no answer)."""

    def plan(self, replies):
        planner = SequenceModel(replies)
        counter = {'used': 0, 'limit': 8}
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), Metered(planner, counter), Metered(planner, counter),
                        local_executor, say=lambda _: None)
        return solver.plan(REQUEST, []), planner

    def reply(self, expected):
        return {'contract': 'run(x) returns the answer', 'input': None, 'verify': None, 'reference': None,
                'small_inputs': None, 'large_inputs': None, 'decomposition': None,
                'checks': [{'input': k, 'expected': e} for k, e in enumerate(expected)]}

    def test_same_expected_values_are_sent_back(self):
        spec, planner = self.plan([self.reply([None, None, None]), self.reply([None, 64, 726724])])
        self.assertEqual([c['expected'] for c in spec['checks']], [None, 64, 726724])
        self.assertIn('every check expects the same result', planner.contexts[1]['previous_reply_rejected'])

    def test_a_check_on_the_requested_input_itself_is_sent_back(self):
        # ERDŐS-979-d round 2 at 2aa344c: a zero-argument contract with one check, input null, expecting "none".
        single = dict(self.reply([{'n': 0, 'representations': []}]), contract='run() returns the witness')
        single['checks'][0]['input'] = None
        spec, planner = self.plan([single, self.reply([64, 726724])])
        self.assertEqual([c['expected'] for c in spec['checks']], [64, 726724])
        self.assertIn('every check has input null', planner.contexts[1]['previous_reply_rejected'])

    def test_control_a_hand_answer_on_a_named_requested_input_is_accepted(self):
        on_answer = dict(self.reply([1, 2]), input=[1, 2, 3])   # [1, 2, 3] -> 14, worked by hand: a real check
        on_answer['checks'] = [{'input': [1, 2, 3], 'expected': 14}]
        spec, planner = self.plan([on_answer])
        self.assertEqual(len(spec['checks']), 1)
        self.assertEqual(planner.calls, 1)

    def test_control_one_check_on_another_input_is_accepted_at_once(self):
        spec, planner = self.plan([self.reply([4])])   # run(0) -> 4, no requested input: a weak but real check
        self.assertEqual(len(spec['checks']), 1)
        self.assertEqual(planner.calls, 1)

    def test_control_the_requested_input_among_other_checks_is_accepted(self):
        mixed = dict(self.reply([64, 726724]), input=1)   # checks on 0 and 1; the requested input is 1
        spec, planner = self.plan([mixed])
        self.assertEqual(len(spec['checks']), 2)
        self.assertEqual(planner.calls, 1)

    def test_control_different_expected_values_are_accepted_at_once(self):
        spec, planner = self.plan([self.reply([1, 2, 3])])
        self.assertEqual(len(spec['checks']), 3)
        self.assertEqual(planner.calls, 1)

    def test_control_a_user_example_is_never_refused(self):
        planner = SequenceModel([self.reply([5, 5])])
        counter = {'used': 0, 'limit': 8}
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), Metered(planner, counter), Metered(planner, counter),
                        local_executor, say=lambda _: None)
        spec = solver.plan(REQUEST, [{'input': 9, 'expected': 5}])
        self.assertIsNotNone(spec)
        self.assertEqual(planner.calls, 1)


class RefusedPlansTeachTests(unittest.TestCase):
    """ERDŐS-979-e: each of three rounds began again with a plan with no checks (refused with "checks must be
    nonempty", which said nothing of what to do), then a zero-argument plan (refused); nothing of either reached the
    next round. The empty-checks refusal now says to generalize a parameter, and a round whose plans were all refused
    leaves a lesson naming why, which the next round's planner receives."""

    def test_the_refusal_says_what_to_do_and_the_next_plan_hears_it(self):
        empty = dict(DegenerateChecksTests.reply(None, []), contract='run() returns the witness')
        null = dict(DegenerateChecksTests.reply(None, [{'n': 0}]), contract='run() returns the witness')
        null['checks'][0]['input'] = None
        planner = SequenceModel([empty, null, DegenerateChecksTests.reply(None, [64, 726724])])
        counter = {'used': 0, 'limit': 8}
        workspace = Workspace(Path(tempfile.mkdtemp()))
        solver = Solver(workspace, Metered(planner, counter), Metered(planner, counter), local_executor,
                        say=lambda _: None)
        self.assertIsNone(solver.plan(REQUEST, []))
        self.assertIn('generalize a parameter', planner.contexts[1]['previous_reply_rejected'])
        lessons = workspace.lessons(REQUEST)
        self.assertEqual(len(lessons), 1)
        self.assertIn('no plan was accepted: every check has input null', lessons[0]['mistake'])
        self.assertIsNotNone(solver.plan(REQUEST, []))   # the next round
        self.assertEqual(planner.contexts[2]['lessons'], lessons)

    def test_control_an_accepted_plan_leaves_no_lesson(self):
        planner = SequenceModel([DegenerateChecksTests.reply(None, [64, 726724])])
        counter = {'used': 0, 'limit': 8}
        workspace = Workspace(Path(tempfile.mkdtemp()))
        solver = Solver(workspace, Metered(planner, counter), Metered(planner, counter), local_executor,
                        say=lambda _: None)
        self.assertIsNotNone(solver.plan(REQUEST, []))
        self.assertEqual(workspace.lessons(REQUEST), [])


class ScaleReportedTests(unittest.TestCase):
    """The result says whether the scale test ran (a first-hand run delivered a slow method as checked, with the
    scale test not run and nothing in the result to say so), and host-protocol guidance does not ask for validation."""

    setUp = ScaleTests.setUp

    def test_the_result_names_the_scale_outcome(self):
        result, _ = ScaleTests.solve(self, '[10**12]')
        self.assertEqual(result['scale'], 'held')

    def test_control_without_an_expression_it_says_not_asked(self):
        result, _ = ScaleTests.solve(self, None)
        self.assertEqual(result['scale'], 'not_asked')

    def test_guidance_under_the_host_protocol_does_not_ask_for_validation(self):
        from kuttaka_runtime.solve import ProgressActor
        journal = Journal(Path(tempfile.mkdtemp()) / 'j.jsonl')
        with journal:
            hosted = ProgressActor(None, journal, set(), host_validates=True).observe_retrieval(
                {}, {'methods': [], 'retrieval': 'empty'})
            plain = ProgressActor(None, journal, set()).observe_retrieval({}, {'methods': [], 'retrieval': 'empty'})
        self.assertIn('do not call validate_method', hosted['suggested_next_step']['instruction'])
        self.assertIn('Then validate or submit', plain['suggested_next_step']['instruction'])
