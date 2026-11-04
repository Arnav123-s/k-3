# Operational CLI and persistent execution check

```sh
python -B -m experiments.astra_operational.run --output /tmp/astra-operational-new
```

Requires the local Docker daemon and the pinned Python image. The output directory must be new. This check starts a local OpenAI-compatible HTTP **protocol stub**, then launches four fresh `python -m kuttaka_runtime` processes. It uses the production transport, tools, verifier, persistent artifact store, and restricted container executor. No live LLM is involved.

The [recorded results](recorded/results.json) show:

| Run | HTTP/model-protocol calls | Container executions | Outcome |
|---|---:|---:|---|
| Cold construction | 5 | 7 | First candidate rejected; replacement passes three checks and executes |
| Retrieval in a fresh process | 2 | 4 | Same artifact retrieved, revalidated, and executed |
| Explicit artifact reuse | 0 | 4 | Same artifact executes on a new input after revalidation |
| Different task contract | 0 | 1 | Validation fails; no deployment output is delivered |

The endpoint's proposals and repair are scripted. Lower protocol-call counts show that retrieval/reuse is connected; they do not establish an LLM performance gain or autonomous discovery. The executions, persisted identities, host comparisons, and process boundaries are real. Existing qualification from another run never substitutes for fresh validation in this broker.

Per-run journals contain task snapshots, exact model contexts, proposals, execution precommits, check receipts, and completion status. Container diagnostics are bounded private task data. The stored bad candidate remains inspectable. The original narrow task qualification remains valid after the different task is rejected.

The endpoint chooses an ephemeral local port, so model identities and artifact hashes can vary between executions. Reproduce the outcomes and receipt relationships, not identical bytes. `task.json` is the final changed task; each earlier run's journal contains its actual task snapshot.

Use the [run guide](../../docs/ASTRA_RUN.md) for the real NVIDIA Muse Glimmer profile and other providers. Live results belong in their own attributed run directories.
