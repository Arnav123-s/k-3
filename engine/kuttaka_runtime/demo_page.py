"""Self-contained HTML trace page for ``python -m kuttaka_runtime.demo --html``.

Everything on the page comes from the records ``demo.show`` builds from the journals and the
sandbox re-execution; nothing is typed in by hand.
"""

from html import escape

STYLE = """
/* Layout: one column per chain, tasks in stream order; reuse drawn as ochre tags that name the earlier task. */
:root {
  --bg: #f1f4f3; --panel: #ffffff; --fg: #18211e; --muted: #5b6863; --line: #d6dedb;
  --accent: #0f6a62; --reuse: #a8581a; --bad: #a3392a; --code: #e8eeec;
  --display: "Yrsa", Georgia, "Times New Roman", serif;
  --body: "Source Sans 3", "Segoe UI", system-ui, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #111816; --panel: #18211e; --fg: #e3ebe8; --muted: #93a39d; --line: #2b3733;
  --accent: #4fb3a8; --reuse: #e0954f; --bad: #e0705e; --code: #0c1210; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #111816; --panel: #18211e; --fg: #e3ebe8; --muted: #93a39d; --line: #2b3733;
  --accent: #4fb3a8; --reuse: #e0954f; --bad: #e0705e; --code: #0c1210; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font-family: var(--body); font-size: 15px; line-height: 1.5; }
.wrap { max-width: 1180px; margin: 0 auto; padding-block: 32px 48px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
header { display: grid; gap: 10px; margin-bottom: 26px; }
.eyebrow { font: 600 12px/1 var(--mono); letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }
h1 { font: 600 clamp(28px, 4vw, 42px)/1.1 var(--display); margin: 0; text-wrap: balance; max-width: 30ch; }
.lede { color: var(--muted); max-width: 68ch; margin: 0; }
.tally { display: flex; flex-wrap: wrap; gap: 8px 22px; margin: 6px 0 0; padding: 14px 0; border-block: 1px solid var(--line); }
.tally div { display: grid; gap: 2px; }
.tally b { font: 600 24px/1 var(--display); font-variant-numeric: tabular-nums; }
.tally span { font-size: 12px; color: var(--muted); letter-spacing: .02em; }
.tally .reuse b { color: var(--reuse); }
.chains { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 18px; align-items: start; }
.chain { display: grid; gap: 12px; min-width: 0; }
.chain h2 { font: 600 20px/1.2 var(--display); margin: 0; text-transform: capitalize; }
.task { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 14px 15px; display: grid; gap: 9px; min-width: 0; }
.task.failed { border-style: dashed; }
.head { display: flex; justify-content: space-between; gap: 8px; align-items: baseline; }
.tid { font: 600 13px/1.3 var(--mono); }
.verdict { font: 600 12px/1 var(--mono); padding: 4px 7px; border-radius: 4px; white-space: nowrap; }
.verdict.ok { color: var(--accent); border: 1px solid var(--accent); }
.verdict.no { color: var(--bad); border: 1px solid var(--bad); }
.req { margin: 0; font-size: 13.5px; color: var(--muted); }
ol.steps { margin: 0; padding: 0 0 0 18px; display: grid; gap: 3px; font-size: 13.5px; }
ol.steps li::marker { color: var(--muted); font: 12px var(--mono); }
li.store { color: var(--accent); }
li.error { color: var(--bad); }
.reusetag { font-size: 13px; color: var(--reuse); border-left: 3px solid var(--reuse); padding: 2px 0 2px 9px; }
.meta { font: 11.5px/1.4 var(--mono); color: var(--muted); overflow-wrap: anywhere; }
details summary { cursor: pointer; font-size: 13px; color: var(--accent); }
details summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
pre { background: var(--code); border-radius: 4px; padding: 10px; margin: 8px 0 0; overflow-x: auto;
  font: 12px/1.45 var(--mono); max-height: 320px; }
footer { margin-top: 34px; padding-top: 16px; border-top: 1px solid var(--line); display: grid; gap: 8px; color: var(--muted); font-size: 13.5px; max-width: 80ch; }
footer code { font: 12.5px var(--mono); background: var(--code); padding: 1px 5px; border-radius: 3px; overflow-wrap: anywhere; }
"""


def _short(value):
    return value[:12] if isinstance(value, str) else '-'


