"""Every task, head to head: the model with Kuttaka against the same model alone, from pushed rows.

    python3 experiments/solve_suite/wins.py --with results/solve-suite/w2-glimmer-kuttaka \
        --alone results/solve-suite/h-glimmer-alone --out results/solve-suite/wins-w2-glimmer.md

Each task is classified from the hidden-case scores alone: WIN (with Kuttaka solved it, alone did
not), LOSS (alone solved it, with Kuttaka did not), BOTH, or NEITHER. A wrong answer delivered
(code returned but failing a hidden case) is counted for both arms. Losses are listed with the
wins. Every row cites the SHA-256 of the rows file it came from and of each delivered source, so
the table can be checked against the pushed results.

--rescored scores each arm from its rescored.json (rescore.py: the same delivered methods under the one
256 KiB sandbox input limit), and cites that file's SHA-256 too. An unavailable rescore is UNKNOWN,
never silently replaced by the as-run result. The as-run table stays the record; the ledger reports
both (rule of 2026-11-01).
"""

import argparse
import hashlib
import json
from pathlib import Path


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def load(folder, rescored=False):
    path = Path(folder) / 'rows.jsonl'
    raw = path.read_bytes()
    rows = {}
    for line in raw.decode().splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row['task']] = row
    digest = sha(raw)
    if rescored:
        again = Path(folder) / 'rescored.json'
        if not again.exists():
            raise SystemExit(f'{again} is missing: run rescore.py on this arm first')
        data = again.read_bytes()
        for row in rows.values():
            # A missing score is not a failed case, nor the old score under a new label.
            row['hidden_passed'] = None if row.get('source') else 0
            row['solved'] = None if row.get('source') else False
            row['solved_unverified'] = None if row.get('unverified_source') else False
            row['unverified_passed'] = None if row.get('unverified_source') else 0
        for entry in json.loads(data)['tasks']:
            row = rows.get(entry['task'])
            if row is not None and type(entry.get('passed_rescored')) is int:
                row['hidden_passed'], row['solved'] = entry['passed_rescored'], bool(entry['solved_rescored'])
            if row is not None and type(entry.get('unverified_passed_rescored')) is int:
                row['unverified_passed'] = entry['unverified_passed_rescored']
                row['solved_unverified'] = bool(entry['unverified_solved_rescored'])
        digest += ' (rescored.json ' + sha(data) + ')'
    return rows, digest


def delivered(row):
    return bool(row.get('source')) and row.get('status') in ('checked', 'delivered')


def outcome(a, b):
    if a is None or b is None:
        return 'UNKNOWN'
    return 'BOTH' if a and b else 'WIN' if a else 'LOSS' if b else 'NEITHER'


def either_score(a, b):
    if a is True or b is True:
        return True
    return None if a is None or b is None else False


def wrong_delivered(row):
    if not delivered(row):
        return False
    return None if row['solved'] is None else not row['solved']


def hidden_score(row):
    return f"{row['hidden_passed']}/{row['hidden_cases']}" if row['hidden_passed'] is not None else 'not rescored'


def compare(with_rows, alone_rows):
    table = []
    for task in with_rows:
        a, b = with_rows[task], alone_rows.get(task)
        if b is None:
            continue
        # Like for like: the model alone is scored on whatever code it returns, so Kuttaka's unverified answer
        # (returned when nothing is verified, never as checked) is scored too; reported beside the checked outcome.
        either = either_score(a['solved'], a.get('solved_unverified', False))
        table.append({
            'task': task, 'level': a.get('level'),
            'outcome': outcome(a['solved'], b['solved']),
            'outcome_with_unverified': outcome(either, b['solved']),
            'with': {'status': a['status'], 'hidden': hidden_score(a), 'solved': a['solved'],
                     'wrong_delivered': wrong_delivered(a), 'calls': a.get('model_calls'),
                     'delivered_by': a.get('delivered_by'), 'reused': len(a.get('reused') or []),
                     'source_sha256': sha(a['source']) if a.get('source') else None},
            'alone': {'status': b['status'], 'hidden': hidden_score(b), 'solved': b['solved'],
                      'wrong_delivered': wrong_delivered(b), 'calls': b.get('model_calls'),
                      'source_sha256': sha(b['source']) if b.get('source') else None}})
    return table


