"""KUTTAKA-C3 offline tests: scripted models only, no model calls.

1. C1/C2 behaviour is byte-identical (golden journal, memory and contract hashes recorded at
   origin/main 402fda6 before any C3 change).
2. The opt-in c3 tools: verified-only, ranked, deduplicated find_methods with interfaces; fit
   screening; interface examples on stored methods; the distinct-call trace.
3. The mechanism on the C3 development stream: a scripted model that composes stored methods by id
   when memory offers them and writes from scratch otherwise. Scoring, replay, resume and the C3
   analysis run end to end.
"""
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.c3 import analyze, lint_stream, main as c3_main
from kuttaka_runtime.campaign import (C3_PROFILE, CAMPAIGN_PROFILE, CampaignIneligible, Ceiling,
                                      _executor_contract, _manifest_digest, _model, compare,
                                      finished_tasks, run_campaign, score_campaign, spent_calls)
from kuttaka_runtime.execution import RUNNER, RUNNER_DISTINCT, PythonExecutor
from kuttaka_runtime.models import C3_REUSE_NOTE, INSTRUCTIONS, INSTRUCTIONS_C3
from kuttaka_runtime.programs import ProgramTask, ProgramTools
from kuttaka_runtime.runtime import Budget
from kuttaka_runtime.store import Journal
from tests.test_kuttaka_campaign import GCD, STREAM, Scripted, local_executor

ROOT = Path(__file__).resolve().parents[1]
DEV_DIR = ROOT / 'experiments' / 'kuttaka_c3'
DEV = json.loads((DEV_DIR / 'dev_stream.json').read_text())
DEV_HOLDOUT = json.loads((DEV_DIR / 'dev_holdout.json').read_text())
_spec = importlib.util.spec_from_file_location('kuttaka_c3_make_dev', DEV_DIR / 'make_dev.py')
REF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REF)


def local_distinct_executor(job):
    """Test stand-in for PythonExecutor(trace='distinct'): same C3 runner, plain subprocess."""
    proc = subprocess.run([sys.executable, '-I', '-c', RUNNER_DISTINCT], input=json.dumps(job).encode(),
                          capture_output=True, timeout=20)
    output = proc.stdout.decode()
    dependency_calls = []
    if proc.returncode == 0:
        output, dependency_calls = PythonExecutor._parse_runner_output(
            output, libraries_present='libraries' in job)
    return {'status': 'completed', 'exit_code': proc.returncode,
            'output': output, 'diagnostics': proc.stderr.decode(),
            'dependency_calls': dependency_calls}


# Recorded at origin/main 402fda6 with the unchanged tests' STREAM, Scripted and local_executor.
GOLDEN = {
    ('KUTTAKA', False): ('4bc234a0348ad2ea752982c17cb0197fca01e4899a0887c4d2d3fe6a7ef303e9',
                         ['bbbbcf90cacc1fd3442f1392293f59f93d01feb42edf744bf3f5a915704a59e4',
                          '1eff39acc11e6fe7f94bed5136af5ff276a42b1338ec36944b908b541aaf30e1'],
                         '4f117207ce261a04f6113280df7d910befe0b1a2c85cd94f6e5bac3e591103f2'),
    ('KUTTAKA', True): ('b80195a3743170bb9d8c489482191264fa7777e577aaf2111f31a66d9d41a282',
                        ['34b903e68d20b19a656b793c0b9ddbcaa887896f0c933eb070e0f4f60110cb0a',
                         '6362c8937d27eafc7d4be72920f56c7ea7b43fab155e551e526b6858734933fa'],
                        '5c80c68119721460ee31b6aa980497c634b9c2ab06f99475053207a57de49a2e'),
    ('MODEL_ALONE', False): ('4bc234a0348ad2ea752982c17cb0197fca01e4899a0887c4d2d3fe6a7ef303e9',
                             ['978c034e1d07160d0754594d52f0710ab67cd84ffd1706de264a3691214213b8',
                              'ebbccb06525f1912a3b229288047c591d852ef7e5f797260bf31a6f7cb653bd5'],
                             'cf08d5b3626d799556e8e2c4b4d5c19d4716a288e98af702eca5c74e10dc4382'),
    ('MODEL_ALONE', True): ('b80195a3743170bb9d8c489482191264fa7777e577aaf2111f31a66d9d41a282',
                            ['c82daf082b033352e10bee6a357d28fed5387a68f3384303910b392b4b1c4147',
                             '3cae24d9f593670ef2fb0b4b8d79514455959d4aa308619c9c01ad5960f89b93'],
                            '2841efdb0ac22af65d0e259c7282cd29eb389809570c0e1caa60edcc6f303703'),
}
GOLDEN_TOOL_DOCS = 'a0bf053b21649af37dc6e34dd51ba11234cd0e73d26857833f92ffa33cc17f24'
GOLDEN_INSTRUCTIONS = '032cd8ef7c63c0a52469ad88e70c6985b0cf8a4e5ce42a9d52c7623d7cc9b66c'
GOLDEN_C1_IDENTITY_INSTRUCTIONS = '2a94227c36dabaaa829dfa9f7ede07aebbddfc24b8ee10ca1fd04d11faa793a5'
TOOL_NAMES = ('remember_method', 'inspect_method', 'find_methods', 'probe_method', 'validate_method')


def program(task):
    return ProgramTask.from_dict({key: task[key] for key in ('request', 'checks') if key in task})


