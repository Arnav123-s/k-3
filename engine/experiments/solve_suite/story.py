"""The climb of one task, told from a recorded run: the demo replay (no model call, no sandbox, no key).

    python3 experiments/solve_suite/story.py results/solve-suite/w4k-glimmer-kuttaka --task h-c1-regex \
        [--alone results/solve-suite/h-glimmer-alone] [--html story.html]
    python3 experiments/solve_suite/story.py results/solve-suite/w4k-glimmer-kuttaka --list

Reads only the arm's own folder (rows.jsonl, its solve journals and each attempt's journal) and, with --alone,
the model-alone arm's rows: never a suite task file or a hidden case (the hidden score is the row's count).
It prints, in order: the task; the contract and checks the planner wrote, and what tested them before code
(the reference, the property, the blind re-derivation); each attempt, with the methods the model wrote and
what the checks said of each (passed, or which check failed and what the method returned); the steps the
solver took after a failure (repair, split, replan, mati, ...); the tests beyond the checks (differential,
property, rivals, memorization, scale) and what they refuted; what was delivered and with what support; the
hidden score, and the same model alone on the same task. Then the climb class (climb.py).
"""

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import climb as climbing  # noqa: E402


def short(value, width=160):
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= width else text[:width - 1] + '…'


def attempt_lines(out, run):
    """What happened inside one attempt: model replies by kind, methods written, and their validations."""
    path = climbing.local_path(out, run)
    lines, written, replies = [], 0, 0
    if path is None or not (path / 'events.jsonl').exists():
        return ['(attempt journal not in this folder)'], 0, 0
    for line in (path / 'events.jsonl').read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        kind, body = e['kind'], e['body']
        if kind == 'model_reply':
            replies += 1
            action = body.get('action') or {}
            if action.get('tool') == 'remember_method':
                written += 1
                name = (action.get('input') or {}).get('name') or '?'
                lines.append(f'model writes method #{written} ({short(name, 60)})')
            elif action.get('kind') == 'invalid':
                lines.append('model reply not valid JSON (rejected, fed back)')
            elif action.get('kind') == 'unresolved':
                lines.append(f'model says unresolved: {short(action.get("reason", ""), 120)}')
            elif action.get('tool'):
                lines.append(f'model calls {action["tool"]}')
        elif kind == 'host_experiment':
            lines.append(f'host runs {body.get("tool")} ({body.get("reason")}, no model call)')
        elif kind == 'method_validation':
            total = len(body.get('check_receipts') or [])
            if body.get('status') == 'qualified':
                lines.append(f'  checks: all {total} pass')
            elif body.get('status') == 'failed':
                failed = body.get('failed_checks') or []
                bad = len(body.get('counterexample_checks') or failed)
                detail = '; '.join(
                    f'{short(f.get("input"), 60)} -> '
                    + (f'got {short(f["actual"], 40)}' if 'actual' in f else
                       'error ' + short((f.get('diagnostics') or '').strip().splitlines()[-1:] or ['?'], 60))
                    + f', expected {short(f.get("expected"), 40)}' for f in failed[:2] if isinstance(f, dict))
                lines.append(f'  checks: {total - bad}/{total} pass' + (f' (fails: {detail})' if detail else ''))
            else:
                lines.append(f'  checks: {body.get("status")} ({len(body.get("infrastructure_checks") or [])} not completed)')
    return lines, written, replies


