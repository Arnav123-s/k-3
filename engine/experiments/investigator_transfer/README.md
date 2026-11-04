# Executable investigator development pilot

This experiment asks the frozen NVIDIA `meta/muse-glimmer-30b` model to derive
its own executable investigation policy from selected prior development traces.
There is no weight training. The engineering agents provide the interface,
observation environments and checkers; they do not write the evaluated policy
or the model's candidate solutions.

The concrete goal is to acquire a policy that actually controls observations and
representation revision, qualify it on new numeric investigations, and measure
its reuse on generated text and mechanical function-inference tasks. All these
tasks ultimately require a Python predictor for a deterministic black box. This
is a development pilot, not evidence of general intelligence or cross-field
research capability.

## Completed source qualification

The exact model-generated v10 policy passed both source instances under the
final common 8,192-token profile. Each used ten model calls, eight observations
and 22 sandbox executions; each passed all 25 checks, including 17 unqueried
inputs. The source policy controlled two live representation revisions in each
run. Experiment design and predictor construction remained model-mediated.

The [reviewed record](recorded/results.json) retains all 18 acquisition calls,
eight completed source attempts, failed protocols, timeouts, truncations and
scripted fixtures. Earlier source failures are separate attempts, not replaced
by these passes. Known token totals and unknown usage are recorded separately.
The execution-access interruption and verified summary recovery are documented
in the [recovery record](recovered/INTERRUPTION.md). The completed target comparison below did not demonstrate a transfer advantage.

## Completed target comparison

All 20 planned runs finished once, without replacement or reruns, using the
frozen `bd7c1b3` snapshot. The executable investigator did not improve the
primary all-checks pass rate in this development pilot.

| Arm | Passed / planned | Model calls | Provider / transport failures | Budget non-passes | Incorrect predictors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ordinary | 1 / 4 | 31 | 2 | 1 | 0 |
| Executable policy | 0 / 4 | 31 | 4 | 0 | 0 |
| Retrieval only | 0 / 4 | 25 | 3 | 0 | 1 |
| Artifact removed | 0 / 4 | 32 | 3 | 0 | 1 |
| Exact artifact restored | 0 / 4 | 38 | 0 | 4 | 0 |

The ordinary agent on mechanical instance 827 passed 24/24 checks, including
20 inputs it had never queried. Removed on mechanical 823 and retrieval on
mechanical 827 each scored 15/24, failing the required all-checks criterion.
The policy/restored conditions submitted no final predictor. Their eight
observed representation revisions are executed behavior, not checked task
success. Source qualification remains 2/2 under the final profile.

The twelve provider/transport failures were six HTTP 500, one HTTP 404, one
HTTP 401, and four request timeouts. Their exact cause is not established by
other projects' endpoint incidents. Five further runs stopped on budget limits:
four call-budget/abstention cases and restored mechanical 827 exceeding the
24,000-byte model-context limit after eight calls. Eighteen responses had
`finish_reason: length`. These categories are retained separately; none is
silently discarded or rescored as an incorrect predictor.

Target cost: 157 model calls, 121 oracle attempts, 138 sandbox executions
(including 135 policy executions), and known partial token sums of 350,456
input / 440,747 output. Twelve failed requests have unknown usage. Acquisition
(18 calls) and all source attempts (76 calls) remain separate in the ledger.
Matched ceilings do not imply equal consumed compute. All 20 journals,
identities, task hashes, memory isolation and caps passed the independent audit.

The small, unseeded comparison with substantial provider failures establishes
no transfer gain and no causal conclusion that the policy worsens capability.
The gain goal remains unmet. The target data are now exposed development
material; future revisions need a separately frozen campaign and fresh instances.

- [Full analysis and secondary measurements](recorded/transfer-v10/analysis.json)
- [Independent integrity/accounting audit](recorded/transfer-v10/audit.json)
- [All 77 projected records](recorded/results.json), preserving the earlier 57 exactly
- [Reporting provenance](recorded/transfer-v10/reporting-provenance.json)
- [Private archive location and SHA-256](recorded/archive.json)

The archive contains 603 files; 602 member hashes were verified against its
manifest (which excludes itself). Raw provider bodies and full model contexts
remain private. The configured credential value was absent from the checked
raw evidence and published projection.

Parallel session integration is documented in
[the cross-repository checkpoint](../../docs/CROSS_REPO_STATUS.md). Its durable
sessions, composition, bounded context and adaptive planning were integrated
**after** this campaign's source freeze and did not alter its execution. The
[verification record](recorded/integration-verification/results.json) retains
318 non-Docker passes, the initial Docker result of six passes/one timeout,
and the separate same-limit successful rerun. A fresh actual-Docker composition
run reused the exact earlier Glimmer method on six cases with zero model calls;
its wrapper is authored engineering, not newly learned adaptation.

