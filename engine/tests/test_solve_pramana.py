"""Pramāṇa: the checks are tested before any code (given conditions), replaying the two hard-suite losses.

h-m1-digits (hard suite at b7e5f5a): the planner's hand-worked check n=99 -> 44 was wrong (37 is
right); the correct method failed it and was replaced by one that fits it. v3 r2-kinematics: two
hand-worked physics checks were wrong, and repair wrote their values in literally. Every method,
reference and property below is written for these tests (no model service, no evaluated task, no
hidden case); they run in the local executor.
"""

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from kuttaka_runtime import pramana
from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store

DIGITS = 'run(input) receives {"n": int} and returns how many k with 1 <= k <= n have a prime digit sum.'
DIGIT_SUM_COUNT = '''def run(input):
    n = input['n']
    prime = lambda s: s > 1 and all(s % d for d in range(2, s))
    return sum(1 for k in range(1, n + 1) if prime(sum(map(int, str(k)))))
'''
DIGIT_REFERENCE = DIGIT_SUM_COUNT.replace('def run(', 'def reference(')
MOD9 = '''def run(input):
    n = input['n']
    return n // 9 * 4 + sum(1 for r in range(1, n % 9 + 1) if r in (2, 3, 5, 7))
'''
HAND_CHECKS = [check({'n': 1}, 0), check({'n': 2}, 1), check({'n': 5}, 3), check({'n': 20}, 9),
               check({'n': 99}, 44)]   # the last one is the planner's wrong hand value; 37 is right

MOTION = 'run(input) receives {"v0": number, "segments": [[a, t], ...]} and returns [position, velocity].'
SIMULATE = '''def run(input):
    x, v = 0.0, float(input['v0'])
    for a, t in input['segments']:
        x += v * t + 0.5 * a * t * t
        v += a * t
    return [round(x, 3), round(v, 3)]
'''
MOTION_CHECKS = [check({'v0': 0, 'segments': []}, [0.0, 0.0]), check({'v0': 2, 'segments': [[0, 3]]}, [6.0, 2.0]),
                 check({'v0': 5, 'segments': [[0, 2], [-1, 4]]}, [6.0, 1.0]),         # wrong: [22.0, 1.0]
                 check({'v0': 10, 'segments': [[-2, 1.5], [3, 2]]}, [20.5, 13.0])]   # wrong: [32.75, 13.0]


def make(plan, source, audit=None, max_calls=20):
    calls = []

    def counted(context):   # every auditor call is counted, whatever it answers
        calls.append(context)
        return audit(context) if audit else {}
    model = Model(plan, lambda request: store(source), audit=counted)
    model.audit_calls = calls
    ws = Workspace(Path(tempfile.mkdtemp()))
    m = Metered(model, {'used': 0, 'limit': max_calls})
    return Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                  say=lambda _: None), ws, model


