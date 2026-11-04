"""L4 (wrong answer delivered as checked later; a regression against 3af0808). _table_of_checks
(kuttaka_runtime/check_memorization.py:243-254 at 1ebed00) spares a table whose keys are all check inputs when the
source is a finite map (_finite_map, lines 230-240) and its keys "cover every known input" (lines 251-253). The
known inputs are the check inputs plus _known_inputs(spec) (solve.py:306-308: the requested input and the small
inputs). A job with no requested input and no reference (so no small inputs) knows only its check inputs, so ANY
pure table of its checks covers "the whole domain": the plainest memorization, `T = {4: 12, 5: 15, 7: 22};
return T[n]`, is read as logic and delivered as checked. (K5's whole-domain tables are right because their checks
happen to span the domain; nothing here tells that apart from a table of the checks.)

Scenario: "Triple a number", no reference, the planner's hand check 7 -> 22 (wrong). The model writes the table of
its three checks. At 1ebed00 it is delivered as checked and stored; the same task asked later with requested input
7 and checks 4 -> 12, 5 -> 15 reuses it by id and answers 22 (right: 21); the later job's detector does not flag it
either (key 7 is no check of that job: "a key no check has"). At 3af0808 the table is flagged and refuted, and the
later job's own method answers 21.

Control: the same first job with a requested input beyond the table (10) flags the table at 1ebed00; nothing is
stored and the later job answers 21. Scripted models; local executor; every method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST, LATER = 'Triple a number: run(n) returns 3 * n.', 'Triple a number: what is run(n) = 3 * n for this n?'
TABLE = 'T = {4: 12, 5: 15, 7: 22}\n\n\ndef run(n):\n    return T[n]\n'
RIGHT = 'def run(n):\n    return 3 * n\n'
CHECKS = [check(4, 12), check(5, 15), check(7, 22)]


def jobs(first_input):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': 'run(n) returns 3 * n for an integer n', 'input': first_input, 'checks': CHECKS}
        return {'contract': 'run(n) returns 3 * n for an integer n', 'input': 7,
                'checks': [check(4, 12), check(5, 15)]}
    act = lambda request: store(TABLE if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
    return first, later


class L4WholeDomainVacuous(unittest.TestCase):
    def test_detector(self):
        with_input = memorized_checks(TABLE, CHECKS, [10])
        without = memorized_checks(TABLE, CHECKS, [])
        print('\nflagged with a requested input 10:', [c['input'] for c in with_input], '| with none:',
              [c['input'] for c in without])
        self.assertEqual([c['input'] for c in with_input], [4, 5, 7], 'control')
        self.assertTrue(without, 'a table of the checks is "the whole domain" when nothing else is known')

    def test_control_end_to_end_with_a_requested_input(self):
        first, later = jobs(10)
        print('\ncontrol: first', first['status'], '| later', later['status'], later.get('answer'))
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', 21))

    def test_the_table_of_the_checks_is_delivered_stored_and_reused(self):
        first, later = jobs(None)
        print('\nfirst job: status', first['status'], '| support', first.get('evidence', {}).get('support'),
              '| flagged', [e['inputs'] for e in events(first, 'solve_memorized_checks')],
              '\nlater job: status', later['status'], '| delivered_by', later.get('delivered_by'),
              '| answer for 7:', later.get('answer'), '(right: 21)')
        self.assertNotEqual(first['status'], 'checked', 'a table of the checks is delivered as checked')
        self.assertNotEqual(later.get('answer'), 22, 'the stored table answers 7 with the wrong hand check')


if __name__ == '__main__':
    unittest.main()