def model_args(**extra):
    values = dict(model='meta/muse-glimmer-30b', base_url='https://integrate.api.nvidia.com/v1',
                  model_timeout=600, max_tokens=4096)
    values.update(extra)
    return Namespace(**values)


class C1UnchangedTests(unittest.TestCase):
    """The default profile reproduces origin/main byte for byte."""

    def test_c1_journals_memory_and_contracts_match_origin_main(self):
        for (arm, repair), (contract, journals, memory_hash) in GOLDEN.items():
            out = Path(tempfile.mkdtemp()) / 'run'
            rows = run_campaign(STREAM, out, arm, Ceiling(Scripted(), 50), budget=Budget(6, 12),
                                executor=local_executor, reply_repair=repair)
            manifest = json.loads((out / 'campaign.json').read_text())
            memory = out / 'memory' / ('events.jsonl' if arm == 'KUTTAKA' else 't2/events.jsonl')
            self.assertEqual(manifest['campaign_contract_sha256'], contract, (arm, repair))
            self.assertEqual([r['journal_sha256'] for r in rows], journals, (arm, repair))
            self.assertEqual(hashlib.sha256(memory.read_bytes()).hexdigest(), memory_hash, (arm, repair))
            self.assertEqual(manifest['options']['profile'], CAMPAIGN_PROFILE)
            self.assertNotIn('delivered_stored_method', rows[1])

    def test_c1_tool_texts_instructions_and_model_identity_unchanged(self):
        docs = {name: getattr(ProgramTools, name).__doc__ for name in TOOL_NAMES}
        self.assertEqual(hashlib.sha256(json.dumps(docs, sort_keys=True).encode()).hexdigest(),
                         GOLDEN_TOOL_DOCS)
        self.assertEqual(hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(), GOLDEN_INSTRUCTIONS)
        self.assertEqual(_model(model_args()).identity['instructions_sha256'], GOLDEN_C1_IDENTITY_INSTRUCTIONS)

    def test_default_executor_and_contract_unchanged(self):
        executor = PythonExecutor(timeout=10)
        self.assertEqual(executor._command('x')[-1], RUNNER)
        self.assertEqual(_executor_contract(executor), {'kind': 'python_executor', 'image': executor.image,
                                                        'timeout': 10, 'output_limit': 262144})

    def test_c1_tools_ignore_c3_only_fields(self):
        with tempfile.TemporaryDirectory() as scratch, ArtifactMemory(Path(scratch) / 'm') as memory, \
                Journal(Path(scratch) / 'j.jsonl') as journal:
            tools = ProgramTools(program(STREAM[0]), memory, journal, local_executor,
                                 provenance={'task': 't1'})
            self.assertEqual({name: fn.__doc__ for name, fn in tools.tools().items()},
                             {name: getattr(ProgramTools, name).__doc__ for name in TOOL_NAMES})
            stored = tools.remember_method({'name': 'gcd', 'description': 'gcd', 'source': GCD})
            self.assertNotIn('examples', memory.get(stored['artifact'])['applicability'])


class C3ProfileTests(unittest.TestCase):
    def test_c3_model_differs_only_by_the_appended_note(self):
        # Amendment C3-a: C3 uses the C1 adapter as it ran at ef81a25 (the c2 adapter), plus the note.
        from kuttaka_runtime.campaign import C2_PROFILE
        c1, c3 = _model(model_args(), profile=C2_PROFILE), _model(model_args(), profile=C3_PROFILE)
        self.assertEqual(c3.instructions, INSTRUCTIONS + '\n' + C3_REUSE_NOTE)
        self.assertEqual(INSTRUCTIONS_C3, c3.instructions)
        self.assertNotEqual(c1.identity['instructions_sha256'], c3.identity['instructions_sha256'])
        self.assertEqual({k: v for k, v in c1.identity.items() if k != 'instructions_sha256'},
                         {k: v for k, v in c3.identity.items() if k != 'instructions_sha256'})
        with self.assertRaises(ValueError):
            _model(model_args(), profile='c9')

    def test_distinct_trace_is_opt_in_and_bound_into_the_contract(self):
        executor = PythonExecutor(timeout=10, trace='distinct')
        self.assertEqual(executor._command('x')[-1], RUNNER_DISTINCT)
        self.assertEqual(_executor_contract(executor)['trace'], 'distinct')
        with self.assertRaises(ValueError):
            PythonExecutor(trace='some')

    def test_distinct_trace_lists_each_called_method_once(self):
        job = {'source': 'def run(p):\n    f = use("%s")\n    return sum(f(i) for i in range(3000))\n' % ('a' * 64),
               'input': 0, 'libraries': {'a' * 64: 'def run(p):\n    return p\n'}}
        self.assertEqual(local_distinct_executor(job)['dependency_calls'], ['a' * 64])
        self.assertEqual(len(local_executor(job)['dependency_calls']), 3000)

    def test_tool_profile_and_campaign_profile_are_validated(self):
        with tempfile.TemporaryDirectory() as scratch, ArtifactMemory(Path(scratch) / 'm') as memory, \
                Journal(Path(scratch) / 'j.jsonl') as journal:
            with self.assertRaises(ValueError):
                ProgramTools(program(STREAM[0]), memory, journal, local_executor,
                             provenance={}, profile='c9')
        with self.assertRaises(ValueError):
            run_campaign(STREAM, Path(tempfile.mkdtemp()) / 'x', 'KUTTAKA', Ceiling(Scripted(), 5),
                         budget=Budget(6, 12), executor=local_executor, profile='c3')

    def test_campaign_cli_accepts_only_known_profiles(self):
        import io
        from contextlib import redirect_stderr
        from kuttaka_runtime import campaign
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            campaign.main(['run', '--stream', 's', '--out', 'o', '--arm', 'KUTTAKA', '--ceiling', '1',
                           '--profile', 'c9'])


