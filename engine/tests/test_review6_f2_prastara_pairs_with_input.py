"""F2. solve_prastara.recipes (solve_prastara.py:97-106 at 324e3ce) pairs only two terms of size 2
(range(2, size - 2) gives left = right = 2, at size 5) and skips `a is b`, yet returns complete=True, and the
commit says it "pairs any two results". A pair holding the input itself ([input, double(input)], 4 nodes;
[input, input.w], 4 nodes) or the same result twice ([input.w, input.w], 5 nodes) is a well-formed recipe the
compiler accepts (composition.py: `array` items may be `input`), never listed, while the list is called
complete. No model; no code run."""
import json
import unittest

from kuttaka_runtime import pramana, solve_prastara
from kuttaka_runtime.composition import compile_recipe
from tests.test_solve_review_regressions import check


def typed(name, checks):
    return (name, name, json.loads(pramana.form(checks)), json.loads(pramana.output_form(checks)))


class F2PairsWithTheInput(unittest.TestCase):
    def test_control_the_recipe_compiles(self):
        recipe = {'version': 1, 'output': {'op': 'array', 'items': [{'op': 'input'},
                  {'op': 'call', 'method': 'd' * 64, 'arg': {'op': 'input'}}]}}
        payload = {'kind': 'method', 'body': {'language': 'python', 'source': 'def run(x):\n    return 2 * x\n'}, 'dependencies': []}
        compiled = compile_recipe(recipe, resolve_method=lambda a: payload)
        self.assertIn('value', compiled.source)

    def test_a_pair_holding_the_input_is_not_listed_while_the_list_is_called_complete(self):
        double = typed('d' * 64, [check(2, 4), check(3, 6)])
        task = [check(2, [2, 4]), check(3, [3, 6])]                       # [x, double(x)]
        complete, listed = solve_prastara.recipes([double], json.loads(pramana.form(task)),
                                                  json.loads(pramana.output_form(task)), 100)
        formulas = [f for _, f, _ in listed]
        print('\ncomplete', complete, '| listed', formulas)
        self.assertFalse(complete and '[input, %s(input)]' % ('d' * 64) not in formulas,
                         'a 4-node pair holding the input is missing from a list called complete')

    def test_the_same_field_twice_is_not_listed_while_the_list_is_called_complete(self):
        area = ('a', 'area', {'h': 'num', 'w': 'num'}, 'num')
        complete, listed = solve_prastara.recipes([area], {'h': 'num', 'w': 'num'}, ['list', ['num']], 100)
        formulas = [f for _, f, _ in listed]
        print('complete', complete, '| listed', formulas)
        # Adapted after the fifth review: a recipe that runs no stored method ([input.w, input.w]) is plumbing,
        # outside prastāra's stated space (compositions of stored methods), and is not listed; the same result
        # twice with a method is.
        self.assertNotIn('[input.w, input.w]', formulas)
        self.assertFalse(complete and '[area(input), area(input)]' not in formulas,
                         'a pair of one result twice is missing from a list called complete')


if __name__ == '__main__':
    unittest.main()
