"""R4 (completion lost; regression vs d5dc8ce). The dispute memory `self._disputed` (kuttaka_runtime/solve.py:626,
1798-1806, 1825 at d330165) is keyed by the CHECK INPUT, while the question it records is about the METHOD
around the literal. A first, wrong method whose literal at a correct check is disputed only because its rule is
wrong NEAR that check (the auditor agrees with the check) marks the check input as disputed; every later method in
the job whose substitution test finds a literal at that input is then "disputed again without asking" and refuted
(written in this job), although the auditor would agree with it everywhere. A correct table is the ordinary case
(the corpus's own 'days in a month by name' is questioned).

Scenario: days in a month by name (common year). Checks February 28, April 30, July 31, all correct and supported
by a correct reference. The model's first table has a typo ("March": 30); its second is correct. Requested input:
"March" (right 31).
  d330165: the first table is disputed around February (March 30 vs the auditor's 31) and refuted; the correct
           second table is disputed without a call and refuted too; nothing is delivered and the correct method is
           marked wrong for every task.
  d5dc8ce: the reference-supported literals are not questioned; the differential test finds March, the correct
           table is written next and delivered: 31.
Control: the same job when the model's first table is already correct: delivered, 31 (also at d330165).
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Days in a month by name: run(name) returns the number of days in that month of a common (non-leap) year.'
CONTRACT = 'run(name) returns the days in the named month of a common year (February 28)'
DAYS = {'January': 31, 'February': 28, 'March': 31, 'April': 30, 'May': 31, 'June': 30, 'July': 31, 'August': 31,
        'September': 30, 'October': 31, 'November': 30, 'December': 31}


def table(days):
    return 'DAYS = %r\n\n\ndef run(name):\n    return DAYS[name]\n' % (days,)


RIGHT = table(DAYS)
TYPO = table(dict(DAYS, March=30))   # wrong: March has 31 days
REFERENCE = RIGHT.replace('def run(name)', 'def reference(name)')
CHECKS = [check('February', 28), check('April', 30), check('July', 31)]


def auditor(context):   # right everywhere
    return {'expected': [DAYS.get(v) if isinstance(v, str) else None for v in context['inputs']]}


def solve(first):
    written = []

    def plan(context):
        return {'contract': CONTRACT, 'input': 'March', 'checks': CHECKS, 'reference': REFERENCE,
                'small_inputs': ['January', 'June', 'October']}

    def act(request):
        written.append(request)
        return store(first if len(written) == 1 else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    solver, _ = make_solver(Model(plan, act, audit=auditor), ws=ws, max_calls=16)
    result = solver.solve(TASK)
    print('\nfirst %s: %s, answer %r (right 31) | questions %s | refuted %d' % (
        'typo' if first == TYPO else 'right', result['status'], result.get('answer'),
        [(e.get('outcome'), e.get('inputs')) for e in events(result, 'solve_literal_questioned')],
        len(ws.refuted())), file=sys.stderr)
    return result, ws


class R4DisputeOutlivesTheMethod(unittest.TestCase):
    def test_control_first_table_right(self):
        result, ws = solve(RIGHT)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 31))

    def test_typo_first_then_the_correct_table(self):
        result, ws = solve(TYPO)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 31),
                         'the correct table written second is disputed without a question and refuted')


if __name__ == '__main__':
    unittest.main()