def remember(tools, name, source, uses=None, description=None):
    body = {'name': name, 'description': description or name, 'source': source}
    if uses:
        body['uses'] = uses
    return tools.remember_method_c3(body)['artifact']


class C3FindTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.memory = ArtifactMemory(self.dir / 'memory')
        self.addCleanup(self.memory.close)
        self.journals = 0

    def tools_for(self, task):
        self.journals += 1
        journal = Journal(self.dir / f'j{self.journals}.jsonl')
        self.addCleanup(journal.close)
        return ProgramTools(program(task), self.memory, journal, local_executor,
                            provenance={'task': 'test'}, profile='c3')

    def test_lists_only_checked_methods_with_interface_and_examples(self):
        first = self.tools_for(STREAM[0])
        good = remember(first, 'gcd euclid', GCD)
        bad = remember(first, 'broken gcd', 'def run(p):\n    return 0\n')
        self.assertTrue(first.validate_method({'artifact': good})['passed'])
        self.assertFalse(first.validate_method({'artifact': bad})['passed'])
        second = self.tools_for(STREAM[1])
        found = second.find_methods_c3({'query': 'gcd', 'limit': 20})
        self.assertEqual([m['id'] for m in found['methods']], [good])
        entry = found['methods'][0]
        self.assertEqual(entry['interface']['task'], STREAM[0]['request'])
        self.assertEqual(entry['interface']['examples'],
                         [{'input': [6, 9], 'output': 3}, {'input': [7, 5], 'output': 1}])
        self.assertEqual(entry['qualified_scopes'], 1)
        self.assertFalse(entry['fits_this_task'])  # gcd is not lcm
        self.assertEqual(self.memory.get(good)['applicability']['examples'], STREAM[0]['checks'])

    def test_ranks_by_origin_request_words_and_deduplicates_identical_source(self):
        first = self.tools_for(STREAM[0])
        gcd = remember(first, 'alpha', GCD, description='number helper')
        first.validate_method({'artifact': gcd})
        other = remember(first, 'beta', 'def run(p):\n    return 3 if p == [6, 9] else 1\n',
                         description='number helper')
        first.validate_method({'artifact': other})
        task_copy = dict(STREAM[0], request='Write run([a,b]) returning gcd(a,b), the same again.')
        again = self.tools_for(task_copy)
        duplicate = remember(again, 'alpha again', GCD + '\n\n', description='number helper')
        again.validate_method({'artifact': duplicate})
        later = self.tools_for(STREAM[1])
        found = later.find_methods_c3({'query': 'returning gcd', 'limit': 3})['methods']
        ids = [m['id'] for m in found]
        self.assertEqual(len(ids), 2)  # the duplicate source is listed once
        self.assertIn(other, ids)
        kept = gcd if gcd in ids else duplicate
        self.assertEqual(next(m for m in found if m['id'] == kept)['qualified_scopes'], 1)

    def test_unrelated_methods_are_not_listed_but_similar_tasks_are_without_a_query_match(self):
        first = self.tools_for(STREAM[0])
        gcd = remember(first, 'gcd', GCD)
        first.validate_method({'artifact': gcd})
        unrelated = {'request': 'Write run(p). Sort the strings in p by length.',
                     'checks': [{'input': ['bb', 'a'], 'expected': ['a', 'bb']}]}
        self.assertEqual(self.tools_for(unrelated).find_methods_c3({'query': 'zebra', 'limit': 50}),
                         {'methods': [], 'retrieval': 'empty'})
        self.assertEqual(self.tools_for(unrelated).find_methods_c3({'query': '', 'limit': 50})['methods'], [])
        # The lcm request shares most of its words with the gcd request: listed with no query match.
        found = self.tools_for(STREAM[1]).find_methods_c3({'query': 'zebra', 'limit': 50})
        self.assertEqual((found['retrieval'], [m['id'] for m in found['methods']]), ('ranked', [gcd]))
        with ArtifactMemory(self.dir / 'other') as memory, Journal(self.dir / 'other.jsonl') as journal:
            empty = ProgramTools(program(STREAM[0]), memory, journal, local_executor, provenance={}, profile='c3')
            self.assertEqual(empty.find_methods_c3({'query': 'gcd', 'limit': 5}),
                             {'methods': [], 'retrieval': 'empty'})

    def test_result_cap_interface_cap_and_schema_errors(self):
        first = self.tools_for(STREAM[0])
        for index in range(9):
            artifact = remember(first, f'gcd {index}', GCD + f'\n# variant {index}\n')
            first.validate_method({'artifact': artifact})
        found = self.tools_for(STREAM[1]).find_methods_c3({'query': '', 'limit': 100})
        self.assertEqual(len(found['methods']), 6)
        self.assertEqual(found['methods'][0]['name'], 'gcd 8')  # equally relevant: most recent first
        self.assertEqual(['interface' in m for m in found['methods']], [True] * 3 + [False] * 3)
        self.assertEqual(['fits_this_task' in m for m in found['methods']], [True] * 3 + [False] * 3)
        tools = self.tools_for(STREAM[1]).tools()
        from kuttaka_runtime.runtime import ToolInputError
        with self.assertRaises(ToolInputError):
            tools['find_methods']({'query': 'x'})

    def test_screening_marks_a_fitting_method_and_spends_executions_once(self):
        first = self.tools_for(STREAM[0])
        gcd = remember(first, 'gcd', GCD, description='greatest common divisor of two integers')
        first.validate_method({'artifact': gcd})
        repeat = {'request': 'Return the greatest common divisor of the two numbers in p.',
                  'checks': [{'input': [12, 18], 'expected': 6}, {'input': [5, 3], 'expected': 1},
                             {'input': [8, 4], 'expected': 4}]}
        tools = self.tools_for(repeat)
        found = tools.find_methods_c3({'query': 'common divisor', 'limit': 5})
        self.assertTrue(found['methods'][0]['fits_this_task'])
        self.assertEqual(tools.execution_attempts, 3)
        tools.find_methods_c3({'query': 'common divisor', 'limit': 5})
        self.assertEqual(tools.execution_attempts, 3)  # cached for this task
        purposes = [e['body']['purpose'] for e in Journal.read(tools.journal.path)
                    if e['kind'] == 'program_execution_precommit']
        self.assertEqual(purposes, ['screen'] * 3)
        self.assertEqual(self.memory.status(gcd, tools.task.scope_id), 'proposed')  # screening is not qualification

    def test_screen_reports_unknown_when_execution_budget_is_spent(self):
        first = self.tools_for(STREAM[0])
        gcd = remember(first, 'gcd', GCD)
        first.validate_method({'artifact': gcd})
        journal = Journal(self.dir / 'tight.jsonl')
        self.addCleanup(journal.close)
        tight = ProgramTools(program(STREAM[1]), self.memory, journal, local_executor,
                             max_executions=0, provenance={}, profile='c3')
        self.assertIsNone(tight.find_methods_c3({'query': 'gcd', 'limit': 5})['methods'][0]['fits_this_task'])


