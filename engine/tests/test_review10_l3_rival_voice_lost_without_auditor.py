"""L3 (wrong answer delivered as checked; not a regression: 3af0808 does the same). rival() records what a rival said
about the requested input only after the blind re-derivation has replied (kuttaka_runtime/solve.py:2127-2129 at
1ebed00). When no model call is left for it (`unsettled_no_calls`, lines 2106-2108) or its reply is unusable
(`unsettled_no_auditor`, lines 2111-2114), rival() returns before recording anything, so the answer gate
(_answer_gate, lines 1665-1712) never hears the rival. Row 15 of docs/ARCHITECTURE.md counts "rival methods not shown
wrong" among the voices, and a rival that disagrees with the method is a 1-1 tie, "another value with as many voices
voids the answer". Here the rival is lost, the method's one voice stands alone, and its wrong answer is delivered.

Scenario (K1's): T(n) = n(n+1)/2, requested input 40; the planner's hand check 0 -> 1 is wrong. One attempt writes B
(right at 0 and at 40: 820) then A (fits 0 -> 1; 800 at 40). B contests check 0 and disagrees at 40.
  - The model-call limit ends with the attempt: A's 800 is delivered as checked.
  - With calls left, an auditor whose reply has the wrong length: A's 800 is delivered as checked.
Control: the same script with calls left and an auditor that re-derives nothing useful about 40 (a third value)
records B's voice, and the gate voids 800. Scripted models; local executor; every method and the auditor are
written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + 2 + ... + n.'
B = 'def run(n):\n    if n < 0:\n        return -1\n    return n * (n + 1) // 2\n'   # right everywhere; 820 at 40
A = ('def run(n):\n    if n < 0:\n        return -1\n    if n == 0:\n        return 1\n'
     '    if n % 20 == 0:\n        return (n // 2) * n\n    return n * (n + 1) // 2\n')   # fits 0 -> 1; 800 at 40


class Actor(Model):
    """Stores its sources in order (automatic submission rejects each that fails a check), as in K1."""
    def __init__(self, plan, audit, sources):
        super().__init__(plan, None, audit=audit)
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


def plan(context):
    return {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
            'checks': [check(0, 1), check(1, 1), check(2, 3), check(5, 15)]}


def solve(limit, auditor):
    m = Metered(Actor(plan, auditor, [B, A]), {'used': 0, 'limit': limit})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK)


def third_value(context):   # agrees with the hand check at 0 (with A) and says 810 at 40: neither side
    return {'expected': [1 if n == 0 else 810 if n == 40 else n * (n + 1) // 2 for n in context['inputs']]}


def wrong_length(context):
    return {'expected': [n * (n + 1) // 2 for n in context['inputs']][:-1]}


def report(name, result):
    print('\n%s: status %s | answer %s (right: 820) | rival events %s | disputed %s' % (
        name, result['status'], result.get('answer'), [e.get('outcome') for e in events(result, 'solve_rival')],
        bool(events(result, 'solve_answer_disputed'))))


class L3RivalVoiceLostWithoutAuditor(unittest.TestCase):
    def test_control_a_recorded_rival_voids_the_answer(self):
        result = solve(20, third_value)
        report('control', result)
        self.assertTrue(events(result, 'solve_rival'), 'precondition: the rival was compared')
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 800))

    def test_no_call_left_for_the_rederivation(self):
        result = None
        for limit in range(2, 8):   # the smallest limit whose attempt still delivers A
            result = solve(limit, third_value)
            if result['status'] == 'checked' or events(result, 'solve_answer_disputed'):
                break
        report('limit %d' % limit, result)
        self.assertIn('unsettled_no_calls', [e.get('outcome') for e in events(result, 'solve_rival')],
                      'precondition: the rival disagreed and no call was left')
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 800),
                            'a rival that disagrees on the requested input is not counted: 800 delivered')

    def test_unusable_rederivation(self):
        result = solve(20, wrong_length)
        report('wrong-length auditor', result)
        self.assertIn('unsettled_no_auditor', [e.get('outcome') for e in events(result, 'solve_rival')])
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 800),
                            'a rival that disagrees on the requested input is not counted: 800 delivered')


if __name__ == '__main__':
    unittest.main()
