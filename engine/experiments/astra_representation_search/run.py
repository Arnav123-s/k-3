"""Label-hidden authored fixture study for representation-lattice search.

All fixtures are developer-authored analytic streams. The search controller is
given only a bounded ``observe_next()`` callback; task names and future values
remain in this outer harness. This is an engineered-controller experiment,
not model-discovered mathematics or a blind generality evaluation.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
import platform
import tarfile
import tempfile
import time

from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.representation_search import RepresentationSearch
from kuttaka_runtime.store import Journal, canonical


ROOT = Path(__file__).resolve().parents[2]
LIMITS = dict(max_observations=16, max_fits=128, max_order=4,
              max_degree=4, max_columns=12, min_fresh=2)
ARMS = {
    "lattice": None,
    "constant_only_d0": [[r, 0] for r in range(1, 5)],
    "first_order_only_r1": [[1, d] for d in range(0, 5)],
}


def _r2d1(a, b, c, count=32):
    """x[n+1]=(a*n+b)*x[n]+c*x[n-1], seeded with x0=x1=1."""
    out = [1, 1]
    while len(out) < count:
        n = len(out) - 1
        out.append((a*n+b)*out[-1] + c*out[-2])
    return out[:count]


def fixtures():
    """Return evaluator-side fixture descriptions and finite rational streams."""
    arithmetic = list(range(32))
    oscillator = [0, 1, 0, -1]
    oscillator = (oscillator * 8)[:32]
    factorial = [1]
    for n in range(1, 32):
        factorial.append(n * factorial[-1])
    delayed = [1, 2, 3]
    while len(delayed) < 32:
        delayed.append(delayed[-1] + delayed[-3])
    noisy = _r2d1(1, 1, 1)
    noisy[12] += 7
    delayed_shift = delayed[:]
    delayed_change = delayed[:]
    delayed_change[12] += 7
    for n in range(12, 31):
        delayed_change[n+1] = delayed_change[n] + delayed_change[n-2]
    # Exact scalar relation (n-9)*x[n+1] + x[n] == 0 until its
    # leading coefficient vanishes at the next, n=9 transition. The changed
    # value at that singular boundary is intentionally still observable.
    future_zero = [Fraction(1)]
    for n in range(9):
        future_zero.append(future_zero[-1] / (9-n))
    future_zero.append(future_zero[-1] + 1)
    while len(future_zero) < 32:
        future_zero.append(future_zero[-1] + 1)
    # A long shared prefix is intentionally uninformative about later branches.
    branch_a = [0] * 6 + list(range(1, 27))
    branch_b = branch_a[:12] + [99, 100, 101, 102, 103, 104, 105, 106, 107, 108,
                                109, 110, 111, 112, 113, 114, 115, 116, 117, 118]
    return {
        "r2d1_base": _r2d1(1, 1, 1),
        "r2d1_variant_a": _r2d1(1, 2, 1),
        "r2d1_variant_b": _r2d1(2, 1, 2),
        "oscillator": oscillator,
        "arithmetic": arithmetic,
        "factorial_ratio": factorial,
        "noisy_counterexample": noisy,
        "delayed_shift": delayed,
        "delayed_change": delayed_change,
        "undersampled_all_zero": [0] * 32,
        "undersampled_budget": [2, 5, 11],
        "future_leading_zero": future_zero,
        "shared_prefix_a": branch_a,
        "shared_prefix_b": branch_b,
    }


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _one_run(output, fixture_name, values, arm_name, allowed_pairs):
    started = time.perf_counter()
    name = f"{fixture_name}__{arm_name}"
    store_path = output / "stores" / name
    journal_path = output / "journals" / f"{name}.jsonl"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    observed = []
    cursor = 0

    def observe_next():
        nonlocal cursor
        if cursor >= len(values):
            raise StopIteration("authored stream exhausted")
        value = str(values[cursor])
        cursor += 1
        observed.append(value)
        return value

    with Journal(journal_path) as journal, ArtifactMemory(store_path) as memory:
        controller = RepresentationSearch(
            memory, journal, observe_next, context="authored-finite-stream-v1",
            **LIMITS, allowed_pairs=allowed_pairs)
        report = controller.run()
        report = json.loads(canonical(report))
        trace = observed[:]
        journal.append("fixture_evaluation", {
            "fixture": fixture_name, "arm": arm_name,
            "observed_count": len(trace), "values": trace,
            "future_values_withheld": len(values)-len(trace),
            "attribution": "developer-authored fixture and host evaluator"})
    return {"fixture": fixture_name, "arm": arm_name, "report": report,
            "observed_values": trace, "stream_length": len(values),
            'elapsed_seconds': time.perf_counter()-started,
            'artifact_store_bytes': sum(p.stat().st_size for p in store_path.rglob('*') if p.is_file()),
            'journal_bytes': journal_path.stat().st_size}


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    all_results = []
    for fixture_name, values in fixtures().items():
        for arm_name, pairs in ARMS.items():
            all_results.append(_one_run(output, fixture_name, values, arm_name, pairs))
    source_paths = [Path(__file__)] + [ROOT / 'kuttaka_runtime' / name for name in
        ('representation_search.py', 'shift_relation.py', 'recurrence.py',
         'artifacts.py', 'store.py', 'inquiry.py', 'beliefs.py')]
    manifest = {
        "schema": "astra-representation-search-fixtures-v1",
        "attribution": "engineered controller; authored analytic fixture families",
        "endpoint_calls": 0, "limits": LIMITS,
        'python_version': platform.python_version(),
        "arms": {k: v for k, v in ARMS.items()},
        "code_sha256": {str(path.relative_to(ROOT)): _sha(path) for path in source_paths},
        'listed_source_bytes': sum(p.stat().st_size for p in source_paths),
        'cost_scope': 'same per-run ceilings; measured work/bytes/time differ; interpreter and OS overhead not included',
        "results": all_results,
    }
    (output / "results.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
    return manifest


def record(output):
    """Keep all journals/stores in a compact archive, with a readable summary."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='astra-representation-record-') as tmp:
        evidence = Path(tmp) / 'evidence'
        full = run(evidence)
        for relative in full['code_sha256']:
            target = evidence / 'sources' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        archive = output / 'evidence.tar.gz'
        file_count = 0
        with archive.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as bundle:
                for path in sorted(evidence.rglob('*')):
                    if not path.is_file() or path.name.endswith('.lock'):
                        continue
                    info = bundle.gettarinfo(str(path), arcname=path.relative_to(evidence).as_posix())
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ''
                    info.mode = 0o644
                    with path.open('rb') as source:
                        bundle.addfile(info, source)
                    file_count += 1
        metrics = ('observations', 'fit_calls', 'forecast_attempts', 'issued_predictions',
                   'correct_issued', 'wrong_issued', 'abstentions')
        aggregates = defaultdict(lambda: defaultdict(int))
        rows = []
        for item in full['results']:
            report = item['report']
            for key in metrics:
                aggregates[item['arm']][key] += report[key]
            rows.append({'fixture': item['fixture'], 'arm': item['arm'],
                         **{key: report[key] for key in metrics},
                         'status': report['status'], 'representation_status': report['representation_status'],
                         'artifact_store_bytes': item['artifact_store_bytes'],
                         'journal_bytes': item['journal_bytes'], 'elapsed_seconds': item['elapsed_seconds']})
        summary = {key: value for key, value in full.items() if key != 'results'}
        summary.update({'runs': len(rows), 'aggregates': dict(aggregates), 'results': rows,
                        'archive': {'file': archive.name, 'sha256': _sha(archive),
                                    'bytes': archive.stat().st_size, 'files': file_count}})
        (output / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="new output directory")
    parser.add_argument('--record', action='store_true', help='write compact summary and complete evidence archive')
    args = parser.parse_args()
    (record if args.record else run)(args.output)


if __name__ == "__main__":
    main()
