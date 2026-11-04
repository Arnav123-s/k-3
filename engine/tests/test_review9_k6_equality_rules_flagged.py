"""K6 (false positives, not a regression: 11e0ea9 flags them too). memorized_checks (check_memorization.py:151-193 at
3af0808) treats any `==` between a name and a non-trivial constant as a special case of one input, so general rules
written with equalities are flagged while the same rule written as a membership is not (H3's fix):
  - `if m == 4 or m == 6 or m == 9 or m == 11: return 30` (check 4 -> 30) is flagged, `if m in (4, 6, 9, 11)` is not;
  - a base case above 2, `if n == 3: return 4` in the 1-2-3-step staircase count (check 3 -> 4);
  - an if-chain over a small domain, `if move == "rock": return "paper"` (check rock -> paper).
In the solve flow a flagged literal is never delivered (since G1), so these correct methods end incomplete.
Control: the membership form of the month rule is not flagged. Pure detector calls; no execution."""
import unittest

from kuttaka_runtime.check_memorization import memorized_checks
from tests.test_solve_review_regressions import check

MONTH_EQ = ('def run(m):\n    if m == 4 or m == 6 or m == 9 or m == 11:\n        return 30\n'
            '    if m == 2:\n        return 28\n    return 31\n')
MONTH_IN = 'def run(m):\n    if m in (4, 6, 9, 11):\n        return 30\n    if m == 2:\n        return 28\n    return 31\n'
STAIRS = ('def run(n):\n    if n == 0:\n        return 1\n    if n == 1:\n        return 1\n    if n == 2:\n        return 2\n'
          '    if n == 3:\n        return 4\n    return run(n - 1) + run(n - 2) + run(n - 3)\n')
RPS_IF = ('def run(move):\n    if move == "rock":\n        return "paper"\n    if move == "paper":\n'
          '        return "scissors"\n    return "rock"\n')


class K6EqualityRulesFlagged(unittest.TestCase):
    def test_control_membership_form_is_not_flagged(self):
        self.assertEqual(memorized_checks(MONTH_IN, [check(4, 30), check(1, 31)]), [])

    def test_general_rules_written_with_equalities(self):
        seen = {'month equalities': memorized_checks(MONTH_EQ, [check(4, 30), check(1, 31)]),
                'base case n == 3': memorized_checks(STAIRS, [check(3, 4), check(5, 13)]),
                'small-domain if-chain': memorized_checks(RPS_IF, [check('rock', 'paper'), check('scissors', 'rock')])}
        print('\nflagged:', seen)
        self.assertEqual(seen, dict.fromkeys(seen, []), 'a general rule written with == is read as a special case')


if __name__ == '__main__':
    unittest.main()
