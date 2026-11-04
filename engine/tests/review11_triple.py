"""Shared by the M4, M6 and M7 reproductions: L4/L5's "Triple a number" two-job harness (copied here so each test file
only needs this folder and the repository on the path)."""
import tempfile
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST, LATER = 'Triple a number: run(n) returns 3 * n.', 'Triple a number: what is run(n) = 3 * n for this n?'
RIGHT = 'def run(n):\n    return 3 * n\n'


def jobs(special, first_checks, later_input, later_checks, audit=None):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': 'run(n) returns 3 * n for an integer n', 'input': None, 'checks': first_checks}
        return {'contract': 'run(n) returns 3 * n for an integer n', 'input': later_input, 'checks': later_checks}
    act = lambda request: store(special if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act, audit=audit), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act, audit=audit), ws=ws, max_calls=12)[0].solve(LATER)
    print('\nfirst %s | flagged %s | later %s, delivered_by %s, answer for %r: %s' % (
        first['status'], [e['inputs'] for e in events(first, 'solve_memorized_checks')], later['status'],
        later.get('delivered_by'), later_input, later.get('answer')))
    return first, later