def settled(result):
    return [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_given_conditions']


def audits(model):
    return len(model.audit_calls)


class Auditing:
    """A blind auditor that works the value out correctly with its own method."""
    def __init__(self, source):
        space = {}
        exec(source, space)
        self.run, self.contexts = space['run'], []

    def __call__(self, context):
        self.contexts.append(context)
        return {'expected': [self.run(value) for value in context['inputs']]}


class GivenConditionTests(unittest.TestCase):
    def test_h_m1_digits_the_wrong_hand_check_is_corrected_before_code_and_the_right_method_delivered(self):
        auditor = Auditing(DIGIT_SUM_COUNT)
        plan = lambda c: {'contract': 'count k <= n with a prime digit sum', 'input': None, 'checks': HAND_CHECKS,
                          'reference': DIGIT_REFERENCE, 'small_inputs': [{'n': 30}, {'n': 50}]}
        solver, ws, model = make(plan, DIGIT_SUM_COUNT, audit=auditor)
        result = solver.solve(DIGITS)
        self.assertEqual(result['status'], 'checked')
        self.assertIn(check({'n': 99}, 37), result['checks'])
        self.assertNotIn(check({'n': 99}, 44), result['checks'])
        self.assertEqual(result['source'], DIGIT_SUM_COUNT)
        body = settled(result)[0]
        self.assertEqual(body['revised'], [{'input': {'n': 99}, 'hand_value': 44, 'value': 37,
                                            'by': 'reference and blind re-derivation'}])
        self.assertEqual(result['evidence']['support'], pramana.REFERENCE)
        self.assertEqual(result['evidence']['checks'], {pramana.TWO_DERIVATIONS: 1, pramana.REFERENCE: 4})
        self.assertGreater(result['evidence']['tested_beyond_checks']['reference'], 0)
        self.assertEqual(auditor.contexts[0]['inputs'], [{'n': 99}])     # only the disputed check is audited
        self.assertNotIn('44', json.dumps(auditor.contexts[0]))          # blind: no check value shown
        self.assertNotIn('expected', json.dumps(auditor.contexts[0]))

    def test_control_without_a_reference_the_wrong_check_still_defeats_the_right_method(self):
        plan = lambda c: {'contract': 'count k <= n with a prime digit sum', 'input': None, 'checks': HAND_CHECKS}
        solver, ws, model = make(plan, DIGIT_SUM_COUNT, max_calls=6)
        result = solver.solve(DIGITS)
        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual(settled(result), [])
        # Śūnya: nothing is delivered, and what is known keeps its place.
        self.assertEqual(result['void']['best_attempt']['passed'], 4)
        self.assertEqual(result['void']['best_attempt']['of'], 5)
        self.assertEqual(result['evidence']['support'], pramana.HAND)

    def test_r2_kinematics_both_wrong_physics_checks_are_corrected_before_code(self):
        auditor = Auditing(SIMULATE)
        plan = lambda c: {'contract': 'piecewise constant acceleration from position 0', 'input': None,
                          'checks': MOTION_CHECKS, 'reference': SIMULATE.replace('def run(', 'def reference(')}
        solver, ws, model = make(plan, SIMULATE, audit=auditor)
        result = solver.solve(MOTION)
        self.assertEqual(result['status'], 'checked')
        self.assertIn(check({'v0': 5, 'segments': [[0, 2], [-1, 4]]}, [22.0, 1.0]), result['checks'])
        self.assertIn(check({'v0': 10, 'segments': [[-2, 1.5], [3, 2]]}, [32.75, 13.0]), result['checks'])
        self.assertEqual(len(settled(result)[0]['revised']), 2)
        self.assertEqual(audits(model), 1)                               # one call settles both

    def test_reference_and_property_agreeing_against_a_hand_value_revise_it_without_a_model_call(self):
        plan = lambda c: {'contract': 'run(x) returns x squared', 'input': None,
                          'checks': [check(2, 4), check(3, 9), check(7, 48)],
                          'reference': 'def reference(x):\n    return x * x\n',
                          'verify': 'def verify(x, y):\n    return y == x * x\n'}
        solver, ws, model = make(plan, 'def run(x):\n    return x * x\n')
        result = solver.solve('Square a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertIn(check(7, 49), result['checks'])
        self.assertEqual(settled(result)[0]['revised'][0]['by'], 'reference and property')
        self.assertEqual(audits(model), 0)

    def test_control_a_reference_that_agrees_costs_no_call_and_supports_every_check(self):
        plan = lambda c: {'contract': 'run(x) returns x squared', 'input': None,
                          'checks': [check(2, 4), check(3, 9)], 'reference': 'def reference(x):\n    return x * x\n'}
        solver, ws, model = make(plan, 'def run(x):\n    return x * x\n')
        result = solver.solve('Square a number.')
        self.assertEqual(result['status'], 'checked')
        body = settled(result)[0]
        self.assertEqual((body['revised'], body['wrong'], body['set_aside']), ([], [], []))
        self.assertEqual(set(body['support'].values()), {pramana.REFERENCE})
        self.assertEqual(audits(model), 0)

    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_a_users_check_is_a_given_condition_and_the_reference_contradicting_it_is_set_aside(self):
        plan = lambda c: {'contract': 'run(x) returns x squared plus one', 'input': None,
                          'checks': [check(2, 5)], 'reference': 'def reference(x):\n    return x * x\n'}
        solver, ws, model = make(plan, 'def run(x):\n    return x * x + 1\n')
        result = solver.solve('Square a number, then add one.', [check(3, 10)])
        self.assertEqual(result['status'], 'checked')
        self.assertIn(check(3, 10), result['checks'])
        self.assertEqual(settled(result)[0]['wrong'], ['reference'])
        self.assertEqual(result['evidence']['checks'], {pramana.GIVEN: 1, pramana.HAND: 1})
        self.assertEqual(audits(model), 0)

    def test_the_check_and_the_property_outvote_a_wrong_reference(self):
        plan = lambda c: {'contract': 'run(x) returns x squared', 'input': None,
                          'checks': [check(2, 4), check(3, 9)],
                          'reference': 'def reference(x):\n    return x + x\n',          # wrong at 3
                          'verify': 'def verify(x, y):\n    return y == x * x\n'}
        solver, ws, model = make(plan, 'def run(x):\n    return x * x\n')
        result = solver.solve('Square a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(settled(result)[0]['wrong'], ['reference'])
        self.assertEqual(settled(result)[0]['revised'], [])

    def test_a_check_nothing_agrees_on_is_set_aside_and_the_rest_stand(self):
        plan = lambda c: {'contract': 'run(x) returns x squared', 'input': None,
                          'checks': [check(2, 4), check(3, 9), check(4, 15)],
                          'reference': 'def reference(x):\n    return x * x\n'}
        solver, ws, model = make(plan, 'def run(x):\n    return x * x\n', audit=lambda c: {'expected': [17]})
        result = solver.solve('Square a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(settled(result)[0]['set_aside'], [4])
        # Heard a third time from the method written without it (2026-11-02, W4-m collide): the method gives 16,
        # as the reference does, so the check returns corrected, resting on two derivations. Before, it stayed out.
        self.assertEqual(result['checks'], [check(2, 4), check(3, 9), check(4, 16)])

    def test_void_when_too_few_checks_can_be_settled(self):
        plan = lambda c: {'contract': 'run(x) returns x squared', 'input': None,
                          'checks': [check(3, 10), check(4, 15)], 'reference': 'def reference(x):\n    return x * x\n'}
        solver, ws, model = make(plan, 'def run(x):\n    return x * x\n', audit=lambda c: {'expected': [11, 17]})
        result = solver.solve('Square a number.')
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['void']['unsettled'], [3, 4])
        self.assertIsNone(result['artifact'])
        self.assertFalse(any(role == 'actor' for role, _ in model.log))   # no code written against guesses

    def test_a_small_execution_budget_goes_to_the_method_not_to_settlement(self):
        plan = lambda c: {'contract': 'run(x) returns x squared', 'input': None,
                          'checks': [check(2, 4)], 'reference': 'def reference(x):\n    return x * x\n'}
        model = Model(plan, lambda request: store('def run(x):\n    return x * x\n'))
        m = Metered(model, {'used': 0, 'limit': 10})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                        sketcher=m, max_executions=2, say=lambda _: None)
        result = solver.solve('Square a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(settled(result), [])


TRIANGLE_VERIFY = """def verify(n, y):
    import math
    r = math.isqrt(8 * y + 1) if isinstance(y, int) and y >= 0 else -1
    return r * r == 8 * y + 1 and (r - 1) // 2 == n
"""


def sequence(*sources):
    """An actor that writes these methods in turn, one per attempt."""
    order = list(sources)
    return lambda request: store(order.pop(0) if len(order) > 1 else order[0])


class UpapattiTests(unittest.TestCase):
    def solver(self, act, audit, verify=TRIANGLE_VERIFY):
        plan = lambda c: {'contract': 'run(n) returns the n-th triangular number', 'input': None,
                          'checks': [check(1, 1), check(2, 3)], 'verify': verify}
        calls = []

        def counted(context):
            calls.append(context)
            return audit(context)
        model = Model(plan, act, audit=counted)
        m = Metered(model, {'used': 0, 'limit': 20})
        return Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                      sketcher=m, max_executions=256, say=lambda _: None), calls

    def test_a_method_that_fits_weak_checks_is_caught_beyond_them_and_replaced(self):
        wrong, right = 'def run(n):\n    return 2 * n - 1\n', 'def run(n):\n    return n * (n + 1) // 2\n'
        solver, calls = self.solver(sequence(wrong, right),
                                    lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]})
        result = solver.solve('The n-th triangular number.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['source'], right)
        self.assertIn('property', result['strategies'])
        self.assertIn('from property testing', result['checks_by'])
        self.assertEqual(len(calls), 1)
        self.assertTrue(solver.ws.refuted())

    def test_control_a_right_method_passes_the_property_beyond_the_checks_with_no_model_call(self):
        right = 'def run(n):\n    return n * (n + 1) // 2\n'
        solver, calls = self.solver(sequence(right), lambda c: {'expected': []})
        result = solver.solve('The n-th triangular number.')
        self.assertEqual(result['status'], 'checked')
        self.assertNotIn('property', result['strategies'])
        self.assertEqual(calls, [])

    def test_a_property_a_blind_re_derivation_contradicts_is_overruled_and_the_method_stands(self):
        right = 'def run(n):\n    return n * (n + 1) // 2\n'
        bad = 'def verify(n, y):\n    return y <= 3\n'   # true on the checks, false beyond them
        solver, calls = self.solver(sequence(right), lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]},
                                    verify=bad)
        result = solver.solve('The n-th triangular number.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['source'], right)
        self.assertTrue(result['property_evidence']['resolution'].startswith('verifier overruled'))
        self.assertFalse(solver.ws.refuted())


class Rewriting(Model):
    """An actor that writes `first`, and when it fails the checks, `second`, in the same attempt; the
    checker's verdict on `first` is what made it switch (as Glimmer did on h-m1-digits)."""
    def __init__(self, plan, first, second, audit=None):
        super().__init__(plan, None, audit=audit)
        self.first, self.second = first, second

    def propose(self, context):
        if 'request' in context and 'history' in context:
            history = context['history']
            stores = [h for h in history if h['action'].get('tool') == 'remember_method']
            last = history[-1] if history else None
            if last and last['action'].get('tool') == 'remember_method' and isinstance(last['observation'], dict) \
                    and last['observation'].get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': last['observation']['artifact']}}
            if len(stores) < 2:
                return store(self.first if not stores else self.second)
            return {'kind': 'unresolved', 'reason': 'scripted stop'}
        return super().propose(context)


class RivalTests(unittest.TestCase):
    def run_rival(self, plan, first, second, audit):
        calls = []

        def counted(context):
            calls.append(context)
            return audit(context)
        model = Rewriting(plan, first, second, audit=counted)
        m = Metered(model, {'used': 0, 'limit': 20})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                        sketcher=m, max_executions=256, say=lambda _: None)
        return solver.solve(DIGITS), calls, solver

    def rivals(self, result):
        return [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_rival']

    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_h_m1_digits_without_a_reference_the_abandoned_right_method_wins_on_the_contested_check(self):
        plan = lambda c: {'contract': 'count k <= n with a prime digit sum', 'input': None, 'checks': HAND_CHECKS}
        result, calls, solver = self.run_rival(plan, DIGIT_SUM_COUNT, MOD9, Auditing(DIGIT_SUM_COUNT))
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['source'], DIGIT_SUM_COUNT)
        self.assertEqual(result['delivered_by'], 'rival')
        self.assertIn(check({'n': 99}, 37), result['checks'])
        self.assertIn('corrected by rival testing', result['checks_by'])
        self.assertEqual(len(calls), 1)
        self.assertNotIn('44', json.dumps(calls[0]))                      # blind
        self.assertEqual(len(solver.ws.refuted()), 1)                     # the formula that fit the wrong check

    def test_control_the_auditor_upholds_the_check_and_the_method_stands(self):
        plan = lambda c: {'contract': 'count k <= n with a prime digit sum', 'input': None, 'checks': HAND_CHECKS}
        # An auditor that agrees with the formula: the check is upheld, and nothing is overturned on one voice.
        space = {}
        exec(MOD9, space)
        result, calls, solver = self.run_rival(plan, DIGIT_SUM_COUNT, MOD9,
                                               lambda c: {'expected': [space['run'](v) for v in c['inputs']]})
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['source'], MOD9)
        self.assertEqual(self.rivals(result)[-1]['outcome'], 'method_upheld')
        self.assertFalse(solver.ws.refuted())

    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_a_rival_that_fails_a_users_check_is_ignored(self):
        plan = lambda c: {'contract': 'count k <= n with a prime digit sum', 'input': None, 'checks': HAND_CHECKS[:4]}
        calls = []
        model = Rewriting(plan, DIGIT_SUM_COUNT, MOD9, audit=lambda c: calls.append(c) or {'expected': []})
        m = Metered(model, {'used': 0, 'limit': 20})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                        sketcher=m, max_executions=256, say=lambda _: None)
        result = solver.solve(DIGITS, [check({'n': 99}, 44)])               # the user insists on 44
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['source'], MOD9)
        self.assertEqual(calls, [])


