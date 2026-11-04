# Live study harness: model-authored shift methods with optional fitting

This directory contains a bounded paired development-study runner. The protocol
and implementation were frozen at commit `d5c143a` after independent review.
The live runner contacts the configured NVIDIA endpoint.
Imports and the focused unit tests do not make network or Docker calls.

The model receives a 12-value public prefix and may author/store/validate a
Python method through the same ordinary tools in both arms. In the treatment,
`fit_relation(order, degree)` adds only the existing exact finite fitter over the
prefix already revealed by the observer. It returns a certificate and receipt;
it does not select dimensions, forecast, or save a method. Every prospective
observation requires the model's prediction in the same committed experiment.
Only four new values can be revealed. Method output uses reduced rational strings
with positive denominators and integers without `/1`; scoring separately records
mathematical equality and exact string formatting. The fitting source and
certificates are engineered support; model-generated hypotheses, method source,
and final exact artifact ID are recorded separately.

Fixture seeds are 1171, 1173, and 1177, paired in fixed sequential arm order.
Seed 1177 is a deliberate all-zero prefix followed by an alteration at index 14;
it tests negative evidence and is not evidence about an infinite law. Seed 1181
is used only after acquisition for no-model fresh-process reuse. The runner
evaluates every final artifact from all six acquisitions on both its acquisition
continuation after all values revealed during acquisition and seed 1181. Missing
artifacts and every failed execution remain in the result set. Each acquisition
run is capped at 47 Python executions and reserves two more for one fresh-process
check on each continuation, for a total of 49 executions per acquired artifact.

The runner retains each acquisition journal and artifact store, task manifests,
private fixture provenance, CLI output, and fresh-process reuse journals. Its
public stdout is limited to a status, call count, and output path. Credentials
are read only by the HTTP client from `NVIDIA_API_KEY`; they are never accepted
on the command line or written to study files.

Run focused non-live tests with:

```sh
python -B -m unittest tests.test_astra_constructor_live -v
```

`freeze.json` binds the reviewed sources and configuration. Later integration
adds runtime files, so use a detached checkout of the frozen commit rather than
the moving branch. A live run requires a new private `.astra` output directory;
it is a separate experiment and cannot replace an earlier outcome:

```sh
git worktree add --detach /tmp/constructor-v1 d5c143a
cd /tmp/constructor-v1
python -B -m experiments.astra_constructor_live.run --output .astra/new-run
```

The runner executes each paired arm sequentially, then invokes the existing
`kuttaka_runtime reuse` CLI in separate processes with zero provider calls. Each
reuse invocation reopens the original memory and requests the exact model-final
artifact ID; it does not choose an earlier successful candidate.

After all six acquisitions and their reuse checks finish, the reporting-only
module in the current checkout can project the private evidence without provider
responses or full contexts:

```sh
python -m experiments.astra_constructor_live.report \
  --run-root /path/to/frozen-source/.astra/completed-run \
  --source-root /path/to/frozen-source --output /path/to/new-report
```

Missing outcomes and integrity failures remain explicit in the report. Known
token subtotals are separate from complete totals. Report generation makes no
model or sandbox calls and does not alter the frozen experiment.
