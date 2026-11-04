"""M4 (wrong answer delivered as checked, then reused by id; not a regression: 1ebed00 misses it too). _guarded_values
reads a conditional expression only when it IS the value returned or assigned (`conditional`,
kuttaka_runtime/check_memorization.py:197-206, called at lines 227-230 on stmt.value). A conditional expression one
call or operator deep, `return int(22 if n == 7 else 3 * n)` or
`return max(0, 22 if n == 7 else 3 * n)`, is never visited, and neither is a guarded literal written as a
conversion, `if n == 7: return float(22)` (_literal fails on the call, line 404-405). These are not the stated limits
(arithmetic on the guard's truth value `2 * x + (x == 3)`, a lambda, `cond and value or other`): the literal and its
equality guard are both written out.

Scenario: "Triple a number", hand check 7 -> 22 (wrong). It is delivered as checked and stored; the later task for
7 reuses it by id: 22 (right: 21).
Control: `return 22 if n == 7 else 3 * n` (the conditional as the returned value) is flagged; the later task answers
21. Scripted models; local executor; every method written for this test."""
import sys
import unittest
from pathlib import Path


from tests.review11_triple import jobs   # noqa: E402
from kuttaka_runtime.check_memorization import memorized_checks   # noqa: E402
from tests.test_solve_review_regressions import check   # noqa: E402

CHECKS = [check(4, 12), check(5, 15), check(7, 22)]
LATER_CHECKS = [check(4, 12), check(5, 15)]
TOP = 'def run(n):\n    return 22 if n == 7 else 3 * n\n'
FORMS = {'int(...)': 'def run(n):\n    return int(22 if n == 7 else 3 * n)\n',
         'max(0, ...)': 'def run(n):\n    return max(0, 22 if n == 7 else 3 * n)\n',
         'float(22) under a guard': 'def run(n):\n    if n == 7:\n        return float(22)\n    return 3 * n\n'}


class M4ConditionalInsideACallUnseen(unittest.TestCase):
    def test_detector(self):
        self.assertEqual(memorized_checks(TOP, CHECKS), [check(7, 22)], 'control: the conditional returned')
        seen = {name: [c['input'] for c in memorized_checks(src, CHECKS)] for name, src in FORMS.items()}
        print('\nflagged:', seen)
        self.assertEqual(seen, dict.fromkeys(FORMS, [7]), 'a guarded literal one call deep is not seen')

    def test_control_end_to_end(self):
        first, later = jobs(TOP, CHECKS, 7, LATER_CHECKS)
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 21))

    def test_conditional_inside_int_is_delivered_stored_and_reused(self):
        first, later = jobs(FORMS['int(...)'], CHECKS, 7, LATER_CHECKS)
        self.assertNotEqual(first['status'], 'checked', 'a special case of a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 22, 'the stored special case answers 7 with the wrong hand check')


if __name__ == '__main__':
    unittest.main()
