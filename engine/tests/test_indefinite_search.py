"""Where Kuttaka broke on an indefinite search (first-hand sessions f and g, 2026-11-02, the Erdős #979 prompt with
the builder as the model; docs/reviews/first_hand_2026-11-02/README.md):

1. One probe input that never finishes ended its whole sandbox run, and every other value in the batch was lost
   (session f: the near inputs and the requested input were lost each time, 20 s each, the same jobs three times).
   Now each value has its own time; a value cut short says nothing, and is not run again under the same source.
2. A method that searches a fixed range returned null on the requested input; the property rightly rejected null;
   the auditor, unable to work the open answer out by hand, said null, which counted as agreeing with the method;
   the property was overruled and null was delivered as a checked answer (session g). Now an auditor null is no
   agreement, a null on the requested input is a perceived failure (the method is asked for again once, shown its
   source), and a null answer is never delivered as checked.
3. A final run that does not finish (6 h in the Erdős runner) was reported as a bare failure, the actor was never
   told the limits, and a later round would run the same method on the same input again. Now the reason names the
   limit, the actor's request states it, and the same method is not run again on the same input with no more time.

Scenario: run({"s": s}) returns the smallest positive integer whose digits sum to s; requested input {"s": 30}
(3999). A fixed-range method searches below 1000 and returns null there. Scripted models; local executor."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import DIFFERENTIAL_RUN, Metered, Solver, Workspace
from tests.test_host_protocol import timed_executor
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Find the smallest positive integer whose decimal digits sum to 30.'
CONTRACT = 'run({"s": s}) returns the smallest positive integer whose decimal digits sum to s, for s >= 1'
FIXED = ('def run(x):\n    for n in range(1, 1000):\n        if sum(map(int, str(n))) == x["s"]:\n'
         '            return n\n    return None\n')
RIGHT = ('def run(x):\n    n = 1\n    while sum(map(int, str(n))) != x["s"]:\n        n += 1\n    return n\n')
VERIFY = 'def verify(input, output):\n    return isinstance(output, int) and sum(map(int, str(output))) == input["s"]\n'
REFERENCE = RIGHT.replace('def run', 'def reference')


def plan(context):
    return {'contract': CONTRACT, 'input': {'s': 30}, 'verify': VERIFY, 'reference': REFERENCE,
            'checks': [check({'s': 1}, 1), check({'s': 2}, 2), check({'s': 10}, 19), check({'s': 11}, 29)],
            'small_inputs': [{'s': 5}, {'s': 9}, {'s': 12}]}


def solve(first, fixes, audit_value, ws=None, deploy_executor=None, requests=None):
    def act(request):
        if requests is not None:
            requests.append(request)
        return store(RIGHT if fixes and 'returned null on the requested input' in request else first)
    audit = lambda c: {'expected': [audit_value if i == {'s': 30} else None for i in c['inputs']]}
    m = Metered(Model(plan, act, audit=audit), {'used': 0, 'limit': 20})
    solver = Solver(ws or Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None, deploy_executor=deploy_executor)
    return solver.solve(TASK)


class NullIsNoAnswer(unittest.TestCase):
    def test_a_fixed_range_method_shown_its_null_is_fixed_and_answers(self):
        result = solve(FIXED, True, None)
        self.assertIn('no answer', result['strategies'])
        self.assertEqual((result['status'], result.get('answer')), ('checked', 3999))
        self.assertEqual([e['outcome'] for e in events(result, 'solve_answer_probe')], ['null'])

    def test_a_null_kept_is_never_delivered_as_checked(self):
        # On the old code: status checked, answer null (the auditor's null overruled the property).
        result = solve(FIXED, False, None)
        self.assertEqual(result['strategies'].count('no answer'), 1)
        self.assertNotEqual(result['status'], 'checked')
        self.assertIsNone(result.get('answer'))
        overruled = [e for e in events(result, 'solve_property_test') if e.get('outcome') == 'property_overruled']
        self.assertEqual(overruled, [], 'an auditor null overruled the property')

    def test_control_a_method_that_answers_is_not_asked_again(self):
        result = solve(RIGHT, False, None)
        self.assertNotIn('no answer', result['strategies'])
        self.assertEqual((result['status'], result.get('answer')), ('checked', 3999))


class SlowDeploy:
    """A final-run executor with a stated limit, on which the requested input never finishes."""
    def __init__(self, timeout, memory='2g'):
        self.timeout, self.memory, self.calls = timeout, memory, 0

    def __call__(self, job):
        self.calls += 1
        return {'status': 'timeout', 'exit_code': None, 'output': '', 'diagnostics': '', 'dependency_calls': []}


class UnfinishedFinalRun(unittest.TestCase):
    def test_the_limit_is_told_named_and_not_spent_twice(self):
        ws, deploy, requests = Workspace(Path(tempfile.mkdtemp())), SlowDeploy(21600), []
        first = solve(RIGHT, False, None, ws=ws, deploy_executor=deploy, requests=requests)
        self.assertIn('with a limit of 21600 seconds and 2g of memory', requests[0])
        self.assertEqual(first['status'], 'incomplete')
        self.assertIn('failed on the requested input: it did not finish within 21600 s', first['reason'])
        self.assertEqual(first['unfinished']['seconds'], 21600)
        self.assertEqual(deploy.calls, 1)
        again = solve(RIGHT, False, None, ws=ws, deploy_executor=deploy)
        self.assertEqual(deploy.calls, 1, 'the same method was run again on the same input with no more time')
        self.assertIn('in an earlier round (not run again)', again['reason'])

    def test_control_more_time_runs_it_again(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        solve(RIGHT, False, None, ws=ws, deploy_executor=SlowDeploy(600))
        longer = SlowDeploy(21600)
        solve(RIGHT, False, None, ws=ws, deploy_executor=longer)
        self.assertEqual(longer.calls, 1)


class CountingExecutor:
    """Runs jobs locally; records each job's input."""
    def __init__(self):
        self.inputs = []

    def __call__(self, job):
        self.inputs.append(job['input'])
        return local_executor(job)


