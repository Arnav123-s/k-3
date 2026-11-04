# Automatic reconstructive memory: an engineered preservation component

Goal: the complete discovery/verification/preservation/composition cycle in `CLAUDE.md`. This experiment tests one preservation component. Read [the system-level synthesis](../../docs/research/reconstructive-memory/SYSTEM_VIEW.md) for its historical motivation, mathematical derivation, and relation to that goal.

Run from the repository root into a new directory:

```sh
python -B -m experiments.astra_sequence_memory.run --output /tmp/astra-sequence-new
python -B -m unittest tests.test_astra_sequence_memory tests.test_astra_query_memory tests.test_astra_artifacts
```

The code selects representations from observed integer sequences without task-name dispatch. It compares literal JSON against degree-0 through degree-8 finite-difference generators plus exact residuals, all compressed using zlib level 9. The family and selection metric are engineered; no model participates. Data are authored development examples, not independent benchmarks or a preregistered discovery experiment.

Recorded results (compressed payload bytes, including context and digests):

| Observations | Literal | Selected | Codec |
|---|---:|---:|---|
| 4,096 progression values | 11,235 | 262 | differences |
| 4,096 squares | 14,908 | 267 | differences |
| 4,096 constant-acceleration samples | 17,528 | 274 | differences |
| 4,096 cubic values | 22,686 | 267 | differences |
| 4,096 deterministic pseudorandom integers | 13,170 | 13,170 | literal |
| 4,096 periodic values outside this polynomial family | 309 | 309 | literal |
| Fragment `[1,4,9]` | 265 | 265 | literal |
| Squares with one exceptional value | 14,920 | 280 | differences + residual |

All eight reconstructed exactly after closing and reopening the artifact store. All refused the first unobserved index. The rare exception was retained exactly. The periodic control matters: a polynomial-only search is not an all-purpose model of sequences; a generic compressor already handles this repetition well.

Full decoder source adds **6,107 bytes** to each standalone deployment. When shared, charge it once and report that boundary. For example, the progression is 6,369 bytes including that source; the three-value fragment is 6,372 bytes, clearly not a tiny stand-alone memory. These source counts include the search implementation as well as decoding, conservatively. Python, standard-library code, filesystem allocation, hardware and search time are excluded, not assumed free. The actual eight-artifact store plus its journal occupies **18,175 file-content bytes**, separately from the decoder and this report. Do not add or compare these overlapping accounting boundaries as if they were disjoint measurements.

The record pins the decoder source identity, original observation digest and context. Source version changes can require explicit migration. Integrity hashes are not authentication against someone replacing both data and hashes. The code never executes arbitrary retained source.

Twenty-seven focused tests passed, including exhaustive roundtrips over all 364 sequences of lengths zero through five over {-1,0,1}, corruption and context refusals, rare exceptions, source snapshots and input bounds. A second experiment run reproduced every retained artifact/journal byte; `results.json` includes the added total store-byte accounting. No live inference, endpoint access, downloaded dependencies, frozen-framework changes or sealed-data reads occurred.

This is exact reconstruction of observations, not predictive validation. Both `(i+1)^2` and `(i+1)^2 + i(i-1)(i-2)` match the three-value fragment, then predict 16 and 22. The experiment records both possibilities. The system cannot recover missing historical facts by selecting whichever continuation compresses best.
