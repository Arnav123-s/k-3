# Forward-stream representation search (authored fixture study)

This study asks whether the same bounded, label-hidden search controller can
select among a small declared representation lattice, and whether its
restricted `d=0` and `r=1` variants lose coverage on streams requiring the
excluded dimension. It is an engineering test of a procedure built by the
developers. Fixture generators, controller, arithmetic domains and limits are
all supplied; no model invents these methods during evaluation.

Run with a new output path:

```sh
python -m experiments.astra_representation_search.run --output /tmp/astra-representation-search-run
python -m experiments.astra_representation_search.run --record --output /tmp/astra-representation-search-record
```

The script executes every authored stream under three arms at the same limits
(16 observations, 128 fits, order and degree at most 4, 12 columns, and two
fresh checks). The only controller input from the fixture is a sequential
`observe_next()` callback returning a rational string. Fixture names, future
terms and scoring remain in the outer harness. The record contains per-run
results, observed values, separate journals and artifact stores, plus hashes
of the harness and controller source.

The streams include arithmetic and periodic controls, factorial-like growth,
coefficient variants of `x[n+1]=(a*n+b)*x[n]+c*x[n-1]`, a delayed recurrence,
an altered term within the observed window, a short three-value stream,
all-zero underdetermination, a rational stream whose fitted leading polynomial
has a root at the next transition, and a pair with a shared prefix.
All are developer-authored and finite. The shared-prefix pair must yield the
same controller decisions until observations differ; this tests information
boundaries, not that a particular fit is historically or scientifically
correct. An observed mismatch is retained as evidence and can invalidate a
candidate. No observation is treated as proof of continuation.

The comparisons are descriptive and bounded to this fixture set. The two
restricted arms test whether selected lattice dimensions matter here; they do
not establish a universal advantage, autonomous mathematical discovery, or
transfer to an unspecified domain. `results.json` reports failures, abstentions,
budget use and correctness alongside successes.

The committed [summary](recorded/summary.json) has 42 runs and these aggregates:

| Arm | Correct issued | Wrong issued | Abstentions | Fits |
|---|---:|---:|---:|---:|
| Combined search | 64 | 3 | 144 | 795 |
| Constant coefficients only | 32 | 1 | 178 | 204 |
| First order only | 30 | 1 | 180 | 279 |

Each arm observes 211 values in total; the short stream supplies three. Maximum
fit calls in any run is 123, below its 128 ceiling. More coverage costs more work
and exposes the combined search to more wrong predictions. On some individual
fixtures a restricted arm does better.

[`evidence.tar.gz`](recorded/evidence.tar.gz) retains all 42 journals, all artifact
stores, full decisions, source snapshots and the full manifest. Its SHA-256 is in
the summary. The archive was extracted and checked: 6,910 journal events validate,
all 42 stores reopen, and archived/current source hashes match. Empty lock files
are omitted. The compact archive avoids adding over 1,600 individual evidence files.

The [research note](../../docs/research/predictive-state/REPRESENTATION_SEARCH.md)
explains the literature, exact certificates, historical seed obligations, the
earlier exploratory result, and limits. No result is a calibrated guarantee of
future correctness. The observer is a trusted interface, not a Python sandbox.
