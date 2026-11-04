"""KUTTAKA-C3: stream lint (the evaluator's generation rules) and the pre-registered analysis.

    python -m kuttaka_runtime.c3 lint --stream S --holdout H [--sealed]
    python -m kuttaka_runtime.c3 analyze --stream S --kuttaka K_DIR --alone A_DIR

``lint`` checks the machine-checkable rules of ``experiments/kuttaka_c3/FAMILY_SPEC.md`` before the
evaluator hashes a stream. ``analyze`` reads the two scored arms (``campaign score`` output) and
applies the decision rules of ``docs/KUTTAKA_C3_PROTOCOL.md``. It calls ``campaign.compare``
unchanged for the C1 measures and adds the C3 ones; it never reruns or rescores anything.
Roles, levels and families live in the stream file but never reach the model: the runner passes
only request and checks.

ENGINEERED: this file is analysis code written before evaluation.
"""

import argparse
import json
import sys
from math import comb
from pathlib import Path

from .campaign import CampaignIneligible, _json_loads, _read_rows, compare, mcnemar
from .store import canonical

FAMILIES = ('lang', 'calendar', 'numbers', 'routes')
ROLES = ('base', 'extend', 'compose', 'variant')
TASK_KEYS = {'id', 'chain', 'family', 'level', 'role', 'needs', 'target', 'request', 'checks'}
SEALED_CHAINS_PER_FAMILY = 2
SEALED_CHAIN_LENGTH = 5
MIN_HOLDOUT = 6
MAX_REQUEST_CHARS = 6000
ALPHA = 0.05
# Phrases that point at another task or at memory: requests must be self-contained (rule G5).
REFERENCE_PHRASES = ('previous task', 'earlier task', 'prior task', 'task above', 'as before',
                     'stored method', 'use(', 'same as task', 'same inputs as', 'from memory',
                     'artifact', 'as in task', 'last task', 'previous step')


def _has_float(value):
    if type(value) is float:
        return True
    if isinstance(value, list):
        return any(_has_float(item) for item in value)
    if isinstance(value, dict):
        return any(_has_float(item) for item in value.values())
    return False


