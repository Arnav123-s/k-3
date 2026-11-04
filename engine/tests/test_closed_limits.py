"""Limits stated in docs/ARCHITECTURE.md before 2026-10-31 evening, now closed, each shown here or in its own test:
  - a rival written in an attempt and never examined was outside the memorization guard's refutation: every method
    written in an attempt is now read when written (this file);
  - a dispute a piece saw only on an input its parent hands it did not reach the parent (H2): the piece gate,
    tests/test_review8_h2_* (no longer an expected failure).
Scripted models; local executor; every method is written for this test."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triple a number: run(n) returns 3 * n.'
COPIES = 'def run(n):\n    if n == 7:\n        return 22\n    return 3 * n + (1 if n == 5 else 0)\n'   # fails 5 -> 15


class Writes(Model):
    """Stores its sources in order (automatic submission rejects each that fails a check)."""
    def __init__(self, plan, sources):
        super().__init__(plan, None)
        self.sources = sources

    def propose(self, context):
        if 'request' in context and 'history' in context:
            history = context['history']
            last = history[-1] if history else None
            if last and last['action'].get('tool') == 'remember_method' and isinstance(last['observation'], dict) \
                    and last['observation'].get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': last['observation']['artifact']}}
            stores = [h for h in history if h['action'].get('tool') == 'remember_method']
            if len(stores) < len(self.sources):
                return store(self.sources[len(stores)])
            return {'kind': 'unresolved', 'reason': 'scripted stop'}
        return super().propose(context)


class RivalReadWhenWritten(unittest.TestCase):
    @mock.patch.object(Solver, 'derive_blind', False)   # the hand check 7 -> 22 must reach the attempt as planned
    def test_a_rival_that_copies_a_hand_check_is_refuted_when_written(self):
        right = 'def run(n):\n    if n == 7:\n        return 22\n    return 3 * n\n'   # fits the wrong hand check
        plan = lambda c: {'contract': 'run(n) returns 3 * n', 'input': None,
                          'checks': [check(4, 12), check(5, 15), check(7, 22)]}
        m = Metered(Writes(plan, [COPIES, right]), {'used': 0, 'limit': 20})
        ws = Workspace(Path(tempfile.mkdtemp()))
        solver = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                        say=lambda _: None)
        result = solver.solve(TASK)
        reasons = [r['reason'] for r in ws._read(ws.refuted_path)]
        self.assertTrue(any('read when written' in r for r in reasons), reasons)


if __name__ == '__main__':
    unittest.main()


class NeverLessThanTheModelAlone(unittest.TestCase):
    """When nothing is verified, the best attempt nothing has shown wrong is still returned, marked unverified, never
    as checked: a void can at worst tie with the model alone, which is scored on whatever code it returns."""

    def test_an_answer_held_back_by_the_gate_is_returned_unverified(self):
        # The method passes every check; the reference, declared to reach the requested input, disagrees there: the
        # gate voids the answer, and the method is returned unverified with its answer, never as checked.
        from tests.test_solve_review_regressions import make_solver, Model
        reference = 'def reference(n):\n    return 28 if n == 9 else 3 * n\n'
        plan = lambda c: {'contract': 'run(n) returns 3 * n', 'input': 9, 'checks': [check(4, 12), check(5, 15)],
                          'reference': reference, 'small_inputs': [9]}
        solver, ws = make_solver(Model(plan, lambda r: store('def run(n):\n    return 3 * n\n')), max_calls=20)
        result = solver.solve('Triple a number.')
        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual(result['unverified']['answer'], 27)
        self.assertEqual(result['unverified']['status'], 'unverified')

    def test_a_refuted_method_is_never_offered(self):
        from tests.test_solve_review_regressions import make_solver, Model
        plan = lambda c: {'contract': 'run(n) returns 3 * n', 'input': 9, 'checks': [check(4, 12), check(5, 15)]}
        solver, ws = make_solver(Model(plan, lambda r: store('def run(n):\n    return 3 * n\n')), max_calls=20)
        self.assertIsNone(solver._unverified('Triple a number.', {'input': 9}, [None]))
