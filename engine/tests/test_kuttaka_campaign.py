import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.campaign import (Ceiling, CeilingReached, CampaignIneligible, _account_attempts, _combine_accounting,
                                      _manifest_digest, _reply_gate_valid,
                                      compare, finished_tasks, run_campaign, score_campaign, spent_calls)
from kuttaka_runtime.execution import RUNNER, PythonExecutor
from kuttaka_runtime.models import parse_action
from kuttaka_runtime.runtime import Budget, TransportFailure, normalize_action
from kuttaka_runtime.store import Journal


def local_executor(job):
    """Test-only stand-in for the Docker executor: same runner, plain subprocess."""
    proc = subprocess.run([sys.executable, '-I', '-c', RUNNER], input=json.dumps(job).encode(),
                          capture_output=True, timeout=20)
    output = proc.stdout.decode()
    dependency_calls = []
    if proc.returncode == 0:
        output, dependency_calls = PythonExecutor._parse_runner_output(
            output, libraries_present='libraries' in job)
    return {'status': 'completed', 'exit_code': proc.returncode,
            'output': output, 'diagnostics': proc.stderr.decode(),
            'dependency_calls': dependency_calls}


GCD = 'def run(p):\n    a, b = p\n    while b:\n        a, b = b, a % b\n    return a\n'
STREAM = [
    {'id': 't1', 'chain': 'c', 'request': 'Write run([a,b]) returning gcd(a,b).',
     'checks': [{'input': [6, 9], 'expected': 3}, {'input': [7, 5], 'expected': 1}]},
    {'id': 't2', 'chain': 'c', 'request': 'Write run([a,b]) returning lcm(a,b).',
     'checks': [{'input': [4, 6], 'expected': 12}, {'input': [3, 5], 'expected': 15}]},
]