def _task(record):
    if record.get('holdout'):
        passed, total = record['holdout']
        solved = passed == total
        verdict = f'{passed}/{total} unseen' if solved else f'unsupported {passed}/{total}'
    elif record.get('status') == 'checked':
        solved, verdict = True, 'checked'
    else:
        solved, verdict = False, 'not solved'
    parts = [f'<article class="task{"" if solved else " failed"}">',
             '<div class="head">', f'<span class="tid">{escape(record["task"])}</span>',
             f'<span class="verdict {"ok" if solved else "no"}">{escape(verdict)}</span>', '</div>']
    if record.get('request'):
        parts.append(f'<p class="req">{escape(record["request"])}</p>')
    if record.get('steps'):
        parts.append('<ol class="steps">')
        parts += [f'<li class="{escape(kind)}">{escape(text)}</li>' for text, kind in record['steps']]
        parts.append('</ol>')
    for item in record.get('reuse', []):
        parts.append(f'<div class="reusetag">Reuse by id: ran <code>{_short(item["artifact"])}</code>, '
                     f'the method it wrote on <b>{escape(item["origin"])}</b></div>')
    if record.get('source'):
        parts.append('<details><summary>Method the model wrote</summary>'
                     f'<pre>{escape(record["source"])}</pre></details>')
    integrity = 'journal intact' if record.get('intact') else 'JOURNAL FAILED INTEGRITY'
    parts.append(f'<div class="meta">{integrity} · {record.get("model_calls")} model calls'
                 f' · head {_short(record.get("journal_sha256"))}</div>')
    parts.append('</article>')
    return '\n'.join(parts)


def render(records):
    meta = records[0]['meta']
    summary = records[0].get('summary', {})
    tasks = records[1:]
    chains = []
    for record in tasks:
        name = record.get('chain') or 'tasks'
        if not chains or chains[-1][0] != name:
            chains.append((name, []))
        chains[-1][1].append(record)
    tally = [('holdout_solved', 'solved on unseen inputs', ''), ('unsupported', 'unsupported answers', ''),
             ('reused_by_id', 'solutions that ran an earlier method', 'reuse'),
             ('model_calls', 'model calls', ''), ('journals_intact', 'journals intact', '')]
    out = ['<title>Kuttaka Trace</title>',
           '<meta name="claude:viewport-fit-sides" content="cover">',
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Yrsa:wght@500;600'
           '&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap">',
           f'<style>{STYLE}</style>', '<div class="wrap">', '<header>',
           f'<div class="eyebrow">{escape(meta["model"])} · {escape(meta["arm"])} arm · {summary.get("tasks", "-")} tasks</div>',
           '<h1>A frozen model writes, checks, keeps and reuses its own methods</h1>',
           '<p class="lede">Each card is one task, in the order the model met it. The steps are read from a '
           'hash-chained journal. The verdict comes from re-running the delivered method in a fresh sandbox on '
           'inputs the model never saw. An ochre line appears only when the sandbox trace shows the code called a '
           'method the model itself wrote on an earlier task.</p>',
           '<div class="tally">']
    for key, label, cls in tally:
        value = summary.get(key, '-')
        if key in ('holdout_solved', 'journals_intact'):
            value = f'{value}/{summary.get("tasks", "-")}'
        out.append(f'<div class="{cls}"><b>{escape(str(value))}</b><span>{escape(label)}</span></div>')
    out += ['</div>', '</header>', '<main class="chains">']
    for name, items in chains:
        out.append(f'<section class="chain"><h2>{escape(name)}</h2>')
        out += [_task(record) for record in items]
        out.append('</section>')
    out += ['</main>', '<footer>',
            '<p>The runtime and this page are engineered by the development agents. The methods shown were written '
            'by the model during the run; only a method that a later task executed by its artifact id counts as '
            'runtime discovery.</p>',
            '<p>These are builder-authored development tasks with no model-alone arm, so this page is a '
            'demonstration, not a comparison. The sealed comparison is KUTTAKA-C1.</p>',
            '<p>Reproduce: <code>python3 -m kuttaka_runtime.demo --run results/kuttaka-c1-dev/run '
            '--holdout experiments/kuttaka_c1/dev_holdout.json --html trace.html</code></p>',
            '</footer>', '</div>']
    return '\n'.join(out) + '\n'
