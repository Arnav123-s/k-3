"""F3. solve_prastara.recipes (solve_prastara.py:108-110 at 324e3ce) checks TERM_LIMIT after a level is built and
before it is offered, so the level that crosses the limit is thrown away, the last one (size 5) included,
where nothing further would be built. With a wide input (70 number fields) the new pairs of fields alone make
4830 size-5 terms, and the size-5 chain r(q(p(input.f00))), listed at 2739320, is no longer listed: prastāra
finds nothing, although the output-form recipes at size 5 number only 70, far under the listing limit (48 is
the solver's; 100 here). No model; no code run."""
import unittest

from kuttaka_runtime import solve_prastara

FIELDS = {'f%02d' % i: 'num' for i in range(70)}
METHODS = [('p', 'p', 'num', 'str'), ('q', 'q', 'str', 'bool'), ('r', 'r', 'bool', {'flag': 'bool'})]


class F3TermLimitDropsTheBuiltLevel(unittest.TestCase):
    def test_a_size_five_chain_is_lost_when_the_last_level_crosses_the_limit(self):
        complete, listed = solve_prastara.recipes(METHODS, FIELDS, {'flag': 'bool'}, 100)
        formulas = [f for _, f, _ in listed]
        print('\ncomplete', complete, '| listed', len(listed), formulas[:2])
        self.assertIn('r(q(p(input.f00)))', formulas, 'the size-5 chain is not listed')

    def test_the_new_pair_of_fields_is_lost_with_ten_fields_and_a_full_library(self):
        # The N4 fix's own case ([input.h, input.w]-like pairs) with a modest input and PRASTARA_METHODS (8)
        # stored number methods: 1 + 10 + 80 + 640 terms to size 4, 5120 calls at size 5, so level 5 is dropped.
        methods = [('m%d' % i, 'm%d' % i, 'num', 'num') for i in range(8)]
        fields = {'f%d' % i: 'num' for i in range(10)}
        complete, listed = solve_prastara.recipes(methods, fields, ['list', ['num']], 48)
        formulas = [f for _, f, _ in listed]
        print('ten fields, eight methods: complete', complete, '| listed', len(listed), formulas[:2])
        # Adapted after the fifth review: a pair of bare fields runs no stored method (plumbing, not listed);
        # the N4 case with a method on one side is.
        self.assertNotIn('[input.f0, input.f1]', formulas)
        # Within five nodes no pair that runs a method has this output form ([m0(input.f0), input.f0] has six),
        # so an empty list called complete is the honest result here; the level is no longer dropped (the F3(a)
        # test above).
        self.assertTrue(complete)


if __name__ == '__main__':
    unittest.main()
