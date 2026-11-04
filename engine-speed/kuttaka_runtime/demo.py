"""Show a finished Kuttaka campaign: discover, verify, store, then reuse by artifact id.

    python -m kuttaka_runtime.demo --run <campaign dir> [--holdout <file>] [--chain numbers]

No model is called. For each task the demo:
  1. checks the task journal's hash chain and that its head equals the row's ``journal_sha256``;
  2. tells what the model did, from the journal (look-ups, methods written, checks, reuse by id);
  3. re-executes the delivered method, with every stored method it depends on, in a fresh
     sandboxed process on inputs the model never saw (``--holdout``). It prints which earlier
     artifacts the code actually called (the runner's ``dependency_calls`` trace).

Attribution (CLAUDE.md): this program and the runtime are ENGINEERED. Only methods the model
wrote during the run, stored, and later executed by id count as runtime discovery.
"""

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

from .artifacts import ArtifactMemory
from .campaign import _closure, _read_rows
from .execution import PythonExecutor, preflight
from .store import Journal, canonical

TOOL_WORDS = {'find_methods': 'searched stored methods', 'inspect_method': 'read stored method',
              'propose_method': 'wrote a method', 'validate_method': 'ran the visible checks on',
              'probe_method': 'ran a stored method on a trial input'}


def short(artifact):
    return artifact[:12] if isinstance(artifact, str) else '-'


def narrate(events):
    """Plain steps from one task journal: (text, kind) pairs."""
    steps = []
    for event in events:
        kind, body = event['kind'], event['body']
        if kind == 'model_reply':
            action = body.get('action') or {}
            if action.get('kind') == 'answer':
                answer = action.get('answer') or {}
                steps.append(('model answered with method ' + short(answer.get('artifact')), 'answer'))
                continue
            if action.get('kind') == 'invalid':
                steps.append(('reply was not a valid action (spent, no tool run)', 'error'))
                continue
            tool = action.get('tool')
            data = action.get('input') or {}
            target = data.get('artifact') or data.get('name') or data.get('query') or ''
            uses = data.get('uses') or []
            text = TOOL_WORDS.get(tool, tool or 'replied')
            if tool in ('inspect_method', 'validate_method', 'probe_method'):
                text += ' ' + short(target)
            elif data.get('source') is not None:
                text = 'wrote method ' + repr(data.get('name') or '')
                if uses:
                    text += ' using ' + ', '.join(short(u) for u in uses)
            steps.append((text, 'model'))
        elif kind == 'model_error':
            steps.append(('model call failed in transport: ' + str(body.get('type', '?')), 'error'))
        elif kind == 'method_remembered':
            uses = body.get('uses') or []
            steps.append(('stored method ' + short(body['artifact'])
                          + (' (depends on ' + ', '.join(short(u) for u in uses) + ')' if uses else ''),
                          'store'))
        elif kind == 'method_validation':
            receipts = body.get('check_receipts') or []
            steps.append(('visible checks passed for ' + short(body.get('artifact'))
                          + ' (' + str(len(receipts)) + ' receipts)', 'verify'))
        elif kind == 'finish':
            steps.append(('finished: ' + str(body.get('status')) + ', ' + str(body.get('model_calls'))
                          + ' model calls', 'finish'))
    return steps


def load_method(memory_dir, artifact):
    """Source of ``artifact`` plus the sources of everything it depends on, read from a copy."""
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / 'memory'
        shutil.copytree(memory_dir, copy, ignore=shutil.ignore_patterns('.writer.lock', '*.lock'))
        with ArtifactMemory(copy) as memory:
            order = _closure(memory, artifact)
            libraries = {a: memory.get(a)['body']['source'] for a in order[:-1]
                         if memory.get(a)['kind'] == 'method'}
            source = memory.get(artifact)['body']['source']
    return source, libraries


def execute(executor, source, libraries, case):
    job = {'source': source, 'input': case['input']}
    if libraries:
        job['libraries'] = libraries
    try:
        response = executor(job)
    except Exception as error:  # a sandbox failure is a failed case, not a crash of the demo
        return False, [], type(error).__name__
    if not (isinstance(response, dict) and response.get('status') == 'completed'
            and response.get('exit_code') == 0 and isinstance(response.get('output'), str)):
        return False, [], 'execution did not complete'
    try:
        report = json.loads(response['output'])
    except ValueError:
        return False, [], 'unreadable output'
    ok = canonical(report.get('result')) == canonical(case['expected'])
    return ok, list(response.get('dependency_calls') or []), None


