"""How each task was climbed: from the first failure to success, or not. Read from an arm's rows and journals.

    python3 experiments/solve_suite/climb.py results/solve-suite/w4k-glimmer-kuttaka [--rescored]

The owner's goal is a systematic thinker that claws its way from failure to success, not one right on the first
try. This report measures that, per task, from the arm's own folder (never a task file or a hidden case):
  first      what the first attempt did: 'checked' (it passed the checks), 'failed' (it did not), or 'refuted'
             (it passed, then the tests beyond the checks or the memorization readings refuted it)
  steps      the ladder steps the solver took after it (audit, repair, split, mati, replan, unmemorize, rival, ...)
  attempts   the attempts the solver made, and the checks passed at each (the progress curve)
  methods    the methods the model wrote across those attempts (more than one in a single attempt is a
             climb inside the attempt: it wrote, tested and wrote again)
  final      solved on the hidden cases (or rescored, with --rescored)
and classifies the task:
  FIRST_TRY    the first attempt passed with the first method written, and was right: nothing to climb
  CLIMBED_IN_ATTEMPT  the first attempt passed and was right, after more than one method within it
  CLIMBED      the first attempt failed or was refuted, and the task ended solved
  CLIMB_FAILED the first attempt failed or was refuted, and the task ended unsolved
  FALSE_STOP   the first attempt passed its checks, was delivered, and was wrong: a failure never perceived
  NOT_SCORED   the requested rescore is unavailable; the old score is not substituted

All rounds belong to their task. New runner rows identify their journals explicitly; older rows are
matched only by task workspace, exact request or an unambiguous contract, never by list position.
Missing or ambiguous journals are NOT_READ. The row's progress curve remains its final-round curve.
"""

import argparse
import json
from pathlib import Path


def journals(out):
    found = {}
    for path in list(out.glob('workspace/runs/solve-*/solve.jsonl')) + list(out.glob('workspaces/*/runs/solve-*/solve.jsonl')):
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        start = next((e['body'] for e in events if e['kind'] == 'solve_started'), {})
        relative = path.relative_to(out)
        task = relative.parts[1] if relative.parts[0] == 'workspaces' else None
        request = start.get('request', '')
        runs = [Path(e['body']['run']).name for e in events
                if e['kind'] == 'solve_attempt_finished' and e['body'].get('run')]
        # The first contract is the root task's; later contracts may belong to children.
        contract = next((e['body'].get('contract') for e in events if e['kind'] == 'solve_check_contract'), None)
        found.setdefault((task, request), []).append({'path': path, 'events': events,
            'stamp': min(runs) if runs else None, 'contract': contract})
    return found


def local_path(out, recorded):
    """Relocate a runner path without mistaking /workspace on the builder for the arm's workspace."""
    parts = Path(recorded).parts
    for i in range(len(parts) - 1, -1, -1):
        if (parts[i] == 'workspace' and parts[i + 1:i + 2] == ('runs',)) or (
                parts[i] == 'workspaces' and parts[i + 2:i + 3] == ('runs',)):
            path = out / Path(*parts[i:])
            return path if path.resolve().is_relative_to(out.resolve()) else None
    return None


def written(out, run):
    """Methods the model wrote in one attempt (its remember_method actions), from the attempt's own journal."""
    path = local_path(out, run)
    if path is not None and (path / 'events.jsonl').exists():
        return sum(1 for line in (path / 'events.jsonl').read_text().splitlines() if line.strip()
                   and json.loads(line)['kind'] == 'model_reply'
                   and (json.loads(line)['body'].get('action') or {}).get('tool') == 'remember_method')
    return None


def climb(events, out):
    attempts = [e['body'] for e in events if e['kind'] == 'solve_attempt_finished']
    refuted = {e['body'].get('artifact') for e in events if e['kind'] == 'solve_refuted'}
    first = None
    if attempts:
        a = attempts[0]
        first = ('refuted' if a.get('artifact') in refuted else 'checked') if a.get('status') == 'checked' else 'failed'
    methods = [written(out, a.get('run', '')) for a in attempts]
    in_first = methods[0] if methods else None
    return first, len(attempts), sum(methods) if all(n is not None for n in methods) else None, in_first


