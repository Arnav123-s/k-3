# Live Astra development experiments

This campaign uses NVIDIA `meta/muse-glimmer-30b`, the real CLI, isolated Docker
execution, and persistent artifact memory. No scripted model responder or
human-authored replacement solution is used. Model weights are unchanged.

These are four public development tasks with authored finite checks: software
(stable uniqueness), mathematics (an integer equation), simulated physics (an
elastic collision), and simulated planning (a grid path). They are not an
independent benchmark or evidence of cross-domain investigator transfer.

## Reproduce

After a successful connection check and `astra doctor --profile muse-glimmer`:

```sh
python -B -m experiments.astra_live.run --output .astra/live-new --workers 2
```

The output directory must be new. The runner records the task manifests,
decoding/budget configuration, source hashes and source snapshot before making
model calls. It attempts all four acquisitions with independent empty memories,
then runs each successful task in a fresh process with retained memory and the
same budgets. Finally it revalidates and executes each acquired artifact on a
new input with zero model calls. The new inputs and their authored expected
outputs are recorded before acquisition and not sent to the model.

Each model run allows 16 model calls, 64 tool/checker calls, 128 executions,
8,192 tokens per response, a 180-second HTTP/socket timeout, and 10 seconds per
execution. The socket timeout is not a hard total-request deadline; actual
run durations are recorded. All attempts count; protocol rejection can receive corrective
feedback within that limit. Transport failures terminate the run. There is no
automatic whole-run retry. A new development attempt gets a separate output
directory and remains distinguishable from previous failures.

The current runner launches children from the archived runtime source. In the
first two recorded development batches, children used the checkout; the second
batch's runtime sources were held unchanged while running and can be compared
with its archived hashes. The first batch records hashes but predates the source
snapshot addition. Do not describe those historical runs as executing the
archived copy.

For the additional targeted revision experiment:

```sh
python -B -m experiments.astra_live.revise --campaign .astra/live-new --output .astra/revision-new
```

This challenges the acquired uniqueness method with lists and dictionaries,
allows the same live model to revise its candidate, reuses a successful revision
in another process, and rechecks the original method in its original scope.
Those additional cases were chosen after observing a set-based candidate. They
are explicitly a development challenge, not sealed validation. No corrected
source is given to the model.

## First live batch, 28 October 2026

The NVIDIA credential was observed ready and present without displaying it.
The greeting returned HTTP 200 in 15.166 seconds with 201 reported tokens.
Docker initially lacked the pinned Python image; pulling the documented digest
made `doctor` ready. A first Git clone lacked command-level network permission;
the same command with network permission succeeded through the inherited proxy.

All four initial task requests returned HTTP 200, but all stopped before tool or
candidate execution:

| Task | Actual response failure | Model calls | Input / output tokens |
| --- | --- | ---: | ---: |
| Stable uniqueness | Mixed prose and multiple JSON fragments | 1 | 479 / 427 |
| Integer equation | Null content | 1 | 509 / 353 |
| Elastic collision | Missing required predictions | 1 | 537 / 172 |
| Grid path | Literal placeholder tool name and missing predictions | 1 | 546 / 277 |

The original runtime terminated on malformed actions. The subsequent increment
adds explicit schema-rejection feedback without fabricating an action, clarifies
the protocol prompt, and gives the model host-owned counterexamples after a
rejected final answer. The changes are tested independently of live outcomes.
This is a combined development change, not a controlled attribution study of
which individual change caused any later improvement.

Raw provider replies, complete usage where supplied, action histories, candidate
sources, execution receipts, and results are retained under the private,
gitignored `.astra/live-2026-10-28/`. Provider response bodies are now preserved
up to the 4 MiB transport ceiling; the first batch used a 32 KiB receipt ceiling.
Only the selected credential is exactly redacted by the adapter; task data still
requires review before publication. Published summaries and source artifacts
will identify their source journals and disclose any missing results.

## Acquisition, reuse, and correction after protocol fixes

All four acquisitions passed their original finite checks and completed
deployment. The same original artifacts then passed fresh-process explicit
reuse on the predeclared new inputs, with zero model calls in every case.

