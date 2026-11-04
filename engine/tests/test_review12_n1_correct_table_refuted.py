"""N1 (completion lost; regression vs 6bca8a7). The substitution test flags CORRECT lookup tables and unit factors.

check_substitution.read (kuttaka_runtime/check_substitution.py:258, 296 at 556adfd) flags a check when one constant,
changed, moves that check's answer alone among the inputs tried, and the constant equals ANY check's answer
(`kinds[j] == 'answer'`). A correct table entry does exactly that: `"h": 3600` is used only by inputs ending in h,
and among the inputs tried (the checks, plus inputs made by putting the source's own constants in a check's place)
only the check "1h" ends in h. For text inputs there are no near or scaled inputs, so nothing else exercises the
entry. The `whole` exemption (lines 301-303) needs every answered input to be a check input, so a table with keys
beyond the checks is flagged even when every check rests on the reference or two derivations.

In _memorized (kuttaka_runtime/solve.py:1818-1839) the test's hit is added to the text reading's (which is clean
here), the method written in this job is refuted for every task, and after MEMORIZATION_ROUNDS the task is left
incomplete. At 6bca8a7 the same job is checked and its method is reused by id by a later task.

Scenario: duration text to seconds, a correct method with the most natural hand checks ("1h" -> 3600, ...); the
month-length table by name (every month listed, checks February/April/July). Both correct, both refuted.
Control: the same duration method with the hand check "2h" -> 7200 instead of "1h" -> 3600 is checked and kept.
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

DURATION = 'UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}\n\n\ndef run(text):\n    return int(text[:-1]) * UNITS[text[-1]]\n'
MONTHS = ('DAYS = {"January": 31, "February": 28, "March": 31, "April": 30, "May": 31, "June": 30, "July": 31,\n'
          '        "August": 31, "September": 30, "October": 31, "November": 30, "December": 31}\n\n\n'
          'def run(name):\n    return DAYS[name]\n')
FIRST = 'Duration to seconds: run(text) returns the seconds in a duration such as "30m" (units s, m, h, d).'
LATER = 'Seconds in a duration: what is run(text) for this duration text?'
CONTRACT = 'run(text) returns int(text[:-1]) times the seconds of the unit text[-1] (s, m, h, d)'
MONTH_TASK = 'Days in a month: run(name) returns the days in the named month of a common (non-leap) year.'


def duration_jobs(first_checks, later_checks):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': CONTRACT, 'input': None, 'checks': first_checks}
        return {'contract': CONTRACT, 'input': '5h', 'checks': later_checks}
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, lambda r: store(DURATION)), ws=ws, max_calls=12)[0].solve(FIRST)
    refuted = set(ws.refuted())
    later = make_solver(Model(plan, lambda r: store(DURATION)), ws=ws, max_calls=12)[0].solve(LATER)
    print('\nfirst %s (%s) | flagged %s | refuted %d | later %s, delivered_by %s, answer %s' % (
        first['status'], first.get('reason'), [e['inputs'] for e in events(first, 'solve_memorized_checks')],
        len(refuted), later['status'], later.get('delivered_by'), later.get('answer')), file=sys.stderr)
    return first, refuted, later


class N1CorrectTableRefuted(unittest.TestCase):
    def test_control_duration_without_a_check_equal_to_a_factor(self):
        first, refuted, later = duration_jobs(
            [check('2h', 7200), check('30m', 1800), check('45s', 45), check('2d', 172800)],
            [check('2h', 7200), check('10s', 10)])
        self.assertEqual(first['status'], 'checked')
        self.assertFalse(refuted)
        self.assertEqual((later['status'], later.get('answer')), ('checked', 18000))

    def test_duration_with_the_natural_check_1h(self):
        first, refuted, later = duration_jobs(
            [check('1h', 3600), check('30m', 1800), check('45s', 45), check('2d', 172800)],
            [check('1h', 3600), check('10s', 10)])
        self.assertEqual(first['status'], 'checked', 'a correct method with a correct check "1h" -> 3600 is left '
                                                     'incomplete: the factor 3600 is read as a literal for "1h"')
        self.assertFalse(refuted, 'the correct method is refuted for every task')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 18000))

    def test_month_lengths_by_name_even_with_support(self):
        checks = [check('February', 28), check('April', 30), check('July', 31)]

        def plan(context):
            return {'contract': 'run(name) returns the number of days of the named month in a common year',
                    'input': None, 'checks': checks,
                    # a correct reference: the checks rest on it (supported), so the method is set aside, not refuted
                    'reference': MONTHS.replace('def run(name)', 'def reference(name)')}
        solver, ws = make_solver(Model(plan, lambda r: store(MONTHS)), max_calls=12)
        result = solver.solve(MONTH_TASK)
        print('\nmonths: %s (%s) | flagged %s | refuted %d' % (
            result['status'], result.get('reason'), [e['inputs'] for e in events(result, 'solve_memorized_checks')],
            len(ws.refuted())), file=sys.stderr)
        self.assertEqual(result['status'], 'checked', 'the correct month table is left incomplete')


if __name__ == '__main__':
    unittest.main()
