"""K2. At depth 0 the answer gate runs the planner's reference on the requested input only
`if spec.get('reference') and key in small and self._executions_left()` (solve.py:1672 at 3af0808), and deploy
(solve.py:1236) may spend the last execution. When it does, the fresh reference is skipped silently and the
answer is judged by the recorded voices alone; nothing in the result says the gate was incomplete. (The
lines just above make the same budget boundary explicit for deploy itself: 'shared execution limit reached
before the final run on the requested input', solve.py:1575-1577.)

Scenario: T(n) = n(n+1)/2, requested input 40, declared small; W's fast path gives 800 at 40 (right: 820).
Differential testing finds W against the reference at 40; the auditor makes W's mistake there (800), so no check
is added and the only recorded voice on 40 agrees with W. With executions to spare the gate's reference (820)
voids the answer (control). With a budget that ends exactly at deploy, 800 is delivered as checked.

Scripted models; local executor; the method, the reference and the auditor are written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + ... + n.'
REFERENCE = 'def reference(n):\n    return sum(range(n + 1))\n'
W = ('def run(n):\n    if n > 0 and n % 20 == 0:\n        return (n // 2) * n\n'
     '    return n * (n + 1) // 2\n')   # 800 at 40


def auditor(context):   # errs at 40 exactly as W does
    return {'expected': [n * n // 2 if n == 40 else n * (n + 1) // 2 for n in context['inputs']]}


def solve(max_executions):
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
                      'reference': REFERENCE, 'small_inputs': [3, 4, 40]}
    m = Metered(Model(plan, lambda r: store(W), audit=auditor), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=max_executions, say=lambda _: None)
    return solver.solve(TASK), solver


class K2GateReferenceSkippedAtBudgetBoundary(unittest.TestCase):
    def test_the_gate_reference_is_skipped_when_deploy_spends_the_last_execution(self):
        control, _ = solve(256)
        used = control['execution_attempts']
        print('\ncontrol (256 executions): status', control['status'], '|', control.get('reason'),
              '| executions used', used, '| disputed by',
              [e.get('by') for e in events(control, 'solve_answer_disputed')])
        self.assertNotEqual(control['status'], 'checked', 'control: the gate reference voids 800')
        self.assertEqual([e.get('by') for e in events(control, 'solve_answer_disputed')], ['reference'])
        result, _ = solve(used - 1)   # everything as before; deploy takes the last execution
        print('budget %d: status' % (used - 1), result['status'], '| answer', result.get('answer'), '(right: 820)',
              '| executions used', result['execution_attempts'],
              '| differential:', [e.get('outcome') for e in events(result, 'solve_differential')])
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 800),
                            'the reference the gate requires was skipped for lack of an execution, silently')


if __name__ == '__main__':
    unittest.main()