| Task | Acquisition model calls | First warm run | Explicit reuse model calls / new output correct |
| --- | ---: | --- | --- |
| Stable uniqueness | 5 | Timeout on call 3 after two empty searches; no candidate executed | 0 / yes |
| Integer equation | 5 | Original artifact retrieved; 4 calls | 0 / yes |
| Elastic collision | 7 | Original artifact retrieved; 4 calls | 0 / yes |
| Grid path | 5 | Regenerated a different artifact; 8 calls | 0 / yes |

The timeout's third-call usage was not returned. Its recorded usage is
incomplete; it must not be treated as zero-cost or a successful retrieval. The
grid warm run solved its task but did not reuse the acquired artifact.

The nested-JSON challenge rejected the acquired uniqueness method with
`TypeError: unhashable type` for both lists and dictionaries. The subsequent
live run initially missed that stored artifact and generated another set-based
method. Host validation rejected the new candidate too. The model then wrote a
list-based correction, passed all five expanded checks, and produced the
expected mixed nested deployment result. This took 10 model calls and 15
executions. Fresh-process reuse of the correction took zero model calls and six
executions. The original method passed its original scope again and remains
qualified there. This demonstrates within-run correction; it does not establish
that a prior run's failure was retrieved from persistent memory.

## Retrieval development

Exact phrase lookup missed descriptions with slightly different wording. A
lexical ranking retest preserved exact matches first and added shared-word
matches. That still missed the uniqueness artifact when the model queried
`unique`; the description used different vocabulary, and the model regenerated
a function. This intermediate result is retained as `retrieval-v3`.

The final change adds a bounded, explicitly labeled `unfiltered_catalog`
fallback when a query has no matches. These are candidates for inspection, not
claims of relevance or qualification. The final `retrieval-v4` retest imports
only the original acquired payloads into fresh stores, with no old
qualification, and runs each child from the archived runtime snapshot.

| Final retest | Original artifact retrieved and qualified | Model calls | New-input deployment |
| --- | --- | ---: | --- |
| Stable uniqueness | yes | 5 | passed |
| Integer equation | yes | 5 | passed |
| Elastic collision | yes | 5 | execution timeout; serial zero-call rerun passed |
| Grid path | yes | 6 | execution timeout; serial zero-call rerun passed |

The last two parallel deployments exceeded the unchanged 10-second execution
limit and were correctly reported as **incomplete**. A subsequent doctor check
was ready and showed no remaining Astra executor containers. Explicit serial
reruns of those exact original artifacts and deployment inputs passed under the
same 10-second limit with zero model calls. The precise timeout cause was not
established; successful reruns do not erase the failures.

Run that retrieval check after acquisition:

```sh
python -B -m experiments.astra_live.retrieve --campaign .astra/live-new --output .astra/retrieval-new
```

These are single stochastic development runs with changed retrieval machinery
and tool descriptions. They do not isolate individual causal contributions or
estimate an independent capability improvement. The full ledger reports
artifact identity, call/execution counts, reported token usage and its
completeness, check failures, and actual outputs for every attempt.

## Preserved evidence and verification

[recorded/results.json](recorded/results.json) contains all 30 run
records, including eight incomplete runs. It also distinguishes regenerated
methods from original-artifact reuse. Per-run event projections retain actual
actions, checks, errors, execution receipts, provider metadata, and source-event
hashes. Nine exact artifact payloads are retained under `recorded/payloads/`.
Raw provider response bodies and complete original journals remain in the
private archive; projections are explicitly not the original hash chains.

[Source lineage](recorded/source-lineage.json) identifies runtime commits and
the intermediate lexical-only patch. The final 104 focused tests, including
real Docker execution, [passed](recorded/focused-tests.txt). A development integration check had caught an
unintended restriction of prediction outcomes to strings; JSON-valued outcomes
were restored before the live corrected batch. Full-root discovery was not run.

To prepare another publication for review, after all child runs have stopped:

```sh
python -B -m experiments.astra_live.export --private .astra/live-new --output /tmp/astra-reviewed-new
```

Review task data before sharing it. The export excludes provider reasoning
bodies and checks for the configured credential value; it is not a general
secret-detection or privacy guarantee. These recorded inputs are the public
development examples and the disclosed targeted nested-value challenge.

See [the goal and demonstration plan](../../docs/ASTRA_GOAL.md) for the larger
capability target. Passing these tasks establishes finite checked method
acquisition and reuse. Investigator synthesis and independent transfer require
their own demonstrations.
