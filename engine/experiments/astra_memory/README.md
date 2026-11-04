# Astra memory contracts and reconstruction experiment

Run from the repository root into a new directory:

```sh
python -B -m experiments.astra_memory.run --output /tmp/astra-memory-new
python -B -m unittest tests.test_astra_query_memory tests.test_astra_artifacts tests.test_astra_inquiry tests.test_astra_beliefs tests.test_astra_runtime tests.test_astra_models
```

Every question, answer table, generator, convention, and candidate completion is researcher-authored. No model, network call, training, or generated code execution is involved. These are executable mechanism checks; the runner refuses an existing output directory.

The [recorded results](recorded/results.json) cover:

- A count memory that cannot distinguish histories with different last bits, followed by an explicit refinement using retained source data. Narrow qualifications remain valid while the expanded scope is invalidated.
- Identical tokens with different unit meanings, with mismatched-context reads refused.
- A fragment whose observed prefix is known, proposed completions disagree, and authorship is unknown.
- Exact reconstruction of 4,096 progression entries from an authored procedure.
- A failed sine recurrence repaired with 18 residuals, showing that a generator can cost more than the original table.
- Reopening persistent artifacts and actually answering from the restored compiled memory.

`QueryTable` and `QueryMemory` contain no domain-specific formulas. The historical recurrence and sequence generator live only in this experiment's `codecs.py`. Finite audit scope and supplied answer correctness remain explicit assumptions. The memory decoder retains code-to-answer alternatives, query definitions, and context without the source-state index; values supplied by the caller may themselves contain identifying information.

`implementation.json` snapshots the exact code used. The output includes literal and reconstruction payloads, artifact blobs and lifecycle records, finite assessments and counterexamples, retained source tables, and separate byte counts. The reported fixed-bit lower bounds concern ideal class identifiers with a shared decoder; they are not the actual JSON package sizes. Total recorded bytes include research evidence and duplicated snapshots, with exclusions stated in `results.json`.

See the [mathematical argument and historical connections](../../docs/research/reconstructive-memory/README.md). The lost-text example is invented and does not claim to reconstruct a missing ancient work. The endpoint must still demonstrate automatic representation acquisition and transfer.