class ParibhashaTests(unittest.TestCase):
    def test_a_stored_method_is_recalled_by_the_form_of_its_inputs_when_the_wording_differs(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = lambda c: {'contract': 'run(x) returns x squared', 'input': None, 'checks': [check(2, 4), check(3, 9)]}
        solver, _, _ = make(first, 'def run(x):\n    return x * x\n')
        solver.ws = ws
        self.assertEqual(solver.solve('Square a number.')['status'], 'checked')
        tile = 'Area of a floor tile whose edge is given.'
        self.assertEqual(ws.helpful_methods(tile), [])                                   # words alone miss it
        self.assertEqual(len(ws.helpful_methods(tile, form=pramana.form([check(5, 25)]))), 1)
        # Worded with no word in common; the same input form and checks the stored method passes.
        second = lambda c: {'contract': 'tile area from its side', 'input': None, 'checks': [check(5, 25), check(6, 36)]}
        model = Model(second, lambda request: self.fail('the model was asked to write code'))
        m = Metered(model, {'used': 0, 'limit': 10})
        again = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                       say=lambda _: None).solve('Area of a floor tile whose edge is given.')
        self.assertEqual(again['status'], 'checked')
        self.assertEqual(again['delivered_by'], 'reuse')

    def test_control_a_different_input_form_is_not_recalled(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        ws.remember_solved('Square a number.', 'x squared', 'a' * 64, form=pramana.form([check(2, 4)]))
        self.assertEqual(ws.helpful_methods('Area of a floor tile.', form=pramana.form([check({'side': 2}, 4)])), [])


class PrastaraTests(unittest.TestCase):
    def library(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        for task, checks, source in (
                ('Square a number.', [check(2, 4), check(3, 9)], 'def run(x):\n    return x * x\n'),
                ('Total of a list of numbers.', [check([1, 2], 3), check([4], 4)], 'def run(xs):\n    return sum(xs)\n')):
            plan = lambda c, checks=checks: {'contract': task, 'input': None, 'checks': checks}
            solver, _, _ = make(plan, source)
            solver.ws = ws
            self.assertEqual(solver.solve(task)['status'], 'checked')
        return ws

    def solve(self, ws, checks, act=None):
        plan = lambda c: {'contract': 'sum of the squares of the values', 'input': None, 'checks': checks}
        model = Model(plan, act or (lambda request: self.fail('the model was asked to write code')))
        m = Metered(model, {'used': 0, 'limit': 10})
        return Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                      say=lambda _: None).solve('The values given: add up their squares.')

    def test_a_composition_of_stored_methods_is_found_in_order_with_no_model_call(self):
        ws = self.library()
        result = self.solve(ws, [check({'values': [1, 2]}, 5), check({'values': [3]}, 9), check({'values': []}, 0)])
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['delivered_by'], 'prastara')
        self.assertEqual(result['composition']['prastara']['formula'], 'm(map(m, input.values))')
        self.assertTrue(result['composition']['prastara']['complete'])
        self.assertEqual(result['mechanical']['prastara_found'], 1)
        place = result['composition']['prastara']['place']
        self.assertLessEqual(result['mechanical']['prastara_screened'], place + 1)   # stops at the first that fits

    def test_control_when_no_composition_fits_the_model_writes_the_method(self):
        ws = self.library()
        result = self.solve(ws, [check({'values': [1, 2]}, 9), check({'values': [3]}, 27)],   # cubes, not squares
                            act=lambda request: store('def run(input):\n    return sum(v ** 3 for v in input["values"])\n'))
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['delivered_by'], 'model')
        self.assertGreater(result['mechanical']['prastara_screened'], 0)
        self.assertNotIn('prastara_found', result['mechanical'])

    def test_the_list_is_complete_typed_and_in_a_fixed_order_with_places(self):
        from kuttaka_runtime import solve_prastara
        methods = [('sq', 'square', 'num', 'num'), ('tot', 'total', ['list', ['num']], 'num')]
        shape = {'values': ['list', ['num']]}
        complete, listed = solve_prastara.recipes(methods, shape, 'num', 100)
        self.assertTrue(complete)
        self.assertEqual(listed, solve_prastara.recipes(methods, shape, 'num', 100)[1])
        self.assertEqual([p for p, _, _ in listed], list(range(len(listed))))
        formulas = [f for _, f, _ in listed]
        self.assertEqual(formulas[:2], ['total(input.values)', 'total(map(square, input.values))'])
        # Bhāvanā: nothing ill-formed is listed (square of a list, total of a number).
        self.assertFalse(any(f.startswith('square(input') or 'total(square(' in f for f in formulas))
        cut, few = solve_prastara.recipes(methods, shape, 'num', 1)
        self.assertEqual((cut, len(few)), (False, 1))


