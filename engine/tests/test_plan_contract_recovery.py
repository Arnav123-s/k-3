"""Authored planning controls; no endpoints or evaluation-generated programs."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.check_contracts import MAX_TEXT_CHARS
from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor


PROMPT = 'Increment an integer and answer for 7.'
CHECKS = [{'input': 1, 'expected': 2}, {'input': 2, 'expected': 3}]
REFERENCE = 'def reference(n):\n    return n + 1\n'
VERIFY = 'def verify(n, result):\n    return result - n == 1\n'
DECOMPOSITION = {
    'subtasks': [{'id': 'increment', 'request': 'Increment an integer.', 'checks': CHECKS}],
    'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'increment', 'arg': {'op': 'input'}}}}
GOOD = {'contract': 'run(n) receives an integer and returns n+1.', 'checks': CHECKS,
        'input': 7, 'reference': REFERENCE, 'small_inputs': [0, 1],
        'verify': VERIFY, 'decomposition': DECOMPOSITION}


class Replies:
    identity = {'model': 'authored-plan-schema-fixture'}
    last_usage = {}

    def __init__(self, replies):
        self.replies, self.contexts = replies, []

    def propose(self, context):
        self.contexts.append(copy.deepcopy(context))
        return copy.deepcopy(self.replies[min(len(self.contexts) - 1, len(self.replies) - 1)])


class PlanContractRecoveryTests(unittest.TestCase):
    def make(self, replies, *, calls=30, executions=8):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.planner = Replies(replies)
        self.actor = Replies([{'kind': 'unresolved', 'reason': 'actor not needed for schema repair'}])
        self.counter = {'used': 0, 'limit': calls}
        self.executions = []

        def execute(job):
            self.executions.append(job)
            return local_executor(job)

        solver = Solver(Workspace(Path(temporary.name)), Metered(self.planner, self.counter),
                        Metered(self.actor, self.counter), execute, max_executions=executions,
                        say=lambda _: None)
        return solver

    def plan(self, solver, examples=(), *, examples_origin='user'):
        path = solver.ws.runs / 'authored-plan-events.jsonl'
        with Journal(path) as journal:
            solver._job = journal
            try:
                spec = solver.plan(PROMPT, list(examples), examples_origin=examples_origin)
            finally:
                solver._job = None
        return spec, Journal.read(path)

    @staticmethod
    def rejections(events):
        return [event['body'] for event in events if event['kind'] == 'solve_plan_rejected']

    def test_malformed_check_row_gets_specific_feedback_and_one_event_per_attempt(self):
        malformed = dict(GOOD, checks=[{'input': 1, 'output': 2}])
        solver = self.make([malformed])
        spec, events = self.plan(solver)
        self.assertIsNone(spec)
        self.assertEqual(self.counter['used'], 2)
        self.assertEqual(len(self.rejections(events)), 2)
        feedback = self.planner.contexts[1]['previous_reply_rejected']
        self.assertIn('checks[0]', feedback)
        self.assertIn('expected', feedback)
        self.assertTrue(all(row.get('reason') for row in self.rejections(events)))
        self.assertEqual(self.actor.contexts, [])
        self.assertEqual(self.executions, [])

    def test_blank_contract_is_rejected_explicitly(self):
        solver = self.make([dict(GOOD, contract='   ')])
        spec, events = self.plan(solver)
        self.assertIsNone(spec)
        self.assertEqual(len(self.rejections(events)), 2)
        feedback = self.planner.contexts[1]['previous_reply_rejected'].lower()
        self.assertIn('contract', feedback)
        self.assertRegex(feedback, r'empty|blank')
        self.assertEqual(self.counter['used'], 2)

    def test_schema_feedback_repairs_second_reply_without_extra_calls(self):
        malformed = dict(GOOD, checks=[{'input': 1, 'output': 2}])
        solver = self.make([malformed, GOOD], calls=2)
        spec, events = self.plan(solver)
        self.assertEqual(spec['contract'], GOOD['contract'])
        self.assertEqual(spec['input'], 7)
        self.assertEqual(spec['decomposition'], DECOMPOSITION)
        self.assertEqual(len(self.rejections(events)), 1)
        self.assertEqual(self.counter['used'], 2)
        self.assertEqual(self.executions, [])

    def test_feedback_does_not_copy_a_huge_rejected_reply_into_next_request(self):
        huge = 'UNTRUSTED-DRAFT-CONTENT-' * (MAX_TEXT_CHARS // 5)
        solver = self.make([dict(GOOD, contract=huge, reference=huge), GOOD], calls=2)
        spec, events = self.plan(solver)
        self.assertIsNotNone(spec)
        self.assertEqual(len(self.rejections(events)), 1)
        retry = self.planner.contexts[1]
        self.assertIn('contract', retry['previous_reply_rejected'].lower())
        self.assertLessEqual(len(retry['previous_reply_rejected']), 1000)
        self.assertLess(len(json.dumps(retry)), 5000)
        self.assertNotIn(huge, json.dumps(retry))
        self.assertEqual(self.counter['used'], 2)
        self.assertEqual(self.executions, [])

    def test_conflicting_model_checks_still_reject_both_plans(self):
        solver = self.make([dict(GOOD, checks=[{'input': 1, 'expected': 2},
                                              {'input': 1, 'expected': 99}])])
        spec, events = self.plan(solver)
        self.assertIsNone(spec)
        self.assertEqual(len(self.rejections(events)), 2)
        self.assertIn('conflict', self.planner.contexts[1]['previous_reply_rejected'].lower())
        self.assertEqual(self.counter['used'], 2)
        self.assertEqual(self.executions, [])

    def test_explicit_empty_proposals_preserve_valid_fields_and_supplied_provenance(self):
        for origin in ('user', 'model'):
            with self.subTest(origin=origin):
                solver = self.make([dict(GOOD, checks=[])])
                spec, events = self.plan(solver, CHECKS, examples_origin=origin)
                self.assertEqual(self.counter['used'], 1)
                self.assertEqual(self.rejections(events), [])
                for field in ('contract', 'input', 'reference', 'small_inputs', 'verify', 'decomposition'):
                    self.assertEqual(spec[field], GOOD[field], field)
                self.assertEqual(spec['checks'], CHECKS)
                records = solver._checkbooks[spec['check_scope']].records
                self.assertTrue(all(row['origin'] == origin and row['source'] == 'supplied' for row in records))
                self.assertEqual(self.executions, [])

    def test_malformed_proposals_with_anchors_are_not_treated_as_empty(self):
        for proposals in (None, {}, '[]', [{'input': 1, 'output': 2}]):
            with self.subTest(proposals=proposals):
                solver = self.make([dict(GOOD, checks=proposals)])
                spec, events = self.plan(solver, CHECKS)
                self.assertEqual(self.counter['used'], 2)
                self.assertEqual(len(self.rejections(events)), 2)
                # Existing safe fallback is allowed; it must not accept the malformed draft.
                self.assertEqual(spec['contract'], PROMPT)
                self.assertIsNone(spec['input'])
                self.assertEqual(spec['checks'], CHECKS)
                self.assertEqual(self.executions, [])

    def test_empty_proposals_without_anchors_remain_uncheckable(self):
        solver = self.make([dict(GOOD, checks=[])])
        spec, events = self.plan(solver)
        self.assertIsNone(spec)
        self.assertEqual(len(self.rejections(events)), 2)
        self.assertIn('check', self.planner.contexts[1]['previous_reply_rejected'].lower())
        self.assertEqual(self.counter['used'], 2)
        self.assertEqual(self.executions, [])

    def test_preserved_optional_decomposition_still_uses_normal_recipe_validation(self):
        malformed = dict(DECOMPOSITION, recipe={'version': 1, 'output': {'op': 'execute_python'}})
        solver = self.make([dict(GOOD, checks=[], decomposition=malformed)])
        spec, events = self.plan(solver, CHECKS)
        self.assertEqual(self.counter['used'], 1)
        self.assertEqual(spec['contract'], GOOD['contract'])
        self.assertEqual(spec['checks'], CHECKS)
        self.assertIsNone(spec['decomposition'])
        self.assertTrue(any(e['kind'] == 'solve_decomposition_rejected' for e in events))
        self.assertEqual(self.executions, [])

    def test_preserved_anchored_plan_still_requires_real_method_qualification(self):
        solver = self.make([dict(GOOD, checks=[])], executions=4)
        spec, _ = self.plan(solver, CHECKS)
        self.assertEqual(self.counter['used'], 1)
        wrong = solver.admit(PROMPT, spec['checks'], 'def run(n):\n    return n * 2\n', 'wrong fixture',
                             check_scope=spec['check_scope'])
        correct = solver.admit(PROMPT, spec['checks'], 'def run(n):\n    return n + 1\n', 'correct fixture',
                               check_scope=spec['check_scope'])
        self.assertEqual(wrong['status'], 'incomplete')
        self.assertEqual(correct['status'], 'checked')
        self.assertEqual(solver._execution_used, 4)
        self.assertEqual(self.counter['used'], 1)
        exhausted = solver.admit(PROMPT, spec['checks'], None, 'exhausted fixture',
                                 stored=correct['artifact'], check_scope=spec['check_scope'])
        self.assertEqual(exhausted['status'], 'incomplete')
        self.assertEqual(solver._execution_used, 4)

    def test_malformed_plan_retries_never_exceed_actual_shared_call_limit(self):
        for calls in (0, 1, 2):
            with self.subTest(calls=calls):
                solver = self.make([dict(GOOD, checks=[{'input': 1}])], calls=calls, executions=0)
                outcome = solver.solve(PROMPT)
                self.assertNotEqual(outcome['status'], 'checked')
                self.assertEqual(self.counter['used'], calls)
                self.assertEqual(len(self.planner.contexts), calls)
                self.assertEqual(self.executions, [])
                self.assertEqual(self.actor.contexts, [])

    def test_contradictory_supplied_anchors_fail_before_any_model_or_execution(self):
        solver = self.make([dict(GOOD, checks=[])])
        outcome = solver.solve(PROMPT, [{'input': 1, 'expected': 2}, {'input': 1, 'expected': 99}])
        self.assertEqual(outcome['status'], 'needs_examples')
        self.assertEqual(self.counter['used'], 0)
        self.assertEqual(self.planner.contexts, [])
        self.assertEqual(self.actor.contexts, [])
        self.assertEqual(self.executions, [])


if __name__ == '__main__':
    unittest.main()