def matched_journals(rows, out):
    """Return journals with identity evidence; never shift later tasks when a journal is absent."""
    groups = journals(out)
    by_path = {item['path']: item for group in groups.values() for item in group}
    candidates = []
    for row in rows:
        keys = [key for key, group in groups.items() if (
            key[0] == row['task'] if key[0] is not None else bool(key[1]) and (
                key[1] == row.get('request', row['task']) or (
                    not row.get('request') and row.get('contract') is not None
                    and any(row['contract'] == item['contract'] for item in group))))]
        candidates.append(keys)
    claimed = set()
    matched = []
    for row, keys in zip(rows, candidates):
        explicit = row.get('solve_journals')
        if isinstance(explicit, list):
            paths = [local_path(out, p) for p in explicit if isinstance(p, str)]
            if (not paths or len(paths) != len(explicit) or len(set(paths)) != len(paths)
                    or any(p not in by_path for p in paths)):
                matched.append(([], 'missing explicit journal'))
                continue
            selected = [by_path[p] for p in paths]
            if row.get('request') and any(next((e['body'].get('request') for e in item['events']
                    if e['kind'] == 'solve_started'), None) != row['request'] for item in selected):
                matched.append(([], 'journal request does not match row'))
                continue
            reason = 'explicit journal paths'
        elif len(keys) == 1 and sum(keys[0] in others for others in candidates) == 1:
            selected = sorted(groups[keys[0]], key=lambda item: item['stamp'] or '~')
            if len(selected) > 1 and any(item['stamp'] is None for item in selected):
                matched.append(([], 'round order unavailable'))
                continue
            reason = 'unique task workspace, request or contract'
        else:
            # A replan changes the first contract; the row's final contract may still name one journal exactly.
            final = [item for group in groups.values() for item in group if row.get('contract') is not None
                     and not row.get('request') and sum(r.get('contract') == row['contract'] for r in rows) == 1
                     and any(e['kind'] == 'solve_check_contract'
                     and e['body'].get('contract') == row['contract'] for e in item['events'])]
            if len(final) != 1:
                matched.append(([], 'missing or ambiguous task identity'))
                continue
            selected, reason = final, 'the final contract, exactly'
        expected = row.get('rounds')
        if type(expected) is int and len(selected) != expected:
            matched.append(([], 'journal count does not match recorded rounds'))
            continue
        if any(item['path'] in claimed for item in selected):
            matched.append(([], 'journal already belongs to another row'))
            continue
        claimed.update(item['path'] for item in selected)
        matched.append((selected, reason))
    return matched


def report(out, rescored=False):
    rows = [json.loads(line) for line in (out / 'rows.jsonl').read_text().splitlines() if line.strip()]
    again = {}
    if rescored:
        data = json.loads((out / 'rescored.json').read_text())
        again = {t['task']: t for t in data['tasks'] if type(t.get('passed_rescored')) is int}
    table = []
    for row, (matched, reason) in zip(rows, matched_journals(rows, out)):
        solved = (again[row['task']]['solved_rescored'] if row['task'] in again else
                  None if row.get('source') else False) if rescored else row['solved']
        progress = row.get('progress')
        curve = progress if isinstance(progress, list) else (progress or {}).get('curve') if isinstance(progress, dict) else None
        steps = row.get('strategies') or []
        first, attempts, methods, in_first = climb([e for item in matched for e in item['events']], out)
        if solved is None:
            kind = 'NOT_SCORED'
        elif first is None or in_first is None:
            kind = 'NOT_READ'
        elif first == 'checked' and attempts == 1:
            kind = ('CLIMBED_IN_ATTEMPT' if in_first > 1 else 'FIRST_TRY') if solved else (
                'FALSE_STOP' if row.get('source') and row.get('status') in ('checked', 'delivered') else 'CLIMB_FAILED')
        else:
            kind = 'CLIMBED' if solved else 'CLIMB_FAILED'
        table.append({'task': row['task'], 'first': first, 'steps': steps, 'attempts': attempts, 'methods': methods,
                      'curve': curve, 'solved': solved, 'calls': row.get('model_calls'), 'class': kind,
                      'journals': [str(item['path'].relative_to(out)) for item in matched], 'journal_match': reason})
    counts = {k: sum(t['class'] == k for t in table)
              for k in ('FIRST_TRY', 'CLIMBED_IN_ATTEMPT', 'CLIMBED', 'CLIMB_FAILED', 'FALSE_STOP', 'NOT_READ', 'NOT_SCORED')}
    return {'arm': str(out), 'rescored': rescored, 'counts': counts, 'tasks': table}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('out', help='an arm folder (rows.jsonl and the journals under workspace/runs)')
    p.add_argument('--rescored', action='store_true')
    args = p.parse_args(argv)
    result = report(Path(args.out), args.rescored)
    for t in result['tasks']:
        print(f"{t['task']:16} {t['class']:18} first={t['first']} attempts={t['attempts']} methods={t['methods']} steps={t['steps']} "
              f"curve={t['curve']} solved={t['solved']} calls={t['calls']}")
    print(json.dumps(result['counts']))


if __name__ == '__main__':
    main()
