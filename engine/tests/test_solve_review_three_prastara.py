"""Findings of the independent review on prastāra and lessons by form (ca54442..cc8d583), reproduced first."""

import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store

SUM_SQUARES = [check({'values': [1, 2]}, 5), check({'values': [3]}, 9), check({'values': []}, 0)]
RIGHT = 'def run(input):\n    return sum(v * v for v in input["values"])\n'


def solve_with(ws, task, checks, act, max_calls=10, contract=None):
    plan = lambda c: {'contract': contract or task, 'input': None, 'checks': checks}
    model = Model(plan, act)
    m = Metered(model, {'used': 0, 'limit': max_calls})
    solver = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                    say=lambda _: None)
    return solver.solve(task), solver, model


def stored(ws, task, checks, source, uses=None):
    result, solver, _ = solve_with(ws, task, checks, lambda r: store(source, uses))
    assert result['status'] == 'checked', result.get('reason')
    return result['artifact'], solver


class P1ARefutedDependencyEndsTheTaskBeforeTheModelIsAsked(unittest.TestCase):
    """CONTROL (passes, no defect): recall drops refuted artifacts but not methods that RUN a refuted one;
    the composition's qualification over its closure still fails (the dependency is invalidated in memory),
    prastara moves on, and the model writes the method. Kept to show the refuted gate holds here."""

    def build(self, refute):
        ws = Workspace(Path(tempfile.mkdtemp()))
        a, solver = stored(ws, 'Square a number.', [check(2, 4), check(3, 9)], 'def run(x):\n    return x * x\n')
        stored(ws, 'Square it and add one.', [check(2, 5), check(3, 10)],
               'def run(x):\n    return use(%r)(x) + 1\n' % a, uses=[a])
        if refute:   # as Solver._refute does inside a job: workspace record + memory invalidation
            solver._job = Journal(ws.root / 'refute.jsonl')
            solver._refute(a, 'shown wrong by a later task')
            solver._job.close()
            solver._job = None
        return ws

    def task(self, ws):
        return solve_with(ws, 'Apply x -> x*x + 1 two times.', [check(1, 5), check(2, 26), check(3, 101)],
                          lambda r: store('def run(x):\n    y = x * x + 1\n    return y * y + 1\n'))

    def test_control_without_refutation_prastara_finds_twice(self):
        result, _, _ = self.task(self.build(refute=False))
        self.assertEqual((result['status'], result['delivered_by']), ('checked', 'prastara'))

    def test_with_the_inner_method_refuted_the_model_still_solves_it(self):
        result, _, model = self.task(self.build(refute=True))
        actor_calls = sum(1 for role, _ in model.log if role == 'actor')
        self.assertEqual(result['status'], 'checked',
                         f'{result.get("reason")!r}; strategies {result["strategies"]}; actor calls {actor_calls}')


