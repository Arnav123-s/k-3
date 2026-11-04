"""Score the methods an arm delivered again, on the same hidden cases, with the sandbox's one input limit.

Why: until 2026-11-01 the sandbox refused a job without libraries above 32 KiB
(`execution.LEGACY_INPUT_LIMIT`) before the method ran, and the scorer's jobs carry no libraries. A
hidden case with a large input (the hard suite's tasks allow a 100000-letter string, or a large graph)
therefore failed for every arm, whatever the method. Found from the rows, without opening a hidden
case: on h-c2-substrings and h-c3-maxflow, four different methods each (model alone, kuttaka, fresh,
W2-b) passed exactly the same number of cases.

What: each delivered source in `<out>/rows.jsonl` (the checked or delivered method, and the unverified
answer when there is one) runs again on that task's hidden cases with run.py's executor settings
(timeout 20 s, trace 'distinct'), now under the one 256 KiB limit. Nothing is asked of any model;
the methods are the ones the arm delivered. `<out>/rescored.json` is written beside the rows; the
as-run rows are never changed.

What it prints: per task, counts only (cases, cases whose job exceeds the old 32 KiB limit, passed as
run, passed rescored). No input or expected value is printed or written.

    python3 experiments/solve_suite/rescore.py --suite hard --out results/solve-suite/w4j-glimmer-kuttaka
"""

import argparse
import ast
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kuttaka_runtime.artifacts import ArtifactMemory  # noqa: E402
from kuttaka_runtime.campaign import _closure  # noqa: E402
from kuttaka_runtime.execution import LEGACY_INPUT_LIMIT, PythonExecutor, preflight  # noqa: E402

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from run import run_cases  # noqa: E402  (the same scoring loop and comparison as the run)


def over_legacy(source, cases, libraries=None):
    """Jobs affected by the old 32 KiB cap; jobs with libraries already had the 256 KiB limit."""
    if libraries:
        return 0
    return sum(1 for case in cases
               if len(json.dumps({'source': source, 'input': case['input']}, allow_nan=False,
                                 separators=(',', ':')).encode('utf-8')) > LEGACY_INPUT_LIMIT)


def _library_index(memory_dir):
    """Index all recorded artifacts, including methods held back by an answer gate.

    solved.jsonl contains successful deliveries only. The immutable artifact journal is the source
    of dependency identities for an unverified answer too. Reading a copy preserves the arm's log.
    Different closures for identical source are ambiguous without the original artifact id.
    """
    index, artifacts = {}, {}
    with tempfile.TemporaryDirectory() as scratch:
        snapshot = Path(scratch) / 'memory'
        shutil.copytree(memory_dir, snapshot, ignore=shutil.ignore_patterns('.writer.lock', '*.lock'))
        with ArtifactMemory(snapshot) as memory:
            for artifact in memory.ids(kind='method'):
                source = memory.get(artifact)['body'].get('source')
                if not isinstance(source, str):
                    continue
                libraries = {aid: memory.get(aid)['body']['source'] for aid in _closure(memory, artifact)[:-1]
                             if memory.get(aid)['kind'] == 'method'}
                artifacts[artifact] = (source, libraries)
                index[source] = libraries if source not in index or index[source] == libraries else None
    return index, artifacts


def _may_use_libraries(source):
    """Do not infer no dependencies from the solver's cleared ``reused`` delivery field."""
    try:
        nodes = ast.walk(ast.parse(source))
        return any((isinstance(node, ast.Name) and node.id == 'use') or
                   (isinstance(node, ast.Constant) and node.value == 'use') for node in nodes)
    except (SyntaxError, ValueError, RecursionError):
        return 'use' in source


def libraries_for(out, row, source, cache=None, artifact=None):
    """Recover the source's exact dependency closure, or mark it unavailable/ambiguous.

    New rows pin their artifact id; its recorded source must match before scoring. For old rows,
    only a standalone source with no evidence of stored-method use can be scored without a matching
    artifact. The cache avoids copying and replaying the whole shared memory for every delivered row.
    """
    cache = {} if cache is None else cache
    found = []
    for ws in (out / 'workspace', out / 'workspaces' / row['task']):
        memory_dir = ws / 'memory'
        if not memory_dir.is_dir():
            continue
        if memory_dir not in cache:
            try:
                cache[memory_dir] = _library_index(memory_dir)
            except (OSError, ValueError, KeyError, TypeError):
                cache[memory_dir] = {}, {}
        index, artifacts = cache[memory_dir]
        if artifact is not None:
            if isinstance(artifact, str) and artifact in artifacts:
                recorded_source, libraries = artifacts[artifact]
                if recorded_source != source:
                    return None, False
                found.append(libraries)
        elif source in index:
            found.append(index[source])
    if found:
        if found[0] is not None and all(libraries == found[0] for libraries in found):
            return found[0], True
        return None, False
    if artifact is None and not row.get('reused') and not row.get('subtasks') and not _may_use_libraries(source):
        return {}, True
    return None, False


