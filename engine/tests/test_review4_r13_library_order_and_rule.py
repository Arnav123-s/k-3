"""R13. shastra.build lists methods "newest first" from a dict keyed by artifact that keeps the first
insertion: a method reused later (remember_solved again) keeps its old place, and its rule and task
become those of the reusing task. Scripted models; local executor."""
import unittest

from kuttaka_runtime.shastra import build
from kuttaka_runtime.solve import Metered, Solver
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store
from tests.test_solve_pramana import make


class R13LibraryOrder(unittest.TestCase):
    def test_a_method_reused_last_is_not_listed_first(self):
        solver, ws, _ = make(lambda c: {'contract': 'run(x) returns x squared', 'input': None,
                                        'checks': [check(2, 4), check(3, 9)]}, 'def run(x):\n    return x * x\n')
        square = solver.solve('Square a number.')['artifact']
        solver, _, _ = make(lambda c: {'contract': 'run(s) returns s reversed', 'input': None,
                                       'checks': [check('ab', 'ba'), check('abc', 'cba')]}, 'def run(s):\n    return s[::-1]\n')
        solver.ws = ws
        solver.solve('Reverse a word.')
        m = Metered(Model(lambda c: {'contract': 'tile area from its side', 'input': None,
                                     'checks': [check(5, 25), check(6, 36)]}, lambda r: self.fail('model asked')),
                    {'used': 0, 'limit': 10})
        again = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                       say=lambda _: None).solve('Area of a square tile whose side is given, a number.')
        self.assertEqual(again['delivered_by'], 'reuse')
        library = build(ws)
        first = library['methods'][0]
        print('\nfirst listed:', first['task'], '| rule:', first['rule'])
        self.assertEqual(first['artifact'], square, 'the method used most recently is not listed first')


if __name__ == '__main__':
    unittest.main()
