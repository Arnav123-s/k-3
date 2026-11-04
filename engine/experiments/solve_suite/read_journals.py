"""Read an arm's solve journals: what each task's run did, and where its model calls and time went.

    python3 experiments/solve_suite/read_journals.py results/solve-suite/w4k-glimmer-kuttaka [--task text]

Reads only the arm's own folder (rows.jsonl, workspace/runs or workspaces/*/runs); never a suite task
file or a hidden case. For each solve journal, in the order its attempts ran: the request's first words,
then every event except the sandbox executions (checks, settlement, blind derivation, differential and
property testing with the disputed inputs and the auditor's values, substitution, the finish), then each
attempt's model calls (output tokens, finish reason, action kind). --task keeps journals whose request
contains the text. Long bodies are cut to --width characters.
"""

import argparse
import json
import re
from pathlib import Path

SKIP = {'solve_execution_intent', 'solve_execution_finished'}


def solve_journals(out):
    found = []
    for path in list(out.glob('workspace/runs/solve-*/solve.jsonl')) + list(out.glob('workspaces/*/runs/solve-*/solve.jsonl')):
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        runs = [e['body'].get('run') for e in events if e['kind'] == 'solve_attempt_finished' and e['body'].get('run')]
        stamp = min((re.search(r'(\d{4}-\d\d-\d\dT\d{6}Z)', r) or [None])[0] or '' for r in runs) if runs else ''
        found.append((stamp, path, events, runs))
    return sorted(found, key=lambda item: (item[0] == '', item[0]))


def calls(run_dir):
    path = Path(run_dir) / 'events.jsonl'
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        e = json.loads(line)
        if e['kind'] == 'model_reply':
            usage, action = e['body'].get('usage') or {}, e['body'].get('action') or {}
            out.append((usage.get('output_tokens'), usage.get('finish_reason'), action.get('kind'), action.get('tool')))
        elif e['kind'] == 'model_error':
            out.append(('error', e['body'].get('kind') or e['body'].get('type'), None, None))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('out', help='an arm folder (holding rows.jsonl)')
    p.add_argument('--task', default='', help='keep journals whose request contains this text')
    p.add_argument('--width', type=int, default=400)
    args = p.parse_args(argv)
    out = Path(args.out)
    root = out.resolve()
    for stamp, path, events, runs in solve_journals(out):
        request = next((e['body'].get('request', '') for e in events if e['kind'] == 'solve_started'), '')
        if args.task not in request:
            continue
        print(f'=== {stamp or "-"} {path.parent.name}: {request[:90]}')
        for e in events:
            if e['kind'] not in SKIP:
                print(f"  {e['kind']}: {json.dumps(e['body'])[:args.width]}")
        for run in runs:
            local = Path(run)
            if not local.is_absolute():   # recorded relative to the runner's checkout: find it under this folder
                parts = local.parts
                local = root / Path(*parts[parts.index('workspace'):]) if 'workspace' in parts else (
                    root / Path(*parts[parts.index('workspaces'):]) if 'workspaces' in parts else local)
            print(f'  attempt {local.name}: ' + ', '.join(f'{t} {f} {k}{"/" + tool if tool else ""}'
                                                        for t, f, k, tool in calls(local)))


if __name__ == '__main__':
    main()
