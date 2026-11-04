"""Provisional root-contract continuity; scripted models and authored test code only."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime.check_contracts import CheckBook
from kuttaka_runtime.runtime import TransportFailure
from kuttaka_runtime.solve import CallsExhausted, Metered, Solver, Workspace, settings, solve_task
from kuttaka_runtime.store import Journal, digest
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_evidence_integration import ScriptedModel, find_action, remember_action

TASK = 'Increment an integer.'
CHECKS = [{'input': 1, 'expected': 2}, {'input': 2, 'expected': 3}]
PARTIAL = 'def run(x):\n    return 2*x\n'
CORRECT = 'def run(x):\n    return x+1\n'
PLAN = {'contract': 'run(x) returns x+1', 'checks': CHECKS, 'input': None}


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root, self.number = Path(self.temp.name), 0

    def solver(self, planner=None, *, resume=None, calls=8, oracle=None, oracle_id=None, ws=None):
        self.number += 1
        counter = {'used': 0, 'limit': calls}
        model = Metered(planner or ScriptedModel(PLAN), counter)
        actor = Metered(ScriptedModel({'kind': 'unresolved', 'reason': 'fixture stop'}), counter)
        return Solver(ws or Workspace(self.root / str(self.number)), model, actor, local_executor,
                      resume_contract=resume, check_oracle=oracle, oracle_id=oracle_id, say=lambda _: None)

    def seed(self, examples=(), origin='user', **kwargs):
        solver = self.solver(calls=1, **kwargs)
        result = solver.solve(TASK, examples, examples_origin=origin)
        saved = solver.resume_contract()
        self.assertIsNotNone(saved, result)
        return solver, saved

    def test_default_solver_gates_recover_partial_after_two_malformed_plans(self):
        class Planner(ScriptedModel):
            def __init__(self):
                super().__init__(None)
                self.round = 0
            def propose(self, context):
                self.contexts.append(json.loads(json.dumps(context)))
                if not any(k in context for k in ('previous_contract', 'previous_reply_rejected', 'given_conditions_missing')):
                    self.round += 1
                return {} if self.round >= 2 else PLAN

        def run(disable):
            planner = Planner()
            def reply(context):
                repairing = planner.round >= 2 and 'Source:\n' + PARTIAL in context['request']
                if not context['history']:
                    return find_action()
                last = context['history'][-1]['observation']
                if 'methods' in last:
                    return remember_action(CORRECT if repairing else PARTIAL)
                if repairing and last.get('artifact'):
                    return {'kind': 'answer', 'answer': {'artifact': last['artifact']}}
                return {'kind': 'unresolved', 'reason': 'fixture partial retained'}
            actor = ScriptedModel(reply)
            auditor = ScriptedModel(lambda c: {'expected': [v + 1 for v in c['inputs']]})
            self.number += 1
            ws = Workspace(self.root / str(self.number))
            patch = mock.patch.object(Solver, 'resume_contract', return_value=None) if disable else mock.patch.dict({}, {})
            with patch:
                result = solve_task(TASK, settings=settings(max_calls=16, rounds=2), workspace=ws,
                                    models=(planner, actor, None, auditor, None), executor=local_executor)
            return result, planner

        before, _ = run(True)
        after, planner = run(False)
        self.assertEqual(before['status'], 'needs_examples')
        self.assertEqual(after['status'], 'checked', after)
        self.assertEqual(len(after['rounds']), 2)
        self.assertLessEqual(after['cost']['model_calls'], 32)
        contexts = [c for c in planner.contexts if 'retained_contract' in c]
        self.assertEqual(len(contexts), 2)
        self.assertTrue(all('previous_contract' not in c for c in contexts))

    def test_binding_rejects_other_task_anchors_origin_or_oracle(self):
        _, saved = self.seed()
        for task, examples, origin, oracle, oracle_id in (
                ('Different task', [], 'user', None, None),
                (TASK, CHECKS[:1], 'user', None, None),
                (TASK, [], 'model', None, None),
                (TASK, [], 'user', lambda *_: None, 'new-oracle')):
            with self.subTest(task=task, origin=origin, oracle=oracle_id):
                solver = self.solver(resume=saved, oracle=oracle, oracle_id=oracle_id)
                self.assertIsNone(solver._read_retained_contract(task, examples, origin))

    def test_ordered_provenance_survives_without_support_or_cache(self):
        for origin in ('user', 'model'):
            with self.subTest(origin=origin):
                first, saved = self.seed(CHECKS[:1], origin)
                first._support[('fake', 'fake')] = 'given'
                first._continuity_spec['property_cache'] = {'fake': True}
                saved = first.resume_contract()
                second = self.solver(resume=saved)
                spec, book = second._read_retained_contract(TASK, CHECKS[:1], origin)
                self.assertEqual(book.records, saved['records'])
                self.assertEqual(book.scope_id, saved['check_scope'])
                self.assertEqual(book.records[0]['origin'], origin)
                self.assertEqual(book.records[0]['source'], 'supplied')
                self.assertEqual(book.records[1]['source'], 'model_proposal')
                self.assertNotIn('property_cache', spec)
                self.assertEqual(second._support, {})

    def test_checkpoint_cannot_mint_user_anchors_even_with_recomputed_identity(self):
        _, saved = self.seed()
        forged = CheckBook(TASK, PLAN['contract'], CHECKS, [])
        saved.update(records=forged.records, check_scope=forged.scope_id)
        saved['identity'] = digest({k: v for k, v in saved.items() if k != 'identity'})
        self.assertIsNone(self.solver(resume=saved)._read_retained_contract(TASK, [], 'user'))

    def test_transport_only_and_budget_exhaustion_do_not_activate_fallback(self):
        _, saved = self.seed()
        def outage(_):
            raise TransportFailure('request_timeout')
        solver = self.solver(ScriptedModel(outage), resume=saved, calls=6)
        result = solver.solve(TASK)
        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual(solver._execution_used, 0)
        self.assertIsNone(solver.resume_contract())
        events = Journal.read(result['solve_journal'])
        self.assertFalse(any(e['kind'] == 'solve_contract_retained' for e in events))
        limited = self.solver(ScriptedModel({}), resume=saved, calls=1)
        limited.solve(TASK)
        self.assertEqual(limited.actor.counter['used'], 1)
        self.assertIsNone(limited.resume_contract())

    def test_no_initial_plan_or_explicit_replan_can_borrow_retained_contract(self):
        initial = self.solver(ScriptedModel({}))
        self.assertEqual(initial.solve(TASK)['status'], 'needs_examples')
        self.assertIsNone(initial.resume_contract())
        _, saved = self.seed()
        solver = self.solver(ScriptedModel({}), resume=saved)
        retained = solver._read_retained_contract(TASK, [], 'user')
        self.assertIsNone(solver.plan(TASK, [], previous='explicit failed contract', retained=retained))

    def test_latest_revision_and_retired_property_are_exported_not_old_snapshot(self):
        solver, _ = self.seed()
        spec = solver._continuity_spec
        old_scope = spec['check_scope']
        book = solver._checkbooks[old_scope]
        book.revise(2, 4, evidence={'kind': 'fixture revision'})
        solver._register_revision(book, old_scope)
        spec.update(checks=book.checks, check_scope=book.scope_id, verify=None, reference=None, given_seen=True,
                    input=99, property_cache={'untrusted': True})
        saved = solver.resume_contract()
        self.assertEqual(saved['check_scope'], book.scope_id)
        self.assertEqual(saved['spec']['input'], 99)
        self.assertIsNone(saved['spec']['verify'])
        self.assertIsNone(saved['spec']['reference'])
        self.assertTrue(saved['spec']['given_seen'])
        self.assertNotIn('property_cache', saved['spec'])

    def test_unexportable_pending_obligations_and_oversized_checkpoints_fail_closed(self):
        for field, value in (('disputed_hand', [{'input': 3, 'hand': 4, 'blind': 9}]),
                             ('property_dispute', {'reason': 'unsettled'}), ('input', 'x' * 65536)):
            with self.subTest(field=field):
                solver, _ = self.seed()
                solver._continuity_spec[field] = value
                self.assertIsNone(solver.resume_contract())
        solver, _ = self.seed()
        solver._voices[(TASK, 'given', 'input')] = [('auditor', 9, 'source')]
        self.assertIsNone(solver.resume_contract())

    def test_explicit_settlement_void_never_exports_prior_contract(self):
        _, saved = self.seed()
        solver = self.solver(ScriptedModel({}), resume=saved)
        with mock.patch.object(solver, '_settle_given', return_value={'reason': 'unsettled contract'}):
            result = solver.solve(TASK)
        self.assertEqual(result['status'], 'incomplete')
        self.assertIsNone(solver.resume_contract())

    def test_pending_blind_set_aside_is_not_exported_at_budget_boundary(self):
        plan = dict(PLAN, checks=CHECKS + [{'input': 3, 'expected': 4}])
        solver = self.solver(ScriptedModel(plan), calls=20)
        solver.auditor = Metered(ScriptedModel({'expected': [2, 3, 99]}), solver.actor.counter)
        with mock.patch.object(solver, 'attempt', side_effect=CallsExhausted('fixture boundary')):
            result = solver.solve(TASK)
        self.assertEqual(result['status'], 'incomplete')
        self.assertTrue(solver._continuity_spec.get('disputed_hand'))
        self.assertIsNone(solver.resume_contract())

    def test_retained_contract_reruns_current_oracle_instead_of_restoring_old_support(self):
        observed = []
        def oracle(prompt, value):
            observed.append((prompt, value))
            return {'expected': value + 1}
        _, saved = self.seed(oracle=oracle, oracle_id='increment-v1')
        self.assertEqual(len(observed), 2)
        solver = self.solver(ScriptedModel({}), resume=saved, calls=2,
                             oracle=oracle, oracle_id='increment-v1')
        solver.solve(TASK)
        self.assertEqual(len(observed), 4)
        self.assertEqual(observed[:2], observed[2:])
        self.assertEqual(solver.actor.counter['used'], 2)

    def test_retained_contract_does_not_requalify_a_refuted_candidate(self):
        first, saved = self.seed()
        scope = saved['check_scope']
        outcome = first.admit(TASK, CHECKS, CORRECT, 'authored method', check_scope=scope)
        artifact = outcome['artifact']
        first.ws.refute(artifact, 'authored control refutation')
        solver = self.solver(ScriptedModel({}), resume=saved, calls=2, ws=first.ws)
        solver._resume_candidates = {scope: [artifact]}
        result = solver.solve(TASK)
        self.assertNotEqual(result['status'], 'checked')
        self.assertIsNone(result.get('artifact'))
        self.assertEqual(solver._execution_used, 0)

    def test_final_property_overrule_is_not_exported_as_a_live_verifier(self):
        plan = dict(PLAN, input=5, verify='def verify(x, y):\n    return x < 5\n')
        solver = self.solver(ScriptedModel(plan))
        def reply(context):
            history = context['history']
            if history and history[-1]['observation'].get('artifact'):
                return {'kind': 'answer', 'answer': {'artifact': history[-1]['observation']['artifact']}}
            return remember_action(CORRECT)
        solver.actor = Metered(ScriptedModel(reply), solver.actor.counter)
        solver.auditor = Metered(ScriptedModel(lambda c: {'expected': [x + 1 for x in c['inputs']]}),
                                 solver.actor.counter)
        # Isolate the final requested-input property gate from the earlier property probe stage.
        with mock.patch.object(solver, 'property_test', return_value=0):
            result = solver.solve(TASK)
        self.assertEqual(result['status'], 'checked', result)
        self.assertIn('verifier overruled', result['property_evidence']['resolution'])
        self.assertIsNone(solver.resume_contract())


if __name__ == '__main__':
    unittest.main()