class Scripted:
    """Stands in for the model; each reply is computed from the visible context only."""
    identity = {'model': 'scripted-test'}
    last_usage = {'input_tokens': 10, 'output_tokens': 5}

    def __init__(self):
        self.calls = 0

    def propose(self, context):
        self.calls += 1
        history, request = context['history'], context['request']
        if not history:
            return {'kind': 'experiment', 'tool': 'find_methods', 'input': {'query': 'gcd', 'limit': 5},
                    'predictions': [{'hypothesis': 'memory may hold gcd', 'outcome': 'a list'}]}
        last = history[-1]['observation']
        if 'methods' in last:
            found = [m['id'] for m in last['methods'] if m['qualified_scopes']]
            if 'gcd' in request:
                source, uses, name = GCD, [], 'gcd'
            elif found:
                source = ('def run(p):\n    g = use("%s")(p)\n    return p[0] * p[1] // g\n' % found[0])
                uses, name = found, 'lcm via gcd'
            else:
                source = ('def run(p):\n    a, b = p\n    x, y = a, b\n    while y:\n        x, y = y, x % y\n'
                          '    return a * b // x\n')
                uses, name = [], 'lcm'
            body = {'name': name, 'description': name, 'source': source}
            if uses:
                body['uses'] = uses
            return {'kind': 'experiment', 'tool': 'remember_method', 'input': body,
                    'predictions': [{'hypothesis': 'stored', 'outcome': 'artifact id'}]}
        if 'artifact' in last:
            return {'kind': 'answer', 'answer': {'artifact': last['artifact']}}
        return {'kind': 'unresolved', 'reason': 'unexpected'}


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def run_arm(self, arm, name=None, replay_from=None, model=None):
        model = model or (None if replay_from else Ceiling(Scripted(), 50))
        return run_campaign(STREAM, self.dir / (name or arm), arm, model, budget=Budget(6, 12),
                            executor=local_executor, replay_from=replay_from), model

    def test_retained_memory_composes_by_artifact_id(self):
        rows, _ = self.run_arm('KUTTAKA')
        self.assertEqual([r['status'] for r in rows], ['checked', 'checked'])
        self.assertEqual(rows[0]['reused_artifacts'], [])
        self.assertEqual(rows[1]['reused_artifacts'], [rows[0]['artifact']])
        self.assertEqual(rows[1]['declared_reused_artifacts'], [rows[0]['artifact']])
        self.assertEqual(rows[1]['executed_reused_artifacts'], [rows[0]['artifact']])

    def test_model_alone_sees_no_earlier_methods(self):
        rows, _ = self.run_arm('MODEL_ALONE')
        self.assertEqual([r['status'] for r in rows], ['checked', 'checked'])
        self.assertEqual(rows[1]['reused_artifacts'], [])

    def test_replay_reproduces_journal_hashes_without_a_model(self):
        rows, _ = self.run_arm('KUTTAKA')
        again, _ = self.run_arm('KUTTAKA', name='replay', replay_from=self.dir / 'KUTTAKA')
        self.assertEqual([r['journal_sha256'] for r in again], [r['journal_sha256'] for r in rows])

    def test_ceiling_stops_the_campaign(self):
        model = Ceiling(Scripted(), 3)
        rows, _ = self.run_arm('KUTTAKA', model=model)
        self.assertEqual(rows[-1]['status'], 'ceiling_reached')
        self.assertEqual(model.used, 3)
        with self.assertRaises(CeilingReached):
            model.propose({})

    def test_holdout_scoring_and_comparison(self):
        k, _ = self.run_arm('KUTTAKA')
        a, _ = self.run_arm('MODEL_ALONE')
        holdout = {'t1': [{'input': [12, 18], 'expected': 6}, {'input': [9, 6], 'expected': 3},
                          {'input': [5, 0], 'expected': 5}, {'input': [17, 17], 'expected': 17},
                          {'input': [13, 2], 'expected': 1}],
                   't2': [{'input': [21, 6], 'expected': 42}, {'input': [0, 5], 'expected': 0},
                          {'input': [7, 3], 'expected': 21}, {'input': [1, 11], 'expected': 11},
                          {'input': [8, 8], 'expected': 8}]}
        sk = score_campaign(self.dir / 'KUTTAKA', STREAM, holdout, local_executor)
        sa = score_campaign(self.dir / 'MODEL_ALONE', STREAM, holdout, local_executor)
        self.assertTrue(all(r['holdout_passed'] for r in sk))
        result = compare(sk, sa)
        self.assertEqual(result['solved'], {'KUTTAKA': 2, 'MODEL_ALONE': 2})
        self.assertTrue(result['claim_eligibility']['eligible'])
        self.assertEqual(result['A4_cooperative_invocation_of_earlier_artifacts'], {
            'reusing': 1, 'solved_later_in_chain': 1,
            'evidence': 'sandbox-reported cooperative invocation; not causal proof'})

    def test_declared_but_unused_dependency_is_not_counted_as_invoked(self):
        class DeclaresUnused(Scripted):
            def propose(self, context):
                history, request = context['history'], context['request']
                if 'lcm' in request and history and 'methods' in history[-1]['observation']:
                    found = [method['id'] for method in history[-1]['observation']['methods']
                             if method['qualified_scopes']]
                    body = {'name': 'lcm without calling declared gcd',
                            'description': 'inline lcm',
                            'source': ('def run(p):\n    a, b = p\n    x, y = a, b\n'
                                       '    while y:\n        x, y = y, x % y\n'
                                       '    return a * b // x\n'),
                            'uses': found}
                    return {'kind': 'experiment', 'tool': 'remember_method', 'input': body,
                            'predictions': [{'hypothesis': 'stored', 'outcome': 'artifact id'}]}
                return super().propose(context)
        rows = run_campaign(STREAM, Path(tempfile.mkdtemp()) / 'unused', 'KUTTAKA',
                            Ceiling(DeclaresUnused(), 50), budget=Budget(6, 12),
                            executor=local_executor)
        self.assertEqual(rows[1]['status'], 'checked')
        self.assertEqual(rows[1]['declared_reused_artifacts'], [rows[0]['artifact']])
        self.assertEqual(rows[1]['reused_artifacts'], [rows[0]['artifact']])
        self.assertEqual(rows[1]['executed_reused_artifacts'], [])

    def test_unsupported_claim_is_counted(self):
        self.run_arm('KUTTAKA', name='KUTTAKA', replay_from=None)
        # Score the complete two-task campaign and make one required case fail.
        holdout = {'t1': [{'input': [12, 18], 'expected': 7}, {'input': [9, 6], 'expected': 3},
                          {'input': [5, 0], 'expected': 5}, {'input': [17, 17], 'expected': 17},
                          {'input': [13, 2], 'expected': 1}],
                   't2': [{'input': [21, 6], 'expected': 42}, {'input': [0, 5], 'expected': 0},
                          {'input': [7, 3], 'expected': 21}, {'input': [1, 11], 'expected': 11},
                          {'input': [8, 8], 'expected': 8}]}
        scored = score_campaign(self.dir / 'KUTTAKA', STREAM, holdout, local_executor)
        self.assertTrue(scored[0]['unsupported'])


