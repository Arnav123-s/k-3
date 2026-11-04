"""N4. R4(c) is fixed only in part. solve_prastara.recipes pairs only `call` terms
(calls_left/calls_right keep t[0]['op'] == 'call'), so a pair holding a map or a field
([map(square, input), total(input)], [f(input), input.x]) is never listed, yet `recipes` returns
complete=True and its docstring, the commit and ARCHITECTURE ("pairs in both orders ... complete up to
five nodes") say every well-formed recipe up to five nodes is listed. R4's own docstring named "a pair
holding a field or a map"; its test asserts only the get-on-a-call half. Also: N4b, the term list now
grows with fields of every dict-shaped term before the output-form limit applies (timing, PLAUSIBLE)."""
import json
import time
import unittest

from kuttaka_runtime import pramana, solve_prastara
from tests.test_solve_review_regressions import check


def typed(name, checks):
    return (name, name, json.loads(pramana.form(checks)), json.loads(pramana.output_form(checks)))


class N4PairsOfMapsAndFields(unittest.TestCase):
    def test_a_pair_holding_a_map_is_not_listed_while_the_list_is_called_complete(self):
        square = typed('square', [check(2, 4), check(3, 9)])
        total = typed('total', [check([1, 2], 3), check([4], 4)])
        task = [check([1, 2], [[1, 4], 3]), check([3], [[9], 3])]       # the squares, and the total
        complete, listed = solve_prastara.recipes([square, total], json.loads(pramana.form(task)),
                                                  json.loads(pramana.output_form(task)), 100)
        formulas = [f for _, f, _ in listed]
        print('\npair with a map: complete', complete, '| listed', formulas)
        self.assertTrue(complete)
        self.assertIn('[map(square, input), total(input)]', formulas, 'a 5-node pair holding a map is missing')

    def test_a_pair_holding_a_field_is_not_listed_while_the_list_is_called_complete(self):
        area = ('a', 'area', {'h': 'num', 'w': 'num'}, 'num')
        complete, listed = solve_prastara.recipes([area], {'h': 'num', 'w': 'num'}, ['list', ['num']], 100)
        formulas = [f for _, f, _ in listed]
        print('pair with a field: complete', complete, '| listed', formulas)
        self.assertTrue(complete)
        self.assertIn('[area(input), input.w]', formulas, 'a 5-node pair holding a field is missing')


class N4bTermGrowth(unittest.TestCase):
    def test_fields_of_fields_are_enumerated_before_any_limit(self):
        methods = [('m%d' % i, 'm%d' % i, 'num', 'num') for i in range(8)]   # PRASTARA_METHODS
        shape = {'f%02d' % i: {'g%02d' % j: 'num' for j in range(99)} for i in range(99)}  # 9901 nodes: holdable
        start = time.time()
        solve_prastara.recipes(methods, shape, 'str', 48)
        elapsed = time.time() - start
        print('enumeration of a 99x99 input form with 8 methods: %.2f s' % elapsed)
        self.assertLess(elapsed, 0.5, 'listing nothing took %.1f s' % elapsed)


if __name__ == '__main__':
    unittest.main()