def story(out, task, alone=None):
    rows = [json.loads(line) for line in (out / 'rows.jsonl').read_text().splitlines() if line.strip()]
    index = next((i for i, r in enumerate(rows) if r['task'] == task), None)
    if index is None:
        raise SystemExit(f'{task}: no row in {out}')
    row = rows[index]
    matched, reason = climbing.matched_journals(rows, out)[index]
    events = [e for item in matched for e in item['events']]
    say = []
    start = next((e['body'] for e in events if e['kind'] == 'solve_started'), {})
    say.append(('task', f'{task} (level {row.get("level")}, {row.get("domain")}): {start.get("request") or row.get("request") or ""}'))
    contracts = seen_attempts = 0
    for e in events:
        kind, body = e['kind'], e['body']
        if kind == 'solve_started' and contracts:
            say.append(('round', f'next round (limit {body.get("model_call_limit")} calls), on the same workspace'))
        elif kind == 'solve_check_contract' and body.get('checks'):
            contracts += 1
            if contracts == 1 or body.get('revisions'):
                say.append(('plan', f'{len(body["checks"])} checks written by the planner: '
                                    + short([[c['input'], c['expected']] for c in body['checks']], 300)))
        elif kind == 'solve_given_conditions':
            parts = []
            if body.get('reference_values'):
                parts.append(f'the slow reference ran on {body["reference_values"]} checks')
            if body.get('property_verdicts'):
                parts.append(f'the property judged {body["property_verdicts"]}')
            if body.get('revised'):
                parts.append(f'{len(body["revised"])} hand value(s) corrected')
            if body.get('set_aside'):
                parts.append(f'{len(body["set_aside"])} set aside')
            say.append(('settle', 'before any code: ' + (', '.join(parts) or 'nothing to test the checks with')))
        elif kind == 'solve_blind_derivation':
            disputed = body.get('disputed') or []
            say.append(('settle', f'blind re-derivation of the hand-only checks: {len(body.get("agreed") or [])} agreed, '
                                  f'{len(disputed)} disputed' + (' (set aside)' if body.get('set_aside') else '')))
        elif kind in ('solve_prastara',) and body.get('found'):
            say.append(('reuse', f'a composition of stored methods fits, no model call: {body["found"]}'))
        elif kind == 'solve_attempt_finished':
            seen_attempts += 1
            lines, written, replies = attempt_lines(out, body.get('run', ''))
            outcome = 'passes every check' if body.get('status') == 'checked' else f'ends {body.get("status")} ({body.get("reason")})'
            say.append(('attempt', f'attempt {seen_attempts}: {replies} model call(s), {written} method(s) written; {outcome}'))
            say.extend(('detail', line) for line in lines)
        elif kind == 'solve_differential':
            confirmed = body.get('confirmed') or []
            say.append(('test', f'differential test against the reference on {body.get("inputs")} new inputs: '
                                f'{body.get("disputed", 0)} disagreements, {len(confirmed)} confirmed by a blind derivation'
                                + (': ' + short([[c['input'], c['expected']] for c in confirmed], 200) if confirmed else '')))
        elif kind in ('solve_property_test', 'solve_property'):
            say.append(('test', f'property test: {short({k: v for k, v in body.items() if k in ("inputs", "rejected", "confirmed", "outcome")}, 200)}'))
        elif kind == 'solve_scale_test':
            slow = body.get('timed_out') or []
            count = slow if isinstance(slow, int) else len(slow)
            say.append(('test', 'scale test: not run (no large input was built)' if body.get('outcome') == 'not_run'
                        else f'scale test on {len(body.get("runs") or [])} input(s) at the top of the range: '
                        + (f'{count} ran out of time or memory' if count else 'finished in time')))
        elif kind == 'solve_substitution':
            say.append(('test', 'memorization reading (each constant changed): '
                                + short({k: body[k] for k in body if k in ('outcome', 'flagged', 'memorized')} or body, 160)))
        elif kind == 'solve_refuted':
            say.append(('refuted', f'method refuted: {body.get("reason")} -> the climb continues'))
        elif kind.startswith('solve_split') or kind == 'solve_piece_budget':
            if kind == 'solve_piece_budget':
                say.append(('split', f'piece: {short(body.get("request", ""), 120)} ({body.get("calls")} calls leased)'))
            elif kind in ('solve_split', 'solve_split_outcome'):
                say.append(('split', f'{kind[6:]}: {short({k: body[k] for k in body if k in ("subtasks", "outcome", "status")}, 200)}'))
        elif kind == 'solve_delivery_refused':
            say.append(('refused', f'delivery refused: {short(body.get("reason") or body.get("by") or body, 160)}'))
        elif kind == 'solve_finished' and body.get('status'):
            say.append(('finish', f'solver reports {body["status"]} after {body.get("model_calls")} model calls'))
    support = row.get('support')
    say.append(('delivered', f'{row.get("status")}, delivered by {row.get("delivered_by")}; evidence: {short(support, 200)}'))
    say.append(('score', f'hidden cases (never shown to the solver): {row.get("hidden_passed")}/{row.get("hidden_cases")}; '
                         f'{row.get("model_calls")} model calls, {row.get("seconds")} s'))
    if alone is not None:
        other = next((json.loads(line) for line in (alone / 'rows.jsonl').read_text().splitlines()
                      if line.strip() and json.loads(line)['task'] == task), None)
        if other:
            say.append(('alone', f'the same model alone: {other.get("hidden_passed")}/{other.get("hidden_cases")} '
                                 f'hidden cases, {other.get("model_calls")} call, {other.get("seconds")} s'))
    first, attempts, methods, in_first = climbing.climb(events, out)
    solved = row.get('solved')
    if first is None or in_first is None:
        kind = 'NOT_READ'
    elif first == 'checked' and attempts == 1:
        kind = ('CLIMBED_IN_ATTEMPT' if in_first > 1 else 'FIRST_TRY') if solved else 'FALSE_STOP'
    else:
        kind = 'CLIMBED' if solved else 'CLIMB_FAILED'
    say.append(('climb', f'{kind}: first attempt {first}, {attempts} attempt(s), {methods} method(s) written, '
                         f'steps {row.get("strategies") or []}; journals matched by {reason}'))
    return say


