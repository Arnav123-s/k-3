"""Recall by role: a method solved once is recognized when the same problem comes back in other words, with other
field names (the owner, 2026-11-02: "recall kuttaka needs a better way"). Āryabhaṭa's rules are stated over named
roles (the rule of three: measure, fruit, requisition), and a problem told about anything is solved by the rule once
its quantities are put in the roles. Before this, recall was by words (the tasks' wording) or by the exact field
names of the input, so the restated task below was written again by the model.

Scenario: the fewest moves with two jugs, run({"a", "b", "d"}), is solved and stored. Then: two containers,
run({"small", "large", "goal"}), worded otherwise. Expected: delivered by role, no actor call.
Controls: an empty library (the actor writes it); a stored method of the same form that computes something else
(no assignment holds; the actor writes it); the field order matters (only the assignment that is right is taken).
Scripted models; local executor."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime import solve_prastara
from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

JUGS = '''def run(x):
    a, b, d = x['a'], x['b'], x['d']
    seen, frontier, moves = {(0, 0)}, [(0, 0)], 0
    while frontier:
        if any(d in state for state in frontier):
            return moves
        nxt = []
        for p, q in frontier:
            u, v = min(p, b - q), min(q, a - p)
            for s in ((a, q), (p, b), (0, q), (p, 0), (p - u, q + u), (p + v, q - v)):
                if s not in seen:
                    seen.add(s)
                    nxt.append(s)
        frontier, moves = nxt, moves + 1
    return -1
'''
c = lambda a, b, d: {'a': a, 'b': b, 'd': d}
JUG_CHECKS = [check(c(3, 5, 0), 0), check(c(3, 5, 5), 1), check(c(3, 5, 2), 2), check(c(2, 4, 3), -1),
              check(c(3, 5, 4), 6)]
RESTATED = ('A cook has two containers, of `small` and `large` cups, both empty. In one move she fills one, empties one, '
            'or tips one into the other until it is empty or the other is full. How few moves give `goal` cups in '
            'one container? Answer -1 when no number of moves does.')
r = lambda small, large, goal: {'small': small, 'large': large, 'goal': goal}
RESTATED_CHECKS = [check(r(3, 5, 4), 6), check(r(3, 5, 0), 0), check(r(3, 5, 2), 2), check(r(2, 4, 3), -1),
                   check(r(4, 9, 6), 8)]
RESTATED_RIGHT = JUGS.replace("x['a'], x['b'], x['d']", "x['small'], x['large'], x['goal']")


def solve_with(ws, task, checks, act, max_calls=10):
    plan = lambda context: {'contract': task, 'input': None, 'checks': checks}
    model = Model(plan, act)
    m = Metered(model, {'used': 0, 'limit': max_calls})
    solver = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                    say=lambda _: None)
    return solver.solve(task), model


def actor_calls(model):
    return sum(1 for role, _ in model.log if role == 'actor')


def library(*methods):
    ws = Workspace(Path(tempfile.mkdtemp()))
    for task, checks, source in methods:
        result, _ = solve_with(ws, task, checks, lambda request: store(source))
        assert result['status'] == 'checked', result.get('reason')
    return ws


JUGS_TASK = ('Two jugs hold a and b liters and start empty. A move fills a jug, empties a jug, or pours one jug into '
             'the other until the first is empty or the second is full. Return the fewest moves after which one jug '
             'holds exactly d liters, or -1 if that is impossible.')


class RecallByRole(unittest.TestCase):
    def test_the_same_problem_in_other_words_and_names_is_recalled_with_no_model_call(self):
        ws = library((JUGS_TASK, JUG_CHECKS, JUGS))
        self.assertEqual(ws.helpful_methods(RESTATED), [], 'word recall already found it: the test shows nothing')
        result, model = solve_with(ws, RESTATED, RESTATED_CHECKS, lambda request: store(RESTATED_RIGHT))
        self.assertEqual((result['status'], result['delivered_by']), ('checked', 'roles'), result.get('reason'))
        self.assertEqual(actor_calls(model), 0)
        found = [e['found'] for e in events(result, 'solve_roles') if e.get('found')]
        assignment = found[0]['assignment']   # the two jugs are symmetric: either order is right
        self.assertEqual((assignment['d'], {assignment['a'], assignment['b']}), ('goal', {'small', 'large'}))

    def test_control_an_empty_library_the_model_writes_it(self):
        result, model = solve_with(Workspace(Path(tempfile.mkdtemp())), RESTATED, RESTATED_CHECKS,
                                   lambda request: store(RESTATED_RIGHT))
        self.assertEqual(result['status'], 'checked')
        self.assertNotEqual(result['delivered_by'], 'roles')
        self.assertGreaterEqual(actor_calls(model), 1)

    def test_control_a_method_of_the_same_form_that_does_another_thing_is_not_taken(self):
        total = 'def run(x):\n    return x["a"] + x["b"] + x["d"]\n'
        ws = library(('Add the three numbers a, b and d.', [check(c(1, 2, 3), 6), check(c(0, 0, 5), 5)], total))
        result, model = solve_with(ws, RESTATED, RESTATED_CHECKS, lambda request: store(RESTATED_RIGHT))
        self.assertEqual(result['status'], 'checked')
        self.assertNotEqual(result['delivered_by'], 'roles')
        self.assertGreaterEqual(actor_calls(model), 1)

    def test_a_method_of_the_same_form_that_does_another_thing_is_screened_and_left(self):
        total = 'def run(x):\n    return x["a"] + x["b"] + x["d"]\n'
        ws = library(('Add the three numbers a, b and d.', [check(c(1, 2, 3), 6), check(c(0, 0, 5), 5)], total))
        result, _ = solve_with(ws, RESTATED, RESTATED_CHECKS, lambda request: store(RESTATED_RIGHT))
        screened = events(result, 'solve_roles')
        self.assertEqual([(e['listed'], e['found']) for e in screened], [(6, None)])

    def test_the_order_of_the_roles_is_found_by_the_checks(self):
        minus = 'def run(x):\n    return x["a"] - x["b"]\n'
        ws = library(('Subtract b from a.', [check({'a': 5, 'b': 2}, 3), check({'a': 1, 'b': 4}, -3)], minus))
        task = 'How much older is the second person than the first? Ages are given as first and second.'
        checks = [check({'first': 30, 'second': 42}, 12), check({'first': 7, 'second': 3}, -4)]
        result, model = solve_with(ws, task, checks, lambda request: store('def run(x):\n    return 0\n'))
        self.assertEqual((result['status'], result['delivered_by'], actor_calls(model)), ('checked', 'roles', 0))
        found = [e['found'] for e in events(result, 'solve_roles') if e.get('found')]
        self.assertEqual(found[0]['assignment'], {'a': 'second', 'b': 'first'})


class Listing(unittest.TestCase):
    def test_one_field_fills_one_role_and_the_method_as_it_is_is_left_out(self):
        jugs = ('J', 'jugs', {'a': 'num', 'b': 'num', 'd': 'num'}, 'num')
        complete, listed = solve_prastara.roles([jugs], {'p': 'num', 'q': 'num', 't': 'num'}, 'num')
        self.assertEqual((complete, len(listed)), (True, 6))   # 3! assignments, none using a field twice
        complete, listed = solve_prastara.roles([jugs], {'a': 'num', 'b': 'num', 'd': 'num'}, 'num')
        self.assertEqual(len(listed), 5)   # the identity is reuse as it is
        self.assertNotIn({'a': 'a', 'b': 'b', 'd': 'd'}, [a for _, a, _, _ in listed])

    def test_forms_must_fit_and_the_output_form_must_match(self):
        method = ('M', 'm', {'items': ['list', ['num']], 'k': 'num'}, 'num')
        _, listed = solve_prastara.roles([method], {'xs': ['list', ['num']], 'n': 'num', 'name': 'str'}, 'num')
        self.assertEqual([a for _, a, _, _ in listed], [{'items': 'xs', 'k': 'n'}])
        self.assertEqual(solve_prastara.roles([method], {'xs': ['list', ['num']], 'n': 'num'}, 'str')[1], [])

    def test_a_whole_input_fills_a_method_of_one_field_and_the_list_is_cut_when_it_must_be(self):
        _, listed = solve_prastara.roles([('S', 'sq', {'n': 'num'}, 'num')], 'num', 'num')
        self.assertEqual(listed[0][1], {'n': None})
        wide = ('W', 'w', {k: 'num' for k in 'abcde'}, 'num')
        complete, listed = solve_prastara.roles([wide], {k: 'num' for k in 'vwxyz'}, 'num')
        self.assertEqual((complete, len(listed)), (False, solve_prastara.ROLE_PER_METHOD))

    def test_the_screen_runs_the_stored_method_on_the_assigned_input(self):
        space = {'use': lambda artifact: (lambda x: [artifact, x])}
        exec(solve_prastara.ROLE_SCREEN % ([['M', {'a': 'q', 'b': None}]],), space)
        self.assertEqual(space['screen']([0, {'q': 2}]), ['M', {'a': 2, 'b': {'q': 2}}])
        json.dumps(solve_prastara.roles([('S', 'sq', {'n': 'num'}, 'num')], 'num', 'num')[1][0][3])


if __name__ == '__main__':
    unittest.main()
