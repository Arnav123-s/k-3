# Next protocol proposal: task-neutral model-output canary

**Status:** superseded proposal for review history only. `PROTOCOL.md` is the controlling draft. Where this document conflicts with it, follow the protocol's fixed `high` then `low` order; do not randomize or flip a coin. This is not authorization to run. The pre-alignment bytes are retained privately in `.astra/alignment-next-study/PROPOSAL-before-fixed-order.md` (SHA-256 `a1a7516e33cf5150789cc7e35be26d0957ca04950b8de88395ab337868fbd5b4`).

## Question and scope

The completed seed-1171 acquisition showed both empty/truncated visible replies and one experiment missing the required `predictions` field. This canary asks whether changing only API `reasoning_effort` changes the rate of well-formed visible action replies on a small public toy interaction. API reasoning effort is distinct from an optional `reasoning_hint` instruction-prefix feature; `reasoning_hint` must remain `None` in both arms and is not equivalent to the API setting.

It does not test sequence inference, method quality, artifact reuse, cross-domain transfer, or the value of the new empty-response feedback helper. It uses the same configured Muse model and endpoint, but makes no stronger-model call, training call, or Docker call.

## Arms and single-factor control

Run two independent fresh sessions in fixed `high` then `low` order with the exact same task text, tool descriptions, toy datastore, request limits, and runtime behavior. Do not randomize this order. The only arm difference is API `reasoning_effort`:

- **Original:** the current configured generation profile, including its existing high reasoning-effort setting.
- **Low reasoning effort:** the same model, endpoint, temperature, top-p, response-token limit, streaming mode, parser, and timeout, with only API `reasoning_effort` changed to `low`.

For the current original profile, record and hold fixed temperature `0.95`, top-p `1.0`, maximum response `8192` tokens, streaming enabled, strict JSON parsing, the 24,000-byte context ceiling, the 300-second request timeout, and `reasoning_hint=None`. Record the endpoint/model identity in the private manifest without exposing credentials. The low-reasoning-effort arm must match every listed field and differ only in API `reasoning_effort`.

Use the frozen/current rejection feedback behavior in both arms. Keep the new response-feedback override disabled in both. This isolates API reasoning effort as the only changed factor; changing it and rejection feedback together would confound their effects. A separate, later protocol could test feedback after this report is reviewed, but no automatic second study is authorized here.

Each arm gets a new empty in-memory toy store so observations cannot cross sessions.

## Toy interaction

The public tool catalog contains three deterministic tools with strict JSON schemas:

- `lookup_item({"key": "sample"})` returns a public fixed toy record, for example `{"value": "cobalt"}`.
- `store_item({"name": "copy", "value": "..."})` stores exactly the supplied value and returns `{"stored": true}`.
- `read_item({"name": "copy"})` returns the stored toy value or an explicit not-found observation.

The model receives the same task in both arms: use `lookup_item` for `sample`, copy only the observed value into `copy`, read it back, then answer with a JSON object containing that observed value. The toy value is ordinary public canary data, not a hidden answer. Tool observations are the only source for the copied value. No scored task data or solution code is present.

Use the existing action contract unchanged. Every experiment must have exactly `kind`, `tool`, `input`, and nonempty `predictions`; final answers must have exactly `kind` and `answer`. The canary is small enough to use three experiments and one answer when actions are valid. The prediction field is recorded and schema-checked, but prediction accuracy is not used to claim model capability.

## Fixed limits and stopping rules

- Maximum 5 model requests per arm; 10 total maximum.
- Maximum 4 tool calls per arm, including the independent final checker call after the three toy tool operations.
- Existing context cap, response cap, timeout, and transport-error handling remain unchanged across both arms.
- Invalid actions consume a model request and receive the same ordinary schema feedback in both arms. A retry may occur only within that arm's five-request budget.
- Stop an arm on checked answer, unresolved/terminal provider failure, or exhausted request budget. No reruns, tuning, retries outside the budget, or result-dependent prompt edits.
- If an arm stops early, do not spend the unused allowance elsewhere.

This protocol has no post-outcome calls. Preserve each request/response accounting row and final status, including incomplete outcomes.

## Failure taxonomy and recorded metadata

Retain per request: logical attempt number; whether the HTTP opener was reached; HTTP status when available; response finish category (`stop`, `length`, `tool_calls`, `content_filter`, `other`, or `unknown`); reported input/output token counts and separate unknown counts; whether the parser returned an action object or invalid sentinel; action kind and field-schema category; tool-call count; and elapsed time if available.

Keep a bounded, access-restricted private response receipt for every actual provider response, with credential-bearing headers excluded. Preserve response content needed for audit under a strict size limit. Public projections expose status and usage categories only; they exclude response bodies and reasoning text.

Keep these event classes separate:

- Empty/whitespace final content with `stop`.
- Empty final content with `length`.
- Nonempty invalid JSON with `length`.
- Other invalid JSON/action schema rejection.
- Valid action and accepted tool use.
- Host context-budget refusal.
- Provider transport/network/HTTP failure, with status category separate from parser/action errors.
- Adapter/parser failure that is not an HTTP or transport failure.

Unknown metadata stays unknown; do not fill missing usage with zero. Do not retain request headers or credentials. Keep private response receipts out of public projections. Toy observations may be retained in this public canary record because the store is deliberately public, but only categorical outcomes are needed for the report.

## Outcomes and interpretation

Report the two sessions descriptively: valid visible action count, empty/truncated reply count, schema-rejection count, successful toy lookup/store/read sequence, final checker status, requests/tool calls, usage completeness, and latency where available. Keep individual arm outcomes and exact profile manifests visible. Because there is one small public task per arm, this is a diagnostic comparison only; it cannot establish general model reliability or gains on the six-run study's target tasks.

## Freeze and go/no-go

Before any call, save hashes for the prompt, tool descriptions, canary fixture, model identity/profile, runtime source, and this proposal; validate that the feature flag for response-feedback override is false and `reasoning_hint` is `None` in both arms. Confirm that both arms use the same endpoint/model and differ only in API reasoning effort. If these checks fail, make no calls. This proposal itself authorizes no run; it remains speculative until reviewed and frozen separately after the current study is complete.