def show(run, holdout=None, chain=None, executor=None, out=sys.stdout, records=None):
    """Print the walkthrough; when ``records`` is a list, also append one dict per task for --html."""
    records = [] if records is None else records
    run = Path(run)
    manifest = json.loads((run / 'campaign.json').read_text()) if (run / 'campaign.json').exists() else {}
    rows = [r for r in _read_rows(run / 'rows.jsonl') if chain is None or r.get('chain') == chain]
    arm = manifest.get('arm', 'KUTTAKA')
    say = lambda text='': print(text, file=out)
    model = '?'
    for row in rows[:1]:
        try:
            start = next(e for e in Journal.read(run / 'runs' / row['task'] / 'events.jsonl') if e['kind'] == 'start')
            model = start['body']['model']['model']
        except (OSError, ValueError, StopIteration, KeyError, TypeError):
            pass
    records.append({'meta': {'run': run.name, 'arm': arm, 'model': model}})
    say(f'Kuttaka demo: {run.name}  arm={arm}  model={model}')
    say('No model is called here; everything below is read from the hash-chained journals and re-executed.')
    summary = {'tasks': 0, 'checked': 0, 'holdout_solved': 0, 'unsupported': 0, 'reused_by_id': 0,
               'model_calls': 0, 'journals_intact': 0}
    origin = {}
    for row in rows:
        summary['tasks'] += 1
        summary['model_calls'] += row.get('model_calls') or 0
        say()
        say(f'== {row["task"]}')
        record = {'task': row['task'], 'chain': row.get('chain'), 'status': row.get('status'),
                  'model_calls': row.get('model_calls'), 'journal_sha256': row.get('journal_sha256'),
                  'artifact': row.get('artifact'), 'steps': [], 'reuse': [], 'intact': True}
        records.append(record)
        journal = run / 'runs' / row['task'] / 'events.jsonl'
        if row.get('journal_sha256') is None and not journal.exists():
            summary['journals_intact'] += 1
            say(f'   not started: {row.get("status")}')
            continue
        try:
            events = Journal.read(journal)
            intact = bool(events) and events[-1]['hash'] == row.get('journal_sha256')
        except (OSError, ValueError):
            events, intact = [], False
        summary['journals_intact'] += intact
        record['intact'] = intact
        say(f'   journal {"intact, head matches the row" if intact else "FAILED integrity or head check"}')
        request = next((e['body'].get('request') for e in events if e['kind'] == 'program_task'), '')
        record['request'] = request
        record['steps'] = narrate(events)
        if request:
            say('   task: ' + request[:160] + ('...' if len(request) > 160 else ''))
        for text, _ in narrate(events):
            say('   - ' + text)
        artifact = row.get('artifact')
        if row.get('status') != 'checked' or not artifact:
            say('   result: not solved')
            continue
        summary['checked'] += 1
        origin.setdefault(artifact, row['task'])
        cases = (holdout or {}).get(row['task'], [])
        if not cases or executor is None:
            continue
        memory_dir = run / 'memory' if arm == 'KUTTAKA' else run / 'memory' / row['task']
        source, libraries = load_method(memory_dir, artifact)
        record['source'] = source
        passed, called = 0, set()
        for case in cases:
            ok, calls, _ = execute(executor, source, libraries, case)
            passed += ok
            called.update(calls)
        earlier = sorted(a for a in called if a != artifact and origin.get(a) not in (None, row['task']))
        record['holdout'] = [passed, len(cases)]
        say(f'   unseen inputs: {passed}/{len(cases)} correct in a fresh sandboxed process')
        if passed == len(cases):
            summary['holdout_solved'] += 1
        else:
            summary['unsupported'] += 1
            say('   UNSUPPORTED: passed its visible checks but failed unseen inputs')
        if earlier:
            summary['reused_by_id'] += 1
            for a in earlier:
                record['reuse'].append({'artifact': a, 'origin': origin[a]})
                say(f'   REUSE BY ID: executed {short(a)}, a method the model wrote on {origin[a]}')
    say()
    say('Summary: ' + json.dumps(summary))
    records[0]['summary'] = summary
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(prog='kuttaka-demo', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', required=True, help='finished campaign directory (rows.jsonl, runs/, memory/)')
    p.add_argument('--holdout', help='{task_id: [{input, expected}]} inputs the model never saw')
    p.add_argument('--chain', help='show only this chain')
    p.add_argument('--execution-timeout', type=float, default=10)
    p.add_argument('--html', help='also write a self-contained trace page to this file')
    args = p.parse_args(argv)
    holdout = json.loads(Path(args.holdout).read_text()) if args.holdout else None
    executor = None
    if holdout is not None:
        if not preflight()['ready']:
            print('Docker or the pinned Python image is unavailable; showing the journals only', file=sys.stderr)
        else:
            executor = PythonExecutor(timeout=args.execution_timeout)
    records = []
    summary = show(args.run, holdout, args.chain, executor, records=records)
    if args.html:
        from .demo_page import render
        Path(args.html).write_text(render(records), encoding='utf-8')
    return 0 if summary['journals_intact'] == summary['tasks'] else 1


if __name__ == '__main__':
    sys.exit(main())
