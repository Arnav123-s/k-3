"""M6 (wrong answer delivered as checked, then reused by id; not a regression: 1ebed00 and 3af0808 miss it too). The
module docstring (kuttaka_runtime/check_memorization.py:14-16) and row 13 name "a conversion of one (`str(n)`)" as a
guard subject, but a guard on the text of a number can never be flagged. Its constant "17" is a string, the known
leaves are the integers of the inputs, so _known("17", ...) is false (lines 255-260) and the constant is "a value no
known input has": _case_analyses (lines 263-278) reads `str(n)` as a case analysis on its own single comparison, its
constant goes to `analysed`, `pinned` is empty and the guard is dropped (lines 375-380). (A one-character text,
`str(n) == "7"`, is trivial, line 83, and dropped as well.) The same holds for `int(s) == 1900` on a text input.
L8's fix made the table keyed by the text (`{"17": 52}.get(str(n))`) visible; the guard is not.

Scenario: "Triple a number", hand check 17 -> 52 (wrong; 51). The method special-cases it by its text, `if str(n) ==
"17": return 52`. It is delivered as checked and stored; the later task for 17 (checks 4 -> 12, 5 -> 15) reuses it by
id and answers 52 (right: 51).
Control: the table keyed by the text, `return {"17": 52}.get(str(n), 3 * n)`, is flagged; the later task answers 51.
Scripted models; local executor; every method written for this test."""
import sys
import unittest
from pathlib import Path


from tests.review11_triple import jobs   # noqa: E402
from kuttaka_runtime.check_memorization import memorized_checks   # noqa: E402
from tests.test_solve_review_regressions import check   # noqa: E402

CHECKS = [check(4, 12), check(5, 15), check(17, 52)]
LATER_CHECKS = [check(4, 12), check(5, 15)]
TABLE = 'def run(n):\n    return {"17": 52}.get(str(n), 3 * n)\n'
GUARD = 'def run(n):\n    if str(n) == "17":\n        return 52\n    return 3 * n\n'


class M6TextGuardExemptsItself(unittest.TestCase):
    def test_detector(self):
        self.assertEqual(memorized_checks(TABLE, CHECKS), [check(17, 52)], 'control: keyed by the text')
        self.assertEqual(memorized_checks(GUARD, CHECKS), [check(17, 52)], 'a guard on str(n) is never flagged')

    def test_detector_text_input_converted(self):
        src = 'def run(s):\n    if int(s) == 1900:\n        return 28\n    return 29 if int(s) % 4 == 0 else 28\n'
        control = 'def run(s):\n    if s == "1900":\n        return 28\n    return 29 if int(s) % 4 == 0 else 28\n'
        checks = [check('2024', 29), check('1900', 28), check('2023', 28)]
        self.assertEqual(memorized_checks(control, checks), [check('1900', 28)], 'control: s == "1900"')
        self.assertEqual(memorized_checks(src, checks), [check('1900', 28)], 'int(s) == 1900 is never flagged')

    def test_control_end_to_end(self):
        first, later = jobs(TABLE, CHECKS, 17, LATER_CHECKS)
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 51))

    def test_text_guard_is_delivered_stored_and_reused(self):
        first, later = jobs(GUARD, CHECKS, 17, LATER_CHECKS)
        self.assertNotEqual(first['status'], 'checked', 'a special case of a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), 52, 'the stored special case answers 17 with the wrong hand check')


if __name__ == '__main__':
    unittest.main()