class GrowthTests(unittest.TestCase):
    def test_a_void_teaches_the_next_task_of_its_form_however_it_is_worded(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        form = pramana.form([check({'n': 3}, 0)])
        ws.learn('Count the primes up to n.', 'count primes -> not solved: off by one at n = 2', form=form)
        task = 'How many numbers up to n have a prime digit sum?'   # related wording, below the word threshold
        self.assertEqual(ws.lessons(task), [])
        self.assertEqual(ws.lessons(task, form=form)[0]['mistake'], 'count primes -> not solved: off by one at n = 2')
        self.assertEqual(ws.lessons(task, form=pramana.form([check(3, 0)])), [])           # another form
        self.assertEqual(ws.lessons('Reverse the letters of a word.', form=form), [])       # unrelated wording


TRIANGULAR_REDUCTION = """def measure(n):
    return n


def small(n):
    return n <= 3


def reduce(n):
    return [n - 1]


def combine(n, answers):
    return answers[0] + n
"""


class ValliTests(unittest.TestCase):
    def run_valli(self, reduction, reference='def reference(n):\n    return sum(range(n + 1))\n', checks=None):
        checks = checks or [check(0, 0), check(4, 10), check(10, 55), check(30, 465)]
        plan = lambda c: {'contract': 'run(n) returns 0 + 1 + ... + n', 'input': None, 'checks': checks,
                          'reference': reference}
        model = Model(plan, lambda request: store('def run(n):\n    return n\n'),   # wrong on most checks
                      split=lambda c: {'reduction': reduction})
        m = Metered(model, {'used': 0, 'limit': 30})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                        sketcher=m, max_executions=256, say=lambda _: None)
        return solver.solve('Sum of the whole numbers up to n.')

    def valli_events(self, result):
        return [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_valli']

    def test_a_self_similar_reduction_is_rebuilt_from_the_reference_at_the_bottom(self):
        result = self.run_valli(TRIANGULAR_REDUCTION)
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['delivered_by'], 'valli')
        self.assertIn('valli', result['split_by'])
        self.assertEqual(self.valli_events(result)[-1]['bottom'], 'reference')
        self.assertEqual(result['mechanical']['valli_checked'], 1)
        ran = local_executor({'source': result['source'], 'input': 200})
        self.assertEqual(json.loads(ran['output'])['result'], 20100)

    def test_a_reduction_that_does_not_shrink_is_never_delivered(self):
        result = self.run_valli(TRIANGULAR_REDUCTION.replace('return [n - 1]', 'return [n]'))
        self.assertNotEqual(result['status'], 'checked')
        self.assertIn('incomplete', [e['outcome'] for e in self.valli_events(result)])

    def test_without_a_reference_or_a_base_there_is_no_bottom_and_nothing_is_assembled(self):
        result = self.run_valli(TRIANGULAR_REDUCTION, reference=None)
        self.assertNotEqual(result['status'], 'checked')
        self.assertIn('incomplete_reduction', [e['outcome'] for e in self.valli_events(result)])

    def test_the_models_own_base_is_used_when_there_is_no_reference(self):
        with_base = TRIANGULAR_REDUCTION + '\n\ndef base(n):\n    return n * (n + 1) // 2\n'
        result = self.run_valli(with_base, reference=None)
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(self.valli_events(result)[-1]['bottom'], 'base')

    def test_the_chain_is_kept_so_shared_smaller_inputs_are_computed_once(self):
        from kuttaka_runtime import solve_valli
        fib = ('def measure(n):\n    return n\n\ndef small(n):\n    return n < 2\n\n'
               'def reduce(n):\n    return [n - 1, n - 2]\n\ndef combine(n, a):\n    return a[0] + a[1]\n'
               '\ndef base(n):\n    return n\n')
        ran = local_executor({'source': solve_valli.assemble(fib), 'input': 90})
        self.assertEqual(json.loads(ran['output'])['result'], 2880067194370816120)   # exponential without the chain