class NormalizeActionTests(unittest.TestCase):
    """Reply shapes observed in the 2026-10-28 live canary (claude/astra-dev-canary 8b29057)."""

    def test_bare_artifact_is_an_answer(self):
        self.assertEqual(normalize_action({'artifact': 'a' * 64}),
                         {'kind': 'answer', 'answer': {'artifact': 'a' * 64}})

    def test_missing_predictions_become_empty(self):
        action = {'kind': 'experiment', 'tool': 'find_methods', 'input': {'query': '', 'limit': 5}}
        self.assertEqual(normalize_action(action)['predictions'], [])

    def test_other_shapes_unchanged(self):
        for action in ({'kind': 'experiment', 'tool': 'x', 'input': {}, 'predictions': [], 'extra': 1},
                       {'artifact': 'a', 'note': 'b'}, 'text', None):
            self.assertEqual(normalize_action(action), action)

    def test_validated_method_is_submitted_without_a_model_turn(self):
        class Validates(Scripted):
            def propose(self, context):
                last = context['history'][-1]['observation'] if context['history'] else {}
                if isinstance(last, dict) and last.get('status') == 'stored':
                    return {'kind': 'experiment', 'tool': 'validate_method',
                            'input': {'artifact': last['artifact']}}  # no predictions, as observed live
                return super().propose(context)
        rows = run_campaign(STREAM[:1], Path(tempfile.mkdtemp()) / 'k', 'KUTTAKA', Ceiling(Validates(), 9),
                            budget=Budget(6, 12), executor=local_executor, reply_repair=True)
        self.assertEqual(rows[0]['status'], 'checked')
        self.assertEqual(rows[0]['model_calls'], 3)  # find, remember, validate; no answer turn


class ReplayErrorTests(unittest.TestCase):
    def test_recorded_model_failure_replays_identically(self):
        class FailsSecond(Scripted):
            def propose(self, context):
                if self.calls == 1:
                    self.calls += 1
                    raise TransportFailure('ConnectionResetError')
                return super().propose(context)
        base = Path(tempfile.mkdtemp())
        rows = run_campaign(STREAM, base / 'run', 'KUTTAKA', Ceiling(FailsSecond(), 20),
                            budget=Budget(6, 12), executor=local_executor)
        self.assertEqual(rows[0]['status'], 'checked')  # the failed call is spent; the task continues
        self.assertEqual(rows[0]['model_calls'], 4)
        again = run_campaign(STREAM, base / 'replay', 'KUTTAKA', None, budget=Budget(6, 12),
                             executor=local_executor, replay_from=base / 'run')
        self.assertEqual([r['journal_sha256'] for r in again], [r['journal_sha256'] for r in rows])


    def test_hard_model_failure_ends_task_and_replays(self):
        class Hard(Scripted):
            def propose(self, context):
                raise PermissionError('HTTP 401')
        base = Path(tempfile.mkdtemp())
        rows = run_campaign(STREAM[:1], base / 'run', 'KUTTAKA', Ceiling(Hard(), 5),
                            budget=Budget(6, 12), executor=local_executor)
        self.assertEqual((rows[0]['status'], rows[0]['model_calls']), ('incomplete', 1))
        again = run_campaign(STREAM[:1], base / 'replay', 'KUTTAKA', None, budget=Budget(6, 12),
                             executor=local_executor, replay_from=base / 'run')
        self.assertEqual(again[0]['journal_sha256'], rows[0]['journal_sha256'])