class Repeater(Scripted):
    """find -> answer a method that fits this task -> otherwise write one, validate (auto-submitted)."""

    def propose(self, context):
        self.calls += 1
        history = context['history']
        if not history:
            return {'kind': 'experiment', 'tool': 'find_methods', 'input': {'query': 'divisor gcd', 'limit': 5},
                    'predictions': [{'hypothesis': 'memory may hold it', 'outcome': 'a list'}]}
        last = history[-1]['observation']
        if isinstance(last, dict) and 'methods' in last:
            fitting = [m['id'] for m in last['methods'] if m.get('fits_this_task')]
            if fitting:
                return {'kind': 'answer', 'answer': {'artifact': fitting[0]}}
            return {'kind': 'experiment', 'tool': 'remember_method',
                    'input': {'name': 'gcd', 'description': 'gcd of two numbers', 'source': GCD},
                    'predictions': [{'hypothesis': 'stored', 'outcome': 'id'}]}
        if isinstance(last, dict) and last.get('status') == 'stored':
            return {'kind': 'experiment', 'tool': 'validate_method', 'input': {'artifact': last['artifact']},
                    'predictions': [{'hypothesis': 'passes', 'outcome': 'passed'}]}
        return {'kind': 'unresolved', 'reason': 'unexpected'}


REPEAT_STREAM = [
    STREAM[0],
    {'id': 't2', 'chain': 'c', 'request': 'Return the greatest common divisor of the two numbers in p.',
     'checks': [{'input': [12, 18], 'expected': 6}, {'input': [5, 3], 'expected': 1},
                {'input': [8, 4], 'expected': 4}]},
]


class C3DeliveryTests(unittest.TestCase):
    def run_arm(self, arm, profile=C3_PROFILE, executor=local_distinct_executor):
        return run_campaign(REPEAT_STREAM, Path(tempfile.mkdtemp()) / arm, arm, Ceiling(Repeater(), 50),
                            budget=Budget(6, 24), executor=executor, reply_repair=True, profile=profile)

    def test_stored_method_is_delivered_in_two_calls_and_counted_as_reuse_by_id(self):
        rows = self.run_arm('KUTTAKA')
        self.assertEqual([r['status'] for r in rows], ['checked', 'checked'])
        self.assertEqual(rows[1]['model_calls'], 2)  # find (fits) + answer
        self.assertEqual(rows[1]['artifact'], rows[0]['artifact'])
        self.assertTrue(rows[1]['delivered_stored_method'])
        self.assertEqual(rows[1]['executed_reused_artifacts'], [rows[0]['artifact']])
        alone = self.run_arm('MODEL_ALONE')
        self.assertEqual(alone[1]['model_calls'], 3)  # find (empty) + remember + validate
        self.assertFalse(alone[1]['delivered_stored_method'])
        self.assertEqual(alone[1]['executed_reused_artifacts'], [])

    def test_c1_profile_does_not_count_direct_delivery_and_has_no_screening(self):
        rows = self.run_arm('KUTTAKA', profile=CAMPAIGN_PROFILE, executor=local_executor)
        self.assertEqual(rows[1]['model_calls'], 3)
        self.assertNotIn('delivered_stored_method', rows[1])


# ---------------------------------------------------------------- the mechanism on the dev stream
def _consts(**values):
    return ''.join(f'{name} = {value!r}\n' for name, value in values.items())