NEVER_BELOW_ZERO = 'def run(n):\n    while n < 0:\n        pass\n    return n * 2\n'


class ProbeTime(unittest.TestCase):
    def test_one_value_that_never_finishes_leaves_the_others(self):
        # On the old template the whole run timed out (status timeout) and no value came back.
        got = timed_executor({'source': DIFFERENTIAL_RUN % (NEVER_BELOW_ZERO, 'run'), 'input': [1, -1, 3]}, timeout=20)
        result = [{k: v for k, v in e.items() if k != 'seconds'} for e in json.loads(got['output'])['result']]
        self.assertEqual(result, [{'value': 2}, {'timeout': True}, {'value': 6}])   # 'seconds': the time it had

    def test_a_cut_value_that_holds_much_memory_does_not_end_the_batch(self):
        # Session h: freeing a large structure after the stop let the repeating stop land outside its handler,
        # and the whole batch ended with an uncaught _OutOfTime (exit code 1), as if no value had run.
        big = ('def run(n):\n    if n >= 0:\n        return n\n    heap = [(i, (i, i, i), i) for i in range(4000000)]\n'
               '    while True:\n        heap.append((1, (1,), 1))\n')
        got = timed_executor({'source': DIFFERENTIAL_RUN % (big, 'run'), 'input': [1, -1]}, timeout=40)
        result = [{k: v for k, v in e.items() if k != 'seconds'} for e in json.loads(got['output'])['result']]
        self.assertEqual((got['exit_code'], result), (0, [{'value': 1}, {'timeout': True}]))

    def test_a_value_cut_short_is_not_run_again_under_the_same_source(self):
        executor = CountingExecutor()
        m = Metered(Model(plan, lambda r: store(RIGHT)), {'used': 0, 'limit': 5})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, executor, say=lambda _: None)
        solver._slow_probes, solver._job, solver._execution_used = set(), None, 0
        source = DIFFERENTIAL_RUN % ('def run(n):\n    return n\n', 'run')
        key_source = DIFFERENTIAL_RUN % (NEVER_BELOW_ZERO, 'run')
        from kuttaka_runtime.solve import canonical, digest
        solver._slow_probes.add((digest({'source': key_source, 'libraries': {}}), canonical(-1)))
        self.assertEqual(solver._sandbox_job(key_source, [-1], None), [{'timeout': True}])
        self.assertEqual(executor.inputs, [], 'a value known to run out was run again')
        self.assertEqual(solver._sandbox_job(key_source, [4, -1], None), [{'value': 8}, {'timeout': True}])
        self.assertEqual(executor.inputs, [[4]])
        # Control: another source is run on the same value.
        self.assertEqual(solver._sandbox_job(source, [-1], None), [{'value': -1}])


if __name__ == '__main__':
    unittest.main()