class CompleteGivenTests(unittest.TestCase):
    def test_missing_given_conditions_are_asked_for_once_before_code_and_tested_at_once(self):
        replies = []

        def plan(context):
            replies.append(sorted(context))
            out = {'contract': 'count k <= n with a prime digit sum', 'input': None, 'checks': HAND_CHECKS}
            if 'given_conditions_missing' in context:
                out['reference'] = DIGIT_REFERENCE
            return out
        solver, ws, model = make(plan, DIGIT_SUM_COUNT, audit=Auditing(DIGIT_SUM_COUNT), max_calls=30)
        result = solver.solve(DIGITS)
        self.assertEqual(result['status'], 'checked')
        self.assertIn(check({'n': 99}, 37), result['checks'])
        self.assertEqual(sum('given_conditions_missing' in r for r in replies), 1)

    def test_a_source_set_aside_as_wrong_is_not_asked_for_again(self):
        asked = []

        def plan(context):
            asked.append('given_conditions_missing' in context)
            return {'contract': 'run(x) returns x squared', 'input': None, 'checks': [check(2, 4), check(3, 9)],
                    'verify': 'def verify(x, y):\n    return y == 3 * x\n'}
        solver, ws, model = make(plan, 'def run(x):\n    return x * x\n',
                                 audit=lambda c: {'expected': [v * v for v in c['inputs']]}, max_calls=30)
        result = solver.solve('Square a number.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(asked, [False])

    def test_a_small_call_budget_goes_to_the_method(self):
        asked = []

        def plan(context):
            asked.append('given_conditions_missing' in context)
            return {'contract': 'run(x) returns x squared', 'input': None, 'checks': [check(2, 4), check(3, 9)]}
        solver, ws, model = make(plan, 'def run(x):\n    return x * x\n', max_calls=6)
        self.assertEqual(solver.solve('Square a number.')['status'], 'checked')
        self.assertEqual(asked, [False])


class ShastraTests(unittest.TestCase):
    def test_the_library_shows_rule_form_support_rationale_uses_lineage_and_open_places(self):
        from kuttaka_runtime.shastra import build, render
        ws = PrastaraTests().library()                       # square and total, stored by two tasks
        PrastaraTests().solve(ws, [check({'values': [1, 2]}, 5), check({'values': [3]}, 9), check({'values': []}, 0)])
        plan = lambda c: {'contract': 'run(x) returns x squared', 'input': None,
                          'checks': [check(3, 10), check(4, 15)], 'reference': 'def reference(x):\n    return x * x\n'}
        solver, _, _ = make(plan, 'def run(x):\n    return x * x\n', audit=lambda c: {'expected': [11, 17]})
        solver.ws = ws
        solver.solve('Square, with checks nobody can settle.')       # a void before code
        library = build(ws)
        self.assertEqual(library['counts']['methods'], 3)
        composed = library['methods'][0]
        self.assertEqual(len(composed['uses']), 2)                     # the composition runs square and total
        self.assertEqual(composed['applies_to']['input'], {'values': ['list', ['num']]})
        self.assertEqual(composed['applies_to']['output'], 'num')
        self.assertIn('support', composed)
        self.assertEqual(library['counts']['open'], 1)
        text = render(library)
        self.assertIn('Open places', text)
        self.assertIn('Takes "num"', text)

    def test_lineage_records_what_a_method_superseded(self):
        from kuttaka_runtime.shastra import build
        wrong, right = 'def run(n):\n    return 2 * n - 1\n', 'def run(n):\n    return n * (n + 1) // 2\n'
        solver, calls = UpapattiTests().solver(sequence(wrong, right),
                                               lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]})
        self.assertEqual(solver.solve('The n-th triangular number.')['status'], 'checked')
        method = build(solver.ws)['methods'][0]
        self.assertEqual(len(method['lineage']['supersedes']), 1)
        self.assertEqual(method['rationale'].get('property', 0) > 0, True)


