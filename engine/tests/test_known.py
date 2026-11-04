"""What is known speaks (the owner, 2026-11-02: "something must tell it I am wrong, and it's knowledge; it isn't about
confidence, it's about knowledge, that's what compounds").

Settlement weighed three voices on a hand-worked check: the reference, the property and a blind re-derivation by
the same model. What Kuttaka already knew from earlier tasks never spoke: a stored method that disagreed with a new
check was only not reused. In W4-m h-r4-collide a wrong hand value and the same model's blind re-derivation outvoted
a right reference. Now a method checked by an earlier task, recognized as this task's function (it gives every
value this task already has support for), is knowledge: where it and the reference agree against a hand value,
the hand value is revised, and the same model is not asked to outvote them.

Scenario: the sum of the squares of a list is solved and stored. A later task, worded otherwise, plans the same
function with one wrong hand check ([2, 2] -> 7; it is 8); its reference is right; its blind auditor repeats the
hand's mistake (the shared misreading). Controls: an empty library, and a stored method of the same form that
computes something else (it gives none of the supported values, so it is not recognized). Scripted models."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

SQUARES = 'def run(x):\n    return sum(v * v for v in x["values"])\n'
REFERENCE = 'def reference(x):\n    total = 0\n    for v in x["values"]:\n        total += v * v\n    return total\n'
TASK = 'Each number in the list times itself, all added together.'
WRONG_HAND = [check({'values': [1, 2]}, 5), check({'values': [3]}, 9), check({'values': [2, 2]}, 7)]


def solve_with(ws, task, checks, act, reference=None, audited=None):
    plan = lambda context: {'contract': 'run({"values": list}) returns the sum of the squares of the values',
                            'input': None, 'checks': checks, 'reference': reference}

    def audit(context):
        if audited is not None:
            audited.append(context['inputs'])
        return {'expected': [7 if i == {'values': [2, 2]} else None for i in context['inputs']]}
    model = Model(plan, act, audit=audit)
    m = Metered(model, {'used': 0, 'limit': 10})
    solver = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                    say=lambda _: None)
    return solver.solve(task), model


def library(task, checks, source):
    ws = Workspace(Path(tempfile.mkdtemp()))
    result, _ = solve_with(ws, task, checks, lambda request: store(source))
    assert result['status'] == 'checked', result.get('reason')
    return ws


def expected_at(result, value):
    return next(c['expected'] for c in result['checks'] if c['input'] == value)


class WhatIsKnownSpeaks(unittest.TestCase):
    def test_a_known_method_and_the_reference_overrule_a_shared_misreading(self):
        ws = library('Add up the squares of the values.', [check({'values': [1, 2]}, 5), check({'values': [3]}, 9),
                                                           check({'values': []}, 0)], SQUARES)
        audited = []
        result, model = solve_with(ws, TASK, WRONG_HAND, lambda request: store(SQUARES), REFERENCE, audited)
        self.assertEqual(result['status'], 'checked', result.get('reason'))
        self.assertEqual(expected_at(result, {'values': [2, 2]}), 8)
        self.assertEqual(audited, [], 'the same model was asked to outvote the reference and what is known')
        recognized = [e for e in events(result, 'solve_established') if e['recognized']]
        self.assertEqual(recognized[0]['speaks_on'], [{'values': [2, 2]}])

    def test_control_with_nothing_known_the_auditor_decides_as_before(self):
        audited = []
        result, _ = solve_with(Workspace(Path(tempfile.mkdtemp())), TASK, WRONG_HAND, lambda r: store(SQUARES),
                               REFERENCE, audited)
        self.assertEqual(audited[:1], [[{'values': [2, 2]}]])   # settlement asks the same model, as before
        self.assertEqual(events(result, 'solve_established'), [])

    def test_control_a_stored_method_of_another_function_is_not_knowledge(self):
        ws = library('Add up the values.', [check({'values': [1, 2]}, 3), check({'values': [4]}, 4)],
                     'def run(x):\n    return sum(x["values"])\n')
        audited = []
        result, _ = solve_with(ws, TASK, WRONG_HAND, lambda r: store(SQUARES), REFERENCE, audited)
        self.assertEqual(audited[:1], [[{'values': [2, 2]}]], 'a method that gives none of the supported values spoke')
        self.assertEqual(events(result, 'solve_established'), [])


if __name__ == '__main__':
    unittest.main()
