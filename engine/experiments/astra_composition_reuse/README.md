# Reusing a live-model method through the current composition runtime

This is a zero-model-call development demonstration. It imports one exact method
artifact acquired in an earlier live run and tests whether the current runtime
can compose it into a new related task.

## Source method and attribution

The imported artifact is the integer-equation solver
`24864fb307094d52eaa6f7e14b3aff33a9cf3e18ee2b782a6e727f5fc110223c`.
Its original payload is copied byte-for-byte to
`recorded/original-method.json` from
`experiments/astra_live/recorded/payloads/24864fb307094d52eaa6f7e14b3aff33a9cf3e18ee2b782a6e727f5fc110223c.json`
at source commit `a97845b2b0a513c331fa4ae996548b8e9e4b3be0`. The payload's
content identity is the artifact ID above. Its provenance says
`meta/muse-glimmer-30b`; the public result row
`tasks-v2/integer-equation/acquire` at that commit records 5 model calls, a
checked result, and a qualified finite-check status. The source payload and
summary row, not raw provider logs, are retained here.

The new wrapper is authored engineering. It applies the imported equation
solver to the relation `m*k + n*y = b-a`, then constructs the least
nonnegative CRT solution modulo `lcm(m,n)`. Two batches cover coprime and
non-coprime moduli, an inconsistent pair, and negative residues. Expected
outputs come from independent host enumeration over each joint period. A
separate declared-but-unused control passes its own simple exact check while
declaring the same dependency; its invocation trace should remain empty.

The successful run returned `[8, 5, null]` and `[14, 4, 4]` for the two CRT
batches. Both exact checks passed. The source method was called three times in
each batch; its imported artifact is reported as `component_only` in the
wrapper's scope. The declared-but-unused control passed with an empty call
trace. Full results, cooperative invocation receipts, sandbox cleanup metadata,
and the event journal are under `recorded/runs/20261029T001838Z-pid51629/`.

Two earlier harness attempts executed the same three sandbox cases but stopped
while assembling the summary because of harness assertion/reporting mistakes.
Their journals and `incomplete_harness_reporting` result files remain beside
the successful run. All nine container cleanup receipts across those attempts
and the successful run returned exit code zero. They also contain "No such
container" diagnostics where automatic removal had already removed the
container; the explicit cleanup did not find a still-running container to
remove. A final container listing was empty.

## Limits

This shows that a method produced and checked in an earlier live-model run can
be imported, composed, and executed by the current runtime, with a cooperative
sandbox trace of calls by ID. The wrapper was authored for this demonstration;
there was no new model call, method discovery claim, model-alone comparison, or
independent transfer evaluation. Finite checks establish only the stated cases.
The invocation trace is sandbox-reported cooperative metadata, not tamper-proof
or causal proof.

## Reproduce

With the pinned Python image already present, run:

```sh
python -B -m experiments.astra_composition_reuse.run
```

The script refuses to overwrite prior runs. It records the imported artifact,
wrapper source, checks, execution receipts, and validation outcomes under
`recorded/runs/`. It makes no model or network calls and does not pull images.

## Recheck after parallel-branch integration

At integrated runtime `7404fb4`, run `recorded/runs/20261029T021902Z-pid61332/`
again passed the six independently enumerated cases and declared-unused
control. It made zero model calls, executed three sandboxes, recorded six child
invocations and zero unused-control invocations, with no cleanup failures. This
is a separate integration recheck; the earlier reporting failures remain.