class AviseshaTests(unittest.TestCase):
    def test_rounds_stop_when_a_round_changes_nothing_the_next_could_use(self):
        from kuttaka_runtime.solve import settings, solve_task
        stuck = lambda c: {'contract': 'run(x) returns x plus seven', 'input': None, 'checks': [check(1, 8), check(2, 9)]}
        model = Model(stuck, lambda request: store('def run(x):\n    return x\n'))   # never right, never different
        result = solve_task('Add seven.', settings=settings(rounds=5, max_calls=6, max_executions=200),
                            workspace=Workspace(Path(tempfile.mkdtemp())),
                            models=(model, model, model, model, model), executor=local_executor)
        self.assertNotEqual(result['status'], 'checked')
        self.assertLess(len(result['rounds']), 5)
        self.assertIn('no change', result['rounds'][-1]['stopped'])

    def test_control_rounds_continue_while_each_one_changes_something(self):
        from kuttaka_runtime.solve import round_signature
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = round_signature({'status': 'incomplete', 'progress': [1]}, ws)
        self.assertNotEqual(first, round_signature({'status': 'incomplete', 'progress': [1, 2]}, ws))
        ws.remember_confirmed('t', check(3, 9))
        self.assertNotEqual(first, round_signature({'status': 'incomplete', 'progress': [1]}, ws))