class ResumeTests(unittest.TestCase):
    def test_resume_after_crash_preserves_outcomes_and_accounts_spent_attempts(self):
        class Crashes(Scripted):
            def propose(self, context):
                if self.calls == 4:  # first call of task 2: the container dies mid-task
                    raise KeyboardInterrupt
                return super().propose(context)
        base = Path(tempfile.mkdtemp())
        with self.assertRaises(KeyboardInterrupt):
            run_campaign(STREAM, base / 'crashed', 'KUTTAKA', Ceiling(Crashes(), 20),
                         budget=Budget(6, 12), executor=local_executor)
        self.assertEqual(finished_tasks(base / 'crashed'), {'t1'})
        self.assertEqual(spent_calls(base / "crashed"), 5)  # includes the attempt cut by the crash
        live = Scripted()
        live.calls = 3
        model = Ceiling(live, 20, used=spent_calls(base / 'crashed'))
        rows = run_campaign(STREAM, base / 'resumed', 'KUTTAKA', model, budget=Budget(6, 12),
                            executor=local_executor, resume_from=base / 'crashed')
        clean, _ = CampaignTests.run_arm(type('T', (), {'dir': base})(), 'KUTTAKA', name='clean')
        self.assertEqual(rows[0]['journal_sha256'], clean[0]['journal_sha256'])
        self.assertEqual([(r['status'], r['artifact']) for r in rows],
                         [(r['status'], r['artifact']) for r in clean])
        self.assertEqual(model.used, 5 + 3)  # spent calls count against the ceiling

    def test_resume_rejects_model_or_arm_binding_changes(self):
        base = Path(tempfile.mkdtemp())
        run_campaign(STREAM, base / 'source', 'KUTTAKA', Ceiling(Scripted(), 50), budget=Budget(6, 12),
                     executor=local_executor)
        class OtherModel(Scripted):
            identity = {'model': 'different-model'}
        with self.assertRaises(CampaignIneligible):
            run_campaign(STREAM, base / 'wrong-model', 'KUTTAKA', Ceiling(OtherModel(), 50, used=6),
                         budget=Budget(6, 12), executor=local_executor, resume_from=base / 'source')
        with self.assertRaises(CampaignIneligible):
            run_campaign(STREAM, base / 'wrong-arm', 'MODEL_ALONE', Ceiling(Scripted(), 50, used=6),
                         budget=Budget(6, 12), executor=local_executor, resume_from=base / 'source')

    def test_spent_calls_fails_closed_on_corrupt_journal(self):
        base = Path(tempfile.mkdtemp())
        run_campaign(STREAM[:1], base / 'run', 'KUTTAKA', Ceiling(Scripted(), 20),
                     budget=Budget(6, 12), executor=local_executor)
        path = base / 'run' / 'runs' / 't1' / 'events.jsonl'
        text = path.read_text()
        path.write_text(text.replace('model_attempt', 'model_attampt', 1))
        with self.assertRaises(CampaignIneligible):
            spent_calls(base / 'run')

    def test_chained_resume_carries_ancestor_attempts_and_task_limits(self):
        class CrashAtNextTaskStart(Scripted):
            def propose(self, context):
                if self.calls == 3:
                    self.calls += 1
                    raise KeyboardInterrupt
                return super().propose(context)
        base = Path(tempfile.mkdtemp())
        with self.assertRaises(KeyboardInterrupt):
            run_campaign(STREAM, base / 'first', 'KUTTAKA', Ceiling(CrashAtNextTaskStart(), 50),
                         budget=Budget(6, 12), executor=local_executor)
        first_spent = spent_calls(base / 'first')
        self.assertEqual(first_spent, 4)
        second_model = CrashAtNextTaskStart()
        second_model.calls = 3
        with self.assertRaises(KeyboardInterrupt):
            run_campaign(STREAM, base / 'second', 'KUTTAKA',
                         Ceiling(second_model, 50, used=first_spent),
                         budget=Budget(6, 12), executor=local_executor, resume_from=base / 'first')
        second_spent = spent_calls(base / 'second')
        self.assertEqual(second_spent, 5)  # includes one ancestor attempt omitted from new journals
        model = Ceiling(Scripted(), 50, used=second_spent)
        rows = run_campaign(STREAM, base / 'third', 'KUTTAKA', model, budget=Budget(6, 12),
                            executor=local_executor, resume_from=base / 'second')
        self.assertEqual([row['model_calls'] for row in rows], [3, 5])
        self.assertEqual(rows[1]['tool_calls'], 3)
        self.assertEqual(spent_calls(base / 'third'), 8)
        manifest = json.loads((base / 'third' / 'campaign.json').read_text())
        self.assertFalse(manifest['claim_context_reconstructed'])

    def test_resume_subtracts_prior_tool_and_execution_budgets(self):
        class CrashAfterValidation(Scripted):
            def propose(self, context):
                history, request = context['history'], context['request']
                if 'lcm' in request and len(history) == 2:
                    last = history[-1]['observation']
                    if isinstance(last, dict) and last.get('status') == 'stored':
                        self.calls += 1
                        return {'kind': 'experiment', 'tool': 'validate_method',
                                'input': {'artifact': last['artifact']},
                                'predictions': [{'hypothesis': 'validation', 'outcome': 'checked'}]}
                if 'lcm' in request and len(history) == 3:
                    self.calls += 1
                    raise KeyboardInterrupt
                return super().propose(context)
        base = Path(tempfile.mkdtemp())
        budget = Budget(6, 12)
        with self.assertRaises(KeyboardInterrupt):
            run_campaign(STREAM, base / 'first', 'KUTTAKA', Ceiling(CrashAfterValidation(), 50),
                         budget=budget, executor=local_executor)
        first = Journal.read(base / 'first' / 'runs' / 't2' / 'events.jsonl')
        first_account = _account_attempts(first)
        self.assertGreater(first_account['tool_calls'], 0)
        self.assertGreater(first_account['execution_attempts'], 0)
        source_model = Scripted()
        source_model.calls = 3
        rows = run_campaign(STREAM, base / 'resumed', 'KUTTAKA', Ceiling(source_model, 50, used=spent_calls(base / 'first')),
                            budget=budget, executor=local_executor, resume_from=base / 'first')
        resumed_events = Journal.read(base / 'resumed' / 'runs' / 't2' / 'events.jsonl')
        start = next(event['body'] for event in resumed_events if event['kind'] == 'start')
        task = next(event['body'] for event in resumed_events if event['kind'] == 'program_task')
        self.assertEqual(start['budget']['tool_calls'], budget.tool_calls - first_account['tool_calls'])
        self.assertEqual(task['execution_limit'], 64 - first_account['execution_attempts'])
        self.assertGreaterEqual(rows[1]['model_calls'], first_account['attempts'])