PAGE = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kuttaka climb</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d8;--good:#1f7a4d;--bad:#b3261e;--accent:#3d5a99}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151513;--fg:#ecebe6;--muted:#9c9a93;--line:#2c2b27;--good:#5cc290;--bad:#f2867d;--accent:#8fa9e6}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0;padding:24px 16px}}
main{{max-width:880px;margin:auto}} h1{{font-size:20px}} .row{{display:grid;grid-template-columns:96px 1fr;gap:12px;
padding:6px 0;border-top:1px solid var(--line)}} .k{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
.detail .v{{color:var(--muted);font-family:ui-monospace,monospace;font-size:13px;padding-left:16px}}
.refuted .v,.refused .v{{color:var(--bad)}} .score .v,.climb .v{{font-weight:600}} .alone .v{{color:var(--accent)}}
</style></head><body><main><h1>{title}</h1>{rows}</main></body></html>'''


def to_html(task, lines):
    rows = ''.join(f'<div class="row {html.escape(k)}"><div class="k">{html.escape(k)}</div>'
                   f'<div class="v">{html.escape(v)}</div></div>' for k, v in lines)
    return PAGE.format(title=html.escape(f'How Kuttaka climbed {task}'), rows=rows)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('out', help='an arm folder (rows.jsonl and its journals)')
    p.add_argument('--task', help='the task id')
    p.add_argument('--alone', help="the model-alone arm's folder, for the comparison line")
    p.add_argument('--html', help='also write the story as a page')
    p.add_argument('--list', action='store_true', help='list the tasks with their climb class')
    args = p.parse_args(argv)
    out = Path(args.out)
    if args.list or not args.task:
        for t in climbing.report(out)['tasks']:
            print(f"{t['task']:16} {t['class']:18} solved={t['solved']} calls={t['calls']} steps={t['steps']}")
        return
    lines = story(out, args.task, Path(args.alone) if args.alone else None)
    for kind, text in lines:
        print(('      ' if kind == 'detail' else f'{kind:>9}: ') + text)
    if args.html:
        Path(args.html).write_text(to_html(args.task, lines))


if __name__ == '__main__':
    main()
