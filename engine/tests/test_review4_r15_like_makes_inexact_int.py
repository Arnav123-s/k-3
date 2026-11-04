"""R15. Settlement accepts "reference and blind re-derivation" when they agree within 1e-9 (same_value), then
writes the reference's value into the check. A reference computed in floats (Binet's formula) gives an
integral float beyond 2**53; _like makes it an int (354224848179263111168), and qualification compares
checks exactly, so the exact, correct method fails a check two derivations "confirmed" (and the value is
persisted to confirmed.jsonl). Not a regression: at cc8d583 the float was written and failed the same way.
The auditor's exact value was available. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store

FIB = 'def run(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n'
BINET = 'def reference(n):\n    s = 5 ** 0.5\n    return float(round(((1 + s) / 2) ** n / s))\n'   # floats, as Binet's formula gives
F100 = 354224848179261915075


def solve(limit):
    def fib(n):
        a, b = 0, 1
        for _ in range(n):
            a, b = b, a + b
        return a
    plan = lambda c: {'contract': 'run(n) returns the n-th Fibonacci number, exactly', 'input': None,
                      'checks': [check(10, 55), check(20, 6765), check(100, fib(99))],   # the last: off by one index
                      'reference': BINET}
    model = Model(plan, lambda r: store(FIB), audit=lambda c: {'expected': [fib(n) for n in c['inputs']]})
    m = Metered(model, {'used': 0, 'limit': limit})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                    max_executions=256, say=lambda _: None)
    return solver.solve('The n-th Fibonacci number.'), m


class R15LikeMakesInexactInt(unittest.TestCase):
    def test_the_revised_check_is_an_integer_no_exact_method_can_return(self):
        result, m = solve(4)
        revised = [c['expected'] for c in result['checks'] if c['input'] == 100]
        print('\nstatus', result['status'], '| calls', m.counter['used'], '| check n=100 ->', revised, '| exact', F100)
        self.assertEqual(result['status'], 'checked', 'the exact method failed a check revised to an inexact int')


if __name__ == '__main__':
    unittest.main()
