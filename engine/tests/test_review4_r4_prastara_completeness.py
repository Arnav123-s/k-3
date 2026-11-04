"""R4. solve_prastara.recipes reports complete=True while leaving out well-formed recipes:
 (a) a method whose checks include the empty list as the first or most common input is typed
     ['list', []] and is never applied to a non-empty list; a task whose first check expects [] is typed
     the same way, so no map recipe has its output form;
 (b) a pair is listed in one order only ([f(x), g(x)], never [g(x), f(x)]);
 (c) a field of a method's result (get on a call), and a pair holding a field or a map, are never listed.
Scripted models; local executor."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime import pramana, solve_prastara
from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store
from tests.test_solve_pramana import make


def typed(name, checks):
    return (name, name, json.loads(pramana.form(checks)), json.loads(pramana.output_form(checks)))


class R4aEmptyListTyping(unittest.TestCase):
    def test_total_checked_on_the_empty_list_first_is_never_applied_to_a_list(self):
        square = typed('square', [check(2, 4), check(3, 9)])
        total = typed('total', [check([], 0), check([4], 4)])          # a natural pair of checks for a sum
        task = [check({'values': [1, 2]}, 5), check({'values': [3]}, 9)]
        complete, listed = solve_prastara.recipes([square, total], json.loads(pramana.form(task)),
                                                  json.loads(pramana.output_form(task)), 100)
        formulas = [f for _, f, _ in listed]
        print('\n(a) total typed', total[2], '| complete', complete, '| listed', formulas)
        self.assertTrue(complete)
        self.assertIn('total(map(square, input.values))', formulas)

    def test_a_task_whose_first_check_expects_the_empty_list_lists_no_map(self):
        square = typed('square', [check(2, 4), check(3, 9)])
        task = [check([], []), check([1, 2], [1, 4])]                    # square each value
        complete, listed = solve_prastara.recipes([square], json.loads(pramana.form(task)),
                                                  json.loads(pramana.output_form(task)), 100)
        print('(a) task typed', pramana.form(task), '->', pramana.output_form(task), '| listed', [f for _, f, _ in listed])
        self.assertIn('map(square, input)', [f for _, f, _ in listed])

    def test_end_to_end_the_model_is_asked_for_code_although_the_composition_exists(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        for task, checks, source in (('Square a number.', [check(2, 4), check(3, 9)], 'def run(x):\n    return x * x\n'),
                                     ('Total of a list of numbers.', [check([], 0), check([4], 4)],
                                      'def run(xs):\n    return sum(xs)\n')):
            solver, _, _ = make(lambda c, t=task, k=checks: {'contract': t, 'input': None, 'checks': k}, source)
            solver.ws = ws
            self.assertEqual(solver.solve(task)['status'], 'checked')
        asked = []
        plan = lambda c: {'contract': 'sum of the squares of the values', 'input': None,
                          'checks': [check({'values': [1, 2]}, 5), check({'values': [3]}, 9), check({'values': []}, 0)]}
        model = Model(plan, lambda r: asked.append(r) or store('def run(i):\n    return sum(v * v for v in i["values"])\n'))
        m = Metered(model, {'used': 0, 'limit': 10})
        result = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                        say=lambda _: None).solve('The values given: add up their squares.')
        self.assertEqual(result['delivered_by'], 'prastara', 'prastara missed total(map(square, input.values))')


class R4bcPairsAndFields(unittest.TestCase):
    def test_a_pair_is_listed_in_one_order_only(self):
        double = ('d', 'double', 'num', 'num')
        square = ('s', 'square', 'num', 'num')
        complete, listed = solve_prastara.recipes([double, square], 'num', ['list', ['num']], 100)
        formulas = [f for _, f, _ in listed]
        print('\n(b) complete', complete, '| listed', formulas)
        self.assertTrue(complete)
        self.assertIn('[double(input), square(input)]', formulas)
        self.assertIn('[square(input), double(input)]', formulas)

    def test_a_field_of_a_result_and_a_pair_with_a_field_are_not_listed(self):
        divmod_ = ('q', 'divmod', {'a': 'num', 'b': 'num'}, {'q': 'num', 'r': 'num'})
        square = ('s', 'square', 'num', 'num')
        complete, listed = solve_prastara.recipes([divmod_, square], {'a': 'num', 'b': 'num'}, 'num', 100)
        formulas = [f for _, f, _ in listed]
        print('(c) complete', complete, '| listed', formulas)
        self.assertTrue(complete)
        self.assertIn('divmod(input).q', formulas)      # size 3: get on a call


if __name__ == '__main__':
    unittest.main()
