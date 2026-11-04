"""Run the graded suite end to end and score every delivered method on hidden inputs.

    python3 experiments/solve_suite/run.py --arm kuttaka --out results/solve-suite/kuttaka
    python3 experiments/solve_suite/run.py --arm fresh   --out results/solve-suite/fresh
    python3 experiments/solve_suite/run.py --arm alone   --out results/solve-suite/alone

Arms (same model, same prompts, in the same easy-to-hard order):
  kuttaka  `kuttaka solve` with one workspace kept across the whole suite (memory, breakdowns, lessons);
  fresh    `kuttaka solve` with a new, empty workspace for every task (the loop without memory from
           other tasks; with --rounds above 1, a task's later rounds keep what its earlier rounds stored);
  alone    the model alone: one call per task that returns run(input), no checks, no feedback.
Scoring uses only hidden.json, never shown to any arm. A task counts as solved when the delivered
method returns the expected value on every hidden case (numbers equal within 1e-9 relative).
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kuttaka_runtime.demo import load_method  # noqa: E402
from kuttaka_runtime.execution import PythonExecutor, preflight  # noqa: E402
from kuttaka_runtime.models import JsonModel, tcp_reachable  # noqa: E402
from kuttaka_runtime.runtime import TransportFailure  # noqa: E402
from kuttaka_runtime.solve import Metered, Solver, Workspace, build_models, round_signature  # noqa: E402

HERE = Path(__file__).parent
EXECUTIONS = 512   # sandbox runs per task, shared by its rounds (the kuttaka solve default)
INFRASTRUCTURE = 3  # exit code: the endpoint or sandbox is down; rerun the same command to resume
INFRA = object()    # a task's return value when the endpoint or sandbox is down


def infrastructure_ready(wait=1800):
    """Wait (no model call) until the endpoint answers TCP and the sandbox is ready."""
    delay, waited = 10, 0
    while True:
        if tcp_reachable(Args.base_url) and preflight()['ready']:
            return True
        if waited >= wait:
            return False
        time.sleep(delay)
        waited += delay
        delay = min(delay * 2, 120)
ALONE_INSTRUCTIONS = '''Solve the user's task by writing Python. Return exactly one JSON object, without
prose or Markdown fences: {"source": "<Python source defining run(input) and returning the result>"}.
Use only the Python standard library.'''


def same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int) and isinstance(b, int):
        # The same pre-registered 1e-9 relative tolerance, in exact integer arithmetic (no float overflow).
        return abs(a - b) * 10**9 <= max(1, abs(b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return abs(a - b) <= 1e-9 * max(1.0, abs(b))
        except OverflowError:
            return False
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


def run_cases(executor, source, libraries, cases):
    passed, called = 0, set()
    for case in cases:
        job = {'source': source, 'input': case['input']}
        if libraries:
            job['libraries'] = libraries
        try:
            response = executor(job)
            report = json.loads(response['output']) if response.get('exit_code') == 0 else {}
            called.update(response.get('dependency_calls') or [])
        except Exception:  # a sandbox or output failure fails this case
            report = {}
        if isinstance(report, dict) and 'result' in report and same(report['result'], case['expected']):
            passed += 1
    return passed, sorted(called)


class Args:
    """The CLI's model settings for the free NVIDIA endpoint (key added by the network proxy)."""
    protocol, key_env, network_secret = 'openai', 'NVIDIA_API_KEY', True
    base_url = 'https://integrate.api.nvidia.com/v1'
    model_timeout, max_tokens = 300, 16384

    def __init__(self, model):
        self.model = model


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--arm', choices=['kuttaka', 'fresh', 'alone'], required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--model', default='meta/muse-glimmer-30b')
    p.add_argument('--max-calls', type=int, default=30, help='hard limit per task')
    p.add_argument('--only', help='comma-separated task ids')
    p.add_argument('--rounds', type=int, default=1,
                   help='kuttaka/fresh: if a round ends incomplete, start another on the same workspace, '
                        'each with its own --max-calls')
    p.add_argument('--verbose', action='store_true',
                   help="print each solver step as it happens, prefixed by the task id (for a recorded demo)")
    p.add_argument('--jobs', type=int, default=1,
                   help='fresh/alone: tasks run side by side (each with its own workspace and limits); '
                        'kuttaka keeps 1, since its tasks share one workspace in order')
    p.add_argument('--conditions', action='store_true',
                   help='kuttaka/fresh: ask the planner for the stated conditions and the written derivation '
                        '(Solver(conditions=True); off by default, as in kuttaka solve)')
    p.add_argument('--suite', choices=['graded', 'growth', 'hard'], default='graded',
                   help='graded: SOLVE-SUITE-1 (tasks.json); growth: SOLVE-SUITE-2 (growth_tasks.json); '
                        'hard: SOLVE-SUITE-3 (hard_tasks.json)')
    args = p.parse_args(argv)
    if args.rounds < 1:
        p.error('--rounds must be at least 1')
    if args.jobs < 1 or (args.jobs > 1 and args.arm == 'kuttaka'):
        p.error('--jobs must be at least 1, and 1 for the kuttaka arm (one shared workspace, in order)')
    if not preflight()['ready']:
        sys.exit('Docker sandbox not ready')
    prefix = {'graded': '', 'growth': 'growth_', 'hard': 'hard_'}[args.suite]
    tasks = json.loads((HERE / (prefix + 'tasks.json')).read_text())
    hidden = json.loads((HERE / (prefix + 'hidden.json')).read_text())
    if args.only:
        tasks = [t for t in tasks if t['id'] in args.only.split(',')]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / 'rows.jsonl'
    # Rounds finished before an outage, kept so a resumed task continues instead of starting over.
    state_path = out / 'rounds_state.json'
    pending = json.loads(state_path.read_text()) if state_path.exists() else {}
    done = {json.loads(l)['task'] for l in rows_path.read_text().splitlines()} if rows_path.exists() else set()
    executor = PythonExecutor(timeout=20, trace='distinct')
    shared = Workspace(out / 'workspace')
    lock = threading.Lock()   # rows, the resume state and the console are shared by parallel tasks

    def say(text):
        with lock:
            print(text, flush=True)

    def save_pending(task_id, state=None):
        """Set (or with None, clear) one task's resume state, under the lock."""
        with lock:
            changed = (pending.pop(task_id, None) is not None) if state is None else True
            if state is not None:
                pending[task_id] = state
            if changed:
                state_path.write_text(json.dumps(pending, indent=1))

    def one(task):
        """Run one task to its row; INFRA when the endpoint or sandbox is down (nothing recorded)."""
        if not infrastructure_ready():
            say('endpoint or sandbox unavailable; stopping so the arm can be resumed')
            return INFRA
        started = time.time()
        counter = {'used': 0, 'limit': args.max_calls}
        row = {'task': task['id'], 'domain': task['domain'], 'level': task['level'], 'arm': args.arm,
               'request': task['prompt']}
        cases = hidden[task['id']]
        if args.arm == 'alone':
            model = Metered(JsonModel(args.model, Args.base_url, protocol='openai', key_env='NVIDIA_API_KEY',
                                      network_secret=True, parse_mode='extract', max_tokens=16384, timeout=300,
                                      stream=True, sampling_fields=False, reasoning_hint='low',
                                      instructions=ALONE_INSTRUCTIONS), counter)
            try:
                reply = model.propose({'task': task['prompt']})
            except TransportFailure as error:
                reply = {'transport_failure': error.kind}
            usage = model.last_usage or {}
            source = reply.get('source') if isinstance(reply.get('source'), str) else None
            passed, _ = run_cases(executor, source, {}, cases) if source else (0, [])
            row.update(status='delivered' if source else 'no_code', model_calls=counter['used'],
                       input_tokens=usage.get('input_tokens'), output_tokens=usage.get('output_tokens'),
                       reused=[], subtasks=0, source=source)
        else:
            ws = shared if args.arm == 'kuttaka' else Workspace(out / 'workspaces' / task['id'])
            # Each round has its own call limit and continues on the same workspace (as --rounds in
            # kuttaka solve); the row's calls and tokens are the totals over the rounds used, including
            # rounds finished before an outage. A round without one model reply is an outage: its
            # totals are kept and the task resumes at that round, never recorded as a model result.
            saved = pending.get(task['id'], {})
            solve_journals = list(saved.get('solve_journals') or [])
            usage = dict(saved.get('usage') or {'input_tokens': 0, 'output_tokens': 0, 'unknown_usage_calls': 0})
            used, replied = saved.get('used', 0), saved.get('replied', 0)
            mechanical, outage_calls = dict(saved.get('mechanical') or {}), saved.get('outage_calls', 0)
            executions = saved.get('executions', 0)   # one sandbox budget shared by all rounds, as in solve_task
            first = saved.get('next_round', 1)
            outage, previous = False, None
            resume_candidates = saved.get('resume_candidates') or {}
            resume_repairs = saved.get('resume_repairs') or {}
            resume_contract = saved.get('resume_contract')
            for number in range(first, args.rounds + 1):
                counter = {'used': 0, 'limit': args.max_calls}
                planner, actor, splitter, auditor, sketcher = build_models(Args(args.model), counter)
                solver = Solver(ws, planner, actor, executor, splitter=splitter, auditor=auditor, sketcher=sketcher,
                                max_executions=max(0, EXECUTIONS - executions),
                                say=(lambda text, tid=task['id']: say(f'{tid}: {text}')) if args.verbose
                                else (lambda text: None),
                                resume_candidates=resume_candidates, resume_repairs=resume_repairs,
                                resume_contract=resume_contract,
                                **({'conditions': True} if args.conditions else {}))
                prior_contract = resume_contract
                try:
                    result = solver.solve(task['prompt'])
                    resume_candidates = solver.resume_candidates()
                    resume_repairs = solver.resume_repairs()
                    resume_contract = solver.resume_contract()
                except Exception as error:  # an infrastructure failure is a failed task, recorded as such
                    result = {'status': 'error', 'reason': type(error).__name__}
                    resume_contract = None
                if not counter.get('replied'):
                    outage, outage_calls = True, outage_calls + counter['used']
                    # No new model information invalidated the prior provisional contract.
                    # It is still revalidated by Solver when this genuine outage resumes.
                    resume_contract = prior_contract
                    save_pending(task['id'], {'next_round': number, 'used': used, 'replied': replied,
                                 'usage': usage, 'mechanical': mechanical, 'outage_calls': outage_calls,
                                 'executions': executions, 'solve_journals': solve_journals,
                                 'resume_candidates': resume_candidates, 'resume_repairs': resume_repairs,
                                 'resume_contract': resume_contract})
                    break
                used, replied = used + counter['used'], replied + counter['replied']
                if result.get('solve_journal'):
                    solve_journals.append(result['solve_journal'])
                executions += result.get('execution_attempts', 0)
                for key in usage:
                    usage[key] += solver.usage[key]
                for key, value in (result.get('mechanical') or {}).items():
                    mechanical[key] = mechanical.get(key, 0) + value
                if result.get('status') == 'checked' or executions >= EXECUTIONS:
                    break
                signature = round_signature(result, ws)
                if number > first and signature == previous:
                    break   # aviśeṣa: the round changed nothing the next could use
                previous = signature
            counter = {'used': used, 'limit': args.max_calls * args.rounds, 'replied': 0 if outage else replied}
            passed = 0
            source = None
            if result.get('status') == 'checked':
                source, libraries = load_method(ws.memory, result['artifact'])
                passed, _ = run_cases(executor, source, libraries, cases)
            elif (result.get('unverified') or {}).get('artifact'):
                # Never less than the model alone: the unverified answer is scored too, on the same hidden cases,
                # and reported apart from the checked ones (the pre-registered `solved` counts checked only).
                fallback, libraries = load_method(ws.memory, result['unverified']['artifact'])
                unverified_passed, _ = run_cases(executor, fallback, libraries, cases)
                row.update(unverified_passed=unverified_passed, unverified_source=fallback,
                           unverified_artifact=result['unverified']['artifact'],
                           solved_unverified=unverified_passed == len(cases))
            row.update(status=result.get('status'), model_calls=used, **usage,
                       reused=result.get('reused', []), subtasks=len(result.get('subtasks', [])),
                       split_by=result.get('split_by'), checks_by=result.get('checks_by'),
                       contract=result.get('contract'), reason=result.get('reason'), source=source,
                       artifact=result.get('artifact'),
                       progress=result.get('progress'), strategies=result.get('strategies'),
                       rounds=number, solve_journals=solve_journals, mechanical=mechanical,
                       delivered_by=result.get('delivered_by'),
                       execution_attempts=executions,
                       outage_calls=outage_calls,
                       support=(result.get('evidence') or {}).get('support'), scale=result.get('scale'),
                       evidence=result.get('evidence'), void=result.get('void'),
                       **({'conditions': result.get('conditions'), 'derivation': result.get('derivation')}
                          if args.conditions else {}))
        if not counter.get('replied'):
            # Not one model reply: the network failed, not the model. Record nothing, resume later.
            say(f'{task["id"]}: no model reply in {counter["used"]} calls; stopping for resume')
            return INFRA
        row.update(hidden_passed=passed, hidden_cases=len(cases), solved=passed == len(cases),
                   replies=counter.get('replied', 0), seconds=round(time.time() - started, 1))
        with lock, rows_path.open('a') as handle:
            handle.write(json.dumps(row) + '\n')
        save_pending(task['id'])
        say(f'{task["id"]:16} {row["status"]:12} hidden {passed}/{len(cases)} calls {row["model_calls"]}'
            f' reused {len(row["reused"])} subtasks {row["subtasks"]} {row["seconds"]}s')
        return row

    todo = [t for t in tasks if t['id'] not in done]
    if args.jobs == 1:
        for task in todo:
            if one(task) is INFRA:
                sys.exit(INFRASTRUCTURE)
    else:
        # Independent tasks run side by side (fresh and alone arms only: each task has its own workspace).
        # Each task keeps its own call limit, rounds and sandbox budget; only wall time changes.
        stop = threading.Event()

        def worker(task):
            if stop.is_set():
                return None
            if one(task) is INFRA:
                stop.set()

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(worker, todo))
        if stop.is_set():
            sys.exit(INFRASTRUCTURE)
    rows = [json.loads(l) for l in rows_path.read_text().splitlines()]
    summary = {'arm': args.arm, 'model': args.model, 'tasks': len(rows), 'solved': sum(r['solved'] for r in rows),
               'model_calls': sum(r['model_calls'] for r in rows),
               'unsupported': sum(r['status'] == 'checked' and not r['solved'] for r in rows),
               # the like-for-like comparison with the model alone (scored on whatever code it returns):
               'solved_with_unverified': sum(r['solved'] or bool(r.get('solved_unverified')) for r in rows),
               'unverified_delivered': sum(1 for r in rows if 'unverified_passed' in r),
               'unverified_wrong': sum(1 for r in rows if 'unverified_passed' in r and not r.get('solved_unverified')),
               'by_level': {lv: sum(r['solved'] for r in rows if r['level'] == lv)
                            for lv in sorted({r['level'] for r in rows})}}
    (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    print(json.dumps(summary))


def keep_going(argv=None):
    """Like main, but after an infrastructure stop wait 5 minutes and resume, until every task has a row."""
    while True:
        try:
            return main(argv)
        except SystemExit as stop:
            if stop.code != INFRASTRUCTURE:
                raise
            print(f'resuming in 5 minutes ({time.strftime("%H:%M")})', flush=True)
            time.sleep(300)


if __name__ == '__main__':
    keep_going()