## Protocol fixed before target execution

- The candidate comes from the live model. Every generation, syntax rejection,
  protocol smoke failure and model correction is retained separately. A smoke
  check uses scripted model replies and real Docker; it is runtime evidence only.
- The artifact must first pass a substantive review: its executable decisions
  must depend on observations and a retained working representation. A forwarding
  adapter or unreachable source-history branches do not meet this criterion.
  Both new source qualification instances must then pass before target execution.
  The runner enforces an explicit review file for `all` and `transfer`, bound
  to the complete candidate and its source hash. A separate transfer invocation
  also requires the same bundle, model identity, budgets and limits as source
  qualification. This records a reviewer decision; it is not an automated proof
  of capability.
  The exact policy source is then frozen. Transfer imports only its versioned
  investigator artifact, without source traces, fitted parameters, solvers,
  observations or qualifications. The v1 interface permits no dependencies.
- Target instances are instantiated after freezing the bundle. Seeds and family
  implementation are development material fixed before their runs; they are
  neither secret from the engineering agents nor an independent final benchmark.
- Four target instances (two text, two mechanical) each have five runs: ordinary
  direct agent, executable policy, policy text as retrieval-only reference,
  artifact removed, exact artifact restored in another fresh process.
  The removed condition is a repeat of the ordinary condition; restored is a
  repeat of the policy condition. These are stochastic on/off repeats, not a
  within-run crossover or identical-random-seed counterfactual.
- Same model, temperature .95, top-p 1, high reasoning, at most 10 model calls,
  8,192 response tokens per call, 24,000 context bytes per call, eight oracle
  observations, nine broker calls including final checking, 49 sandbox executions
  and 48 policy steps. Execution timeout is 30 seconds; model timeout 300 seconds.
  These are matched ceilings, not equal consumed compute or a hard aggregate
  token budget. Policy execution overhead and actual reported tokens are charged
  and reported; unavailable usage remains unknown.
- Only requested oracle outputs enter the agent context. No candidate hypotheses,
  formulas, expected scoring outputs or hidden scorer code are supplied. A final
  submitted predictor is run in a fresh sandbox over host-selected checks. Scoring
  is terminal; failed answers receive no corrective scoring feedback.
- Every arm also receives a generic isolated Python computation tool, charged to
  the same broker/execution budgets. The model must supply its own source. This
  lets ordinary and retrieval-only controls perform computations and test their
  own conjectures against already-observed data; it supplies neither a solver
  nor additional device observations or scoring answers.

## Scoring and stopping

Primary endpoint: exact agreement on **all** host checks for an instance. Report
paired policy versus ordinary outcomes by seed and family. Incorrect answers,
abstention, malformed actions that exhaust the budget, timeouts, missing results,
and infrastructure failures count as non-passes in the full planned denominator;
also retain their distinct failure classifications.

Secondary outcomes are per-case accuracy, performance on check inputs never
queried by that run, representation revisions, observations, model calls,
reported input/output tokens, sandbox executions, memory size and elapsed time.
Acquisition, protocol correction, source qualification and target costs remain
separate. No aggregate is allowed to hide failed attempts or unknown usage.

Run all 20 planned target jobs once, using the rotated arm ordering in the runner
and at most two workers. Do not stop early for a favorable result. Recovery runs
must use new directories and are additional observations, never replacements.
No policy or task edits are allowed during a target campaign. Any subsequent
development revision requires a new campaign and explicit exposure disclosure.

With two instances per family and unseeded stochastic decoding, no significance,
uncertainty threshold, broad transfer conclusion or confirmation claim is planned.
A consistent observed improvement would motivate a larger preregistered study;
an absent or reversed improvement means the capability-gain goal remains unmet.

## Reproduction

Use the configured `NVIDIA_API_KEY` without printing it. The Docker image is the
same pinned digest as the earlier method-execution experiment. Commands create
new private directories and refuse to overwrite evidence:

```bash
python -B -m experiments.investigator_transfer.acquire --output .astra/investigator-new/acquisition
python -B -m experiments.investigator_transfer.smoke --candidate .astra/investigator-new/acquisition/candidate.json --output .astra/investigator-new/smoke
python -B -m experiments.investigator_transfer.run --candidate .astra/investigator-new/acquisition/candidate.json --output .astra/investigator-new/qualification --stage qualify --workers 2
python -B -m experiments.investigator_transfer.run --candidate .astra/investigator-new/acquisition/candidate.json --output .astra/investigator-new/transfer --stage transfer --qualification .astra/investigator-new/qualification/summary.json --review .astra/investigator-new/acquisition/candidate-review.json --workers 2
```