class SameValueOnce(unittest.TestCase):
    """First-hand session p (2026-11-02, the totient sum to 10^10): the checked method ran four times on the requested
    input, 9 s each, in the null probe, the scale test (its large input was the requested input), the differential
    and the final run. A value a probe computed under the same code is not computed again in the same solve; the
    final run is the delivery and still runs."""

    def solve_counting(self):
        jobs = []

        def executor(job):
            jobs.append(job)
            return local_executor(job)
        plan_large = lambda context: dict(plan(context), large_inputs="[{'s': 30}]")
        m = Metered(Model(plan_large, lambda r: store(RIGHT), audit=lambda c: {}), {'used': 0, 'limit': 20})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, executor, splitter=m, auditor=m, sketcher=m,
                        max_executions=256, say=lambda _: None)
        result = solver.solve(TASK)
        on_requested = [j for j in jobs if "space['run'](value)" in j['source'] and {'s': 30} in j['input']
                        or j['source'].startswith('def run') and j['input'] == {'s': 30}]
        return result, on_requested

    def test_the_method_runs_on_the_requested_input_once_before_the_final_run(self):
        result, on_requested = self.solve_counting()
        self.assertEqual((result['status'], result.get('answer'), result['scale']), ('checked', 3999, 'held'))
        self.assertEqual(len(on_requested), 2, 'a probe and the final run')

    def test_a_finished_value_is_reused_only_for_the_same_source(self):
        executor = CountingExecutor()
        m = Metered(Model(plan, lambda r: store(RIGHT)), {'used': 0, 'limit': 5})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, executor, say=lambda _: None)
        solver._job, solver._execution_used = None, 0
        double = DIFFERENTIAL_RUN % ('def run(n):\n    return n * 2\n', 'run')
        triple = DIFFERENTIAL_RUN % ('def run(n):\n    return n * 3\n', 'run')
        self.assertEqual(solver._sandbox_job(double, [4], None), [{'value': 8}])
        self.assertEqual(solver._sandbox_job(double, [4, 5], None), [{'value': 8}, {'value': 10}])
        self.assertEqual(executor.inputs, [[4], [5]])
        self.assertEqual(solver._sandbox_job(triple, [4], None), [{'value': 12}])   # control: another source runs
        self.assertEqual(executor.inputs, [[4], [5], [4]])


class HowItRanOut(unittest.TestCase):
    """First-hand session r (2026-11-02, an exact evaluator): the scale test rightly failed a method on 300 nested
    parentheses, but told the actor only "use an algorithm whose cost fits the stated sizes (a sieve, dynamic
    programming...)"; the method had run out of recursion depth, not time. The actor is now told how each input ran
    out. Scenario: a recursive sum of 1..n, checked on small n; the large input n = 100000 overflows the stack."""

    def test_the_actor_is_told_the_recursion_ran_out_and_the_rewrite_is_checked(self):
        recursive = 'def run(x):\n    n = x["n"]\n    return 0 if n == 0 else n + run({"n": n - 1})\n'
        loop = 'def run(x):\n    return x["n"] * (x["n"] + 1) // 2\n'
        requests = []

        def act(request):
            requests.append(request)
            return store(loop if 'RecursionError' in request else recursive)
        plan_sum = lambda c: {'contract': 'run({"n": n}) returns 1 + 2 + ... + n for 0 <= n <= 100000', 'input': None,
                              'checks': [check({'n': 0}, 0), check({'n': 1}, 1), check({'n': 4}, 10)],
                              'large_inputs': "[{'n': 100000}]"}
        m = Metered(Model(plan_sum, act, audit=lambda c: {}), {'used': 0, 'limit': 10})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                        max_executions=256, say=lambda _: None)
        result = solver.solve('Add up the numbers 1 to n.')
        self.assertEqual((result['status'], result['scale']), ('checked', 'held'))
        self.assertIn('"ran out": "RecursionError', requests[1])
        self.assertNotIn('did not finish within the time limit', requests[1])


