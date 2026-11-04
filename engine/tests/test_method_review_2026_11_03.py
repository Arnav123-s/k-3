"""The second review of 2026-11-03: Kuttaka as a whole, method by method, beside Āryabhaṭa's
(docs/reviews/aryabhata_method_review_2026-11-03.md). Its findings on labels and limits:

1. A value labelled for what it is (āsanna, Gaṇitapāda 10): an unverified offer that failed checks was labelled "the
   best attempt nothing has shown wrong", and the report printed "nothing showed it wrong". It now says how many
   checks it passes, or that it passed them all and was held back, and why.
2. Limits stated, with the method (Golapāda 47-48): the printed report of a checked method said nothing of a scale
   test it failed; it now states the limit.
3. The instrument's limits, stated before the rite (prāyaścitta known in advance): the actor learned the sandbox's
   time, memory and recursion limits only after a method had run out; the request now states them.
4. The two agras (Gaṇitapāda 32-33, Clark's note: an agra is a provisional value that satisfies one condition; the
   kuṭṭaka joins two into the value that satisfies both). The frontier kept attempts each right where the other is
   wrong, but repair showed only the best one. It now shows the other with it.

Every method here is written for this test and run only by the local executor."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace, _report
from tests import test_indefinite_search
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

CHECKS = test_indefinite_search.NestedByRule.CHECKS   # the deepest nesting of parentheses, five checks
WRONG = 'def run(x):\n    return 0\n'                 # passes one of them (the empty string)


def nesting(writes, limit=6):
    requests = []

    def act(request):
        requests.append(request)
        return store(writes[min(len(requests), len(writes)) - 1])
    plan = lambda c: {'contract': 'run({"s": s}) returns the deepest nesting of the balanced parentheses in s',
                      'input': {'s': '(())'}, 'checks': CHECKS}
    m = Metered(Model(plan, act, audit=lambda c: {}), {'used': 0, 'limit': limit})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                    max_executions=256, say=lambda _: None)
    return solver.solve('How deeply are the parentheses nested in (())?'), requests


def printed(result):
    out = io.StringIO()
    entry = {'reused': [], 'model_calls': 1, 'input_tokens': 0, 'output_tokens': 0, 'unknown_usage_calls': 0,
             'subtasks': 0}
    with contextlib.redirect_stdout(out):
        _report(result, entry)
    return out.getvalue()


class LabelledForWhatItIs(unittest.TestCase):
    def test_an_unverified_offer_says_how_many_checks_it_passes(self):
        result, _ = nesting([WRONG])
        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual(result['unverified']['note'], 'not verified: passes 1 of 5 checks; nothing else has refuted it')
        self.assertIn('passes 1 of 5 checks', printed(result))   # old code: "nothing showed it wrong"
        self.assertNotIn('nothing showed it wrong', printed(result))

    def test_control_an_offer_with_no_note_keeps_the_old_label(self):
        m = Metered(Model(lambda c: {}, lambda r: store(WRONG)), {'used': 0, 'limit': 1})
        solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, say=lambda _: None)
        self.assertIsNone(solver._unverified('Triple a number.', {'input': 9}, [None]))


class LimitsStatedWithTheMethod(unittest.TestCase):
    RESULT = {'status': 'checked', 'artifact': 'a' * 64, 'checks': [], 'checks_by': 'model', 'source': 'def run(x): ...',
              'evidence': {}}

    def test_a_checked_method_that_ran_out_at_scale_is_reported_with_its_limit(self):
        self.assertIn('Limit: it ran out', printed(dict(self.RESULT, scale='slow')))

    def test_control_a_method_that_held_has_no_limit_line(self):
        self.assertNotIn('Limit:', printed(dict(self.RESULT, scale='held')))
        self.assertNotIn('Limit:', printed(dict(self.RESULT, scale='not_asked')))


class TheInstrumentsLimitsBeforehand(unittest.TestCase):
    def test_the_first_request_states_the_sandbox_limits(self):
        _, requests = nesting([test_indefinite_search.NestedByRule.FIXED])
        self.assertIn('Each check and probe runs in a sandbox with about 20 seconds and 256m of memory', requests[0])
        self.assertIn("Python's recursion limit there is its default (1000)", requests[0])



SQUARE_CHECKS = [check(n, n * n) for n in range(1, 7)]
UP_TO_FOUR = 'def run(n):\n    # attempt A\n    return n * n if n * n < 20 else 0\n'   # right on 1-4, wrong on 5 and 6
NOT_FOUR = 'def run(n):\n    # attempt B\n    return n * n if n % 4 else 0\n'           # right on all but 4
SQUARE = 'def run(n):\n    return n * n\n'


def two_attempts():
    requests = []

    def act(request):
        requests.append(request)
        if 'Best attempt so far passes 4 of 6' in request:
            return store(NOT_FOUR)
        if 'Best attempt so far passes 5 of 6' in request:
            return store(SQUARE if '# attempt A' in request else NOT_FOUR)
        return store(UP_TO_FOUR)
    plan = lambda c: {'contract': 'run(n) returns n squared', 'input': None, 'checks': SQUARE_CHECKS}
    m = Metered(Model(plan, act, audit=lambda c: {}), {'used': 0, 'limit': 14})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                    max_executions=256, say=lambda _: None)
    return solver.solve('The square of n.'), requests


class TwoAgras(unittest.TestCase):
    def test_the_repair_of_the_best_attempt_is_shown_the_attempt_right_where_it_is_wrong(self):
        result, requests = two_attempts()
        joined = [r for r in requests if 'Best attempt so far passes 5 of 6' in r]
        self.assertTrue(joined)
        self.assertIn('Another attempt passes 1 of the checks this one fails ([4])', joined[0])
        self.assertIn('# attempt A', joined[0])   # old code: only attempt B's source
        self.assertEqual((result['status'], result['source']), ('checked', SQUARE))
        self.assertTrue(events(result, 'solve_repair_two_agras'))

    def test_control_the_first_repair_has_no_other_attempt_to_show(self):
        _, requests = two_attempts()
        first = next(r for r in requests if 'Best attempt so far passes 4 of 6' in r)
        self.assertNotIn('Another attempt passes', first)

if __name__ == '__main__':
    unittest.main()
