"""K3 (completion lost; a regression against 11e0ea9). _answer_gate (solve.py:1672-1686 at 3af0808) uses the fresh
reference only as a veto: when it agrees with the answer it is not counted, and the answer then has only the
method's one voice against whatever was recorded. differential() now records the auditor's value on the
requested input every time it is asked (solve.py:1869-1870), whichever method it was asked against, and keeps
it for the whole job. So one blind re-derivation that erred on the requested input while an earlier, wrong
method was being refuted voids the next, right method, although the reference agrees with it there.

Scenario: T(n), requested input 40, declared small. W is wrong at 40 (800) and at 41 (862). Differential: the
auditor errs at 40 (800, W's mistake) and is right at 41, so 41 -> 861 is added and W is refuted. RIGHT passes
every check and agrees with the reference on every probe, 40 included; the gate's fresh reference says 820
too. The record on 40 is RIGHT 820 + reference 820 against one auditor 800 from W's round: void, "another
value has as many voices". At 11e0ea9 (no record from differential) RIGHT's 820 is delivered.

Control: the same script with an auditor right at 40 delivers 820. Scripted models; local executor."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

TASK = 'Triangular number: run(n) returns 0 + 1 + ... + n.'
REFERENCE = 'def reference(n):\n    return sum(range(n + 1))\n'
W = ('def run(n):\n    if n > 0 and n % 20 == 0:\n        return (n // 2) * n\n'
     '    if n > 0 and n % 41 == 0:\n        return n * (n + 1) // 2 + 1\n    return n * (n + 1) // 2\n')
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'


def solve(errs_at_40):
    def auditor(context):
        return {'expected': [n * n // 2 if (errs_at_40 and n == 40) else n * (n + 1) // 2 for n in context['inputs']]}
    plan = lambda c: {'contract': 'run(n) returns n*(n+1)/2 for n >= 0', 'input': 40,
                      'checks': [check(0, 0), check(1, 1), check(2, 3), check(5, 15)],
                      'reference': REFERENCE, 'small_inputs': [3, 4, 40, 41]}
    act = lambda request: store(RIGHT if '"input": 41' in request else W)
    m = Metered(Model(plan, act, audit=auditor), {'used': 0, 'limit': 20})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m,
                    sketcher=m, max_executions=256, say=lambda _: None)
    return solver.solve(TASK), solver


class K3AgreeingReferenceOutvotedByOneStaleAuditor(unittest.TestCase):
    def test_control_an_auditor_right_at_40_delivers(self):
        result, _ = solve(errs_at_40=False)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 820))

    def test_one_stale_auditor_voice_voids_the_right_answer_the_reference_confirms(self):
        result, solver = solve(errs_at_40=True)
        disputed = events(result, 'solve_answer_disputed')
        print('\nstatus', result['status'], '|', result.get('reason'),
              '\n  checks at the end:', result['checks'],
              '\n  refuted:', [e['reason'][:40] for e in events(result, 'solve_refuted')],
              '\n  gate record:', [(d.get('answer'), d.get('reference'), d.get('voices'), d.get('by')) for d in disputed])
        self.assertTrue(events(result, 'solve_refuted'), 'precondition: W was refuted on 41')
        self.assertEqual((result['status'], result.get('answer')), ('checked', 820),
                         'the reference agrees with RIGHT at 40, yet one auditor voice from W\'s round voids it')


if __name__ == '__main__':
    unittest.main()
