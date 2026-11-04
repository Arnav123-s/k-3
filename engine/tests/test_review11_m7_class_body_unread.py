"""M7 (wrong answer delivered as checked, then reused by id; a REGRESSION against 1ebed00 and 3af0808, which both flag
it). Since 6bca8a7 the detector reads guards scope by scope: _scopes (kuttaka_runtime/check_memorization.py:169-178)
collects the module and every FunctionDef/AsyncFunctionDef, and _guarded_values.visit (line 210-211) skips a nested
FunctionDef, AsyncFunctionDef AND ClassDef as "its own scope". A ClassDef is never collected as a scope, so the
statements of a class body are read by no one. At 1ebed00 the statement walk entered every `body` (ClassDef
included) and caught the guard.

Scenario: "Triple a number", hand check 7 -> 22 (wrong); the method computes in a class body inside run,
`class K: v = 22 if n == 7 else 3 * n` (or the same as an if statement). It is delivered as checked and stored; the
later task for 7 reuses it by id: 22 (right: 21).
Control: the same conditional written in run's own body is flagged; the later task answers 21. Scripted models;
local executor; every method written for this test. Run the detector test with 1ebed00's kuttaka_runtime on the path
to see it pass there."""
import sys
import unittest
from pathlib import Path


from tests.review11_triple import jobs   # noqa: E402
from kuttaka_runtime.check_memorization import memorized_checks   # noqa: E402
from tests.test_solve_review_regressions import check   # noqa: E402

CHECKS = [check(4, 12), check(5, 15), check(7, 22)]
LATER_CHECKS = [check(4, 12), check(5, 15)]
IN_RUN = 'def run(n):\n    v = 22 if n == 7 else 3 * n\n    return v\n'
CLASS_EXPR = 'def run(n):\n    class K:\n        v = 22 if n == 7 else 3 * n\n    return K.v\n'
CLASS_IF = 'def run(n):\n    class K:\n        if n == 7:\n            v = 22\n        else:\n            v = 3 * n\n    return K.v\n'


class M7ClassBodyUnread(unittest.TestCase):
    def test_detector(self):
        self.assertEqual(memorized_checks(IN_RUN, CHECKS), [check(7, 22)], 'control: in run\'s own body')
        seen = {name: [c['input'] for c in memorized_checks(src, CHECKS)] for name, src in
                (('class body, conditional', CLASS_EXPR), ('class body, if', CLASS_IF))}
        print('\nflagged:', seen)
        self.assertEqual(seen, {'class body, conditional': [7], 'class body, if': [7]}, 'a class body is never read')

    def test_control_end_to_end(self):
        first, later = jobs(IN_RUN, CHECKS, 7, LATER_CHECKS)
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 21))

    def test_class_body_special_case_is_delivered_stored_and_reused(self):
        first, later = jobs(CLASS_EXPR, CHECKS, 7, LATER_CHECKS)
        self.assertNotEqual(first['status'], 'checked', 'a special case of a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 22, 'the stored special case answers 7 with the wrong hand check')


if __name__ == '__main__':
    unittest.main()