def totals(table):
    count = lambda key: sum(r['outcome'] == key for r in table)  # noqa: E731
    return {'tasks': len(table), 'wins': count('WIN'), 'losses': count('LOSS'), 'both': count('BOTH'),
            'neither': count('NEITHER'), 'unknown': count('UNKNOWN'),
            'solved_with': sum(r['with']['solved'] is True for r in table),
            'solved_alone': sum(r['alone']['solved'] is True for r in table),
            'wrong_delivered_with': sum(r['with']['wrong_delivered'] is True for r in table),
            'wrong_delivered_alone': sum(r['alone']['wrong_delivered'] is True for r in table),
            'unscored_with': sum(r['with']['solved'] is None for r in table),
            'unscored_alone': sum(r['alone']['solved'] is None for r in table),
            'calls_with': sum(r['with']['calls'] or 0 for r in table),
            'calls_alone': sum(r['alone']['calls'] or 0 for r in table),
            'with_unverified': {k.lower(): sum(r.get('outcome_with_unverified') == k for r in table)
                                for k in ('WIN', 'LOSS', 'BOTH', 'NEITHER', 'UNKNOWN')}}


def markdown(table, total, with_dir, alone_dir, with_hash, alone_hash, names=('With Kuttaka', 'alone')):
    lines = [f'# {names[0]} vs {names[1]}: `{with_dir}` vs `{alone_dir}`', '',
             f'rows.jsonl SHA-256: {names[0]} `{with_hash}`, {names[1]} `{alone_hash}`.', '',
             '| Outcome | Count |', '|---|---|']
    lines += [f'| {k} | {v} |' for k, v in total.items() if k != 'with_unverified']
    lines += ['', 'Counting unverified answers too (the model alone is scored on whatever code it returns): '
              + ', '.join(f'{k} {v}' for k, v in total.get('with_unverified', {}).items()) + '.']
    lines += ['', f'| Task | Level | Outcome | {names[0]}: status, hidden, calls, written by | {names[1]}: status, hidden |'
              f' Wrong answer delivered ({names[0]} / {names[1]}) |', '|---|---|---|---|---|---|']
    for r in table:
        w, a = r['with'], r['alone']
        wrong = lambda value: 'unknown' if value is None else 'yes' if value else 'no'
        lines.append(f"| {r['task']} | {r['level']} | **{r['outcome']}** | {w['status']}, {w['hidden']}, "
                     f"{w['calls']}, {w['delivered_by'] or '-'} | {a['status']}, {a['hidden']} | "
                     f"{wrong(w['wrong_delivered'])} / {wrong(a['wrong_delivered'])} |")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--with', dest='with_dir', required=True, help='the arm with Kuttaka (kuttaka or fresh)')
    p.add_argument('--alone', required=True, help='the model-alone arm, same model and suite')
    p.add_argument('--out', required=True, help='a .md file; the same name with .json is written too')
    p.add_argument('--names', default='With Kuttaka,alone',
                   help='labels for the two arms, e.g. "Kuttaka with memory,Kuttaka without memory"')
    p.add_argument('--rescored', action='store_true',
                   help="score both arms from their rescored.json (one 256 KiB sandbox input limit)")
    args = p.parse_args(argv)
    (with_rows, with_hash), (alone_rows, alone_hash) = (load(args.with_dir, args.rescored),
                                                        load(args.alone, args.rescored))
    table = compare(with_rows, alone_rows)
    total = totals(table)
    out = Path(args.out)
    out.write_text(markdown(table, total, args.with_dir, args.alone, with_hash, alone_hash,
                            tuple(args.names.split(',', 1))))
    out.with_suffix('.json').write_text(json.dumps({'with': args.with_dir, 'alone': args.alone,
        'rows_sha256': {'with': with_hash, 'alone': alone_hash}, 'totals': total, 'tasks': table}, indent=1) + '\n')
    print(json.dumps(total))


if __name__ == '__main__':
    main()
