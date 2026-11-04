"""Failed decomposition outcomes inform another split without becoming proof.

Scripted models only; executable methods are existing authored test fixtures.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor
from tests.test_kuttaka_solve import Actor, Planner


ROOT = 'Sum the squares of a list.'
CHECKS = [{'input': [1, 2], 'expected': 5}, {'input': [], 'expected': 0}]


def useful_split():
    return {'subtasks': [
        {'id': 'square', 'request': 'Square a number.', 'checks': [{'input': 3, 'expected': 9}]},
        {'id': 'total', 'request': 'Add up a list.', 'checks': [{'input': [1, 2], 'expected': 3}]}],
        'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'total',
                   'arg': {'op': 'map', 'method': 'square', 'arg': {'op': 'input'}}}}}


class Splitter:
    identity = {'model': 'scripted-outcome-sensitive-splitter'}
    last_usage = {'input_tokens': 1, 'output_tokens': 1}

    def __init__(self, reply=None):
        self.contexts = []
        self.reply = reply

    def propose(self, context):
        self.contexts.append(json.loads(json.dumps(context)))
        if self.reply is not None:
            return self.reply
        prior = context.get('previous_breakdown_outcomes', [])
        if any(child['obstruction'] == 'cycle' for row in prior for child in row['children']):
            return useful_split()
        return {'subtasks': [{'id': 'again', 'request': context['task'], 'checks': context['checks']}],
                'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'again', 'arg': {'op': 'input'}}}}


class SplitOutcomeFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.serial = 0

    def solver(self, splitter, *, actor=None, calls=40):
        self.serial += 1
        counter = {'used': 0, 'limit': calls}
        solver = Solver(Workspace(self.root / str(self.serial)), Metered(Planner(), counter),
                        Metered(actor or Actor(), counter), local_executor,
                        splitter=Metered(splitter, counter), say=lambda _: None)
        solver.derive_blind = False
        return solver

    def test_failed_child_changes_next_question_and_checked_pieces_complete_parent(self):
        control_splitter = Splitter()
        control = self.solver(control_splitter)
        with mock.patch.object(control, '_record_split_outcome'):
            before = control.solve(ROOT, CHECKS)
        self.assertNotEqual(before['status'], 'checked')
        self.assertEqual(control_splitter.contexts[0], control_splitter.contexts[1])

        splitter = Splitter()
        solver = self.solver(splitter)
        result = solver.solve(ROOT, CHECKS)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['answer'], 25)
        checked = [part for part in result['subtasks'] if part['status'] == 'checked']
        self.assertEqual(len(checked), 2)
        self.assertEqual(set(result['reused']), {part['artifact'] for part in checked})
        self.assertLessEqual(solver.actor.counter['used'], 40)
        prior = splitter.contexts[1]['previous_breakdown_outcomes']
        child = prior[-1]['children'][0]
        self.assertEqual(child['obstruction'], 'cycle')
        self.assertEqual(child['qualification'], 'not_checked')
        self.assertIsNone(child['check_scope'], 'cycle return did not supply a planned qualification scope')
        self.assertNotIn('checked_artifact', child)
        self.assertEqual(prior[-1]['evidence_role'], 'scheduling_only')
        events = Journal.read(result['solve_journal'])
        recorded = [event['body'] for event in events if event['kind'] == 'solve_split_outcome']
        self.assertIn(prior[-1], recorded)

    def test_budget_stopped_child_can_retry_and_finish_under_same_global_ceiling(self):
        class TwoExtraReplies(Actor):
            injected = 0

            def propose(self, context):
                history = context['history']
                if self.injected < 2 and history:
                    self.injected += 1
                    return {'kind': 'invalid', 'raw_reply': 'authored malformed reply'}
                return super().propose(context)

        proposal = {'subtasks': [{'id': 'square', 'request': 'Square a number.',
                                 'checks': [{'input': 3, 'expected': 9}]}],
                    'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'square',
                                                     'arg': {'op': 'input'}}}}
        splitter = Splitter(proposal)
        solver = self.solver(splitter, actor=TwoExtraReplies(), calls=13)
        prompt = 'Square a number.'
        spec = solver.plan(prompt, [])
        request = prompt + '\n\nContract: ' + spec['contract']
        initial = {'reason': 'whole attempt unresolved'}
        first, _, _ = solver.split(prompt, spec, [], initial, None, request, [], 0, '')
        self.assertNotEqual(first[0]['status'], 'checked')
        second, _, outcome = solver.split(prompt, spec, [], initial, None, request, [], 0, '')
        self.assertEqual(second[0]['status'], 'checked')
        self.assertEqual(outcome['status'], 'checked')
        previous = splitter.contexts[1]['previous_breakdown_outcomes'][-1]['children'][0]
        self.assertEqual(previous['obstruction'], 'budget')
        self.assertTrue(previous['attempted'])
        self.assertEqual(previous['qualification'], 'not_checked')
        self.assertEqual(solver.actor.counter['limit'], 13)
        self.assertLessEqual(solver.actor.counter['used'], 13)

    def test_checked_piece_is_preserved_while_next_split_replaces_failed_child(self):
        class NoWholeActor(Actor):
            def propose(self, context):
                if context['request'].startswith(ROOT):
                    return {'kind': 'unresolved', 'reason': 'whole task needs smaller functions'}
                return super().propose(context)

        class ReplaceFailedChild(Splitter):
            def propose(self, context):
                self.contexts.append(json.loads(json.dumps(context)))
                previous = context.get('previous_breakdown_outcomes', [])
                if previous:
                    retained = next(child['checked_artifact'] for row in previous for child in row['children']
                                    if child['qualification'] == 'child_finite_checks_only')
                    return {'subtasks': [useful_split()['subtasks'][1]],
                            'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'total',
                                       'arg': {'op': 'map', 'method': retained, 'arg': {'op': 'input'}}}}}
                return {'subtasks': [useful_split()['subtasks'][0],
                                    {'id': 'again', 'request': context['task'], 'checks': context['checks']}],
                        'recipe': {'version': 1, 'output': {'op': 'call', 'method': 'again',
                                   'arg': {'op': 'map', 'method': 'square', 'arg': {'op': 'input'}}}}}

        splitter = ReplaceFailedChild()
        solver = self.solver(splitter, actor=NoWholeActor())
        result = solver.solve(ROOT, CHECKS)
        self.assertEqual(result['status'], 'checked', result)
        self.assertEqual(result['answer'], 25)
        prior = splitter.contexts[1]['previous_breakdown_outcomes'][-1]['children']
        self.assertEqual([child['qualification'] for child in prior],
                         ['child_finite_checks_only', 'not_checked'])
        square = prior[0]['checked_artifact']
        self.assertIn(square, result['reused'])
        self.assertTrue(any(square in method for method in splitter.contexts[1]['available_methods']))
        self.assertEqual(len([part for part in result['subtasks'] if part['task'] == 'Square a number.']), 1)

    def test_context_is_bounded_scope_specific_and_never_exports_unchecked_code(self):
        sub = {'request': 'piece ' + 'x' * 2000, 'checks': [{'input': 1, 'expected': 2}]}
        splitter = Splitter({'subtasks': [sub]})
        solver = self.solver(splitter)
        spec = {'check_scope': 'a' * 64, 'contract': 'parent', 'checks': CHECKS}
        failed = {'status': 'unresolved', 'reason': 'failure ' + 'z' * 2000,
                  'artifact': 'c' * 64, 'source': 'must never enter context',
                  'check_evidence': {'scope': 'd' * 64}}
        for _ in range(8):
            solver._record_split_outcome(spec, [sub] * 4, [failed] * 4, None)
        other = dict(spec, check_scope='b' * 64)
        solver._record_split_outcome(other, [dict(sub, request='irrelevant other task')], [failed], None)
        with mock.patch.object(solver, '_solve_piece', return_value=failed):
            solver.split('parent', spec, [], {'reason': 'failed'}, None, 'parent', [], 0, '')
        previous = splitter.contexts[0]['previous_breakdown_outcomes']
        self.assertEqual(len(previous), 3)
        self.assertLess(len(json.dumps(previous)), 12000)
        self.assertNotIn('irrelevant other task', json.dumps(previous))
        self.assertNotIn('must never enter context', json.dumps(previous))
        for row in previous:
            self.assertEqual(row['parent_check_scope'], spec['check_scope'])
            for child in row['children']:
                self.assertEqual(child['check_scope'], 'd' * 64)
                self.assertEqual(child['qualification'], 'not_checked')
                self.assertNotIn('checked_artifact', child)
                self.assertLessEqual(len(child['request']), 240)
                self.assertLessEqual(len(child['reason']), 240)

    def test_no_budget_before_child_is_not_recorded_as_attempted(self):
        splitter = Splitter({'subtasks': [{'request': 'Square a number.',
                                          'checks': [{'input': 2, 'expected': 4}]}]})
        solver = self.solver(splitter, calls=1)
        spec = {'check_scope': 'a' * 64, 'contract': 'parent', 'checks': CHECKS}
        solver.split('parent', spec, [], {'reason': 'failed'}, None, 'parent', [], 0, '')
        child = solver._split_outcomes[spec['check_scope']][-1]['children'][0]
        self.assertFalse(child['attempted'])
        self.assertEqual(child['obstruction'], 'budget')
        self.assertIsNone(child['check_scope'])

    def test_whitespace_cycle_protocol_transport_and_missing_reason_remain_diagnostics(self):
        solver = self.solver(Splitter())
        spec = {'check_scope': 'a' * 64}
        sub = {'request': 'piece', 'checks': [{'input': 1, 'expected': 2}]}
        for reason, expected in (
                ('recursive subproblem only changes surrounding whitespace of an active goal and checks', 'cycle'),
                ('repeated invalid JSON replies', 'protocol'),
                ('transport outage threshold reached', 'transport'),
                (None, 'unsolved')):
            with self.subTest(reason=reason):
                solver._record_split_outcome(spec, [sub], [{'status': 'incomplete', 'reason': reason}], None)
                child = solver._split_outcomes[spec['check_scope']][-1]['children'][0]
                self.assertEqual(child['obstruction'], expected)
                self.assertEqual(child['qualification'], 'not_checked')

    def test_invalid_proposal_is_not_reported_as_a_failed_child(self):
        solver = self.solver(Splitter({'subtasks': 'not a list'}))
        spec = {'check_scope': 'a' * 64, 'contract': 'parent', 'checks': CHECKS}
        solver.split('parent', spec, [], {'reason': 'failed'}, None, 'parent', [], 0, '')
        record = solver._split_outcomes[spec['check_scope']][-1]
        self.assertEqual(record['origin'], 'proposal_validation')
        self.assertEqual(record['children'], [])
        self.assertEqual(len(record['proposal_rejections']), 2)


if __name__ == '__main__':
    unittest.main()