def lint_stream(stream, holdout, *, sealed=False):
    """Problems with a C3 stream and holdout, as strings; an empty list means the rules hold."""
    problems = []
    say = problems.append
    if not isinstance(stream, list) or not stream:
        return ['the stream must be a nonempty list']
    ids = [task.get('id') if isinstance(task, dict) else None for task in stream]
    if len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        return ['every task needs a unique nonempty string id']
    chains, order = {}, []
    for position, task in enumerate(stream):
        tid = task['id']
        extra = set(task) - TASK_KEYS
        if extra:
            say(f'{tid}: unknown keys {sorted(extra)} (G1)')
        for key in ('chain', 'family', 'request'):
            if not isinstance(task.get(key), str) or not task.get(key):
                say(f'{tid}: {key} must be a nonempty string (G1)')
        if task.get('family') not in FAMILIES:
            say(f'{tid}: family must be one of {", ".join(FAMILIES)} (G1)')
        if task.get('role') not in ROLES:
            say(f'{tid}: role must be one of {", ".join(ROLES)} (G1)')
        if type(task.get('level')) is not int:
            say(f'{tid}: level must be an integer (G1)')
        if not isinstance(task.get('needs'), list) or any(not isinstance(n, str) for n in task.get('needs', [])):
            say(f'{tid}: needs must be a list of task ids (G1)')
            task = dict(task, needs=[])
        chain = task.get('chain')
        if chain not in chains:
            chains[chain] = []
            order.append(chain)
        elif order[-1] != chain and sealed:
            say(f'{tid}: the tasks of chain {chain} must be contiguous in the stream (G2)')
        members = chains[chain]
        if members and task.get('family') != members[0].get('family'):
            say(f'{tid}: a chain has one family (G2)')
        if task.get('level') != len(members) + 1:
            say(f'{tid}: levels in a chain must run 1, 2, 3, ... in stream order (G2)')
        earlier = {member['id']: member for member in members}
        role, needs = task.get('role'), task.get('needs', [])
        if any(n not in earlier for n in needs) or len(set(needs)) != len(needs):
            say(f'{tid}: needs must name distinct earlier tasks of the same chain (G2)')
        if (role == 'base') != (len(members) == 0):
            say(f'{tid}: the first task of a chain, and only it, has role base (G2)')
        if role == 'base' and needs:
            say(f'{tid}: a base task needs nothing (G2)')
        if role == 'extend' and len(needs) < 1:
            say(f'{tid}: an extend task needs at least one earlier task (G2)')
        if role == 'compose' and len(needs) < 2:
            say(f'{tid}: a compose task needs at least two earlier tasks (G2)')
        if role == 'variant':
            target = task.get('target')
            if target not in earlier or earlier[target].get('role') == 'base' or needs != [target]:
                say(f'{tid}: a variant names an earlier non-base task of its chain as target and needs exactly it (G2)')
        elif 'target' in task:
            say(f'{tid}: only a variant has a target (G2)')
        checks = task.get('checks')
        minimum = 4 if role == 'base' else 3
        if (not isinstance(checks, list) or not minimum <= len(checks) <= 8
                or any(not isinstance(c, dict) or set(c) != {'input', 'expected'} for c in checks)):
            say(f'{tid}: needs {minimum} to 8 visible checks of exactly input and expected (G3)')
            checks = []
        inputs = [canonical(c['input']) for c in checks]
        if len(set(inputs)) != len(inputs):
            say(f'{tid}: visible check inputs must be distinct (G3)')
        if _has_float(checks):
            say(f'{tid}: checks must be exact JSON without floats (G4)')
        request = task.get('request') if isinstance(task.get('request'), str) else ''
        lowered = request.casefold()
        if not request.startswith('Write run(p).') or len(request) > MAX_REQUEST_CHARS:
            say(f'{tid}: a request starts with "Write run(p)." and has at most {MAX_REQUEST_CHARS} characters (G5)')
        for phrase in REFERENCE_PHRASES:
            if phrase in lowered:
                say(f'{tid}: request refers to other work ({phrase!r}); it must restate every rule (G5)')
        for other in ids:
            if other != tid and other.casefold() in lowered:
                say(f'{tid}: request names another task id {other!r} (G5)')
        cases = holdout.get(tid) if isinstance(holdout, dict) else None
        if (not isinstance(cases, list) or len(cases) < MIN_HOLDOUT
                or any(not isinstance(c, dict) or set(c) != {'input', 'expected'} for c in cases)):
            say(f'{tid}: needs at least {MIN_HOLDOUT} holdout cases of exactly input and expected (G6)')
            cases = []
        if any(canonical(c['input']) in inputs for c in cases):
            say(f'{tid}: a holdout input repeats a visible check input (G6)')
        if _has_float(cases):
            say(f'{tid}: holdout cases must be exact JSON without floats (G4)')
        members.append(task)
    if isinstance(holdout, dict) and set(holdout) != set(ids):
        say('holdout keys must equal the stream task ids (G6)')
    if sealed:
        families = [chains[c][0].get('family') for c in order]
        for family in FAMILIES:
            if families.count(family) != SEALED_CHAINS_PER_FAMILY:
                say(f'sealed: family {family} needs exactly {SEALED_CHAINS_PER_FAMILY} chains (S1)')
        if any(a == b for a, b in zip(families, families[1:])):
            say('sealed: consecutive chains must come from different families (S2)')
        for chain in order:
            roles = [t.get('role') for t in chains[chain]]
            if (len(roles) != SEALED_CHAIN_LENGTH or roles[0] != 'base' or roles[1] != 'extend'
                    or roles[2] not in ('extend', 'compose') or roles[3] != 'compose'
                    or roles[4] != 'variant'):
                say(f'sealed: chain {chain} must be base, extend, extend|compose, compose, variant (S1)')
    return problems


def _solved(row):
    return row.get('holdout_passed') is True


def _tokens(row):
    values = [row.get('input_tokens'), row.get('output_tokens')]
    return sum(values) if all(type(v) is int and v >= 0 for v in values) else None


def _sign_test(pairs):
    """One-sided exact sign test that the first member of each pair is smaller."""
    smaller = sum(a < b for a, b in pairs)
    larger = sum(a > b for a, b in pairs)
    n = smaller + larger
    p = sum(comb(n, i) for i in range(smaller, n + 1)) / 2 ** n if n else None
    return {'cheaper': smaller, 'dearer': larger, 'ties': len(pairs) - n, 'p_one_sided': p}


def _verdict(eligible, supported, available=True):
    if not eligible:
        return 'NOT ELIGIBLE'
    if not available:
        return 'NOT AVAILABLE'
    return 'SUPPORTED' if supported else 'NOT SUPPORTED'