class OutageTests(unittest.TestCase):
    def test_consecutive_transport_failures_stop_the_arm_resumably(self):
        class Down(Scripted):
            def propose(self, context):
                if self.calls >= 3:  # endpoint goes down after task 1
                    self.calls += 1
                    raise TransportFailure('URLError')
                return super().propose(context)
        base = Path(tempfile.mkdtemp())
        model = Ceiling(Down(), 50, outage_after=5)
        rows = run_campaign(STREAM, base / 'out', 'KUTTAKA', model, budget=Budget(6, 12), executor=local_executor)
        self.assertEqual(model.used, 3 + 5)  # no call is spent after the fifth failure in a row
        self.assertEqual(finished_tasks(base / 'out'), {'t1'})  # task 2 stays unfinished for resume
        self.assertEqual(rows[-1]['status'], 'transport_outage')

    def test_task_outage_limit_tracks_trailing_failure_streak_through_replay(self):
        class FailsFirstTwo(Scripted):
            def propose(self, context):
                if self.calls < 2:
                    self.calls += 1
                    raise TransportFailure('URLError')
                return super().propose(context)
        base = Path(tempfile.mkdtemp())
        rows = run_campaign(STREAM, base / 'run', 'KUTTAKA', Ceiling(FailsFirstTwo(), 20),
                            budget=Budget(2, 12), executor=local_executor)
        self.assertEqual(len(rows), 2)
        events = Journal.read(base / 'run' / 'runs' / 't2' / 'events.jsonl')
        start = next(event['body'] for event in events if event['kind'] == 'start')
        self.assertEqual(start['runtime_options']['outage_limit'], 3)
        replay = run_campaign(STREAM, base / 'replay', 'KUTTAKA', None, budget=Budget(2, 12),
                              executor=local_executor, replay_from=base / 'run')
        self.assertEqual([row['journal_sha256'] for row in replay],
                         [row['journal_sha256'] for row in rows])