def _src(*functions):
    return '\n'.join(inspect.getsource(fn) for fn in functions)


def _run(name):
    return f'\n\ndef run(p):\n    return {name}(p)\n'


def _use(**names):
    return ''.join(f'{alias} = use({artifact!r})\n' for alias, artifact in names.items())


KAL_CORE = _consts(LIMIT=REF.LIMIT, PREC=REF.PREC)
MERU_CORE = _consts(WEEK=REF.WEEK) + _src(REF.meru_leap, REF.meru_length)
SLIPPED_LEAP = 'def meru_leap(y):\n    return y % 5 == 0 and y % 30 != 0\n'  # forgets the 150 exception


def slipped(source):
    """The Meru core re-derived with the 150 exception forgotten, in the leap test and the day count."""
    source = source.replace(inspect.getsource(REF.meru_leap), SLIPPED_LEAP)
    return source.replace('n // 5 - n // 30 + n // 150', 'n // 5 - n // 30')
SCRATCH = {
    'kal-lex': _src(REF.kal_lex) + _run('kal_lex'),
    'kal-rpn': KAL_CORE + _src(REF.kal_lex, REF.kal_rpn) + _run('kal_rpn'),
    'kal-eval': KAL_CORE + _src(REF.kal_lex, REF.kal_rpn, REF.kal_eval) + _run('kal_eval'),
    'kal-run': KAL_CORE + _src(REF.kal_lex, REF.kal_rpn, REF.kal_eval, REF.kal_run) + _run('kal_run'),
    'kal-script': KAL_CORE + _src(REF.kal_lex, REF.kal_rpn, REF.kal_eval, REF.kal_run, REF.kal_script)
                  + _run('kal_script'),
    'meru-ord': MERU_CORE + _src(REF.meru_ord) + _run('meru_ord'),
    'meru-weekday': MERU_CORE + _src(REF.meru_ord, REF.meru_weekday) + _run('meru_weekday'),
    'meru-market': MERU_CORE + _src(REF.meru_ord, REF.meru_weekday, REF.meru_market) + _run('meru_market'),
    'meru-text': MERU_CORE + _src(REF.meru_ord, REF.meru_weekday, REF.meru_market, REF.meru_text)
                 + _run('meru_text'),
}
MARKET_FROM_PARTS = '''

def month_length(y, m):
    for d in (36, 35, 30):
        if meru_ord({'y': y, 'm': m, 'd': d}) is not None:
            return d
    return 0


def run(p):
    y, m = p['y'], p['m']
    if y < 1 or not 1 <= m <= 12:
        return None
    last = month_length(y, 12)
    return [d for d in range(1, month_length(y, m) + 1)
            if not (m == 7 and d == 1) and not (m == 12 and d == last)
            and meru_weekday({'y': y, 'm': m, 'd': d}) in ('Bel', 'Dun')]
'''
COMPOSED = {
    'kal-rpn': lambda ids: _use(kal_lex=ids['kal-lex']) + KAL_CORE + _src(REF.kal_rpn) + _run('kal_rpn'),
    'kal-eval': lambda ids: _use(kal_rpn=ids['kal-rpn']) + KAL_CORE + _src(REF.kal_eval) + _run('kal_eval'),
    'kal-run': lambda ids: _use(kal_lex=ids['kal-lex'], kal_eval=ids['kal-eval']) + _src(REF.kal_run)
                           + _run('kal_run'),
    'kal-script': lambda ids: _use(kal_run=ids['kal-run']) + _src(REF.kal_script) + _run('kal_script'),
    'meru-weekday': lambda ids: _use(meru_ord=ids['meru-ord']) + _consts(WEEK=REF.WEEK)
                                + _src(REF.meru_weekday) + _run('meru_weekday'),
    'meru-market': lambda ids: _use(meru_ord=ids['meru-ord'], meru_weekday=ids['meru-weekday'])
                               + MARKET_FROM_PARTS,
    'meru-text': lambda ids: _use(meru_market=ids['meru-market']) + _src(REF.meru_text) + _run('meru_text'),
}
BY_REQUEST = {task['request']: task for task in DEV}