class PlacementTests(unittest.TestCase):
    def test_the_requested_input_faces_the_reference_before_its_answer_is_delivered(self):
        # Right on every check and near input, wrong only at the input the user asked about.
        wrong = 'def run(n):\n    return n * (n + 1) // 2 if n < 50 else 0\n'
        right = 'def run(n):\n    return n * (n + 1) // 2\n'
        plan = lambda c: {'contract': 'run(n) returns 0 + 1 + ... + n', 'input': 60,
                          'checks': [check(1, 1), check(3, 6), check(4, 10)],
                          'reference': 'def reference(n):\n    return sum(range(n + 1))\n'}
        model = Model(plan, sequence(wrong, right), audit=lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]})
        m = Metered(model, {'used': 0, 'limit': 20})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                        sketcher=m, max_executions=256, say=lambda _: None)
        result = solver.solve('Sum of the whole numbers up to 60.')
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['answer'], 1830)
        self.assertIn(check(60, 1830), result['checks'])

    def test_reuse_is_tried_again_on_checks_settled_late(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = lambda c: {'contract': 'run(x) returns x squared', 'input': None, 'checks': [check(2, 4), check(3, 9)]}
        solver, _, _ = make(first, 'def run(x):\n    return x * x\n')
        solver.ws = ws
        self.assertEqual(solver.solve('Square a number.')['status'], 'checked')

        def plan(context):   # a wrong hand check; the reference only when asked for it
            out = {'contract': 'run(x) returns x squared', 'input': None, 'checks': [check(5, 25), check(6, 35)]}
            if 'given_conditions_missing' in context:
                out['reference'] = 'def reference(x):\n    return x * x\n'
            return out
        model = Model(plan, lambda request: self.fail('the model was asked to write code'),
                      audit=lambda c: {'expected': [v * v for v in c['inputs']]})
        m = Metered(model, {'used': 0, 'limit': 30})
        again = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                       say=lambda _: None).solve('Square a number, please.')
        self.assertEqual(again['status'], 'checked')
        self.assertEqual(again['delivered_by'], 'reuse')
        self.assertIn(check(6, 36), again['checks'])