class ParseActionTests(unittest.TestCase):
    def test_fenced_and_wrapped_json(self):
        self.assertEqual(parse_action('```json\n{"kind":"answer","answer":1}\n```', parse_mode='extract'),
                         {'kind': 'answer', 'answer': 1})
        self.assertEqual(parse_action('Here it is: {"kind":"answer","answer":2} done', parse_mode='extract'),
                         {'kind': 'answer', 'answer': 2})
        self.assertEqual(parse_action('no json', parse_mode='extract')['kind'], 'invalid')


class CampaignGateTests(unittest.TestCase):
    def test_run_api_rejects_path_traversal_task_ids_before_output_creation(self):
        base = Path(tempfile.mkdtemp())
        out = base / 'escape'
        with self.assertRaises(ValueError):
            run_campaign([{'id': '../escape', 'request': 'x', 'checks': []}], out, 'KUTTAKA',
                         Ceiling(Scripted(), 10), budget=Budget(6, 12), executor=local_executor)
        self.assertFalse(out.exists())

    def test_reply_gate_accepts_exactly_90_percent_and_rejects_below(self):
        self.assertTrue(_reply_gate_valid(9, 10))
        self.assertFalse(_reply_gate_valid(8, 10))

    def test_ceiling_delegates_receipt_and_clears_it_on_refusal(self):
        class Receipts:
            identity = {'model': 'receipt-test'}
            last_response = {'status': 200, 'body': 'ok'}
            last_usage = {'input_tokens': 1, 'output_tokens': 2}
            def propose(self, context):
                return {'kind': 'unresolved', 'reason': 'scripted'}
        model = Ceiling(Receipts(), 1)
        model.propose({})
        self.assertEqual(model.last_response, {'status': 200, 'body': 'ok'})
        self.assertEqual(model.last_usage, {'input_tokens': 1, 'output_tokens': 2})
        with self.assertRaises(CeilingReached):
            model.propose({})
        self.assertIsNone(model.last_response)
        self.assertEqual(model.last_usage, {})
        self.assertEqual(model.used, 1)

    def test_reply_count_excludes_action_schema_rejection(self):
        usage = {'input_tokens': 3, 'output_tokens': 2}
        events = [
            {'kind': 'model_attempt', 'body': {}},
            {'kind': 'model_response', 'body': {'usage': usage}},
            {'kind': 'model_reply', 'body': {}},
            {'kind': 'action_rejected', 'body': {}},
            {'kind': 'model_attempt', 'body': {}},
            {'kind': 'model_response', 'body': {'usage': usage}},
            {'kind': 'model_reply', 'body': {}},
        ]
        account = _account_attempts(events)
        self.assertEqual((account['attempts'], account['replies']), (2, 1))
        self.assertEqual((account['input_tokens'], account['output_tokens']), (6, 4))

    def test_returned_count_keeps_schema_rejections_and_drops_transport_failures(self):
        # Amendment C3-b: a malformed reply still returned; a transport failure did not.
        events = [
            {'kind': 'model_attempt', 'body': {}},
            {'kind': 'model_reply', 'body': {}},
            {'kind': 'action_rejected', 'body': {}},
            {'kind': 'model_attempt', 'body': {}},
            {'kind': 'model_error', 'body': {'type': 'TransportFailure', 'soft': True}},
            {'kind': 'model_attempt', 'body': {}},
            {'kind': 'model_reply', 'body': {}},
        ]
        account = _account_attempts(events)
        self.assertEqual((account['attempts'], account['replies'], account['returned']), (3, 1, 2))
        old_prior = {'attempts': 4, 'replies': 2, 'transport_errors': {'transport_failure': 1}}
        combined = _combine_accounting(account, old_prior)
        self.assertEqual((combined['attempts'], combined['replies'], combined['returned']), (7, 3, 5))

    def test_missing_usage_keeps_totals_unknown_and_a2_unavailable(self):
        unknown = _account_attempts([{'kind': 'model_attempt', 'body': {}},
                                     {'kind': 'model_response', 'body': {'usage': None}},
                                     {'kind': 'model_reply', 'body': {}}])
        self.assertIsNone(unknown['input_tokens'])
        self.assertEqual(unknown['known_input_tokens'], 0)
        base = {'task': 't', 'holdout_passed': True, 'unsupported': False, 'model_calls': 1,
                'executed_reused_artifacts': [], 'chain': 'c', 'campaign_arm': 'KUTTAKA',
                'task_manifest_sha256': 'task-hash', 'campaign_manifest_sha256': 'k-hash',
                'campaign_contract_sha256': 'same-contract', 'holdout_sha256': 'same-holdout',
                'score_executor_contract': {'kind': 'scripted_callable', 'identity': 'test'},
                'campaign_eligibility': {'eligible': True, 'diagnostics': [], 'planned_tasks': ['t']},
                'input_tokens': None, 'output_tokens': None}
        base['score_contract_sha256'] = _manifest_digest({
            'campaign_contract_sha256': base['campaign_contract_sha256'],
            'holdout_sha256': base['holdout_sha256'],
            'score_executor_contract': base['score_executor_contract']})
        other = dict(base, campaign_arm='MODEL_ALONE', campaign_manifest_sha256='a-hash',
                     input_tokens=3, output_tokens=2)
        result = compare([base], [other])
        self.assertFalse(result['A2_fewer_tokens_when_both_solve']['available'])
        self.assertEqual(result['A2_fewer_tokens_when_both_solve']['unavailable_pairs'], 1)

    def test_asymmetric_usage_preserves_known_dimension(self):
        account = _account_attempts([
            {'kind': 'model_attempt', 'body': {}},
            {'kind': 'model_response', 'body': {'usage': {'input_tokens': 7}}},
            {'kind': 'model_reply', 'body': {}},
        ])
        self.assertEqual(account['input_tokens'], 7)
        self.assertIsNone(account['output_tokens'])
        self.assertEqual(account['unknown_input_calls'], 0)
        self.assertEqual(account['unknown_output_calls'], 1)
        self.assertEqual(account['unknown_usage_calls'], 1)

    def test_v2_compare_rejects_truncated_campaign_rows(self):
        row = {'task': 't1', 'holdout_passed': True, 'unsupported': False, 'model_calls': 1,
               'executed_reused_artifacts': [], 'chain': 'c', 'campaign_arm': 'KUTTAKA',
               'task_manifest_sha256': 'task-hash', 'campaign_manifest_sha256': 'k-hash',
               'campaign_contract_sha256': 'same-contract', 'holdout_sha256': 'same-holdout',
               'score_executor_contract': {'kind': 'scripted_callable', 'identity': 'test'},
               'campaign_eligibility': {'eligible': True, 'diagnostics': [],
                                        'planned_tasks': ['t1', 't2']}}
        row['score_contract_sha256'] = _manifest_digest({
            'campaign_contract_sha256': row['campaign_contract_sha256'],
            'holdout_sha256': row['holdout_sha256'],
            'score_executor_contract': row['score_executor_contract']})
        other = dict(row, campaign_arm='MODEL_ALONE', campaign_manifest_sha256='a-hash')
        with self.assertRaises(CampaignIneligible):
            compare([row], [other])

    def test_v2_compare_rejects_different_model_budget_or_holdout_contracts(self):
        def row(arm, manifest, contract, holdout):
            score_executor = {'kind': 'scripted_callable', 'identity': 'test'}
            return {'task': 't', 'holdout_passed': True, 'unsupported': False, 'model_calls': 1,
                    'executed_reused_artifacts': [], 'chain': 'c', 'campaign_arm': arm,
                    'task_manifest_sha256': 'task-hash', 'campaign_manifest_sha256': manifest,
                    'campaign_contract_sha256': contract, 'holdout_sha256': holdout,
                    'score_executor_contract': score_executor,
                    'score_contract_sha256': _manifest_digest({
                        'campaign_contract_sha256': contract, 'holdout_sha256': holdout,
                        'score_executor_contract': score_executor}),
                    'campaign_eligibility': {'eligible': True, 'diagnostics': [], 'planned_tasks': ['t']}}
        k = row('KUTTAKA', 'k-manifest', 'model-budget-options-a', 'holdout-a')
        a = row('MODEL_ALONE', 'a-manifest', 'model-budget-options-b', 'holdout-a')
        with self.assertRaises(CampaignIneligible):
            compare([k], [a])
        a = row('MODEL_ALONE', 'a-manifest', 'model-budget-options-a', 'holdout-b')
        with self.assertRaises(CampaignIneligible):
            compare([k], [a])

    def test_legacy_comparison_has_no_claim_eligibility(self):
        row = {'task': 't', 'holdout_passed': True, 'unsupported': False, 'model_calls': 1,
               'executed_reused_artifacts': [], 'chain': 'c', 'input_tokens': 1, 'output_tokens': 1}
        result = compare([row], [dict(row)])
        self.assertFalse(result['claim_eligibility']['eligible'])
        self.assertIn('descriptive comparison only', result['claim_eligibility']['diagnostics'][0])

    def test_malformed_checker_output_is_counted_as_unsupported(self):
        base = Path(tempfile.mkdtemp())
        run_campaign(STREAM, base / 'out', 'MODEL_ALONE', Ceiling(Scripted(), 20), budget=Budget(6, 12),
                     executor=local_executor)
        holdout = {task['id']: [{'input': [5, 5], 'expected': 5 + i}
                                for i in range(5)] for task in STREAM}
        scored = score_campaign(base / 'out', STREAM, holdout,
                                lambda job: {'status': 'completed', 'exit_code': 0, 'output': '[]'})
        self.assertTrue(all(row['unsupported'] for row in scored))
        self.assertTrue(all(not row['holdout_passed'] for row in scored))


if __name__ == '__main__':
    unittest.main()


class CutOffResponseTests(unittest.TestCase):
    """Amendment C3-c: a reply cut off mid-read is a soft transport failure."""

    def test_incomplete_read_becomes_a_transport_failure(self):
        import http.client
        from kuttaka_runtime.models import JsonModel

        class Cut:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def getcode(self):
                return 200

            def read(self, limit):
                raise http.client.IncompleteRead(b'partial')

        class Opener:
            def open(self, request, timeout):
                return Cut()
        model = JsonModel('m', 'https://example.invalid/v1', protocol='openai', stream=True,
                          sampling_fields=False)
        model._opener = Opener()
        with self.assertRaises(TransportFailure) as caught:
            model.propose({'request': 'x'})
        self.assertEqual(caught.exception.kind, 'response_read_error')
