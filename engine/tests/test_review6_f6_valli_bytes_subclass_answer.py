"""F6 (low). solve_valli._thaw (solve_valli.py:51-52 at 324e3ce) tells a pickled snapshot from a deep-copied one
by isinstance(frozen, bytes). An answer of a bytes subclass defined in the reduction cannot be pickled (its
class is not importable), so it is stored by deepcopy, still a bytes instance, and the next lookup of that
input runs pickle.loads on it: the recursion fails (or would return garbage) on a shared smaller input. At
2739320 (deepcopy only) the same reduction runs. In process, as tests/test_review5_n8 does; the reduction is
written for this test."""
import unittest

from kuttaka_runtime import solve_valli

REDUCTION = '''class Word(bytes):
    pass
def measure(n):
    return n
def small(n):
    return n <= 1
def reduce(n):
    return [n - 1, n - 2] if n > 2 else [n - 1]
def combine(n, parts):
    return Word(b'%d' % sum(len(p) for p in parts))
def base(n):
    return Word(b'x')
'''


class F6BytesSubclassAnswer(unittest.TestCase):
    def test_a_shared_input_with_a_bytes_subclass_answer(self):
        space = {}
        exec(compile(solve_valli.assemble(REDUCTION), '<valli>', 'exec'), space)
        try:
            out = space['run'](6)
        except Exception as error:
            self.fail('the chain cannot hand out a stored answer: %r' % error)
        self.assertIsInstance(out, bytes)


if __name__ == '__main__':
    unittest.main()
