# Task-neutral model-output interface canary

**State: DRAFT ONLY.** This document is not authorization to execute. Root review and a separate freeze manifest are required first. Current study outputs and frozen files remain unchanged.

## Objective

Compare the same Muse model on one public, deterministic lookup/store/read toy interaction under two API `reasoning_effort` settings: `high` followed by `low`. This is a diagnostic of visible action output and interface usability only. It does not test any evaluated task, learned method, transfer, or model capability.

## Fixed profiles

Both arms use model `meta/muse-glimmer-30b` at the fixed configured development endpoint, temperature `0.95`, top-p `1.0`, maximum response `8192` tokens, streaming, strict JSON parsing, context ceiling `24000` bytes, urllib socket I/O timeout `300` seconds, and `reasoning_hint=None`. The timeout is the client library's socket I/O timeout, not an absolute end-to-end wall-clock deadline for a streamed response. API `chat_template_kwargs` are omitted in both arms. The local `reasoning_hint` prefix, the API `reasoning_effort` field, and API `chat_template_kwargs` are separate mechanisms. The documented `reasoning_effort` controls reasoning before answering, and reasoning shares `max_tokens` with the answer. Both use `detailed_response_feedback=False`, no normalization, no transport-failure continuation, and no automatic submission. The only arm difference is API `reasoning_effort`: `high` for the first arm, `low` for the second. These profiles are supported by the provider API documentation, but this document makes no claim that they differ in outcome.

## Order, limits, and stopping

Run two fresh isolated toy stores in fixed order: `high`, then `low`. Each arm has at most 5 logical model attempts, 5 actual HTTP requests, and 4 total runtime tool/checker calls. The four tool/checker calls cover three toy operations and the independent final checker. The campaign maximum is 10 logical attempts, 10 actual HTTP requests, and 8 tool/checker calls. There are no internal HTTP retries, follow-up runs, model tuning, or reruns. Stop an arm on checked answer, unresolved terminal response, hard 4xx, other terminal provider error, or exhausted attempt budget. Preserve both arm outcomes; a terminal failure in one does not trigger any retry of that arm.

Preflight checks only: explicit `--live` opt-in, exact fixed endpoint in the frozen profile, presence of `NVIDIA_API_KEY`, validated separate freeze manifest, and a fresh output directory below the repository `.astra`. Preflight does not contact the endpoint or make a token-check request.

## Toy task and tools

The public request asks the model to look up key `sample`, store only the returned value under `copy`, read it back, and answer with that observed value. The deterministic public record is `sample -> cobalt`. The available tools have strict schemas:

- `lookup_item`: exactly `{"key": "sample"}`; returns `{"found": true, "value": "cobalt"}`.
- `store_item`: exactly `{"name": "copy", "value": <string returned by lookup_item>}`; returns `{"stored": true}`.
- `read_item`: exactly `{"name": "copy"}` after successful storage; returns the stored value.

The independent checker consumes the fourth tool budget slot and requires a successful host log sequence `lookup_item`, `store_item`, `read_item`, followed by the matching answer. A guessed public value without that sequence fails. All toy data is public; there are no hidden outcomes.

Use the existing strict action schema unchanged. Each experiment must contain exactly `kind`, `tool`, `input`, and nonempty `predictions`; answers contain exactly `kind` and `answer`. Invalid actions consume a logical model attempt and receive ordinary generic schema rejection feedback. `ToyToolError` is translated to a static `ToolInputError` message at the runtime adapter boundary; raw exception strings are not returned to the model. Detailed empty-response feedback remains disabled in both arms.

## Evidence and accounting

Write only under a newly created mode-0700 `.astra` directory. Keep a hash-chained journal per arm, private protocol/freeze provenance, a private result/accounting file, and a defensive copy of the public toy host log. The model adapter caps each retained response receipt at its existing 4 MiB body limit and redacts the credential value before journaling. Do not store request headers or credentials.

For each logical model attempt, record monotonic elapsed time, whether the context check refused before HTTP, actual HTTP opener calls, status when present, parser/action category, finish category from the allowlist (`stop`, `length`, `tool_calls`, `content_filter`, `other`, `unknown`), known input/output token counts, and explicit unknown usage counts. Keep host context refusals, provider transport/HTTP failures, and adapter/protocol failures separate. Preserve bounded response receipts privately for audit; no public projection is defined by this protocol.

Elapsed time is measured monotonically per logical attempt and per arm. The logical-attempt timer includes host context validation and ends when the model call returns or raises. Unknown usage is never reported as zero. Both successful and incomplete arm outcomes are retained, with their journal hashes and exact profile identities. Once an arm reaches its terminal result, no additional call for that arm or rerun is permitted. The next predeclared arm still runs within its own limits.

## Freeze requirements

Before execution, root review must create a separate `freeze.json` binding the exact repository and canary source paths, all `kuttaka_runtime/*.py` source hashes, canary runner/test/protocol hashes, public toy fixture hash, model/API profiles, fixed arm order, budgets, endpoint, and `detailed_response_feedback=False`. This draft runner refuses live execution unless that manifest exists and exactly matches. The superseded `PROPOSAL.md` is retained as design history and does not control execution. Do not generate or edit the freeze manifest during implementation.
