"""F4. _extend_checks (solve.py:1806 and 1816 at 324e3ce) cuts the confirmed checks to the free slots
(`fitting = confirmed[:MAX_MODEL_CHECKS - len(proposed)]`) BEFORE admitting them one by one, so when the book
is nearly full and the first confirmed check does not fit the contract, a later one that fits is never tried:
N1's failure ("a check that fits is dropped because another does not") survives in extending, which the commit
says is fixed ("Checks admitted one by one, in carrying and extending"). Here 11 checks the reference supports
(not hand values, so none is displaced), small inputs [70000, 12]: the counterexample 12 fits but is never
added, the refuted method meets the delivery gate and the job ends incomplete; the right method, which the
scripted actor writes on its next attempt, is never asked for. Control: the same with small inputs [12, 70000]
adds the check and delivers the right method. Scripted models; local executor."""
import unittest

from tests.test_solve_review_regressions import Model, check, events, make_solver, store

WRONG = 'def run(n):\n    return "a" * min(n, 11)\n'
RIGHT = 'def run(n):\n    return "a" * n\n'


def solve(small):
    def plan(context):
        return {'contract': 'run(n) returns "a" repeated n times', 'input': None,
                'checks': [check(n, 'a' * n) for n in range(11)],
                'reference': 'def reference(n):\n    return "a" * n\n', 'small_inputs': small}
    attempts = []

    def act(request):
        attempts.append(request)
        return store(WRONG if len(attempts) == 1 else RIGHT)
    solver, ws = make_solver(Model(plan, act, audit=lambda c: {'expected': ['a' * n for n in c['inputs']]}))
    result = solver.solve('Letters: run(n) returns a string of n letters a.')
    return result, ws


class F4ExtendChecksSlicesBeforeAdmitting(unittest.TestCase):
    def test_control_the_fitting_counterexample_first(self):
        result, _ = solve([12, 70000])
        self.assertEqual((result['status'], result.get('source')), ('checked', RIGHT))

    def test_a_fitting_counterexample_behind_an_oversized_one_is_never_tried(self):
        result, ws = solve([70000, 12])
        rejected = [e for e in events(result, 'solve_differential') if e.get('outcome') == 'rejected']
        print('\nstatus:', result['status'], '| check 12 added:', check(12, 'a' * 12) in result['checks'],
              '| rejected events:', len(rejected), '| refused at the gate:', len(events(result, 'solve_delivery_refused')))
        self.assertIn(check(12, 'a' * 12), result['checks'], 'the counterexample that fits was not added')
        self.assertEqual((result['status'], result.get('source')), ('checked', RIGHT))


if __name__ == '__main__':
    unittest.main()
