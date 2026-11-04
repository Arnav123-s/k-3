"""Integration regressions for finite-check evidence and progress controls.

All replies and execution are local fixtures. These checks cover host behavior,
not model capability or task-solving performance.
"""

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from kuttaka_runtime.solve import MAX_DEPTH, Metered, Solver, Workspace
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import local_executor


def check(value, expected):
    return {'input': value, 'expected': expected}


def find_action():
    return {'kind': 'experiment', 'tool': 'find_methods',
            'input': {'query': 'task', 'limit': 4},
            'predictions': [{'hypothesis': 'a method may apply', 'outcome': 'method list'}]}


def remember_action(source, name='method', description='method'):
    return {'kind': 'experiment', 'tool': 'remember_method',
            'input': {'source': source, 'name': name, 'description': description},
            'predictions': [{'hypothesis': 'candidate is retained', 'outcome': 'artifact id'}]}


class ScriptedModel:
    identity = {'model': 'local-script'}
    last_usage = {'input_tokens': 2, 'output_tokens': 1}

    def __init__(self, reply):
        self.reply = reply
        self.contexts = []
        self.calls = 0

    def propose(self, context):
        self.contexts.append(context)
        self.calls += 1
        return self.reply(context) if callable(self.reply) else self.reply


class SequenceModel(ScriptedModel):
    def __init__(self, replies, *, fallback=None):
        super().__init__(None)
        self.replies = list(replies)
        self.fallback = fallback or {'kind': 'unresolved', 'reason': 'script exhausted'}

    def propose(self, context):
        self.contexts.append(context)
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else self.fallback
        if reply.get('kind') == 'answer' and not reply.get('answer'):
            history = context.get('history', [])
            observation = history[-1].get('observation', {}) if history else {}
            artifact = observation.get('artifact')
            if not artifact:
                fit = next((row for row in observation.get('methods', []) if row.get('fits_this_task')), None)
                artifact = fit.get('id') if fit else None
            if artifact:
                return {'kind': 'answer', 'answer': {'artifact': artifact}}
            return {'kind': 'unresolved', 'reason': 'no artifact in latest observation'}
        return reply


class IntegrationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.workspace = Workspace(self.root / 'workspace')
        self.counter = {'used': 0, 'limit': 80}

    def make_solver(self, planner, actor, *, splitter=None, auditor=None, executor=None, limit=80):
        self.counter.update(used=0, limit=limit)
        return Solver(self.workspace, Metered(planner, self.counter), Metered(actor, self.counter),
                      executor or local_executor,
                      splitter=Metered(splitter, self.counter) if splitter else None,
                      auditor=Metered(auditor, self.counter) if auditor else None,
                      say=lambda _: None)

    @staticmethod
    def plan(contract='run(x) returns x+1', checks=None, input_value=None):
        return {'contract': contract,
                'checks': checks if checks is not None else [check(1, 2)],
                'input': input_value}

    def test_more_than_twelve_user_examples_are_preserved(self):
        anchors = [check(i, i + 1) for i in range(20)]
        proposed = [check(100 + i, 101 + i) for i in range(3)]
        planner = ScriptedModel(self.plan(checks=proposed))
        actor = ScriptedModel({'kind': 'unresolved', 'reason': 'fixture stop'})
        solver = self.make_solver(planner, actor)

        spec = solver.plan('increment each input', anchors)

        self.assertEqual(spec['checks'][:20], anchors)
        self.assertEqual(len(spec['checks']), 23)
        book = solver._checkbooks[spec['check_scope']]
        self.assertEqual(book.metadata['admitted_model_checks'], 3)

    def test_contradictory_user_anchors_return_needs_examples_before_any_call(self):
        planner = ScriptedModel(self.plan())
        actor = ScriptedModel({'kind': 'unresolved', 'reason': 'must not run'})
        solver = self.make_solver(planner, actor)

        result = solver.solve('increment', [check(1, 2), check(1, 99)])

        self.assertEqual(result['status'], 'needs_examples')
        self.assertEqual((planner.calls, actor.calls, self.counter['used']), (0, 0, 0))
        events = Journal.read(result['solve_journal'])
        self.assertTrue(any(event['kind'] == 'solve_constraints_rejected' for event in events))

    def test_model_conflict_is_recorded_but_user_anchor_stays_active(self):
        planner = ScriptedModel(self.plan(checks=[check(1, 999), check(2, 3)]))
        actor = ScriptedModel({'kind': 'unresolved', 'reason': 'fixture stop'})
        solver = self.make_solver(planner, actor)

        result = solver.solve('increment', [check(1, 2)], depth=MAX_DEPTH)

        evidence = result['check_evidence']
        self.assertIn(check(1, 2), evidence['checks'])
        self.assertNotIn(check(1, 999), evidence['checks'])
        self.assertEqual(evidence['conflicts'][0]['rejected_expected'], 999)
        self.assertEqual(evidence['records'][0]['origin'], 'user')

    def test_recursive_subtask_checks_keep_model_provenance(self):
        parent = self.plan('combine two operations', [check([1], 2)])
        child = self.plan('run(x) returns x squared', [check(2, 4)])

        def plan_reply(context):
            return child if context['task'] == 'square a value' else parent

        planner = ScriptedModel(plan_reply)
        splitter = ScriptedModel({'subtasks': [{'request': 'square a value', 'checks': [check(2, 4)]}]})
        actor = ScriptedModel({'kind': 'unresolved', 'reason': 'fixture stop'})
        solver = self.make_solver(planner, actor, splitter=splitter)

        result = solver.solve('combine two operations')

        self.assertEqual(len(result['subtasks']), 1)
        child_result = result['subtasks'][0]
        self.assertEqual(child_result['checks_by'], 'model')
        self.assertEqual(child_result['check_evidence']['records'][0]['origin'], 'model')

    def test_blind_check_correction_keeps_both_scopes_and_heuristic_support(self):
        planner = ScriptedModel(self.plan('run(x) returns twice x', [check(1, 2), check(3, 7)]))
        candidate_attempt = lambda source, reason: [
            find_action(), remember_action(source), {'kind': 'answer', 'answer': {}},
            {'kind': 'unresolved', 'reason': reason}]
        actor = SequenceModel(
            candidate_attempt('def run(x):\n    return 2*x\n', 'initial checks fail')
            + candidate_attempt('def run(x):\n    return 2*x\n', 'repair still fails')
            + candidate_attempt('def run(x):\n    return 2*x\n', 'corrected checks pass'))

        class Auditor(ScriptedModel):
            def propose(self, context):
                self.contexts.append(context)
                self.calls += 1
                return {'expected': [6] * len(context['inputs'])}

        auditor = Auditor(None)
        solver = self.make_solver(planner, actor, auditor=auditor)

        result = solver.solve('double a number', depth=MAX_DEPTH)

        self.assertEqual(result['status'], 'checked', result)
        events = Journal.read(result['solve_journal'])
        revised = [event['body']['revision'] for event in events
                   if event['kind'] == 'solve_check_revised']
        self.assertEqual(len(revised), 1)
        revision = revised[0]
        self.assertEqual(revision['before']['expected'], 7)
        self.assertEqual(revision['after']['expected'], 6)
        self.assertNotEqual(revision['scope_before'], revision['scope_after'])
        self.assertEqual(revision['support'], 'heuristic_not_verified')
        scopes = [event['body']['scope'] for event in events if event['kind'] == 'solve_check_contract']
        self.assertIn(revision['scope_before'], scopes)
        self.assertIn(revision['scope_after'], scopes)

    def test_same_active_subproblem_is_blocked_before_actor_recursion(self):
        splitter = ScriptedModel({'subtasks': [{'request': 'repeat this task', 'checks': [check(1, 1)]}]})
        # Root and child need identical request and contract/checks to hit the exact active key.
        planner = ScriptedModel(lambda context: self.plan('same contract', [check(1, 1)]))
        actor = ScriptedModel({'kind': 'unresolved', 'reason': 'root cannot solve'})
        # Root planning, root actor, split proposal, and child planning exhaust
        # the shared call limit. The cycle check must run before child actor use.
        solver = self.make_solver(planner, actor, splitter=splitter, limit=4)

        result = solver.solve('repeat this task')

        self.assertEqual(result['subtasks'][0]['status'], 'incomplete')
        self.assertIn('repeats an active goal', result['subtasks'][0]['reason'])
        self.assertEqual(actor.calls, 1)
        events = Journal.read(result['solve_journal'])
        self.assertTrue(any(event['kind'] == 'solve_cycle_blocked' for event in events))

    def test_repeated_admitted_source_is_host_stopped_and_raw_action_is_preserved(self):
        planner = ScriptedModel(self.plan('run(x) returns x', [check(1, 1)]))
        source = 'def run(x):\n    return x\n'
        actor = SequenceModel([find_action(), remember_action(source, 'first', 'one label'),
                               remember_action(source, 'renamed', 'new description')])
        solver = self.make_solver(planner, actor, limit=20)

        result = solver.solve('identity method', depth=MAX_DEPTH)

        events = Journal.read(result['solve_journal'])
        attempts = [event['body'] for event in events if event['kind'] == 'solve_attempt_finished']
        interventions = []
        for attempt in attempts:
            attempt_events = Journal.read(Path(attempt['run']) / 'events.jsonl')
            interventions.extend((attempt, local) for local in attempt_events
                                  if local['kind'] == 'solve_stall_intervention')
        self.assertTrue(interventions)
        attempt, intervention_event = interventions[0]
        # The intervention belongs to an actual charged model attempt in that
        # attempt journal; the persistent strategy loop may make later attempts.
        attempt_events = Journal.read(Path(attempt['run']) / 'events.jsonl')
        self.assertGreaterEqual(sum(event['kind'] == 'model_attempt' for event in attempt_events), 1)
        intervention = intervention_event['body']
        self.assertEqual(intervention['model_action']['input']['name'], 'renamed')
        self.assertEqual(intervention['model_action']['input']['description'], 'new description')
        self.assertEqual(intervention['host_action']['kind'], 'unresolved')
        self.assertIn('same admitted candidate', intervention['reason'])

    def test_two_candidates_keep_the_stronger_partial_when_later_candidate_is_worse(self):
        planner = ScriptedModel(self.plan('piecewise values', [check(1, 1), check(2, 2), check(3, 3)]))
        strong = 'def run(x):\n    return {1: 1, 2: 2}.get(x, 0)\n'
        weak = 'def run(x):\n    return 1 if x == 1 else 0\n'
        actor = SequenceModel([find_action(), remember_action(strong, 'strong'),
                               remember_action(weak, 'weak'),
                               {'kind': 'unresolved', 'reason': 'two partial candidates'}])
        solver = self.make_solver(planner, actor)

        result = solver.solve('piecewise values', depth=MAX_DEPTH)

        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual(result['check_evidence']['checks'], [check(1, 1), check(2, 2), check(3, 3)])
        # The persistent strategy loop may continue after this candidate pair;
        # retain evidence that the first, stronger artifact remains on the frontier.
        solve_events = Journal.read(result['solve_journal'])
        evidence = [event['body'] for event in solve_events if event['kind'] == 'solve_partial_evidence']
        self.assertEqual(len(evidence), 2)
        self.assertEqual(max(row['receipt']['passed_count'] for row in evidence), 2)
        strongest = max(evidence, key=lambda row: row['receipt']['passed_count'])
        frontier = result['evidence_frontiers'][strongest['check_scope']]['frontier']
        self.assertIn(strongest['receipt']['artifact_id'], {row['artifact_id'] for row in frontier})

    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_same_scope_replan_preserves_stronger_partial_for_repair(self):
        checks = [check(1, 1), check(2, 2), check(3, 3)]
        planner = ScriptedModel(self.plan('piecewise values', checks))
        actor = ScriptedModel({'kind': 'unresolved', 'reason': 'fixture only'})
        solver = self.make_solver(planner, actor)
        spec = solver.plan('piecewise values', [])
        strong_source = 'def run(x):\n    return {1: 1, 2: 2}.get(x, 0)\n'
        weak_source = 'def run(x):\n    return 1 if x == 1 else 0\n'

        def partial(source, passed, failed):
            return {'source': source, 'passed_checks': checks[:passed],
                    'failed_checks': [{'input': checks[i]['input'],
                                       'expected': checks[i]['expected'], 'got': 0}
                                      for i in failed],
                    'artifact': 'partial-' + str(passed)}

        incomplete = lambda part: {'status': 'incomplete', 'reason': 'scripted partial',
                                   'partial': part, 'artifact': None, 'reused': [],
                                   'task': None, 'run': 'scripted'}
        attempts = iter([incomplete(partial(strong_source, 2, [2])),
                         incomplete(partial(weak_source, 1, [1, 2])),
                         incomplete(partial(weak_source, 1, [1, 2]))])
        repair_requests = []

        def scripted_attempt(request, _checks, _notes=(), *, check_scope=None):
            if '\nSource:\n' in request:
                repair_requests.append(request)
            return next(attempts)

        steps = iter(['replan', 'repair'])
        solver.attempt = scripted_attempt
        solver._next_step = lambda _best, _tried, _spec, _examples, _depth: next(steps, None)

        result = solver._solve_planned('piecewise values', [], 0, spec)

        # Initial plan, the one request for given conditions before code (the plan had no reference or
        # property), and the same-scope replan.
        self.assertEqual(planner.calls, 3)
        self.assertEqual(result['progress_epochs'], [
            {'scope': spec['check_scope'], 'observed_pass_counts': [2, 1, 1]}])
        self.assertEqual(len(repair_requests), 1)
        self.assertIn(strong_source, repair_requests[0])
        self.assertNotIn(weak_source, repair_requests[0])

    def test_shared_budget_exhaustion_is_persisted_as_incomplete(self):
        planner = ScriptedModel(self.plan())
        actor = ScriptedModel({'kind': 'unresolved', 'reason': 'must not get a call'})
        solver = self.make_solver(planner, actor, limit=1)

        result = solver.solve('increment')

        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(self.counter['used'], 1)
        self.assertEqual(actor.calls, 0)
        events = Journal.read(result['solve_journal'])
        finished = [event for event in events if event['kind'] == 'solve_finished']
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]['body']['status'], 'incomplete')

    def test_new_check_scope_does_not_inherit_old_progress_scores(self):
        planner = ScriptedModel(self.plan('run(x) returns twice x', [check(1, 2), check(3, 7)]))
        actor = SequenceModel([find_action(), remember_action('def run(x):\n    return 2*x\n'),
                               {'kind': 'answer', 'answer': {}},
                               {'kind': 'unresolved', 'reason': 'initial contract mismatch'},
                               find_action(), remember_action('def run(x):\n    return 0\n'),
                               {'kind': 'unresolved', 'reason': 'repair candidate is worse'},
                               find_action(), remember_action('def run(x):\n    return 0\n'),
                               {'kind': 'unresolved', 'reason': 'later candidate is worse'}])

        class Auditor(ScriptedModel):
            def propose(self, context):
                self.calls += 1
                return {'expected': [6] * len(context['inputs'])}

        solver = self.make_solver(planner, actor, auditor=Auditor(None))

        result = solver.solve('double a number', depth=MAX_DEPTH)

        scopes = result['evidence_frontiers']
        self.assertEqual(len(scopes), 2)
        events = Journal.read(result['solve_journal'])
        revision = next(event['body']['revision'] for event in events
                        if event['kind'] == 'solve_check_revised')
        old_scope, new_scope = revision['scope_before'], revision['scope_after']
        self.assertNotEqual(old_scope, new_scope)
        self.assertEqual(scopes[old_scope]['check_count'], 2)
        self.assertEqual(scopes[new_scope]['check_count'], 2)
        self.assertEqual(scopes[old_scope]['best_count'], 1)
        self.assertEqual(scopes[new_scope]['best_count'], 0)

    def test_job_execution_cap_includes_initial_check_and_partial_diagnostics(self):
        planner = ScriptedModel(self.plan('returns two', [check(1, 2)]))
        actor = SequenceModel([find_action(), remember_action('def run(x):\n    return 0\n'),
                               {'kind': 'answer', 'answer': {}},
                               {'kind': 'unresolved', 'reason': 'candidate is wrong'}])
        executor_calls = []

        def counting_executor(job):
            executor_calls.append(job)
            return local_executor(job)

        solver = self.make_solver(planner, actor, executor=counting_executor, limit=20)
        solver.max_executions = 1

        result = solver.solve('return two for one', depth=MAX_DEPTH)

        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual(result['execution_attempts'], 1)
        self.assertEqual(len(executor_calls), 1)
        events = Journal.read(result['solve_journal'])
        self.assertEqual(sum(event['kind'] == 'solve_execution_intent' for event in events), 1)

    def test_unchanged_oracle_support_survives_an_unrelated_scope_revision(self):
        planner = ScriptedModel(self.plan('run(x) returns x', [check(1, 1), check(2, 7)]))

        class Auditor(ScriptedModel):
            def propose(self, context):
                self.calls += 1
                self.contexts.append(context)
                return {'expected': [6] * len(context['inputs'])}

        auditor = Auditor(None)
        oracle = lambda _prompt, value: {'expected': 1} if value == 1 else None
        solver = self.make_solver(planner, ScriptedModel({'kind': 'unresolved', 'reason': 'fixture'}),
                                  auditor=auditor)
        solver.check_oracle, solver.oracle_id = oracle, 'local-check-v1'
        spec = solver.plan('identity', [])
        solver._resolve_checks('identity', spec)
        old_scope = spec['check_scope']
        old_book = solver._checkbooks[old_scope]
        supported_id = next(row['id'] for row in old_book.records if row['input'] == 1)

        revised = solver.audit('identity', spec, {'artifact': 'candidate', 'failed_checks': [
            {'input': 1, 'expected': 1, 'got': 99},
            {'input': 2, 'expected': 7, 'got': 6},
        ]}, [])

        self.assertEqual(revised, 1)
        self.assertEqual(auditor.contexts[0]['inputs'], [2])
        self.assertNotEqual(spec['check_scope'], old_scope)
        supported_ids = solver._snapshot(spec)['oracle_supported_check_ids']
        self.assertIn(supported_id, supported_ids)
        self.assertEqual(solver.audit('identity', spec, {'artifact': 'candidate', 'failed_checks': [
            {'input': 1, 'expected': 1, 'got': 99},
        ]}, []), 0)
        self.assertEqual(auditor.calls, 1)

    # The blind derivation before code (tests/test_blind_derivation.py) is off here: this test exercises a mechanism
    # after code, and its scenario needs the hand checks to reach the attempt as planned.
    @mock.patch.object(Solver, 'derive_blind', False)
    def test_infrastructure_failure_is_not_an_expected_value_for_blind_audit(self):
        planner = ScriptedModel(self.plan('run(x) returns x', [check(1, 1), check(2, 2)]))
        actor = SequenceModel([find_action(), remember_action('def run(x):\n    return x\n'),
                               {'kind': 'unresolved', 'reason': 'stop after candidate'}])

        def infra_executor(job):
            return {'status': 'timeout', 'exit_code': None, 'output': '',
                    'diagnostics': 'local fixture timeout', 'dependency_calls': []}

        class Auditor(ScriptedModel):
            def propose(self, context):
                self.calls += 1
                self.contexts.append(context)
                return {'expected': [1] * len(context['inputs'])}

        auditor = Auditor(None)
        solver = self.make_solver(planner, actor, auditor=auditor, executor=infra_executor)

        result = solver.solve('identity method', depth=MAX_DEPTH)

        self.assertNotEqual(result['status'], 'checked')
        self.assertEqual(auditor.calls, 0)
        self.assertNotIn('got', json.dumps(auditor.contexts))
        events = Journal.read(result['solve_journal'])
        self.assertFalse(any(event['kind'] == 'solve_check_revised' for event in events))


if __name__ == '__main__':
    unittest.main()
