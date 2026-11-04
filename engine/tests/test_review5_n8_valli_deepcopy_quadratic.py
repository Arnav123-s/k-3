"""N8. The R7 fix deep-copies every answer as it is stored in the chain (and again on every hit). A vallī
recursion whose answers are lists that grow with the input (the n-th row of a triangle, the first n of
something: combine(n, [prev]) = prev + [x]) now spends a deepcopy of size n at each of n levels: about 40
times slower than before, so inputs well inside vallī's documented reach (about 20000 levels) exceed the
20 s sandbox timeout that solve_task gives every run. Measured here on the assembled source, in process
(no model code: the reduction is written for this test)."""
import time
import unittest

from kuttaka_runtime import solve_valli

REDUCTION = '''def measure(n):
    return n
def small(n):
    return n <= 1
def reduce(n):
    return [n - 1]
def combine(n, parts):
    return parts[0] + [n]
def base(n):
    return list(range(n + 1))
'''


class N8ValliDeepcopyQuadratic(unittest.TestCase):
    def test_a_list_valued_chain_of_8000_levels_runs_in_a_few_seconds(self):
        space = {}
        exec(compile(solve_valli.assemble(REDUCTION), '<valli>', 'exec'), space)
        start = time.time()
        out = space['run'](8000)
        elapsed = time.time() - start
        print('\nvalli, 8000 levels, list answers: %.2f s' % elapsed)
        self.assertEqual(out, list(range(8001)))
        self.assertLess(elapsed, 3.0, 'the chain copies make an 8000-level list recursion take %.1f s' % elapsed)


if __name__ == '__main__':
    unittest.main()
