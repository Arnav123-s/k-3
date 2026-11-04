# Working context and changing goals

This experiment tests two engineering mechanisms. Replies, observations, goals and the planning model are authored fixtures. No model endpoint or Docker execution occurs, and no frozen-model capability gain is claimed.

## Bounded context with exact recall

All conditions have an 8,000-byte model-context ceiling and the same stream of 128 observations. The scripted controller asks for the stream, then retrieves observation three, which contains an earlier counterexample. The full-history control can answer directly from history when it fits; a separate short-stream test confirms that positive control.

| Condition | Observations completed | Result | Maximum submitted context |
| --- | ---: | --- | ---: |
| Full history | 15 | Incomplete: next context exceeds ceiling | See full/stage-0.json |
| Bounded viewport + recall | 128 | Checked exact earlier observation | 4,330 bytes |
| Same viewport, new process after observation 100 | 128 | Same checked answer and semantic events | 4,330 bytes |

Each completed condition uses 130 scripted model-interface calls and 130 broker calls: 128 observations, one exact recall and one final check. These are **zero endpoint requests**. Context paging does not make the two conditions consume equal calls; the full-history control exhausts its context before it can continue. This is a capacity/continuation check, not an LLM benchmark.

`ContextMemory` marks history as partial, exposes occurrence indices and content digests, and supplies charged `history_search` and `history_read` tools. Reads return exact JSON values or explicitly typed pages. Search is literal matching inside string values/keys and non-string scalar JSON; it is not semantic retrieval or a relevance guarantee. Unicode and all envelopes count against the runtime's ASCII-escaped canonical byte limit. Recall does not re-execute observation tools.

The class bounds the model-visible history viewport and individual recall responses. Full evidence remains in host history and the journal. **Host RAM, disk usage and search time are not bounded by this increment.** Arbitrary goal text, tool descriptions, state and representation can still exceed the whole-context ceiling, producing `incomplete` rather than silent truncation.

## A goal changes what memory must preserve

The authored complete Boolean model has 1,000 variables and two actions. Its first goal needs only `done`, so a goal-cost projection omits `red` and `toggle_red`. After reopening disk memory, the goal changes to `red`; the wrapper recovers the exact retained source and builds a different view. A joint goal then requires both variables.

| Goal | Relevant variables | Enumerated states | Plan | Compiled record bytes |
| --- | ---: | ---: | --- | ---: |
| done | 1 | 2 | finish, cost 1 | 456 |
| red | 1 | 2 | toggle_red, cost 1 | 460 |
| done and red | 2 | 4 | finish then toggle_red, cost 2 | 736 |

The source specification is 39,915 canonical bytes; its complete artifact payload is 40,382 bytes. These remain stored alongside view metadata, receipts and the journal. The small compiled records are **not** total storage costs. Plans are executed against the supplied source transition model; that check shares the compiler's expression evaluator. Existing compiler tests cover preservation separately.

`AdaptivePlanningMemory` binds views to exact source, compiler version, context, goal and preservation obligation. Same-binding reuse requires the wrapper's host qualification; changed goals recompile from retained source. Missing source, changed context, invalidation or unqualified fabricated views cannot silently yield a trusted cached plan. Recompiling identical invalidated input is refused; explicit host review or a revised source/context is needed. Qualification receipts assume a trusted store writer and are not cryptographic attestations against malicious imports.

This only covers the existing declared-complete, deterministic Boolean model contract. The wrapper does not learn the world, infer a user's goal, or establish that a supplied model matches reality.

## Run and use

```sh
python -B -m experiments.astra_working_memory.run --output /tmp/working-memory-new
python -B -m unittest tests.test_astra_context_memory tests.test_astra_context_investigation tests.test_astra_adaptive_planning_memory
```

Final verification: 33 focused tests pass. The full `test_astra*.py` selection runs 253 tests with seven optional skips; 68 additional investigator/recovery/artifact/kuttaka compatibility tests pass. Combined: 314 passes, seven skips, zero failures. No full-root or new Docker run is claimed for this increment.

The output directory must be new. `recorded/` was copied from `/tmp/astra-working-memory-development-4`; its exact source fingerprints are in `implementation.json`. Earlier development repetitions and the independent review repetition remain in `/tmp` and had the same outcomes. During review we fixed an exact byte-bound edge, an unadvanceable search page, and implicit requalification of invalidated planning sources/views; regression tests retain those controls.

Applications opt in through the direct-investigator API:

```python
from kuttaka_runtime import ContextMemory, run_investigation

# model, tools, checker, journal and budget are supplied by the trusted host.
result = run_investigation(goal, model, tools, checker, journal, budget,
    context_memory=ContextMemory(max_bytes=6000, recent_records=4, read_bytes=4000))
```

The memory configuration is part of the resume contract. Existing direct calls default to full history. `run_policy` and the frozen investigator pilot are unchanged: a v1 policy that reasons over all history must not silently receive a partial viewport. A policy or model must learn/use the new recall contract before any capability claim can be evaluated.
