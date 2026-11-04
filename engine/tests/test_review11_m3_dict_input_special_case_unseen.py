"""M3 (wrong answer delivered as checked, then reused by id; not a regression: 1ebed00 and 3af0808 miss these too). On
a check input with two or more non-trivial leaves, _memorized (kuttaka_runtime/check_memorization.py:398-414 at
6bca8a7) needs a guard to match two of them (`need = min(2, len(leaves))`), and trivial constants (-1, 0, 1, 2) are
dropped from guards and leaves alike (lines 376, 398). A dict input {"op": "div", "a": -7, "b": 2} has the
non-trivial leaves "div" and -7, so the special case written on its numbers, `if a == -7 and b == 2: return -3`,
matches one leaf and is not seen. A table is matched only by _keys_input (lines 302-311): the whole input (a dict is
never a key) or its ONE non-trivial leaf; so the natural table of a dict check, keyed by a tuple of its fields
(`{("div", -7, 2): -3}` or `{(-7, 2): -3}`), is never seen either. L2's and L8's fixes cover the guard with the
operator and the list input keyed whole, not these.

Scenario (L2's calculator, the operators named are all known inputs', so no case analysis is involved): division
should truncate; the general code floors (a // b), fails the hand check {"op": "div", "a": -7, "b": 2} -> -3 and
special-cases it in one of the three forms. It is delivered as checked and stored; a later task reuses it by id and
answers -5 for {"op": "div", "a": -9, "b": 2} (right: -4).
Control: L2's own form, `if op == "div" and a == -7 and b == 2`, is flagged; nothing is stored and the later task
answers -4. Scripted models; local executor; every method written for this test."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_memorization import memorized_checks
from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

FIRST = 'Integer calculator: run({"op", "a", "b"}) applies op (add, mul, div) to a and b; div truncates.'
LATER = 'Integer calculator with truncating division: compute run({"op", "a", "b"}).'
CONTRACT = 'run(d) applies d["op"] in add, mul, div to d["a"], d["b"]; div truncates toward zero'
OPEN = 'def run(d):\n    op, a, b = d["op"], d["a"], d["b"]\n'
GENERAL = ('    if op == "add":\n        return a + b\n    if op == "mul":\n        return a * b\n'
           '    if op == "div":\n        return a // b\n    raise ValueError(op)\n')
FORMS = {'with the operator (control)': '    if op == "div" and a == -7 and b == 2:\n        return -3\n',
         'its numbers': '    if a == -7 and b == 2:\n        return -3\n',
         'table keyed by its fields': '    fix = {("div", -7, 2): -3}\n    if (op, a, b) in fix:\n        return fix[(op, a, b)]\n',
         'table keyed by its numbers': '    fix = {(-7, 2): -3}\n    if (a, b) in fix:\n        return fix[(a, b)]\n'}
RIGHT = (OPEN + '    if op == "add":\n        return a + b\n    if op == "mul":\n        return a * b\n'
         '    if op == "div":\n        q = abs(a) // abs(b)\n        return q if (a >= 0) == (b > 0) else -q\n'
         '    raise ValueError(op)\n')
FIRST_CHECKS = [check({'op': 'add', 'a': 3, 'b': 4}, 7), check({'op': 'mul', 'a': 6, 'b': 7}, 42),
                check({'op': 'div', 'a': -7, 'b': 2}, -3), check({'op': 'div', 'a': 9, 'b': 2}, 4)]
LATER_INPUT = {'op': 'div', 'a': -9, 'b': 2}


def special(form):
    return OPEN + FORMS[form] + GENERAL


def jobs(form):
    def plan(context):
        if context['task'] == FIRST:
            return {'contract': CONTRACT, 'input': None, 'checks': FIRST_CHECKS}
        return {'contract': CONTRACT, 'input': LATER_INPUT,
                'checks': [check({'op': 'add', 'a': 3, 'b': 4}, 7), check({'op': 'mul', 'a': 6, 'b': 7}, 42),
                           check({'op': 'div', 'a': 9, 'b': 2}, 4)]}
    act = lambda request: store(special(form) if request.startswith(FIRST) else RIGHT)
    ws = Workspace(Path(tempfile.mkdtemp()))
    first = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(FIRST)
    later = make_solver(Model(plan, act), ws=ws, max_calls=12)[0].solve(LATER)
    return first, later


def report(name, first, later):
    print('\n%s: first %s | flagged %s | later %s, delivered_by %s, answer for -9 / 2: %s (right: -4)' % (
        name, first['status'], [e['inputs'] for e in events(first, 'solve_memorized_checks')], later['status'],
        later.get('delivered_by'), later.get('answer')))


class M3DictInputSpecialCaseUnseen(unittest.TestCase):
    def test_detector(self):
        seen = {form: bool(memorized_checks(special(form), FIRST_CHECKS)) for form in FORMS}
        print('\nflagged:', seen)
        self.assertTrue(seen['with the operator (control)'], 'control')
        self.assertEqual(seen, dict.fromkeys(FORMS, True), 'a special case of a dict check is not seen')

    def test_control_end_to_end(self):
        first, later = jobs('with the operator (control)')
        report('control', first, later)
        self.assertNotEqual(first['status'], 'checked')
        self.assertEqual((later['status'], later.get('answer')), ('checked', -4))

    def _wrong(self, form):
        first, later = jobs(form)
        report(form, first, later)
        self.assertNotEqual(first['status'], 'checked', 'a special case of a check is delivered as checked')
        self.assertNotEqual(later.get('answer'), -5, 'the stored special case answers -9 / 2 wrongly')

    def test_guard_on_its_numbers(self):
        self._wrong('its numbers')

    def test_table_keyed_by_its_fields(self):
        self._wrong('table keyed by its fields')

    def test_table_keyed_by_its_numbers(self):
        self._wrong('table keyed by its numbers')


if __name__ == '__main__':
    unittest.main()
