# Proposed interface canary — offline preparation only

The completed first constructor-study acquisition produced five HTTP 200 replies
with no final content. Its valid SSE envelopes contained reasoning deltas only;
two ended with `stop`, three with `length`. The
[receipt-structure audit](../astra_constructor_live/recorded/diagnostics/first-acquisition-empty-receipt-metadata.md)
records counts and digests without provider text.

This directory contains deterministic toy lookup/store/read tools, a host checker,
and a guarded live entry point. Twenty-seven focused offline tests pass. Live
execution requires explicit opt-in and a separate reviewed sibling freeze
manifest; no such manifest has been created for execution. Guessing the public
value cannot pass: the checker requires the actual successful tool sequence.
Its four-call ceiling includes final checking.

[PROTOCOL.md](PROTOCOL.md) specifies a later task-neutral comparison of API
`reasoning_effort=high` and `low`, keeping other options fixed. It is not a
frozen or executed study. The earlier [proposal](PROPOSAL.md) remains as design
context. NVIDIA's
[API reference](https://docs.api.nvidia.com/nim/reference/meta-muse-glimmer-30b-infer)
documents both settings and a shared reasoning/answer token ceiling. That supports
the proposed comparison, not a claim that a lower setting fixes these failures.

The active six-run constructor study continues unchanged from `d5c143a`. No
new provider calls were made for this preparation, and no target-task solution
was authored here. The reviewed source binding accepts relocation to an immutable
snapshot while rejecting foreign runtime imports or a foreign freeze path.
Offline verification:

```sh
python -B -m unittest tests.test_astra_interface_canary -v
```
