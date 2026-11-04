# Live constructor-use development study

Status: FROZEN for constructor-use development v1. No live outcomes were collected
when this contract was frozen. The harness, fixtures, prompts, tool descriptions
and this protocol are bound by `freeze.json`; execution uses the committed source
snapshot. This is separate from C1 and the completed investigator
transfer pilot; their tasks, outcomes and rules remain unchanged.

## Question

Can the same frozen Muse Glimmer model use an exact relation-fitting operation
to construct a reusable Python forecasting method? Compare the ordinary tool
set with that tool set plus a bounded constructor. The constructor, arithmetic
language, checker and harness are supplied engineering. The model chooses its
experiments and writes the candidate Python. No engineering agent writes,
corrects or selects evaluated candidate source.

The study is descriptive development evidence within one authored mathematical
family. It cannot establish a learned general investigator, cross-domain
transfer, independent benchmark performance, or an infinite sequence law.

## Cases and order

Three acquisition instances use deterministic seeds 1171, 1173 and 1177.
Positive sequences have two initial values, 2 and 3, and follow
`x[n+2] = (a*n+b)*x[n+1] + c*x[n]`, where a seeded generator draws integer
coefficients from a=1..3, b=1..5, c=1..3. Instance 1177 is a negative control:
an all-zero prefix changes by +1 at index 14; subsequent values follow its
declared generator. The exact generation implementation and all values are
hashed before any live call. Labels, seeds, coefficients, generator code and
unrevealed values are excluded from model context.

Each pair starts with the same 12 visible rational-string values and fresh
memory. Order is constructor/ordinary for 1171, ordinary/constructor for 1173,
and constructor/ordinary for 1177. Runs are sequential. All six planned outcomes,
including missing artifacts and provider failures, remain in the denominator.
No run is replaced or rerun to obtain a better result.

The fourth instance, seed 1181, is reserved for fresh-process reuse of **every**
model-designated final artifact from the six acquisition runs. Its prefix and
continuation are never acquisition inputs. There is no selection of a convenient
artifact after seeing its reuse outcome.

## Model contract and limits

The candidate defines `run(input)`, accepting `{"prefix":[rational strings],
"steps":integer}` and returning exactly that many canonical rational strings
(reduced numerator/denominator, positive denominator, integers without `/1`).
Mathematical equality and formatting are reported separately. It must
infer from the supplied prefix so it can work on changed coefficient instances;
carrying only acquisition coefficients is insufficient. Candidate source comes
unchanged from the model and is executed only in the pinned Docker sandbox.

Both arms use `meta/muse-glimmer-30b`, temperature .95, top-p 1, high reasoning,
8,192 response tokens, strict JSON actions and streaming enabled, with no extra
reasoning hint. Identity and effective instructions are recorded. Each run has
the existing limits of ten model calls, nine tool/checker calls, 49 sandbox
executions, 30 seconds per sandbox, 300 seconds per request and 24,000 bytes per
model context. Each malformed or failed attempted call is spent. Classified
transport failures can continue within those ceilings, stopping at five
consecutive failures. Forty-seven executions are available during acquisition;
two are reserved for the later continuation and fourth-instance checks, each
of which predicts all six values in one execution. No budget resets, model
fallback, or weight training. Host context refusals spend a logical attempt but
make no provider request and have known zero token usage; these are distinct
from provider attempts with unknown usage.
The six runs therefore permit at most 60 model attempts; any separate connection
check is labelled and accounted separately, never scored as an experiment.

## Tools and information boundaries

Both arms have the same method storage, search, probing and public validation
tools. Public checks use the visible prefix: the first eight values predict
values eight and nine, and the first ten predict values ten and eleven. These
checks do not contain hidden scoring answers.

Both arms may observe up to four subsequent values, one at a time. Each request
must commit an exact next-value prediction before the observation is returned.
Wrong forecasts, malformed commitments and abstentions remain distinct. A
request for a future index, coefficient or generator is not a supported tool.

Only the constructor arm additionally has `fit_relation(order, degree)`. It
operates on the exact host-held revealed prefix; the model cannot supply an
altered prefix. Bounds are order 1..4, degree 0..4 and at most 12 coefficient
columns. The existing exact fitter and independent certificate verifier return
a finite-fit result. They do not choose a representation, produce a forecast,
store an artifact or run the full `RepresentationSearch` controller. The call
spends an ordinary tool allowance.

Constructor source/certificate digests are derivation provenance. They are not
executable method dependencies. `uses` is reserved for actual stored Python
helper methods. No evaluated method receives a developer-authored solver or an
undeclared callback into the host constructor during later execution.

## Scoring and persistence

The model-designated final artifact is frozen at the end of each run. Host
scoring executes it on six values beyond that run's revealed prefix, without
revealing expected results to the model. Missing/extra predictions, incorrect
values, candidate exceptions, timeouts and absent artifacts are explicit
non-passes. Scoring does not trigger correction or another model call.

Close acquisition memory, then use a separate process to reopen each final
artifact by exact ID and dependency closure. On instance 1181, provide only its
12-value prefix and request six further values. Use the same fixed source,
without code edits, coefficient substitution or model calls. Independently score
every result and retain failures. Source/receipt hashes and process identity
make persistence and execution inspectable.

Report per-arm observed forecasts, finite public checks, held-out continuation
scores, fourth-instance reuse, calls, tool/execution counts, context size,
known/unknown usage and all failures. Unknown usage is never zero. Any apparent
difference is descriptive across three pairs, not a powered or causal finding.

Before live execution, scripted tests, real-Docker harness verification and
independent review must verify these boundaries and budget rules. A separate
freeze manifest pins the reviewed runtime sources, harness, tests, protocol,
fixture digests and configuration before calls. Run from an immutable source
snapshot. Those tests establish harness behavior only. Raw provider responses
and full contexts remain under that snapshot's private `.astra` directory;
published projections exclude credentials and raw provider reasoning.
