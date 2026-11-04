"""The code review of 2026-11-03 (the branch against main, read beside the Āryabhaṭīya as a whole).

1. A correction that cannot be made never discards what it was to correct (the mean place stands when a correction
   is not available, Kālakriyā 17-24). A checked method sent back by a gate that shows it wrong on no check (scale,
   raised, no answer, witness, against both) was lost when the method asked for failed: the result was incomplete
   and a weaker partial was offered as "the best attempt nothing has shown wrong". With no model call left the scale
   gate asked anyway and lost it the same way.
2. A limit is stated for the rule it belongs to (Golapāda 47-48). The result's scale outcome was that of an earlier
   method; the one delivered after the last allowed ask was never run at the top of the range.
3. A value cut short says nothing (an eclipse below perceptibility is not "no eclipse"): a probe value that ran out
   only because earlier values in its batch spent the time was never run again in the solve.
4. Testimony is examined, not taken from the first source that agrees (Brahmagupta's Tantraparīkṣā): a right method
   was contradicted by the first published sequence holding its first terms when another that holds them agrees.
5. Parts that check one another (the II 6 / II 21 gap): what is known was set against a new task's hand values, but a
   method written for the task was never set against the stored methods recognized as its function.
6. The nesting rule wrapped a whole stdin-shaped text; it now wraps the line that holds the brackets.

Every method here is written for this test and run only by the local executor."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import (DIFFERENTIAL_RUN, NEST_DEPTH, Metered, Solver, Workspace, _nested_inputs,
                                   canonical, digest)
from kuttaka_runtime.witness import OEISWitness
from tests import test_indefinite_search
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

RECURSIVE, FIXED, CHECKS = (getattr(test_indefinite_search.NestedByRule, name)
                             for name in ('RECURSIVE', 'FIXED', 'CHECKS'))   # its scenario; its tests run there
WRONG = 'def run(x):\n    return 0\n'
ALSO_RECURSIVE = RECURSIVE.replace('def run(x):\n', 'def run(x):\n    # the same walk, written again\n')


def nesting(writes, limit=10):
    """The deepest nesting of parentheses; the actor writes `writes` in order (the last one repeats)."""
    requests = []

    def act(request):
        requests.append(request)
        return store(writes[min(len(requests), len(writes)) - 1])
    plan = lambda c: {'contract': 'run({"s": s}) returns the deepest nesting of the balanced parentheses in s',
                      'input': None, 'checks': CHECKS}
    m = Metered(Model(plan, act, audit=lambda c: {}), {'used': 0, 'limit': limit})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                    max_executions=256, say=lambda _: None)
    return solver.solve('How deeply are the parentheses nested?'), requests


class CheckedMethodStands(unittest.TestCase):
    def test_a_failed_ask_after_the_scale_test_keeps_the_checked_method(self):
        result, requests = nesting([RECURSIVE, WRONG])
        self.assertEqual((result['status'], result['scale']), ('checked', 'slow'))   # old code: incomplete
        self.assertEqual(result['source'], RECURSIVE)
        self.assertIn('kept', result['strategies'])
        self.assertIn('RecursionError', requests[1])
        self.assertTrue(events(result, 'solve_checked_method_kept'))

    def test_with_no_call_left_the_scale_gate_does_not_ask(self):
        result, requests = nesting([RECURSIVE], limit=2)
        self.assertEqual((result['status'], result['scale'], len(requests)), ('checked', 'slow', 1))
        self.assertNotIn('scale', result['strategies'])

    def test_control_a_method_that_scales_replaces_the_slow_one(self):
        result, _ = nesting([RECURSIVE, FIXED])
        self.assertEqual((result['status'], result['scale'], result['source']), ('checked', 'held', FIXED))
        self.assertNotIn('kept', result['strategies'])


class ScaleNamesTheDeliveredMethod(unittest.TestCase):
    def test_the_method_delivered_after_the_last_ask_is_tested_and_named(self):
        result, requests = nesting([RECURSIVE, ALSO_RECURSIVE, FIXED])
        self.assertEqual(len(requests), 3)
        self.assertEqual((result['status'], result['source']), ('checked', FIXED))
        self.assertEqual(result['scale'], 'held')   # old code: 'slow', the second method's outcome
        tested = [e['artifact'] for e in events(result, 'solve_scale_test')]
        self.assertEqual(len(tested), 3)
        self.assertEqual(tested[-1], result['artifact'])

    def test_control_after_the_last_ask_a_slow_method_is_named_slow(self):
        result, requests = nesting([RECURSIVE, ALSO_RECURSIVE, ALSO_RECURSIVE + '\n'])
        self.assertEqual((result['status'], result['scale'], len(requests)), ('checked', 'slow', 3))


class CutShortIsHeardAgain(unittest.TestCase):
    def solver(self, replies):
        m = Metered(Model(lambda c: {}, lambda r: store(WRONG)), {'used': 0, 'limit': 1})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, say=lambda _: None)
        solver._slow_probes, solver._probe_values, solver._finished_runs = set(), {}, set()
        ran = []

        def once(source, values, libraries):
            ran.append(list(values))
            return replies.pop(0)
        solver._sandbox_once = once
        return solver, ran

    def test_a_value_starved_by_its_batch_runs_again(self):
        source = DIFFERENTIAL_RUN % (WRONG, 'run')
        solver, ran = self.solver([[{'value': 0}, {'timeout': True, 'seconds': 0.2}],
                                   [{'value': 0}]])
        solver._sandbox_job(source, [1, 2], None)
        self.assertEqual(solver._slow_probes, set())
        self.assertEqual(solver._sandbox_job(source, [2], None), [{'value': 0}])
        self.assertEqual(ran, [[1, 2], [2]])

    def test_control_a_value_that_had_its_time_is_not_run_again(self):
        source = DIFFERENTIAL_RUN % (WRONG, 'run')
        solver, ran = self.solver([[{'timeout': True, 'seconds': 12.0}]])
        solver._sandbox_job(source, [5], None)
        self.assertEqual(solver._slow_probes, {(digest({'source': source, 'libraries': {}}), canonical(5))})
        self.assertEqual(solver._sandbox_job(source, [5], None), [{'timeout': True}])
        self.assertEqual(ran, [[5]])


SQUARES = 'def run(x):\n    n = x["n"]\n    return n * n\n'
PARTS_AT_20 = lambda n: n * n if n < 20 else n * n + 1   # a sequence that shares the squares' first twenty terms


def two_sequences(listed):
    """A stand-in for the OEIS (its JSON search and b-file formats): `listed` sequences, in search order."""
    known = {'A999990': ('Squares until 20, then one more.', PARTS_AT_20), 'A000290': ('The squares.', lambda n: n * n)}

    def fetch(url):
        if '/search?' in url:
            return json.dumps([{'number': int(a[1:]), 'name': known[a][0], 'offset': '0,1',
                                'data': ','.join(str(known[a][1](n)) for n in range(30))} for a in listed])
        for number, (_, f) in known.items():
            if url.endswith('/%s/b%s.txt' % (number, number[1:])):
                return '\n'.join('%d %d' % (n, f(n)) for n in range(2001))
        raise OSError('not found')
    return OEISWitness(fetch=fetch)


def witnessed(listed):
    requests = []

    def act(request):
        requests.append(request)
        return store(SQUARES)
    plan = lambda c: {'contract': 'run({"n": n}) returns n squared', 'input': {'n': 1000},
                      'checks': [check({'n': 0}, 0), check({'n': 2}, 4), check({'n': 5}, 25)]}
    m = Metered(Model(plan, act, audit=lambda c: {}), {'used': 0, 'limit': 10})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                    max_executions=256, say=lambda _: None, witness=two_sequences(listed))
    return solver.solve('n squared, for n = 1000.'), requests


class EveryWitnessIsExamined(unittest.TestCase):
    def test_a_sequence_that_parts_later_does_not_contradict_when_another_agrees(self):
        result, requests = witnessed(['A999990', 'A000290'])
        self.assertEqual((result['status'], result.get('answer')), ('checked', 1000000))   # old code: incomplete
        self.assertEqual((result['witness']['sequence'], result['witness']['outcome']), ('A000290', 'agrees'))
        self.assertEqual(result['witness']['examined'], ['A999990', 'A000290'])
        self.assertEqual(len(requests), 1)

    def test_control_when_every_sequence_parts_from_the_method_it_is_contradicted(self):
        result, _ = witnessed(['A999990'])
        self.assertNotEqual(result['status'], 'checked')
        self.assertIn('contradicted by the published sequence A999990', result['reason'])


SUM_OF_SQUARES = 'def run(x):\n    return sum(v * v for v in x["nums"])\n'
SHORT_ONLY = 'def run(x):\n    v = x["nums"]\n    return sum(a * a for a in v) if len(v) < 3 else 0\n'
EMPTY_WRONG = 'def run(x):\n    v = x["nums"]\n    return sum(a * a for a in v) if v else -1\n'
# A slow reference that judges lists of one or two numbers only: differential testing has no evidence elsewhere.
SHORT_REFERENCE = ('def reference(x):\n    v = x["nums"]\n    if not v or len(v) > 2:\n        raise ValueError("out of reach")\n'
                   '    return sum(a * a for a in v)\n')
STORED = lambda source: source.replace('x["nums"]', 'x["values"]')   # the earlier task named its field otherwise


def examined(writes, library=True):
    """A library holding SHORT_ONLY, stored and checked by an earlier task on [1, 2], [3] and [] (field "values"); it is
    right there and on the empty list, and wrong on three numbers or more. Then a task worded otherwise, field "nums",
    checked on [1, 2] and [3] (the reference agrees) and by hand on [1, 1, 1] -> 3, which the stored method fails, so it
    is not reused; settlement recognizes it by role ({"values": "nums"}) on the two supported checks. The blind
    auditor works the values out."""
    ws = Workspace(Path(tempfile.mkdtemp()))
    if library:
        plan0 = lambda c: {'contract': 'run({"values": list}) returns the sum of the squares of the values',
                           'input': None, 'checks': [check({'values': [1, 2]}, 5), check({'values': [3]}, 9),
                                                     check({'values': []}, 0)]}
        m0 = Metered(Model(plan0, lambda r: store(STORED(SHORT_ONLY)), audit=lambda c: {}), {'used': 0, 'limit': 10})
        first = Solver(ws, m0, m0, local_executor, splitter=m0, auditor=m0, sketcher=m0, max_executions=256,
                       say=lambda _: None).solve('Add up the squares of the values.')
        assert first['status'] == 'checked', first.get('reason')
    requests, audited = [], []

    def act(request):
        requests.append(request)
        return store(writes[min(len(requests), len(writes)) - 1])

    def audit(context):
        audited.append(context['inputs'])
        return {'expected': [sum(a * a for a in i['nums']) if isinstance(i, dict) and isinstance(i.get('nums'), list)
                             and all(type(a) is int for a in i['nums']) else None for i in context['inputs']]}
    plan = lambda c: {'contract': 'run({"nums": list}) returns the sum of the squares of the numbers', 'input': None,
                      'checks': [check({'nums': [1, 2]}, 5), check({'nums': [3]}, 9), check({'nums': [1, 1, 1]}, 3)],
                      'reference': SHORT_REFERENCE}
    m = Metered(Model(plan, act, audit=audit), {'used': 0, 'limit': 12})
    result = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                    say=lambda _: None).solve('Each number in the list times itself, all added together.')
    return result, requests, audited


class Pariksha(unittest.TestCase):
    def test_the_method_is_set_against_what_is_known_and_each_side_is_corrected_where_it_is_wrong(self):
        result, requests, _ = examined([EMPTY_WRONG, SUM_OF_SQUARES])
        self.assertEqual((result['status'], result['source']), ('checked', SUM_OF_SQUARES))   # old code: EMPTY_WRONG
        self.assertIn('pariksha', result['strategies'])
        self.assertEqual(len(requests), 2)
        told = [e for e in events(result, 'solve_pariksha') if e.get('outcome') == 'disagree' and e['confirmed']]
        self.assertEqual(told[0]['confirmed'], [{'input': {'nums': []}, 'expected': 0}])   # the stored method's check
        seen = [e for e in events(result, 'solve_pariksha') if e.get('outcome') == 'known_contradicted']
        self.assertTrue(seen and all(len(c['input']['nums']) >= 3 for e in seen for c in e['contradicted']))

    def test_control_with_an_empty_library_the_wrong_method_stands_as_before(self):
        result, _, _ = examined([EMPTY_WRONG], library=False)
        self.assertEqual((result['status'], result['source']), ('checked', EMPTY_WRONG))
        self.assertEqual(events(result, 'solve_pariksha'), [])

    def test_control_a_right_method_is_not_sent_back_by_a_stored_method_that_is_wrong(self):
        result, requests, _ = examined([SUM_OF_SQUARES])
        self.assertEqual((result['status'], result['source'], len(requests)), ('checked', SUM_OF_SQUARES, 1))
        self.assertNotIn('pariksha', result['strategies'])

    def test_the_stored_method_that_is_wrong_is_seen_contradicted(self):
        result, _, _ = examined([SUM_OF_SQUARES])
        self.assertTrue([e for e in events(result, 'solve_pariksha') if e.get('outcome') == 'known_contradicted'])


class NestingKeepsTheShapeOfTheText(unittest.TestCase):
    def test_a_stdin_text_has_its_bracket_line_wrapped(self):
        deep = _nested_inputs([check('3\n(()\n', 1)])[0]
        self.assertEqual(deep.split('\n'), ['3', '(' * NEST_DEPTH + '(()' + ')' * NEST_DEPTH, ''])

    def test_control_a_text_of_one_line_is_wrapped_whole(self):
        self.assertEqual(_nested_inputs([check({'p': '(a|b)*c'}, 1)]),
                         [{'p': '(' * NEST_DEPTH + '(a|b)*c' + ')' * NEST_DEPTH}])


if __name__ == '__main__':
    unittest.main()