class Composer:
    """Scripted stand-in for the model on the dev stream (it reads only its visible context).

    It searches memory, and when every method the task builds on is listed (by name), it writes a
    short composition that calls them with use(ID); otherwise it writes the whole method from
    scratch. ``slip`` re-derives the Meru leap rule wrongly whenever it rewrites the calendar core
    on a later task: an assumed fault, to show how scoring treats it, not evidence about a model.
    """
    identity = {'model': 'scripted-composer'}

    def __init__(self, slip=False):
        self.calls, self.slip = 0, slip
        self.last_usage = {}

    def propose(self, context):
        self.calls += 1
        action = self._act(context)
        self.last_usage = {'input_tokens': len(json.dumps(context)) // 4,
                           'output_tokens': len(json.dumps(action)) // 4}
        return action

    def _act(self, context):
        task = BY_REQUEST[context['request']]
        history = context['history']
        if not history:
            return {'kind': 'experiment', 'tool': 'find_methods',
                    'input': {'query': task['chain'] + ' ' + ' '.join(task['needs']), 'limit': 6},
                    'predictions': [{'hypothesis': 'earlier methods may apply', 'outcome': 'a list'}]}
        last = history[-1]['observation']
        if isinstance(last, dict) and 'methods' in last:
            fitting = [m['id'] for m in last['methods'] if m.get('fits_this_task')]
            if fitting:
                return {'kind': 'answer', 'answer': {'artifact': fitting[0]}}
            ids = {m['name']: m['id'] for m in last['methods'] if m['qualified_scopes']}
            body = {'name': task['id'], 'description': task['id'] + ' for the ' + task['chain'] + ' chain'}
            if task['needs'] and all(need in ids for need in task['needs']):
                body['source'] = COMPOSED[task['id']](ids)
                body['uses'] = sorted(ids[need] for need in task['needs'])
            else:
                source = SCRATCH[task['id']]
                if self.slip and task['chain'] == 'meru' and task['role'] != 'base':
                    source = slipped(source)
                body['source'] = source
            return {'kind': 'experiment', 'tool': 'remember_method', 'input': body,
                    'predictions': [{'hypothesis': 'stored', 'outcome': 'an id'}]}
        if isinstance(last, dict) and last.get('status') == 'stored':
            return {'kind': 'experiment', 'tool': 'validate_method', 'input': {'artifact': last['artifact']},
                    'predictions': [{'hypothesis': 'the visible checks pass', 'outcome': 'passed'}]}
        return {'kind': 'unresolved', 'reason': 'unexpected observation'}


class C3DevStreamTests(unittest.TestCase):
    BUDGET = Budget(6, 24)

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp())
        cls.rows = {}
        for arm in ('KUTTAKA', 'MODEL_ALONE'):
            for slip in (False, True):
                name = f'{arm}-{"slip" if slip else "plain"}'
                cls.rows[name] = run_campaign(DEV, cls.base / name, arm, Ceiling(Composer(slip), 60),
                                              budget=cls.BUDGET, executor=local_distinct_executor,
                                              reply_repair=True, profile=C3_PROFILE)

    def scored(self, name):
        return score_campaign(self.base / name, DEV, DEV_HOLDOUT, local_executor)

    def test_dev_stream_passes_the_lint_and_its_references_reproduce_it(self):
        self.assertEqual(lint_stream(DEV, DEV_HOLDOUT), [])
        stream, holdout, references = REF.build()
        self.assertEqual(stream, DEV)
        self.assertEqual(holdout, DEV_HOLDOUT)
        self.assertEqual(REF.discriminative(stream, references), [])
        self.assertEqual(len(DEV), 9)
        self.assertTrue(all(len(holdout[t['id']]) >= 6 for t in DEV))

    def test_kuttaka_composes_every_later_task_from_its_own_stored_methods(self):
        rows = self.rows['KUTTAKA-plain']
        self.assertEqual([r['status'] for r in rows], ['checked'] * 9)
        later = [r for r, t in zip(rows, DEV) if t['role'] != 'base']
        self.assertTrue(all(r['executed_reused_artifacts'] for r in later))
        by_task = {r['task']: r for r in rows}
        # kal-script runs kal-run, which runs kal-eval and kal-lex; kal-eval runs kal-rpn.
        self.assertEqual(set(by_task['kal-script']['executed_reused_artifacts']),
                         {by_task[t]['artifact'] for t in ('kal-lex', 'kal-rpn', 'kal-eval', 'kal-run')})
        alone = self.rows['MODEL_ALONE-plain']
        self.assertEqual([r['status'] for r in alone], ['checked'] * 9)
        self.assertTrue(all(not r['executed_reused_artifacts'] for r in alone))

    def test_holdout_scoring_and_c3_analysis_without_a_fault(self):
        k, a = self.scored('KUTTAKA-plain'), self.scored('MODEL_ALONE-plain')
        self.assertEqual(sum(r['holdout_passed'] for r in k), 9)
        self.assertEqual(sum(r['holdout_passed'] for r in a), 9)
        result = analyze(DEV, k, a)
        self.assertTrue(result['claim_eligibility']['eligible'])
        self.assertEqual(result['A1_primary_reuse_eligible_tasks']['tasks'], 7)
        self.assertEqual(result['A1_primary_reuse_eligible_tasks']['verdict'], 'NOT SUPPORTED')
        self.assertEqual(result['A4_reuse_by_id']['reusing'], 7)
        self.assertEqual(result['A4_reuse_by_id']['verdict'], 'SUPPORTED')
        self.assertEqual(result['descriptive_by_role']['variant']['tasks'], 2)

    def test_a_fault_in_rewritten_core_is_caught_as_unsupported_only_where_the_core_is_rewritten(self):
        k, a = self.scored('KUTTAKA-slip'), self.scored('MODEL_ALONE-slip')
        unsupported_alone = sorted(r['task'] for r in a if r['unsupported'])
        self.assertEqual(unsupported_alone, ['meru-market', 'meru-text', 'meru-weekday'])
        self.assertEqual([r['task'] for r in k if r['unsupported']], [])
        result = analyze(DEV, k, a)
        primary = result['A1_primary_reuse_eligible_tasks']
        self.assertEqual((primary['wins'], primary['losses']), (3, 0))
        self.assertAlmostEqual(primary['p_one_sided'], 0.125)
        self.assertEqual(primary['verdict'], 'NOT SUPPORTED')  # 9 dev tasks cannot reach alpha
        self.assertEqual(result['A3_unsupported'], dict(KUTTAKA=0, MODEL_ALONE=3, verdict='SUPPORTED'))

    def test_c3_replay_is_identical(self):
        source = self.base / 'KUTTAKA-plain'
        again = run_campaign(DEV, self.base / 'replay', 'KUTTAKA', None, budget=self.BUDGET,
                             executor=local_distinct_executor, replay_from=source, reply_repair=True,
                             profile=C3_PROFILE)
        self.assertEqual([r['journal_sha256'] for r in again],
                         [r['journal_sha256'] for r in self.rows['KUTTAKA-plain']])
        with self.assertRaises(CampaignIneligible):  # a replay must name the same profile
            run_campaign(DEV, self.base / 'replay-c1', 'KUTTAKA', None, budget=self.BUDGET,
                         executor=local_distinct_executor, replay_from=source, reply_repair=True)

    def test_c3_resume_after_a_crash_rebuilds_the_same_run(self):
        class Crashes(Composer):
            def propose(self, context):
                if self.calls == 13:  # inside the fifth task
                    raise KeyboardInterrupt
                return super().propose(context)
        crashed = self.base / 'crashed'
        with self.assertRaises(KeyboardInterrupt):
            run_campaign(DEV, crashed, 'KUTTAKA', Ceiling(Crashes(), 60), budget=self.BUDGET,
                         executor=local_distinct_executor, reply_repair=True, profile=C3_PROFILE)
        self.assertEqual(len(finished_tasks(crashed)), 4)
        live = Composer()
        live.calls = 12
        rows = run_campaign(DEV, self.base / 'resumed', 'KUTTAKA', Ceiling(live, 60, used=spent_calls(crashed)),
                            budget=self.BUDGET, executor=local_distinct_executor, resume_from=crashed,
                            reply_repair=True, profile=C3_PROFILE)
        clean = self.rows['KUTTAKA-plain']
        self.assertEqual([r['journal_sha256'] for r in rows[:4]], [r['journal_sha256'] for r in clean[:4]])
        self.assertEqual([(r['status'], r['artifact']) for r in rows], [(r['status'], r['artifact']) for r in clean])

    def test_scoring_accepts_c3_and_compare_refuses_to_pair_c1_with_c3(self):
        c1 = run_campaign(DEV[:2], self.base / 'c1-alone', 'MODEL_ALONE', Ceiling(Composer(), 60),
                          budget=self.BUDGET, executor=local_distinct_executor, reply_repair=True)
        self.assertEqual(len(c1), 2)
        c3 = run_campaign(DEV[:2], self.base / 'c3-k', 'KUTTAKA', Ceiling(Composer(), 60),
                          budget=self.BUDGET, executor=local_distinct_executor, reply_repair=True,
                          profile=C3_PROFILE)
        holdout = {t['id']: DEV_HOLDOUT[t['id']] for t in DEV[:2]}
        s1 = score_campaign(self.base / 'c1-alone', DEV[:2], holdout, local_executor)
        s3 = score_campaign(self.base / 'c3-k', DEV[:2], holdout, local_executor)
        self.assertEqual(len(c3), 2)
        with self.assertRaises(CampaignIneligible):
            compare(s3, s1)

    def test_token_accounting_of_reuse_in_this_simulation(self):
        k, a = self.rows['KUTTAKA-plain'], self.rows['MODEL_ALONE-plain']
        for kr, ar, task in zip(k, a, DEV):
            if task['role'] == 'base':
                # Nothing relevant is stored yet, so the search lists nothing: no token overhead.
                self.assertEqual((kr['input_tokens'], kr['output_tokens']),
                                 (ar['input_tokens'], ar['output_tokens']), task['id'])
            else:
                self.assertLess(kr['output_tokens'], ar['output_tokens'], task['id'])  # shorter code
                self.assertGreater(kr['input_tokens'], ar['input_tokens'], task['id'])  # the listing
        self.assertEqual([r['model_calls'] for r in k], [r['model_calls'] for r in a])


