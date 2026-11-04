"""R2. Refutation by source digest (_refuted_in, Workspace.refuted_sources) is global: a source shown wrong
for one task's contract is refused delivery on every later task, although it is right there. Methods,
references and auditors are written for this test; local executor only."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store
from tests.test_solve_pramana import sequence

SQUARE = 'def run(n):\n    return n * n\n'
TRIANGLE = 'def run(n):\n    return n * (n + 1) // 2\n'


def solver(ws, plan, act, audit=None, limit=20):
    m = Metered(Model(plan, act, audit=audit), {'used': 0, 'limit': limit})
    return Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256, say=lambda _: None)


def triangular_task(ws):
    plan = lambda c: {'contract': 'run(n) returns the n-th triangular number', 'input': None,
                      'checks': [check(0, 0), check(1, 1)],
                      'reference': 'def reference(n):\n    return sum(range(n + 1))\n', 'small_inputs': [2, 3]}
    return solver(ws, plan, sequence(SQUARE, TRIANGLE),
                  audit=lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]}).solve('The n-th triangular number.')


def square_task(ws):
    plan = lambda c: {'contract': 'run(n) returns n squared', 'input': None, 'checks': [check(2, 4), check(3, 9)]}
    return solver(ws, plan, sequence(SQUARE)).solve('Square a number.')


class R2SourceRefutationCrossesTasks(unittest.TestCase):
    def test_control_square_alone_is_delivered(self):
        result = square_task(Workspace(Path(tempfile.mkdtemp())))
        self.assertEqual(result['status'], 'checked')

    def test_square_is_refused_after_the_same_text_was_wrong_for_triangular_numbers(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        first = triangular_task(ws)
        self.assertEqual(first['status'], 'checked')
        self.assertEqual(len(ws.refuted()), 1)                       # n*n refuted for the triangular contract
        result = square_task(ws)
        refused = [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_delivery_refused']
        print('\nsquare task:', result['status'], '|', result.get('reason'), '| refused:', bool(refused))
        self.assertEqual(result['status'], 'checked',
                         'a correct square method is refused because the same text was wrong for another task')


if __name__ == '__main__':
    unittest.main()