Before the transfer command, review the artifact and observed policy behavior.
The review JSON must contain `source_sha256`, `candidate_sha256` (the canonical
JSON digest of the complete candidate), `substantive_review_passed: true`,
`target_task_exposure: false`, `dependencies: []`, and a nonempty `review`
describing the actual evidence. Do not set a passing decision for a forwarding
adapter merely because it passed the syntax or protocol fixture.

If a smoke check fails, `acquire --repair PATH/feedback.json` asks the same model
for a new candidate and records a separate acquisition. Source qualification
failures require an explicitly documented development revision; no hand repair
of the policy is permitted. Each campaign executes child processes from its
archived runtime source and records file hashes and the original Git commit.

## Development attempts before transfer

Every row below is development evidence. Generated source is never repaired by
an engineering agent. Provider failures, host mistakes, fixture failures and
later corrections remain separate records.

| Candidate or attempt | Actual outcome |
| --- | --- |
| v1 | Failed scripted protocol smoke: two model calls and no experiment. |
| v2 | Passed protocol smoke but remained a forwarding adapter. Live numeric source runs failed: one policy sandbox timeout and one exhausted budget; neither submitted an answer. |
| v3 | Passed protocol smoke; substantive review rejected forwarding/copying behavior. |
| Design v4 / v5 | v4 returned code instead of the requested design. v5 proposed a design, but later implementations did not implement its full hypothesis-pruning scheme. |
| v5 / v6 / v7 | Successive actual Docker counterexamples exposed ignored final answers, erased conflicting observations and a no-progress revision loop. v7 corrected those narrow defects. |
| v7 source diagnostics | Affine seed 907 passed all 25 checks, including 17 unqueried inputs, with 10 model calls. Piecewise seed 911 timed out on request 7 after six observations and submitted no answer; usage for that request is unknown. These runs do not constitute full investigator qualification. |
| Alternative design branch | One design response was recovered after a host hashing TypeError. Three separate implementation requests timed out (180/300/300 seconds); their usage is unknown. |
| v8, three code generations | Added prediction-evidence revisions. An initial one-hit/one-miss assessment was too narrow: independent fixtures showed its authored two-example threshold does revise and continue. Later counterevidence left stale support, and a string representation crashed. The original assessment and its correction are both retained. |
| Interrupted design / fresh v9 | One issued request was interrupted after orchestration changed direction; provider work and usage are unknown. A separate fresh-code request timed out at 180 seconds, also with unknown usage. |
| v10 | The same live model corrected stale support and non-object representation handling. Actual Docker fixtures demonstrate two-hit/two-miss revisions, continuation, retraction after counterevidence, and string/list/number/bool/null handling. Protocol smoke passed. These fixtures are scripted and do not establish task performance. |

The reviewed v10 artifact is a **narrow prediction-evidence controller**. Its
code decides when to revise a hypothesis representation and whether to forward
or withhold a proposed action. Experiment design and final predictor construction
remain delegated to Muse Glimmer. Hypothesis identity uses exact strings; the
threshold labels are working conjectures, not host-verified conclusions. No full
implementation of the earlier proposed investigator design is claimed.

The original high-reasoning acquisitions used 8,192 response tokens and a
180-second request timeout. Later v8/v9 generation used medium reasoning and
6,000 tokens; v10 used the same acquisition profile with a 300-second timeout.
These are disclosed acquisition settings, separate from the initial high-reasoning
4,096-token source evaluation profile.

Following the observed 10-second sandbox timeout, later campaigns use a
separately declared 30-second sandbox wall limit and 3,900-second process cap.
Three serial diagnostic identity executions completed in 2.4–3.5 seconds. Four
old execution containers were found in Docker's `Created` state; a startup or
cleanup stall is plausible, but the exact cause of the earlier timeout is not
established. The original timeout remains a failed run.

Before any target execution, the model request timeout was extended from 180 to
300 seconds for every arm after the v7 piecewise timeout. Model identity,
decoding, response-token ceilings and count budgets are unchanged. New source
qualification and target runs must share this profile; the runner rejects a
qualification summary with different settings.

A further pre-target evaluation revision restores the original live experiment's
8,192-token response allowance. The first v10 qualification passed affine, but
piecewise requests repeatedly returned HTTP 200 with `finish_reason: length` at
exactly 4,096 tokens and no valid action. The archived 4,096-token campaign
finished with affine checked and piecewise unresolved after six truncated
responses. It remains a separate attempt. The next qualification and
all target arms use 8,192 tokens, the same ten-call/eight-observation ceilings and
300-second request limit. No target results informed either resource change.