def volatile_executor(job):
    """Like the Docker executor, each execution reports a fresh container name."""
    import uuid
    return dict(local_distinct_executor(job), container_name='kuttaka-exec-' + uuid.uuid4().hex)


class C3ReplayStabilityTests(unittest.TestCase):
    def replays_identically(self, profile):
        base = Path(tempfile.mkdtemp())
        rows = run_campaign(STREAM, base / 'run', 'KUTTAKA', Ceiling(Scripted(), 20), budget=Budget(6, 12),
                            executor=volatile_executor, reply_repair=True, profile=profile)
        again = run_campaign(STREAM, base / 'replay', 'KUTTAKA', None, budget=Budget(6, 12),
                             executor=volatile_executor, replay_from=base / 'run', reply_repair=True,
                             profile=profile)
        self.assertEqual([r['status'] for r in rows], ['checked', 'checked'])
        return [r['journal_sha256'] for r in rows] == [r['journal_sha256'] for r in again]

    def test_c3_journals_drop_volatile_sandbox_metadata_so_replays_are_identical(self):
        self.assertTrue(self.replays_identically(C3_PROFILE))

    def test_c1_profile_still_journals_it_unchanged(self):
        # Documents origin/main behaviour (kept byte-identical): with the Docker executor's
        # container names in the journal, a C1/C2-profile replay differs from its run.
        self.assertFalse(self.replays_identically(CAMPAIGN_PROFILE))


