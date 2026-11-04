"""A scripted actor can solve small pieces even when whole-task retries cannot help."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime.check_contracts import CheckBook
from kuttaka_runtime.runtime import TransportFailure
from kuttaka_runtime.solve import MAX_DEPTH, Metered, Solver, Workspace, _execution_fingerprint, round_signature, settings, solve_task
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_evidence_integration import ScriptedModel, remember_action
from tests.test_solve_mechanical_composition import (
    ComponentActor, ScriptedPlanner, SQUARE, TOTAL, decomposition,
)


WHOLE = 'sum squares list'
PARTIAL = 'def run(xs):\n    return sum(xs)\n'
WHOLE_SOLUTION = 'def run(xs):\n    return sum(x*x for x in xs)\n'
BROAD = [{'input': [], 'expected': 0}, {'input': [1], 'expected': 1},
         {'input': [2], 'expected': 4}, {'input': [2, 3], 'expected': 13}]
NEAR = [{'input': [], 'expected': 0}, {'input': [1], 'expected': 1},
        {'input': [1, 1], 'expected': 2}, {'input': [2], 'expected': 4}]


class WeakWholeActor(ComponentActor):
    def __init__(self, *, repair_works=False):
        super().__init__({'square each value': SQUARE, 'sum values': TOTAL})
        self.repair_works = repair_works

    def propose(self, context):
        if not context['request'].startswith(WHOLE):
            return super().propose(context)
        self.parent_calls += 1
        self.contexts.append(context)
        repaired = self.repair_works and 'Best attempt so far' in context['request']
        if not context['history']:
            return remember_action(WHOLE_SOLUTION if repaired else PARTIAL)
        observation = context['history'][-1]['observation']
        if repaired and observation.get('artifact'):
            return {'kind': 'answer', 'answer': {'artifact': observation['artifact']}}
        return {'kind': 'unresolved', 'reason': 'cannot solve the whole task'}


class DecompositionProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = Workspace(Path(self.temp.name))

    def solver(self, checks, calls, actor):
        counter = {'used': 0, 'limit': calls}
        planner = ScriptedPlanner(parent_checks=checks)
        splitter = ScriptedModel(decomposition())
        solver = Solver(self.ws, Metered(planner, counter), Metered(actor, counter), local_executor,
                        splitter=Metered(splitter, counter), say=lambda _: None)
        return solver, counter, splitter

    def test_many_failing_checks_split_before_repeating_the_whole_task(self):
        actor = WeakWholeActor()
        solver, counter, splitter = self.solver(BROAD, 12, actor)
        result = solver.solve(WHOLE, BROAD)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['delivered_by'], 'recipe')
        self.assertEqual(result['strategies'], ['split'])
        self.assertEqual(actor.parent_calls, 2)
        self.assertEqual([part['status'] for part in result['subtasks']], ['checked', 'checked'])
        self.assertLessEqual(counter['used'], 12)

    def test_control_old_repair_first_order_cannot_finish_both_pieces_with_same_budget(self):
        actor = WeakWholeActor()
        solver, counter, _ = self.solver(BROAD, 12, actor)
        next_step = solver._next_step

        def repair_first(best, tried, spec, examples, depth):
            # Restore the old parent ordering only. Models, checks, child solvers and budget are identical
            # to the positive case above, isolating the cost of repeating the failed whole-task method.
            if depth == 0 and best is not None and 'repair' not in tried:
                return 'repair'
            return next_step(best, tried, spec, examples, depth)

        with mock.patch.object(solver, '_next_step', side_effect=repair_first):
            result = solver.solve(WHOLE, BROAD)
        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual(result['strategies'][:2], ['repair', 'split'])
        self.assertEqual(actor.parent_calls, 4, 'old ordering spends two extra calls on the unsolvable whole')
        self.assertEqual(len(result['subtasks']), 2)
        self.assertEqual(result['subtasks'][0]['status'], 'checked')
        self.assertNotEqual(result['subtasks'][1]['status'], 'checked')
        self.assertEqual(counter['used'], 12)

    def test_near_correct_method_gets_one_useful_repair_before_decomposition(self):
        actor = WeakWholeActor(repair_works=True)
        solver, counter, splitter = self.solver(NEAR, 12, actor)
        result = solver.solve(WHOLE, NEAR)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['strategies'], ['repair'])
        self.assertEqual(splitter.calls, 0)
        self.assertEqual(actor.parent_calls, 4)

    def test_failed_near_correct_repair_reduces_before_mati(self):
        actor = WeakWholeActor()
        solver, counter, splitter = self.solver(NEAR, 14, actor)
        result = solver.solve(WHOLE, NEAR)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['strategies'], ['repair', 'split'])
        self.assertEqual(actor.parent_calls, 4)
        self.assertEqual(result['delivered_by'], 'recipe')
        self.assertLessEqual(counter['used'], 14)

    def test_new_round_keeps_failed_repair_as_a_scheduling_hint(self):
        actor = WeakWholeActor()
        planner = ScriptedPlanner(parent_checks=NEAR)
        splitter = ScriptedModel(decomposition())
        result = solve_task(WHOLE, NEAR, settings=settings(max_calls=10, rounds=2), workspace=self.ws,
                            models=(planner, actor, splitter, None, None), executor=local_executor)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(len(result['rounds']), 2)
        self.assertEqual(actor.parent_calls, 4, 'another round must not repeat the repair that already stalled')
        self.assertEqual(result['strategies'][:2], ['resume partial', 'split'])
        events = Journal.read(result['solve_journal'])
        self.assertTrue(any(event['kind'] == 'solve_partial_resumed' for event in events),
                        'a scheduling hint must not replace fresh partial execution')
        self.assertLessEqual(result['cost']['model_calls'], 20)

    def test_failed_split_can_fall_back_to_a_useful_whole_repair(self):
        actor = WeakWholeActor(repair_works=True)
        solver, counter, _ = self.solver(BROAD, 12, actor)
        solver.splitter = Metered(ScriptedModel({'subtasks': []}), counter)
        result = solver.solve(WHOLE, BROAD)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['strategies'], ['split', 'repair'])
        self.assertLessEqual(counter['used'], 12)

    def test_repair_hints_match_scope_source_and_dependencies_only(self):
        solver, _, _ = self.solver(NEAR, 12, WeakWholeActor())
        spec = solver.plan(WHOLE, NEAR)
        best = {'source': PARTIAL, 'uses': [], 'passed_checks': NEAR[:-1], 'failed_checks': NEAR[-1:]}
        self.assertEqual(solver._next_step(best, set(), spec, NEAR, 0), 'repair')
        key = (spec['check_scope'], _execution_fingerprint(PARTIAL, []))
        solver._stalled_repairs.add(key)
        self.assertEqual(solver._next_step(best, set(), spec, NEAR, 0), 'split')
        for changed in (dict(best, source=PARTIAL + '\n'), dict(best, uses=['a' * 64])):
            self.assertEqual(solver._next_step(changed, set(), spec, NEAR, 0), 'repair')
        other = CheckBook(WHOLE, 'a different contract', NEAR, [])
        solver._checkbooks[other.scope_id] = other
        self.assertEqual(solver._next_step(best, set(), dict(spec, check_scope=other.scope_id), NEAR, 0), 'repair')
        self.assertEqual(solver._next_step(best, {'split'}, spec, NEAR, 0), 'repair')
        self.assertEqual(solver._next_step(best, set(), spec, NEAR, MAX_DEPTH), 'repair')

    def test_transport_only_repair_does_not_create_a_stall_hint(self):
        class InterruptedRepair(WeakWholeActor):
            def propose(self, context):
                if 'Best attempt so far' in context['request']:
                    raise TransportFailure('request_timeout')
                return super().propose(context)

        solver, _, _ = self.solver(NEAR, 8, InterruptedRepair())
        result = solver.solve(WHOLE, NEAR)
        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual(solver.resume_repairs(), {})
        self.assertFalse(any(e['kind'] == 'solve_repair_stalled' for e in Journal.read(result['solve_journal'])))

    def test_new_scheduling_hints_affect_stopping_but_repeated_hints_do_not(self):
        base = {'status': 'incomplete', 'progress': [3]}
        before = round_signature(base, self.ws)
        hinted = dict(base, repair_hints={'a' * 64: ['b' * 64]})
        after = round_signature(hinted, self.ws)
        self.assertNotEqual(before, after)
        duplicate = dict(base, repair_hints={'a' * 64: ['b' * 64, 'b' * 64]})
        self.assertEqual(after, round_signature(duplicate, self.ws))

    def test_round_progress_uses_partial_executable_identity_not_artifact_metadata(self):
        row = {'candidate_id': 'executable-a', 'artifact_id': 'artifact-a', 'passed_ids': ['first']}
        result = {'status': 'incomplete', 'progress': [1],
                  'evidence_frontiers': {'scope': {'frontier': [row]}}}
        before = round_signature(result, self.ws)
        row['artifact_id'] = 'same-source-new-metadata'
        self.assertEqual(before, round_signature(result, self.ws))
        row['candidate_id'] = 'executable-b'
        self.assertNotEqual(before, round_signature(result, self.ws))


if __name__ == '__main__':
    unittest.main()
