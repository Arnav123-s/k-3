"""G4 (low; pre-existing shape, kept by the rewrite). _admit_one_by_one (solve.py:476-517 at 9fc29a0) counts a check as
taken whenever CheckBook(...) does not raise, but CheckBook silently omits a proposal once the book holds MAX_CHECKS
(64) records (check_contracts.py:204-206). With 60 user examples and 4 planner checks, a confirmed counterexample is
reported taken (so _extend_checks returns 1 and labels it) although the returned book does not hold it.
Control: with 50 user examples the same counterexample is in the book. Pure helper; no model or executor."""
import unittest

from kuttaka_runtime.check_contracts import MAX_CHECKS
from kuttaka_runtime.solve import _admit_one_by_one


def run(users):
    spec = {'contract': 'run(n) returns n + 1', 'examples_origin': 'user'}
    supplied = [{'input': n, 'expected': n + 1} for n in range(users)]
    proposed = [{'input': n, 'expected': n + 1} for n in range(1000, 1004)]
    extra = [{'input': 5000, 'expected': 5001}]
    return _admit_one_by_one('Add one.', spec, supplied, proposed, extra)


class G4TakenNotInBook(unittest.TestCase):
    def test_control_room_left(self):
        book, taken, _ = run(50)
        self.assertEqual(taken, [{'input': 5000, 'expected': 5001}])
        self.assertIn({'input': 5000, 'expected': 5001}, book.checks)

    def test_taken_matches_the_book_at_the_record_limit(self):
        book, taken, _ = run(MAX_CHECKS - 4)
        print('\ntaken:', taken, '| in book:', {'input': 5000, 'expected': 5001} in book.checks,
              '| omitted:', book.metadata['omitted_model_checks'])
        for check in taken:
            self.assertIn(check, book.checks, 'a check reported taken is not in the book')


if __name__ == '__main__':
    unittest.main()
