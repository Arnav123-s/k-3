"""M1 (wrong answer delivered as checked; L3's fix does not hold once a rival disagrees on six inputs). rival()
now records the rival's voice before the auditor is asked (kuttaka_runtime/solve.py:2117-2120 at 6bca8a7), but only
for the inputs in `disputed = (contested + elsewhere)[:6]` (line 2094). The contested checks come first, then the
probe batches in _probe_batches order: small inputs, inputs near the checks, and LAST the requested input
(solve.py:535-551). A rival that disagrees with the method on six or more checks / probe inputs before it never has
its value on the requested input recorded; when the auditor cannot settle (no call left, or an unusable reply),
rival() returns and the answer gate (_answer_gate) counts the method's one voice alone.

Scenario (L3's harness and task): T(n) = n(n+1)/2, requested input 40. The planner's six hand checks are all off by
one (it computed T(n+1)); one attempt writes B (right: 820 at 40) then A = (n+1)(n+2)/2 (fits every check; 861 at
40). B contests all six checks, so disputed holds those six and drops 40.
  - The model-call limit ends with the attempt: A's 861 is delivered as checked.
  - With calls left, an auditor whose reply has the wrong length: A's 861 is delivered as checked.
Control: the same with two off-by-one checks (2 contested + 2 near + the requested input fit under the cap): B's
voice on 40 is recorded and the gate voids 861. Scripted models; local executor; every method is written here."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + 2 + ... + n.'
B = 'def run(n):\n    if n < 0:\n        return -1\n    return n * (n + 1) // 2\n'            # right; 820 at 40
A = 'def run(n):\n    if n < 0:\n        return -1\n    return (n + 1) * (n + 2) // 2\n'      # fits T(n+1) checks; 861


class Actor(Model):
    """Stores its sources in order (automatic submission rejects each that fails a check), as in K1 and L3."""
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


def planner(inputs):
    def plan(context):
        return {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                'checks': [check(n, (n + 1) * (n + 2) // 2) for n in inputs]}   # hand values: T(n+1)
    return plan


SIX, TWO = [1, 2, 3, 4, 5, 6], [1, 2]


def solve(inputs, limit, auditor):
    m = Metered(Actor(planner(inputs), auditor, [B, A]), {'used': 0, 'limit': limit})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK)


def same_mistake(context):   # re-derives T(n+1) on the checks with the planner, and nothing usable at 40
    return {'expected': [(n + 1) * (n + 2) // 2 if n != 40 else 840 for n in context['inputs']]}


def wrong_length(context):
    return {'expected': [n * (n + 1) // 2 for n in context['inputs']][:-1]}


def smallest_limit(inputs):
    result, limit = None, None
    for limit in range(2, 9):   # the smallest limit whose attempt still delivers A (no call left for the auditor)
        result = solve(inputs, limit, same_mistake)
        if result['status'] == 'checked' or events(result, 'solve_answer_disputed'):
            break
    return limit, result


def report(name, result):
    print('\n%s: status %s | answer %s (right: 820) | rival events %s | disputed %s' % (
        name, result['status'], result.get('answer'), [e.get('outcome') for e in events(result, 'solve_rival')],
        bool(events(result, 'solve_answer_disputed'))))


class M1RivalVoiceCutBySixCap(unittest.TestCase):
    def test_control_two_contested_checks_the_rival_voice_voids_the_answer(self):
        limit, result = smallest_limit(TWO)
        report('control, two checks, limit %d' % limit, result)
        self.assertIn('unsettled_no_calls', [e.get('outcome') for e in events(result, 'solve_rival')],
                      'precondition: the rival disagreed and no call was left')
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 861))

    def test_six_contested_checks_no_call_left(self):
        limit, result = smallest_limit(SIX)
        report('six checks, limit %d' % limit, result)
        self.assertIn('unsettled_no_calls', [e.get('outcome') for e in events(result, 'solve_rival')],
                      'precondition: the rival disagreed and no call was left')
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 861),
                            'the rival disagrees on 40 but its voice was cut by the six-input cap: 861 delivered')

    def test_six_contested_checks_unusable_rederivation(self):
        result = solve(SIX, 20, wrong_length)
        report('six checks, wrong-length auditor', result)
        self.assertIn('unsettled_no_auditor', [e.get('outcome') for e in events(result, 'solve_rival')])
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 861),
                            'the rival disagrees on 40 but its voice was cut by the six-input cap: 861 delivered')

    def test_control_two_checks_unusable_rederivation(self):
        result = solve(TWO, 20, wrong_length)
        report('control, two checks, wrong-length auditor', result)
        self.assertIn('unsettled_no_auditor', [e.get('outcome') for e in events(result, 'solve_rival')])
        self.assertNotEqual((result['status'], result.get('answer')), ('checked', 861))


if __name__ == '__main__':
    unittest.main()
