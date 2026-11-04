# Observed checkpoint at execution interruption

The execution workspace became unavailable on 29 October 2026 while the second
v10 source-qualification campaign was running. The cloud status tool still
reported running/connected/credential-ready, but two execution attempts returned
"No usable execution environment is available." No retained diagnostic capture
was available. This mismatch does not establish a cause, process termination,
or the eventual outcomes of the running jobs.

This is an authored checkpoint from observed tool outputs, not a reconstructed
journal or a substitute for the original evidence projection. The full new
journals, provider receipts, source snapshots and failed attempts were saved at
`/workspace/kuttaka/.astra/investigator-2026-10-28/`. Their current accessibility
and the running jobs' eventual results must be checked after environment recovery.

## Concrete result

Muse Glimmer generated a portable prediction-evidence controller and corrected
its own stale-support and representation-type failures. Actual Docker fixtures
demonstrated different revisions after two supporting versus two failing
predictions, continuation from retained representations, retraction after later
counterevidence, and handling of string/list/number/boolean/null representations.
The protocol smoke used scripted replies and is not live task-performance evidence.

The [exact v10 candidate](candidate-v10.json) was recovered from its previously
observed output and verified before saving here:

- Source: 7,435 bytes, SHA-256
  `9c35846efb7c8e4b95b66cd9bb358dc8affc57459f9e9884d195870b54c4eb87`.
- Canonical candidate JSON SHA-256:
  `9a81b283ef018d500bddc640163869ff39633e0335615e30e90aa053bed63854`.
- Exact equality with the original parsed provider reply had also been checked
  in the workspace before interruption. No engineering agent changed its source.
- Acquisition used the same `meta/muse-glimmer-30b` model, medium reasoning,
  6,000 response tokens and a 300-second timeout; reported usage was 3,667 input
  and 3,215 output tokens.
- Experiment selection and final predictor construction remain model-mediated.
  This is a narrow evidence-revision procedure, not the full proposed
  hypothesis-pruning investigator or a demonstrated transfer advantage.

## Completed live source runs

| Candidate / source run | Actual outcome | Model calls | Known input / output tokens |
| --- | --- | ---: | ---: |
| v2 affine 803 | Incomplete: policy sandbox timeout; no final predictor | 9 | 12,826 / 11,140 |
| v2 piecewise 809 | Unresolved: exhausted budget; no final predictor | 10 | 13,224 / 15,084 |
| v7 affine 907 | Passed all 25 checks, including 17 unqueried cases | 10 | 18,728 / 15,953 |
| v7 piecewise 911 | Request 7 timed out after six observations; no answer | 7 | 9,108 / 9,774, partial |
| v10 affine 907, 4,096-token profile | Passed all 25 checks; two live representation revisions | 10 | 30,668 / 17,481 |
| v10 piecewise 911, 4,096-token profile | Unresolved: four observations, six responses truncated at 4,096 tokens, no answer | 10 | 25,764 / 32,861 |

The initial v10 affine and piecewise runs took 509.493 and 840.668 seconds at the
invocation level. All response-cap failures returned HTTP 200 with
`finish_reason: length`; they are not successful actions. The v7 timed-out
request has unknown usage, which is excluded from the known partial totals.

A completed-evidence export preflight verified 28 original journal chains and
separately hashed six acquisition attempts stored as individual files. It
contained 18 live acquisition calls (five with unknown usage) and these six
source runs (56 attempted model calls, one with unknown usage). Known acquisition
totals were 109,157 input / 52,550 output tokens; known completed-source totals
were 110,318 / 102,293. Scripted fixtures were accounted for separately and made
no NVIDIA requests. These totals exclude the unobserved remainder of the active
8,192-token campaign. The preflight output was temporary; the final committed
evidence projection and private archive remain to be produced from the originals.

Other retained failures include forwarding adapters, ignored answers, erased
conflicting observations, a no-progress revision loop, a host hashing TypeError,
three alternative implementation timeouts, an interrupted issued request, and a
fresh-code timeout. The initial v8 one-hit/one-miss assessment was corrected by
an independent two-example-threshold fixture; both assessments are retained.

## Active campaign: outcome unknown

`.astra/investigator-2026-10-28/qualification-v10-8192/` started only after the
4,096-token campaign completed. It uses the unchanged v10 source, high reasoning,
8,192 response tokens, 300-second request timeout and the same ten-model-call,
eight-oracle-observation, nine-broker-call, 49-sandbox-execution and 48-policy-step
ceilings. Every future target arm must use this same profile.

At the last successful observation (00:11:55 UTC), each source process had
attempted three model calls. No final result from this campaign was observed.
The execution session was 75320, but it may no longer be addressable. Inspect
existing results and process state before making recovery runs. Do not overwrite
evidence or assume that either process completed.

**No target run was started. No transfer or capability-gain claim is established.**

Only after both source instances pass under the matching profile may the frozen
20-job target comparison run: four target instances, ordinary/policy/retrieval/
removed/restored arms, at most two workers. Use the existing exact candidate and
its review at
`.astra/investigator-2026-10-28/substantive-v10/attempt-01/candidate-review.json`.
The runner checks candidate hashes, review, qualification identifiers, bundle,
model identity, budgets and limits. A failed or missing source result cannot be
treated as qualification.

## Validation and recovery

The focused suite passed **145 tests with real Docker integration**. Later
reporting-only changes passed ten focused reporting tests, and the final
8,192-token profile passed seven runner tests. Full-root discovery was not rerun.

Code, harness, tests and the preceding handoff were pushed through `d01c269`
before interruption. This recovery directory and subsequent checkpoint edits
were committed through the GitHub connector while execution was unavailable.
Fetch the branch before resuming local work; preserve any recovered local changes.

After recovery: inspect the active campaign; verify all original evidence and
artifact hashes; complete qualifying source work and all predeclared target runs;
analyze the full planned denominator; export reviewed records and archive raw
evidence; update status and PR #2. Keep raw provider reasoning and credentials
private. No weight training occurred. The earlier four-method acquisition,
correction and zero-model-call reuse record remains unchanged in
[`experiments/astra_live`](../../astra_live/README.md).

## Recovery observed later on 29 October 2026

Execution access returned and the original workspace and processes were present.
No task was restarted. Both 8,192-token source runs subsequently had sealed
result files and successful invocation records: each passed 25/25 checks,
including all 17 unqueried cases, with ten model calls, eight observations and
22 sandbox executions. Affine reported 32,604 input / 16,963 output tokens;
piecewise reported 39,999 / 23,398. Their result-level elapsed times were
431.035 and 611.303 seconds respectively.

The parent process was no longer present and summary.json was absent. Its exact
failure cause remains unconfirmed. The new recovery helper validated the original
journal chains, file hashes, invocation/report equality, identifiers, model,
bundle, budgets and limits, then wrote a new recovered-summary.json. It created
no synthetic journals and reran no tasks. Its file SHA-256 is
`0b059f0d056b54329101d5993829a200364ae39deb036b8a1d021c0ac9e8ad03`.
The [recovered summary](../recorded/qualification-v10-8192/recovered-summary.json)
and [reviewed results](../recorded/results.json) now preserve the completed source
qualification. The recovered candidate also compared exactly equal to the
original workspace candidate after access returned.

Future campaign stdout/stderr are redirected to durable files so progress
output does not depend on a recoverable terminal session. The interruption
record above describes what was known at that time; it is not the current result
of the completed source tasks.