class SharedCrash(unittest.TestCase):
    """First-hand session r (2026-11-02): a fault shared by the method and the reference passes every check (both
    crashed on results of more than 4300 digits, Python's limit on writing integers). Where the plan itself declares
    an input valid (a small input, or a large input at the top of the stated range), an exception is no value of the
    contract, whatever the reference does: before, the scale test counted it as the method refusing a bad input and
    the differential as no evidence. The method is now asked for again once, shown the exception.
    Scenario: run({"n": n}) returns the decimal digits of 2^n for 0 <= n <= 20000; method and reference both write
    str(2 ** n), which raises at n = 20000."""
    CRASHES = 'def run(x):\n    return str(2 ** x["n"])\n'
    FIXED = 'import sys\n\ndef run(x):\n    sys.set_int_max_str_digits(0)\n    return str(2 ** x["n"])\n'

    def solve(self, large=None, small=None, first=None):
        requests = []

        def act(request):
            requests.append(request)
            return store(self.FIXED if 'raised an exception' in request else (first or self.CRASHES))
        plan_digits = lambda c: {'contract': 'run({"n": n}) returns the decimal digits of 2^n as a string, 0 <= n <= 20000',
                                 'input': None, 'checks': [check({'n': 0}, '1'), check({'n': 1}, '2'), check({'n': 10}, '1024')],
                                 'reference': 'def reference(x):\n    return str(2 ** x["n"])\n',
                                 'small_inputs': small or [{'n': 3}, {'n': 7}], 'large_inputs': large}
        m = Metered(Model(plan_digits, act, audit=lambda c: {}), {'used': 0, 'limit': 12})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                        max_executions=256, say=lambda _: None)
        return solver.solve('Write the decimal digits of 2 to the power n.'), requests

    def test_a_crash_on_a_large_input_the_plan_declares_valid_is_asked_about(self):
        result, requests = self.solve(large="[{'n': 20000}]")
        self.assertEqual(result['status'], 'checked')
        self.assertIn('raised', result['strategies'])
        self.assertIn('ValueError', requests[1])
        self.assertIn('set_int_max_str_digits', result['source'])

    def test_a_crash_the_reference_shares_on_a_declared_small_input_is_asked_about(self):
        result, requests = self.solve(small=[{'n': 3}, {'n': 20000}])
        self.assertEqual(result['status'], 'checked')
        self.assertIn('raised', result['strategies'])
        self.assertIn('the method and the reference alike', requests[1])

    def test_control_a_method_that_does_not_crash_is_not_asked_again(self):
        result, requests = self.solve(large="[{'n': 20000}]", small=[{'n': 3}, {'n': 20000}], first=self.FIXED)
        self.assertEqual(result['status'], 'checked')
        self.assertNotIn('raised', result['strategies'])
        self.assertEqual(len(requests), 1)


class NestedByRule(unittest.TestCase):
    """W4-r h-c1-regex (2026-11-03): the delivered method raised RecursionError on 300 nested groups, an input its
    contract allows (the runner's probe; not shown to be the hidden case it failed), and no scale test ran because the
    plan named no sizes. Now a check whose text holds a bracket pair is also wrapped NEST_DEPTH levels deep, by a rule,
    for the scale test, where only running out counts. Scenario: the deepest nesting of parentheses in a string, by a
    method that recurses twice per character."""
    RECURSIVE = ('def walk(s, i, d, best):\n    return best if i == len(s) else step(s, i, d, best)\n\n\n'
                 'def step(s, i, d, best):\n    d = d + 1 if s[i] == "(" else d - 1\n    return walk(s, i + 1, d, max(best, d))\n\n\n'
                 'def run(x):\n    return walk(x["s"], 0, 0, 0)\n')
    FIXED = 'import sys\n' + RECURSIVE.replace('def run(x):\n', 'def run(x):\n    sys.setrecursionlimit(20000)\n')
    REFUSES_LONG = ('def run(x):\n    s = x["s"]\n    if len(s) > 100:\n        raise ValueError("too long")\n'
                    '    d = best = 0\n    for c in s:\n        d = d + 1 if c == "(" else d - 1\n        best = max(best, d)\n'
                    '    return best\n')
    CHECKS = [check({'s': ''}, 0), check({'s': '()'}, 1), check({'s': '(())'}, 2), check({'s': '()()'}, 1),
              check({'s': '(()())'}, 2)]

    def solve(self, first, checks=None):
        requests = []

        def act(request):
            requests.append(request)
            return store(self.FIXED if 'RecursionError' in request else first)
        plan_depth = lambda c: {'contract': 'run({"s": s}) returns the deepest nesting of the balanced parentheses in s',
                                'input': None, 'checks': checks or self.CHECKS}
        m = Metered(Model(plan_depth, act, audit=lambda c: {}), {'used': 0, 'limit': 10})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                        max_executions=256, say=lambda _: None)
        return solver.solve('How deeply are the parentheses nested?'), requests

    def test_a_method_that_runs_out_of_recursion_on_nesting_is_told_and_fixed(self):
        result, requests = self.solve(self.RECURSIVE)
        self.assertEqual((result['status'], result['scale']), ('checked', 'held'))
        self.assertIn('scale', result['strategies'])
        self.assertIn('RecursionError', requests[1])
        self.assertIn('setrecursionlimit', result['source'])

    def test_control_a_refusal_of_a_nested_input_the_plan_never_declared_is_not_a_failure(self):
        result, requests = self.solve(self.REFUSES_LONG)
        self.assertEqual((result['status'], len(requests)), ('checked', 1))
        self.assertNotIn('raised', result['strategies'])

    def test_control_a_task_without_brackets_gets_no_nested_input(self):
        checks = [check({'s': ''}, 0), check({'s': 'ab'}, 2), check({'s': 'abc'}, 3)]
        result, requests = self.solve('def run(x):\n    return len(x["s"])\n', checks=checks)
        self.assertEqual((result['status'], len(requests)), ('checked', 1))
        self.assertEqual(events(result, 'solve_scale_test'), [])
