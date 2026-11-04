"""False positives of the memorization readings on 96 correct, ordinary methods: tests/substitution_corpus.py (the
tenth review's 43: tables of named constants, unit factors, calendars, parsing, geometry, combinatorics, thresholds)
and tests/substitution_corpus2.py (the eleventh review's 31, each with a requested input: float formulas, text
normalisation, simulations, memo tables, dict-of-dict inputs) and tests/substitution_corpus3.py (the twelfth
review's 22, with the contract's domain). Each is solved end to end with scripted models (the
method is the corpus's, the checks are its correct hand checks, and a blind auditor right everywhere: it runs the
corpus's own method, correct by construction, in the local executor) and must be delivered, with the right answer
when one was asked: the text reading must flag none, and a question about the method around a literal must find
the method agreeing with the auditor, never set aside or refuted.

The rate of questions asked (one auditor call each) is printed, not asserted. Local executor; every method is
test-written (by the reviewer)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import judge
from kuttaka_runtime.solve import Workspace, canonical
from kuttaka_runtime.solve import DIFFERENTIAL_RUN
from tests.substitution_corpus import CORPUS
from tests.substitution_corpus2 import CORPUS2
from tests.substitution_corpus3 import CORPUS3
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, events, make_solver, store

EVERY = ([(n, s, c, k, None) for n, s, c, k in CORPUS] + [(n, s, c, k, (ask, a)) for n, s, c, k, ask, a in CORPUS2]
         + [(n, s, c, k, (ask, a)) for n, s, c, k, ask, a, _ in CORPUS3])
VALID = {n: valid for n, _, _, _, _, _, valid in CORPUS3 if valid}


def sig6(value):   # a calculator display: 6 significant digits
    return float('%.6g' % value) if isinstance(value, float) and value != int(value) else value


def reformat(values, how):
    if how == 'sig6':
        return [sig6(v) for v in values]
    if how == 'ints':   # integral floats as ints, ints as floats
        return [int(v) if isinstance(v, float) and v == int(v) else float(v) if type(v) is int else v for v in values]
    return values


def truth(source, inputs):
    """What the corpus's correct method answers, run by the local executor (a blind auditor right everywhere)."""
    response = local_executor({'source': DIFFERENTIAL_RUN % (source, 'run'), 'input': inputs})
    out = json.loads(response['output'])['result'] if response['exit_code'] == 0 else []
    return [o.get('value') if isinstance(o, dict) else None for o in out] or [None] * len(inputs)


def solve(name, source, checks, known, asked=None, how='right'):
    def plan(context):
        return dict({'contract': 'run(x): ' + name, 'input': asked[0] if asked else None, 'checks': checks},
                    **({'small_inputs': known} if known else {}))

    def audit(context):
        values = reformat(truth(source, context['inputs']), how)
        if how == 'domain' and name in VALID:   # right on the contract's domain, "invalid" outside it
            values = [v if VALID[name](x) else 'invalid' for x, v in zip(context['inputs'], values)]
        return {'expected': values}
    ws = Workspace(Path(tempfile.mkdtemp()))
    solver, _ = make_solver(Model(plan, lambda r: store(source), audit=audit), ws=ws, max_calls=12)
    return solver.solve(name), ws


class CorpusOfCorrectMethods(unittest.TestCase):
    def test_the_text_reading_flags_none(self):
        flagged = [name for name, source, checks, known, _ in EVERY if judge(source, checks, known, ())[0]]
        self.assertEqual(flagged, [])

    def test_every_correct_method_is_delivered_and_none_refuted(self):
        self.delivered_with('right')

    def test_with_an_auditor_that_rounds_reformats_or_refuses_outside_the_domain(self):
        # Right auditors that write floats to 6 significant digits, swap ints and floats, or answer "invalid" for
        # an input outside the contract (the twelfth review's R5, R6): none sets a correct method aside.
        for how in ('sig6', 'ints', 'domain'):
            with self.subTest(auditor=how):
                self.delivered_with(how)

    def delivered_with(self, how):
        questioned, failed = [], []
        for name, source, checks, known, asked_for in EVERY:
            result, ws = solve(name, source, checks, known, asked_for, how)
            asked = events(result, 'solve_literal_questioned')
            if asked:
                questioned.append(name)
            wrong = asked_for is not None and canonical(result.get('answer')) != canonical(asked_for[1])
            if result['status'] != 'checked' or ws.refuted() or wrong:
                failed.append((name, result['status'], result.get('reason'), [e.get('outcome') for e in asked]))
        print('\n[%s auditor] questions asked on %d of %d correct methods: %s' % (how, len(questioned), len(EVERY),
              questioned), file=sys.stderr)
        self.assertEqual(failed, [])


if __name__ == '__main__':
    unittest.main()
