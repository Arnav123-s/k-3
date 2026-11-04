"""K7 (completion lost; a regression against 11e0ea9). _answer_gate (solve.py:1672-1680 at 3af0808) voids the answer
whenever the fresh reference disagrees on a requested input declared small, before any voice is counted: the
reference decides alone, against the method and a blind re-derivation that agree. Everywhere else a disagreement is
settled two against one (differential: "A reference the auditor contradicts is ignored", solve.py:1824; rival:
"the reference ... never decides alone", ARCHITECTURE row 13), and K3 shows the converse: when the reference agrees
it is not counted at all.

Scenario: T(n), requested input 40, declared small; the planner's reference stops at 39 (780 at 40). RIGHT passes
every check; differential finds RIGHT against the reference at 40, the auditor says 820 (with RIGHT), so no check
is added and the reference is ignored there. The gate runs the reference again: 780 != 820, void. Method + auditor
(820) against the reference alone (780). At 11e0ea9 820 is delivered.

Control: a reference right at 40 delivers 820. Scripted models; local executor.

Kept as the rule (see the second test's docstring): the veto is stated in docs/ARCHITECTURE.md, row 15."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + ... + n.'
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'


def solve(bound):
    reference = 'def reference(n):\n    return sum(range(min(n, %d) + 1))\n' % bound
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
                      'reference': reference, 'small_inputs': [3, 4, 40]}
    auditor = lambda c: {'expected': [n * (n + 1) // 2 for n in c['inputs']]}
    m = Metered(Model(plan, lambda r: store(RIGHT), audit=auditor), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK)


class K7ReferenceDecidesAlone(unittest.TestCase):
    def test_control_a_right_reference_delivers(self):
        result = solve(10 ** 6)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 820))

    def test_a_reference_wrong_at_the_requested_input_outvotes_method_and_auditor(self):
        """Kept as the rule, after the seventh review (the finding is reproduced; the assertion states the rule).

        On a requested input the planner declared small, the reference has a veto: its disagreement voids the
        answer, even against the method and a blind re-derivation. That loses this right answer (a reference
        bounded at 39). The other side is tests/test_review8_h7_*: a method and an auditor that share one
        mistake on the requested input, the reference right; counted two against one, the wrong answer would
        be delivered as checked. A void costs one answer; a wrong delivery costs the meaning of "checked".
        Only the requested input carries the veto: elsewhere a reference the auditor contradicts is ignored."""
        result = solve(39)
        disputed = events(result, 'solve_answer_disputed')
        print('\nstatus', result['status'], '|', result.get('reason'),
              '\n  differential:', [(e.get('outcome'), e.get('method_values')) for e in events(result, 'solve_differential')],
              '\n  gate record:', [(d.get('answer'), d.get('reference'), d.get('voices'), d.get('by')) for d in disputed])
        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual([d.get('by') for d in disputed], ['reference'])
        self.assertEqual(disputed[-1].get('reference'), 780)

if __name__ == '__main__':
    unittest.main()