def analyze(stream, kuttaka_rows, alone_rows):
    """Apply the KUTTAKA-C3 decision rules to two scored arms of the same stream."""
    base = compare(kuttaka_rows, alone_rows)  # unchanged C1 measures; raises on a bad pairing
    roles = {task['id']: task.get('role') for task in stream}
    k_ids = [row['task'] for row in kuttaka_rows]
    if k_ids != [task['id'] for task in stream] or any(roles[t] not in ROLES for t in k_ids):
        raise CampaignIneligible('scored rows must cover the stream in order, and every task needs a C3 role')
    eligible = base['claim_eligibility']['eligible']
    alone = {row['task']: row for row in alone_rows}
    pairs = [(row, alone[row['task']]) for row in kuttaka_rows]
    reuse_pairs = [(k, a) for k, a in pairs if roles[k['task']] != 'base']
    a1 = mcnemar([_solved(k) for k, _ in reuse_pairs], [_solved(a) for _, a in reuse_pairs])
    a1['tasks'] = len(reuse_pairs)
    solved_later = [k for k, _ in reuse_pairs if _solved(k)]
    reusing = [k for k in solved_later if k.get('executed_reused_artifacts')]
    variant_pairs = [(k, a) for k, a in pairs if roles[k['task']] == 'variant' and _solved(k) and _solved(a)]
    variant_tokens = [(_tokens(k), _tokens(a)) for k, a in variant_pairs]
    a5_available = bool(variant_pairs) and all(None not in pair for pair in variant_tokens)
    a5 = _sign_test([pair for pair in variant_tokens if None not in pair])
    a5.update({'pairs': len(variant_pairs), 'available': a5_available})
    a2 = base['A2_fewer_tokens_when_both_solve']
    a3 = base['A3_unsupported']
    by_role = {}
    for role in ROLES:
        rows = [(k, a) for k, a in pairs if roles[k['task']] == role]
        entry = {'tasks': len(rows)}
        for arm, index in (('KUTTAKA', 0), ('MODEL_ALONE', 1)):
            chosen = [pair[index] for pair in rows]
            known = [_tokens(row) for row in chosen]
            entry[arm] = {'solved': sum(_solved(row) for row in chosen),
                          'unsupported': sum(bool(row.get('unsupported')) for row in chosen),
                          'checked': sum(row.get('status') == 'checked' for row in chosen),
                          'model_calls': sum(row.get('model_calls') or 0 for row in chosen),
                          'tokens': sum(t for t in known if t is not None),
                          'tokens_unknown_tasks': sum(t is None for t in known),
                          'executed_reuse': sum(bool(row.get('executed_reused_artifacts'))
                                                for row in chosen if _solved(row))}
        by_role[role] = entry
    depth = [len(set(k.get('executed_reused_artifacts') or [])) for k in solved_later]
    return {
        'tasks': len(pairs),
        'claim_eligibility': base['claim_eligibility'],
        'solved': base['solved'],
        'A1_primary_reuse_eligible_tasks': dict(
            a1, verdict=_verdict(eligible, a1['p_one_sided'] <= ALPHA)),
        'A1_all_tasks_as_C1': dict(
            base['A1_solves_more'], verdict=_verdict(eligible, base['A1_solves_more']['p_one_sided'] <= ALPHA)),
        'A2_fewer_tokens_when_both_solve': dict(
            a2, verdict=_verdict(eligible, a2['p_one_sided'] is not None and a2['p_one_sided'] <= ALPHA,
                                 a2['available'])),
        'A3_unsupported': dict(a3, verdict=_verdict(eligible, a3['KUTTAKA'] <= a3['MODEL_ALONE'])),
        'A4_reuse_by_id': {'reusing': len(reusing), 'solved_reuse_eligible': len(solved_later),
                           'verdict': _verdict(eligible, bool(solved_later) and 3 * len(reusing) >= len(solved_later),
                                               bool(solved_later)),
                           'evidence': 'sandbox-reported execution of an earlier artifact; not causal proof'},
        'A5_variant_tokens': dict(
            a5, verdict=_verdict(eligible, a5['p_one_sided'] is not None and a5['p_one_sided'] <= ALPHA,
                                 a5_available)),
        'descriptive_by_role': by_role,
        'descriptive_reuse_depth': {'solved_reuse_eligible': len(depth),
                                    'distinct_earlier_artifacts_executed': sum(depth)},
        'model_calls': base['model_calls'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kuttaka-c3', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    lint = sub.add_parser('lint')
    lint.add_argument('--stream', required=True)
    lint.add_argument('--holdout', required=True)
    lint.add_argument('--sealed', action='store_true', help='also enforce the sealed size and order rules')
    report = sub.add_parser('analyze')
    report.add_argument('--stream', required=True)
    report.add_argument('--kuttaka', required=True, help='scored KUTTAKA arm folder (score.jsonl)')
    report.add_argument('--alone', required=True, help='scored MODEL_ALONE arm folder (score.jsonl)')
    args = parser.parse_args(argv)
    stream = _json_loads(Path(args.stream).read_text())
    if args.cmd == 'lint':
        problems = lint_stream(stream, _json_loads(Path(args.holdout).read_text()), sealed=args.sealed)
        print(json.dumps({'tasks': len(stream), 'problems': problems, 'ok': not problems}, indent=1))
        return 0 if not problems else 1
    result = analyze(stream, _read_rows(Path(args.kuttaka) / 'score.jsonl'),
                     _read_rows(Path(args.alone) / 'score.jsonl'))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