class P2RecallOrderAndLimitMakeTheSearchIncomplete(unittest.TestCase):
    """The docstring promises 'a complete search over a small, stated space'. The space is cut twice:
    methods_taking keeps the 4 most RECENT part-form methods, and PRASTARA_LIMIT=24 stops before size 4
    once 4 methods are recalled. Storing more methods makes a composition found before unreachable."""

    def library(self, extra):
        ws = Workspace(Path(tempfile.mkdtemp()))
        stored(ws, 'Square a number.', [check(2, 4), check(3, 9)], 'def run(x):\n    return x * x\n')
        stored(ws, 'Total of a list of numbers.', [check([1, 2], 3), check([4], 4)], 'def run(xs):\n    return sum(xs)\n')
        for task, checks, source in extra:
            stored(ws, task, checks, source)
        return ws

    def task(self, ws):
        return solve_with(ws, 'The values given: add up their squares.', SUM_SQUARES, lambda r: store(RIGHT),
                          contract='sum of the squares of the values')

    def test_control_two_methods_found_by_prastara(self):
        result, _, _ = self.task(self.library([]))
        self.assertEqual(result['delivered_by'], 'prastara')

    def test_two_more_stored_methods_push_the_size_4_recipe_past_place_24(self):
        extra = [('Double a number.', [check(2, 4), check(5, 10)], 'def run(x):\n    return 2 * x\n'),
                 ('Cube a number.', [check(2, 8), check(3, 27)], 'def run(x):\n    return x ** 3\n')]
        result, _, _ = self.task(self.library(extra))
        found = [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_prastara']
        self.assertEqual(len(found[0]['methods']), 4)        # square and total are both recalled
        self.assertEqual(result['delivered_by'], 'prastara', 'total(map(square, values)) is never screened')

    def test_three_more_recent_methods_evict_square_from_recall(self):
        extra = [('Double a number.', [check(2, 4), check(5, 10)], 'def run(x):\n    return 2 * x\n'),
                 ('Cube a number.', [check(2, 8), check(3, 27)], 'def run(x):\n    return x ** 3\n'),
                 ('Negate a number.', [check(2, -2), check(5, -5)], 'def run(x):\n    return -x\n')]
        ws = self.library(extra)
        result, _, _ = self.task(ws)
        found = [e['body'] for e in Journal.read(Path(result['solve_journal'])) if e['kind'] == 'solve_prastara']
        square = [r['artifact'] for r in ws._read(ws.solved_path) if r['task'] == 'Square a number.'][0]
        self.assertIn(square, found[0]['methods'], 'the piece the composition needs was not recalled')



class P3TheSettlementVoidTeachesNothing(unittest.TestCase):
    """cc8d583: 'a void teaches the next task of its form'. The void that _settle_given returns (checks that
    cannot be settled) leaves _solve before _solve_planned, where learn() is called: no lesson is kept."""

    def test_a_void_before_code_records_no_lesson(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        plan = lambda c: {'contract': 'run(x) returns x squared', 'input': None,
                          'checks': [check(3, 10), check(4, 15)], 'reference': 'def reference(x):\n    return x * x\n'}
        model = Model(plan, lambda r: store('def run(x):\n    return x * x\n'), audit=lambda c: {'expected': [11, 17]})
        m = Metered(model, {'used': 0, 'limit': 10})
        result = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=256,
                        say=lambda _: None).solve('Square a number.')
        self.assertIn('void', result)
        self.assertTrue(ws._read(ws.lessons_path), 'the void left no lesson for the next task of its form')


class P4SmallBudgetsNeverCrashOrOverrun(unittest.TestCase):
    """Control (passes): prastara stops before the shared execution limit at every small budget."""

    def test_every_budget_from_0_to_12(self):
        for limit in range(13):
            ws = P2RecallOrderAndLimitMakeTheSearchIncomplete().library([])
            plan = lambda c: {'contract': 'sum of squares', 'input': None, 'checks': SUM_SQUARES}
            model = Model(plan, lambda r: store(RIGHT))
            m = Metered(model, {'used': 0, 'limit': 10})
            solver = Solver(ws, m, m, local_executor, splitter=m, auditor=m, sketcher=m, max_executions=limit,
                            say=lambda _: None)
            result = solver.solve('The values given: add up their squares.')
            self.assertLessEqual(result['execution_attempts'], limit)



class P5APieceUsedByPrastaraIsRefutedAsMemorizing(unittest.TestCase):
    """prastara composes a stored method from another task into this one; the loop's memorization scan
    runs over every library of the delivered method against THIS task's checks, and _refute()s each
    'culprit' globally. A correct stored table method (if m == 2: return 28) is refuted for good."""

    DAYS = ('def run(m):\n    if m == 2:\n        return 28\n'
            '    return 30 if m in (4, 6, 9, 11) else 31\n')

    def test_a_correct_stored_method_is_refuted_for_a_composition_of_it(self):
        ws = Workspace(Path(tempfile.mkdtemp()))
        days, _ = stored(ws, 'Days in a month (non-leap year).', [check(1, 31), check(4, 30), check(12, 31)], self.DAYS)
        checks = [check({'month': 2}, 28), check({'month': 4}, 30), check({'month': 1}, 31)]
        table = 'def run(i):\n    return [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][i["month"] - 1]\n'
        result, _, _ = solve_with(ws, 'Days in the given month.', checks, lambda r: store(table))
        self.assertIn('prastara', result['strategies'])
        self.assertNotIn(days, ws.refuted(), 'a correct method from another task is refuted forever')


if __name__ == '__main__':
    unittest.main()
