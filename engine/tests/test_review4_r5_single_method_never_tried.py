"""R5. solve_prastara.recipes leaves out a single method on the whole input because "reuse as it is has
tried it", but Solver.adopt tries only Workspace.helpful_methods (at most 4, wording first). A stored method
that passes the checks as it is, beyond those 4, is in the prastara's method list and is never tried.
Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store
from tests.test_solve_pramana import make


def library(distractors):
    ws = Workspace(Path(tempfile.mkdtemp()))
    rows = [('Square a number.', [check(2, 4), check(3, 9)], 'def run(x):\n    return x * x\n')]
    for k in range(distractors):   # worded like the new task, other input form
        rows.append((f'Area of a floor tile from its width and height, variant {k}.',
                     [check({'w': 2, 'h': 3}, 6 + k), check({'w': 1, 'h': 1}, 1 + k)],
                     f'def run(i):\n    return i["w"] * i["h"] + {k}\n'))
    for task, checks, source in rows:
        solver, _, _ = make(lambda c, t=task, k=checks: {'contract': t, 'input': None, 'checks': k}, source)
        solver.ws = ws
        assert solver.solve(task)['status'] == 'checked'
    return ws


def tile(ws):
    asked = []
    plan = lambda c: {'contract': 'tile area from its side', 'input': None, 'checks': [check(5, 25), check(6, 36)]}
    m = Metered(Model(plan, lambda r: asked.append(r) or store('def run(s):\n    return s ** 2\n')), {'used': 0, 'limit': 10})
    result = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                    say=lambda _: None).solve('Area of a floor tile whose edge is given.')
    return result, asked


class R5SingleMethodNeverTried(unittest.TestCase):
    def test_control_with_no_distractors_the_stored_square_is_reused(self):
        result, asked = tile(library(0))
        self.assertEqual(result['delivered_by'], 'reuse')

    def test_with_four_methods_worded_alike_the_stored_square_is_never_tried(self):
        result, asked = tile(library(4))
        print('\ndelivered_by:', result['delivered_by'], '| prastara screened:',
              result['mechanical'].get('prastara_screened'), '| model asked for code:', bool(asked))
        self.assertIn(result['delivered_by'], ('reuse', 'prastara'), 'a stored method passing as it is was never tried')


if __name__ == '__main__':
    unittest.main()
