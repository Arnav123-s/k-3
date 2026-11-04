"""W4-m, h-c1-regex: a function delivered as checked while the reference disagreed with it, unsettled.

The run's last attempt passed all 10 checks; differential testing then found the method and the planner's reference
disagreeing on 6 of 39 new inputs, with no model call left for the blind re-derivation (`unsettled_no_calls` in
results/solve-suite/w4m-glimmer-fresh/workspaces/h-c1-regex/runs/solve-37e1154edd444acfaa608b3c68cfc89b/solve.jsonl).
The method was delivered as checked and failed a hidden case (8/9). On those inputs the method and the reference
are one voice each: a tie, which the answer gate already treats as no answer for a requested input. For a function
(no requested input) nothing heard the tie.

Scenario: run(n) returns n*(n+1)/2 for n >= 0, no requested input. W is right on the planner's checks and wrong
at multiples of 20, which the planner's small inputs reach; the reference is right.
  - the call limit ends as W passes its checks (no call for the auditor): W must not be delivered as checked;
  - calls left, but the auditor's reply has the wrong length: the same.
Controls: a right method at the same call limit is delivered as checked (no dispute, no gate); with calls left and
an auditor that sides with W, W stands as checked (two voices to one); with an auditor that sides with the
reference, W is refuted as before. Scripted models; local executor; every method and the auditor are written for
this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + ... + n.'
REFERENCE = 'def reference(n):\n    return sum(range(n + 1))\n'
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'
W = ('def run(n):\n    if n > 0 and n % 20 == 0:\n        return (n // 2) * n\n'
     '    return n * (n + 1) // 2\n')   # 800 at 40 (right: 820)


def plan(context):
    return {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': None,
            'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
            'reference': REFERENCE, 'small_inputs': [3, 4, 20, 40]}


def right_auditor(context):
    return {'expected': [n * (n + 1) // 2 for n in context['inputs']]}


def w_auditor(context):   # makes W's mistake
    return {'expected': [(n // 2) * n if n > 0 and n % 20 == 0 else n * (n + 1) // 2 for n in context['inputs']]}


def wrong_length(context):
    return {'expected': right_auditor(context)['expected'][:-1]}


def solve(source, limit, auditor):
    m = Metered(Model(plan, lambda r: store(source), audit=auditor), {'used': 0, 'limit': limit})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK)


def outcomes(result):
    return [e.get('outcome') for e in events(result, 'solve_differential')]


def at_boundary(source, auditor):
    """The smallest call limit at which the method is checked and the differential runs."""
    for limit in range(2, 10):
        result = solve(source, limit, auditor)
        if outcomes(result):
            return limit, result
    raise AssertionError('the differential never ran')


class UnsettledDisagreementGate(unittest.TestCase):
    def test_no_call_left_for_the_rederivation(self):
        limit, result = at_boundary(W, right_auditor)
        print('\nlimit %d: status %s | differential %s | reason %s' % (limit, result['status'], outcomes(result),
                                                                      result.get('reason')))
        self.assertIn('unsettled_no_calls', outcomes(result), 'precondition: disputed with no call left')
        self.assertNotEqual(result['status'], 'checked', 'a tie with the reference is delivered as checked')
        self.assertEqual(result['unsettled']['by'], 'reference')
        self.assertIn('unverified', result, 'the best attempt is still offered, marked unverified')
        self.assertEqual(result['unverified']['status'], 'unverified')
        self.assertIn('unsettled disagreement', [e.get('by') for e in events(result, 'solve_delivery_refused')])

    def test_unusable_rederivation(self):
        result = solve(W, 20, wrong_length)
        print('\nwrong-length auditor: status %s | differential %s' % (result['status'], outcomes(result)))
        self.assertIn('unsettled_no_auditor', outcomes(result))
        self.assertNotEqual(result['status'], 'checked')
        self.assertIn('unverified', result)

    def test_control_a_right_method_at_the_same_limit_is_checked(self):
        limit, _ = at_boundary(W, right_auditor)
        result = solve(RIGHT, limit, right_auditor)
        print('\ncontrol, right method at limit %d: status %s | differential %s' % (
            limit, result['status'], outcomes(result)))
        self.assertEqual(outcomes(result), ['agree'])
        self.assertEqual(result['status'], 'checked')
        self.assertNotIn('unsettled', result)

    def test_control_an_auditor_with_the_method_settles_it(self):
        result = solve(W, 20, w_auditor)
        print('\ncontrol, auditor sides with W: status %s | differential %s' % (result['status'], outcomes(result)))
        self.assertFalse([o for o in outcomes(result) if o.startswith('unsettled')])
        self.assertEqual(result['status'], 'checked', 'two voices to one: the method stands, as before')
        self.assertNotIn('unsettled', result)

    def test_control_an_auditor_with_the_reference_refutes_it(self):
        result = solve(W, 20, right_auditor)
        print('\ncontrol, auditor sides with the reference: status %s | differential %s' % (
            result['status'], outcomes(result)))
        self.assertFalse([o for o in outcomes(result) if o.startswith('unsettled')])
        self.assertNotEqual((result['status'], result.get('artifact') is not None), ('checked', True))
        self.assertNotIn('unsettled', result)


if __name__ == '__main__':
    unittest.main()