class C3LintTests(unittest.TestCase):
    def mutate(self, index, **changes):
        stream = [dict(task) for task in DEV]
        stream[index].update(changes)
        return lint_stream(stream, DEV_HOLDOUT)

    def test_rules_reject_each_kind_of_violation(self):
        self.assertTrue(any('G5' in p for p in self.mutate(1, request=DEV[1]['request'] + ' Use the previous task.')))
        self.assertTrue(any('G5' in p for p in self.mutate(2, request=DEV[2]['request'] + ' See kal-lex.')))
        self.assertTrue(any('G2' in p for p in self.mutate(3, needs=['kal-eval'])))  # compose needs >= 2
        self.assertTrue(any('G2' in p for p in self.mutate(4, target='kal-lex', needs=['kal-lex'])))
        self.assertTrue(any('G3' in p for p in self.mutate(0, checks=DEV[0]['checks'][:3])))
        self.assertTrue(any('G4' in p for p in self.mutate(1, checks=DEV[1]['checks'][:2] + [
            {'input': {'expr': '1'}, 'expected': 1.5}])))
        self.assertTrue(any('G1' in p for p in self.mutate(0, family='poetry')))
        short = dict(DEV_HOLDOUT, **{'kal-lex': DEV_HOLDOUT['kal-lex'][:5]})
        self.assertTrue(any('G6' in p for p in lint_stream(DEV, short)))
        leaked = dict(DEV_HOLDOUT, **{'kal-lex': DEV_HOLDOUT['kal-lex'] + [DEV[0]['checks'][0]]})
        self.assertTrue(any('G6' in p for p in lint_stream(DEV, leaked)))

    def test_sealed_rules_require_eight_five_task_chains(self):
        problems = lint_stream(DEV, DEV_HOLDOUT, sealed=True)
        self.assertTrue(any('S1' in p for p in problems))

    def test_cli(self):
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()) as out:
            code = c3_main(['lint', '--stream', str(DEV_DIR / 'dev_stream.json'),
                            '--holdout', str(DEV_DIR / 'dev_holdout.json')])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())['ok'])


class C3AnalyzeTests(unittest.TestCase):
    def rows(self, arm, solved, tokens=None, reuse=()):
        rows = []
        for index, task in enumerate(DEV):
            row = {'task': task['id'], 'chain': task['chain'], 'campaign_arm': arm,
                   'holdout_passed': task['id'] in solved, 'unsupported': False, 'status': 'checked',
                   'model_calls': 3, 'input_tokens': (tokens or {}).get(task['id'], 100),
                   'output_tokens': 10,
                   'executed_reused_artifacts': ['x'] if task['id'] in reuse else [],
                   'task_manifest_sha256': 't', 'campaign_manifest_sha256': arm,
                   'campaign_contract_sha256': 'c', 'holdout_sha256': 'h',
                   'score_executor_contract': {'kind': 'scripted_callable', 'identity': 's'},
                   'campaign_eligibility': {'eligible': True, 'diagnostics': [],
                                            'planned_tasks': [t['id'] for t in DEV]}}
            row['score_contract_sha256'] = _manifest_digest({
                'campaign_contract_sha256': 'c', 'holdout_sha256': 'h',
                'score_executor_contract': row['score_executor_contract']})
            rows.append(row)
        return rows

    def test_primary_excludes_base_tasks_and_variant_rule_uses_variant_pairs_only(self):
        everything = {t['id'] for t in DEV}
        later = {t['id'] for t in DEV if t['role'] != 'base'}
        k = self.rows('KUTTAKA', everything, tokens={'kal-script': 50, 'meru-text': 60}, reuse=later)
        a = self.rows('MODEL_ALONE', everything - later)  # alone solves only the base tasks
        result = analyze(DEV, k, a)
        self.assertEqual(result['A1_primary_reuse_eligible_tasks']['wins'], 7)
        self.assertAlmostEqual(result['A1_primary_reuse_eligible_tasks']['p_one_sided'], 1 / 128)
        self.assertEqual(result['A1_primary_reuse_eligible_tasks']['verdict'], 'SUPPORTED')
        self.assertEqual(result['A5_variant_tokens']['verdict'], 'NOT AVAILABLE')  # alone solved no variant
        a2 = self.rows('MODEL_ALONE', everything)
        result = analyze(DEV, k, a2)
        self.assertEqual(result['A5_variant_tokens']['cheaper'], 2)
        self.assertEqual(result['A5_variant_tokens']['p_one_sided'], 0.25)
        self.assertEqual(result['A5_variant_tokens']['verdict'], 'NOT SUPPORTED')
        self.assertEqual(result['A1_primary_reuse_eligible_tasks']['verdict'], 'NOT SUPPORTED')

    def test_ineligible_arms_get_no_verdicts(self):
        everything = {t['id'] for t in DEV}
        k = self.rows('KUTTAKA', everything)
        for row in k:
            row['campaign_eligibility'] = dict(row['campaign_eligibility'], eligible=False,
                                               diagnostics=['reply gate failed'])
        result = analyze(DEV, k, self.rows('MODEL_ALONE', set()))
        self.assertEqual(result['A1_primary_reuse_eligible_tasks']['verdict'], 'NOT ELIGIBLE')

    def test_analysis_needs_roles_for_every_task(self):
        everything = {t['id'] for t in DEV}
        stream = [dict(t) for t in DEV]
        stream[0].pop('role')
        with self.assertRaises(CampaignIneligible):
            analyze(stream, self.rows('KUTTAKA', everything), self.rows('MODEL_ALONE', everything))


if __name__ == '__main__':
    unittest.main()
