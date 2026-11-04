"""Q5 (wrong answer delivered as checked by a later task, reusing by id a piece the auditor disputed; regression vs
556adfd). A piece written in this job that copies the parent's wrong hand check, in a form only the substitution test
sees, is found at the parent (source key = the piece), questioned, and DISPUTED by a right auditor; the parent is set
aside, but the piece is not refuted (`_memorized`, kuttaka_runtime/solve.py:1855-1877 at d5dc8ce: a dispute only adds
the check to `found`; culprits come from the text reading alone). The piece was already delivered and remembered as
solved by its own nested solve, so it stays in memory and a later task reuses it by id. At 556adfd the substitution
test's hit made the piece a culprit (written in this job, holding a literal of a check no derivation supports) and it
was refuted for every task, as the text reading does for the same piece (the second review's 7 -> 22; rule kept on
purpose: "a piece refuted for its parent's hand checks").

Scenario: the second review's memorizing piece (tests/test_solve_review_regressions.py,
test_a_memorizing_piece_is_refuted_and_never_reused) with the special case written as a named literal.
Control: the piece as written there (`if x == 7: return 22`, read by the text): refuted, never reused.
Scripted models; local executor; every method is written here."""
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

PARENT, PIECE, LATER = 'Triple a number.', 'Triple a number exactly.', 'Triple a number exactly, for any integer.'
READ = 'def run(x):\n    if x == 7:\n        return 22\n    return 3 * x\n'
NAMED = 'SPECIAL = 22\n\n\ndef run(x):\n    if x == 7:\n        return SPECIAL\n    return 3 * x\n'


def plan(context):
    if context['task'] == PARENT:
        return {'contract': 'run(x) returns 3*x', 'input': None, 'checks': [check(5, 15), check(7, 22)],   # 7 -> 22 wrong
                'decomposition': {'subtasks': [{'id': 'triple', 'request': PIECE, 'checks': [check(2, 6), check(3, 9)]}],
                                  'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'triple',
                                                                       'arg': {'op': 'input'}}}}}
    if context['task'] == PIECE:
        return {'contract': 'run(x) returns 3*x', 'input': None, 'checks': [check(2, 6), check(3, 9)]}
    return {'contract': 'run(x) returns 3*x', 'input': 7, 'checks': [check(4, 12), check(10, 30)]}


def right(context):
    return {'expected': [3 * x for x in context['inputs']]}


def jobs(piece_source):
    def act(request):
        if request.startswith(PIECE):
            return store(piece_source)
        return {'kind': 'unresolved', 'reason': 'not attempted directly'}
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act, audit=right), ws=ws, max_calls=12)[0].solve(PARENT)
    pieces = [e['artifact'] for e in events(first, 'solve_decomposition_piece') if e['status'] == 'checked']
    later = make_solver(Model(plan, act, audit=right), ws=ws, max_calls=12)[0].solve(LATER)
    print('\nfirst %s | questioned %s | piece refuted %s | later %s, delivered_by %s, answer for 7: %s (right 21)' % (
        first['status'], [e.get('outcome') for e in events(first, 'solve_literal_questioned')],
        bool(pieces) and set(pieces) <= ws.refuted(), later['status'], later.get('delivered_by'),
        later.get('answer')), file=sys.stderr)
    return first, pieces, ws, later


class Q5DisputedPieceKept(unittest.TestCase):
    def test_precondition(self):
        self.assertEqual(memorized_checks(NAMED, [check(5, 15), check(7, 22)]), [])

    def test_control_piece_read_by_the_text(self):
        first, pieces, ws, later = jobs(READ)
        self.assertTrue(pieces and set(pieces) <= ws.refuted())
        self.assertNotEqual(later.get('answer'), 22)

    def test_piece_disputed_at_the_parent(self):
        first, pieces, ws, later = jobs(NAMED)
        self.assertNotEqual(first['status'], 'checked')
        self.assertNotEqual(later.get('answer'), 22, 'the disputed piece is kept and a later task answers 7 with 22')


if __name__ == '__main__':
    unittest.main()