class HelperTests(unittest.TestCase):
    def test_near_inputs_move_each_number_up_and_never_negative(self):
        near = pramana.near_inputs([{'n': 3, 'k': 0}])
        self.assertIn({'n': 4, 'k': 0}, near)
        self.assertIn({'n': 7, 'k': 0}, near)
        self.assertIn({'n': 3, 'k': 1}, near)
        self.assertTrue(all(v['n'] >= 3 and v['k'] >= 0 for v in near))

    def test_form_ignores_values_and_list_lengths(self):
        a = [check({'v0': 1, 'segments': [[0, 1]]}, 0), check({'v0': 2.5, 'segments': [[1, 2], [3, 4]]}, 0)]
        b = [check({'segments': [[9, 9]], 'v0': 7}, 1)]
        self.assertEqual(pramana.form(a), pramana.form(b))
        self.assertNotEqual(pramana.form(a), pramana.form([check({'n': 1}, 0)]))

    def test_support_summary_is_the_weakest_label(self):
        self.assertEqual(pramana.summarize([pramana.GIVEN, pramana.GIVEN]), pramana.GIVEN)
        self.assertEqual(pramana.summarize([pramana.GIVEN, pramana.REFERENCE, pramana.HAND]), pramana.HAND)
        self.assertEqual(pramana.summarize([pramana.TWO_DERIVATIONS, pramana.REFERENCE]), pramana.REFERENCE)


if __name__ == '__main__':
    unittest.main()
