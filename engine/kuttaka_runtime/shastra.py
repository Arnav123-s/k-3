"""Śāstra: the method library as one structured whole, the way the tradition kept its knowledge.

A śāstra holds rules (sūtra), the conditions under which each applies (paribhāṣā), worked examples
(udāharaṇa), rationales (upapatti) and a lineage of commentators who checked, corrected and extended
it (paramparā). Kuttaka's workspace holds the same parts, spread over its files; this module reads
them into one view:

    rule          a stored method's contract and source name
    applies to    the form of the inputs it was checked on, and the form of what it returns
    examples      its checks, with the support each had (given, two derivations, reference, ...)
    rationale     what it held on beyond its checks (property, reference, rival)
    uses          the stored methods it runs
    lineage       what it superseded (methods shown wrong on the way to it), what superseded it,
                  and whether it was itself shown wrong, with the reason
    open places   tasks left void (śūnya), by form: what is not known yet, kept in its place

Read only: nothing here changes the workspace, takes its writer lock or runs code. Stored methods are
read straight from their immutable, content-addressed records, each checked against its id, so the
library can be read while a solve is writing.
"""
import hashlib
import json
import zlib
from collections import defaultdict
from pathlib import Path

from . import pramana


def _payload(memory_dir, artifact):
    """A stored record by id, or None when it is absent or does not match its id."""
    try:
        raw = zlib.decompress((Path(memory_dir) / 'blobs' / (artifact + '.json.zlib')).read_bytes())
    except (OSError, zlib.error):
        return None
    if hashlib.sha256(raw).hexdigest() != artifact:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _dependencies(memory_dir):
    """What each stored method runs, from the memory's append-only event log (read, not locked)."""
    uses = {}
    try:
        lines = (Path(memory_dir) / 'events.jsonl').read_text().splitlines()
    except OSError:
        return uses
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue   # a line still being written
        if event.get('kind') == 'put' and isinstance(event.get('body'), dict):
            uses[event['body'].get('id')] = list(event['body'].get('dependencies') or [])
    return uses


def _rows(path):
    """The rows of a workspace log, skipping a last line still being written."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def build(workspace):
    """The library as a JSON-ready dict: methods (most recently used first), by input form, open places."""
    solved = _rows(workspace.solved_path)
    refuted = {r['artifact']: r['reason'] for r in _rows(workspace.refuted_path)}
    reused = defaultdict(int)
    for entry in _rows(workspace.history_path):
        for artifact in entry.get('reused') or []:
            reused[artifact] += 1
    first, last, order, used_for = {}, {}, {}, defaultdict(list)
    for position, row in enumerate(solved):
        first.setdefault(row['artifact'], row)          # the task it was made for: its rule
        last[row['artifact']] = row                     # its most recent use
        order[row['artifact']] = position               # ties within a second: later in the log is newer
        if row['task'] != first[row['artifact']]['task'] and row['task'] not in used_for[row['artifact']]:
            used_for[row['artifact']].append(row['task'])
    superseded_by = defaultdict(list)
    for row in solved:
        for old in row.get('supersedes') or []:
            if row['artifact'] not in superseded_by[old]:
                superseded_by[old].append(row['artifact'])
    methods, uses = [], _dependencies(workspace.memory)
    for artifact in sorted(last, key=lambda a: (last[a]['time'], order[a]), reverse=True):
        row = first[artifact]
        payload = _payload(workspace.memory, artifact)
        if payload is None:
            continue
        examples = (payload.get('applicability') or {}).get('examples') or []
        evidence = row.get('evidence') or {}
        methods.append({
            'artifact': artifact,
            'name': payload['body'].get('name'),
            'task': row['task'],
            'used_for': used_for.get(artifact, []),
            'rule': row.get('contract'),
            'applies_to': {'input': json.loads(row['form']) if row.get('form') else
                           (json.loads(pramana.form(examples)) if examples else None),
                           'output': json.loads(pramana.output_form(examples)) if examples else None},
            'examples': len(examples),
            'support': evidence.get('support', 'not recorded'),
            'support_by_check': evidence.get('checks', {}),
            'rationale': evidence.get('tested_beyond_checks', {}),
            # The analysis it was made from (bīja), recorded only under Solver(conditions=True): the stated
            # conditions with what tested each, and the written derivation, labelled unverified.
            'conditions': last[artifact].get('conditions') or [],
            'derivation': last[artifact].get('derivation'),
            'uses': uses.get(artifact, []),
            'reused': reused.get(artifact, 0),
            'lineage': {'supersedes': sorted({a for r in solved if r['artifact'] == artifact
                                              for a in r.get('supersedes') or []}),
                        'superseded_by': superseded_by.get(artifact, []),
                        'shown_wrong': refuted.get(artifact)},
            'time': last[artifact]['time'],
        })
    by_form = defaultdict(list)
    for method in methods:
        by_form[json.dumps(method['applies_to']['input'], sort_keys=True)].append(method['artifact'])
    solved_tasks = {row['task'] for row in solved}
    open_places = [{'task': l['task'], 'form': json.loads(l['form']) if l.get('form') else None,
                    'what_is_known': l['mistake']}
                   for l in _rows(workspace.lessons_path) if l['task'] not in solved_tasks]
    return {'methods': methods, 'by_form': dict(by_form), 'open': open_places,
            'counts': {'methods': len(methods), 'shown_wrong': sum(1 for m in methods if m['lineage']['shown_wrong']),
                       'forms': len(by_form), 'open': len(open_places)}}


def render(library):
    """The library as text, grouped by the form of the inputs each method takes."""
    lines = [f'{library["counts"]["methods"]} methods over {library["counts"]["forms"]} input forms; '
             f'{library["counts"]["shown_wrong"]} shown wrong; {library["counts"]["open"]} open places.']
    index = {m['artifact']: m for m in library['methods']}
    for form, artifacts in library['by_form'].items():
        lines.append('')
        lines.append(f'Takes {form}:')
        for artifact in artifacts:
            m = index[artifact]
            mark = ' [shown wrong: ' + m['lineage']['shown_wrong'] + ']' if m['lineage']['shown_wrong'] else ''
            beyond = ', '.join(f'{n} by {k}' for k, n in m['rationale'].items()) or 'none recorded'
            lines.append(f'  {artifact[:12]} {m["name"] or ""}: {m["rule"]}{mark}')
            lines.append(f'    returns {json.dumps(m["applies_to"]["output"])}; support {m["support"]}; '
                         f'held beyond its checks: {beyond}; reused {m["reused"]} times')
            for c in m['conditions']:
                lines.append(f'    condition: {c["condition"]} ({c.get("tested", "not tested")}; '
                             f'method {c.get("method", "not run")})')
            if m['derivation']:
                lines.append(f'    derivation ({m["derivation"]["status"]}): {m["derivation"]["text"]}')
            if m['uses']:
                lines.append('    uses ' + ', '.join(u[:12] for u in m['uses']))
            if m['lineage']['supersedes'] or m['lineage']['superseded_by']:
                lines.append('    lineage: supersedes ' + (', '.join(a[:12] for a in m['lineage']['supersedes']) or '-')
                             + '; superseded by ' + (', '.join(a[:12] for a in m['lineage']['superseded_by']) or '-'))
    if library['open']:
        lines.append('')
        lines.append('Open places (void: no verified answer yet):')
        for place in library['open']:
            lines.append(f'  {place["task"][:100]}: {place["what_is_known"][:160]}')
    return '\n'.join(lines)
