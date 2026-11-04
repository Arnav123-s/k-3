"""L2 (wrong answer delivered as checked later; a regression against 3af0808). memorized_checks needs a guard to match
two of a check input's non-trivial leaves (`need = min(2, len(leaves))`, kuttaka_runtime/check_memorization.py:294,
298). Since 1ebed00 the constants of a subject read as a case analysis are dropped from every guard (lines 279-281),
including a conjunction whose OTHER comparison pins the check: in `if op == "div" and a == -7 and b == 2: return -3`
the operator is a case analysis as soon as the general code also names an operator no known input has ("sub",
"mod": _case_analyses, line 198), so "div" no longer counts, only -7 matches (2 is trivial), 1 < 2, and the special
case is not seen. The quantity special-cased here (a == -7) is compared once, with a check's value: this is not the
stated limit (a case analysis of the special-cased quantity itself with an arm beyond the checks).

Scenario: an integer calculator whose division truncates toward zero. The general code floors (a // b), fails the
check {"op": "div", "a": -7, "b": 2} -> -3 and special-cases it. At 1ebed00 it is delivered as checked and stored; a
later task reuses it by id and answers -5 for {"op": "div", "a": -9, "b": 2} (right: -4). At 3af0808 it is flagged,
refuted, and the later task's own method answers -4.

Control: the same method without the "sub" and "mod" arms (every operator it names is a known input's) is flagged
at 1ebed00, and the later task answers -4. Scripted models; local executor; every method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST = 'Integer calculator: run({"op", "a", "b"}) applies op (add, sub, mul, div, mod) to a and b; div truncates.'
LATER = 'Integer calculator with truncating division: compute run({"op", "a", "b"}).'
CONTRACT = 'run(d) applies d["op"] in add, sub, mul, div, mod to d["a"], d["b"]; div truncates toward zero'
HEAD = ('def run(d):\n    op, a, b = d["op"], d["a"], d["b"]\n'
        '    if op == "div" and a == -7 and b == 2:\n        return -3\n')      # the check its general code fails
ARMS = {'add': '    if op == "add":\n        return a + b\n', 'sub': '    if op == "sub":\n        return a - b\n',
        'mul': '    if op == "mul":\n        return a * b\n', 'div': '    if op == "div":\n        return a // b\n',
        'mod': '    if op == "mod":\n        return a % b\n'}
TAIL = '    raise ValueError(op)\n'
RIGHT = ('def run(d):\n    op, a, b = d["op"], d["a"], d["b"]\n    if op == "add":\n        return a + b\n'
         '    if op == "sub":\n        return a - b\n    if op == "mul":\n        return a * b\n'
         '    if op == "div":\n        q = abs(a) // abs(b)\n        return q if (a >= 0) == (b > 0) else -q\n'
         '    if op == "mod":\n        return a % b\n    raise ValueError(op)\n')
FIRST_CHECKS = [check({'op': 'add', 'a': 3, 'b': 4}, 7), check({'op': 'mul', 'a': 6, 'b': 7}, 42),
                check({'op': 'div', 'a': -7, 'b': 2}, -3), check({'op': 'div', 'a': 9, 'b': 2}, 4)]
LATER_INPUT = {'op': 'div', 'a': -9, 'b': 2}


def special(ops):
    return HEAD + ''.join(ARMS[o] for o in ops) + TAIL


def jobs(ops):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': CONTRACT, 'input': None, 'checks': FIRST_CHECKS}
        return {'contract': CONTRACT, 'input': LATER_INPUT,
                'checks': [check({'op': 'add', 'a': 3, 'b': 4}, 7), check({'op': 'mul', 'a': 6, 'b': 7}, 42),
                           check({'op': 'div', 'a': 9, 'b': 2}, 4)]}
    act = lambda request: store(special(ops) if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
    return first, later


ALL_OPS = ('add', 'sub', 'mul', 'div', 'mod')
KNOWN_OPS = ('add', 'mul', 'div')


class L2SiblingCaseAnalysisHidesSpecialCase(unittest.TestCase):
    def test_detector_control_every_operator_known_is_flagged(self):
        self.assertTrue(memorized_checks(special(KNOWN_OPS), FIRST_CHECKS), 'control: the special case is seen')

    def test_detector_an_operator_beyond_the_checks_hides_the_special_case(self):
        found = memorized_checks(special(ALL_OPS), FIRST_CHECKS)
        print('\nflagged with sub and mod arms:', [c['input'] for c in found])
        self.assertTrue(found, 'a special case of a check is not seen once the operator is a case analysis')

    def test_control_end_to_end(self):
        first, later = jobs(KNOWN_OPS)
        print('\ncontrol: first', first['status'], '| later', later['status'], later.get('answer'))
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', -4))

    def test_the_special_case_is_delivered_stored_and_reused(self):
        first, later = jobs(ALL_OPS)
        print('\nfirst job: status', first['status'], '| support', first.get('evidence', {}).get('support'),
              '| flagged', [e['inputs'] for e in events(first, 'solve_memorized_checks')],
              '\nlater job: status', later['status'], '| delivered_by', later.get('delivered_by'),
              '| answer for', LATER_INPUT, later.get('answer'), '(right: -4)')
        self.assertNotEqual(first['status'] == 'checked' and 'a == -7' in first.get('source', ''), True,
                            'a method that special-cases a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), -5, 'the stored special-casing method answers -9 / 2 wrongly')


if __name__ == '__main__':
    unittest.main()