def rescore(out, hidden, executor):
    rows = [json.loads(line) for line in (out / 'rows.jsonl').read_text().splitlines() if line.strip()]
    report, cache = [], {}
    for row in rows:
        cases = hidden[row['task']]
        entry = {'task': row['task'], 'status': row.get('status'), 'cases': len(cases),
                 'passed_as_run': row.get('hidden_passed'), 'solved_as_run': bool(row.get('solved'))}
        for key, label in (('source', ''), ('unverified_source', 'unverified_')):
            source = row.get(key)
            if not isinstance(source, str) or not source:
                continue
            entry[label + 'delivered'] = True
            if not cases:
                entry[label + 'rescored'] = 'not rescored: no cases supplied'
                continue
            libraries, ok = libraries_for(out, row, source, cache, artifact=row.get(label + 'artifact'))
            if not ok:
                entry[label + 'rescored'] = ('not rescored: artifact/source mismatch, missing stored methods, '
                                             'or ambiguous dependencies')
                continue
            entry[label + 'over_32k'] = over_legacy(source, cases, libraries)
            passed, _ = run_cases(executor, source, libraries, cases)
            entry[label + 'passed_rescored'] = passed
            entry[label + 'solved_rescored'] = passed == len(cases)
        if 'unverified_passed' in row:
            entry['unverified_passed_as_run'] = row['unverified_passed']
        report.append(entry)
    # A delivered method is scored only when the run delivered one; as in run.py, a row without a
    # checked (or, for the alone arm, delivered) source is unsolved either way.
    scored = [e for e in report if 'passed_rescored' in e]
    unscored = [e for e in report if e.get('delivered') and 'passed_rescored' not in e]
    unverified_unscored = [e for e in report if e.get('unverified_delivered') and
                          'unverified_passed_rescored' not in e]
    summary = {
        'tasks': len(report),
        'delivered': sum(bool(e.get('delivered')) for e in report),
        'rescored': len(scored),
        'not_rescored': len(unscored),
        'checked_rescored': sum(e['status'] == 'checked' for e in scored),
        'checked_not_rescored': sum(e['status'] == 'checked' for e in unscored),
        'unverified_delivered': sum(bool(e.get('unverified_delivered')) for e in report),
        'unverified_rescored': sum('unverified_passed_rescored' in e for e in report),
        'unverified_not_rescored': len(unverified_unscored),
        'complete': not unscored and not unverified_unscored,
        'solved_as_run': sum(e['solved_as_run'] for e in report),
        'solved_rescored': sum(bool(e.get('solved_rescored')) for e in report),
        'unsupported_as_run': sum(e['status'] == 'checked' and not e['solved_as_run'] for e in report),
        'unsupported_rescored': sum(e['status'] == 'checked' and not e.get('solved_rescored') for e in scored),
        'solved_with_unverified_rescored': sum(bool(e.get('solved_rescored') or e.get('unverified_solved_rescored'))
                                               for e in report),
        'cases_over_32k': sum(e.get('over_32k', 0) for e in report),
    }
    return {'rule': 'same delivered methods, same hidden cases, one 256 KiB sandbox input limit',
            'summary': summary, 'tasks': report}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--suite', choices=['graded', 'growth', 'hard'], default='hard')
    p.add_argument('--out', required=True, help='an arm folder holding rows.jsonl')
    args = p.parse_args(argv)
    if not preflight()['ready']:
        sys.exit('Docker sandbox not ready')
    prefix = {'graded': '', 'growth': 'growth_', 'hard': 'hard_'}[args.suite]
    hidden = json.loads((HERE / (prefix + 'hidden.json')).read_text())
    out = Path(args.out)
    result = rescore(out, hidden, PythonExecutor(timeout=20, trace='distinct'))
    (out / 'rescored.json').write_text(json.dumps(result, indent=1) + '\n')
    for e in result['tasks']:
        print(f"{e['task']:16} {str(e['status']):10} cases {e['cases']} over_32k {e.get('over_32k', '-')}"
              f" as_run {e['passed_as_run']} rescored {e.get('passed_rescored', '-')}", flush=True)
    print(json.dumps(result['summary']))


if __name__ == '__main__':
    main()
