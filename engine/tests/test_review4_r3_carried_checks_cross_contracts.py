"""R3. _carry_confirmed keys confirmed checks by prompt text only (filtered by input form): a check two
derivations settled for one contract replaces a correct check of another task worded the same (here the
user's own examples show a different rule). Scripted models; local executor."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime import pramana
from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store

PROMPT = 'Apply the rule the examples show to x.'


def task_a(ws):          # doubling; the planner's hand check 3 -> 7 is corrected to 6 before code (confirmed)
    plan = lambda c: {'contract': 'run(x) returns 2*x', 'input': None, 'checks': [check(3, 7), check(5, 10)],
                      'reference': 'def reference(x):\n    return 2 * x\n'}
    m = Metered(Model(plan, lambda r: store('def run(x):\n    return 2 * x\n'),
                      audit=lambda c: {'expected': [2 * v for v in c['inputs']]}), {'used': 0, 'limit': 20})
    return Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                  say=lambda _: None).solve(PROMPT, [check(1, 2), check(2, 4)])


def task_b(ws, limit=20):   # tripling, by the user's own examples; the planner's checks are all right
    plan = lambda c: {'contract': 'run(x) returns 3*x', 'input': None, 'checks': [check(3, 9), check(4, 12)]}
    model = Model(plan, lambda r: store('def run(x):\n    return 3 * x\n'),
                  audit=lambda c: {'expected': [3 * v for v in c['inputs']]})
    m = Metered(model, {'used': 0, 'limit': limit})
    solver = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                    say=lambda _: None)
    return solver.solve(PROMPT, [check(1, 3), check(2, 6)]), m, solver


class R3CarriedChecksCrossContracts(unittest.TestCase):
    def test_control_task_b_alone_is_solved_within_three_calls(self):
        result, m, _ = task_b(Workspace(Path(tempfile.mkdtemp())), limit=3)
        self.assertEqual(result['status'], 'checked')
        self.assertLessEqual(m.counter['used'], 3)

    def test_a_check_settled_for_doubling_replaces_a_right_check_of_tripling(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        self.assertEqual(task_a(ws)['status'], 'checked')
        self.assertIn({'input': 3, 'expected': 6}, ws.confirmed(PROMPT))
        result, m, solver = task_b(ws)
        carried = [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_confirmed_carried']
        print('\ntask B:', result['status'], '| calls', m.counter['used'], '| carried', json.dumps(carried[:1] and carried[0]['carried']),
              '| strategies', result.get('strategies'))
        self.assertEqual(carried, [], 'a check settled under contract "2*x" was carried into contract "3*x"')

    def test_with_a_small_budget_the_carried_check_loses_the_task(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        task_a(ws)
        result, m, _ = task_b(ws, limit=3)
        print('\ntask B, 3 calls:', result['status'], '|', result.get('reason'))
        self.assertEqual(result['status'], 'checked', 'the right tripling method failed a check from another contract')


if __name__ == '__main__':
    unittest.main()
