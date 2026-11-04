# Decision-preserving memory and planning

Run the authored development experiments from the repository root into new directories:

```sh
python -B -m experiments.astra_planning_memory.run --output /tmp/planning-new
python -B -m experiments.astra_planning_memory.factored --output /tmp/factored-new
python -B -m unittest tests.test_astra_factored_memory tests.test_astra_planning_memory tests.test_astra_query_memory tests.test_astra_artifacts tests.test_astra_sequence_memory
```

[Mathematical contracts, proof, source attribution and limits](../../docs/research/reconstructive-memory/PLANNING_MEMORY.md).

| Measurement | Explicit model | Retained planning model |
|---|---:|---:|
| States | 16,384 | 32 |
| JSON bytes | 2,093,057 | 3,742 |
| States expanded for the recorded query | 15,873 | 32 |
| Minimum plan cost | 31 | 31 |

Temporary encoder: 257,025 JSON bytes. Recorded compilation: about 2.58 seconds; one raw planning call about 0.192 seconds, quotient call about 0.000365 seconds. Timing includes validation/copying and is machine-specific, not an optimized benchmark. Compilation is real acquisition cost; repeated identical queries could instead use a cache. The compressed artifact store/journal uses 789 file-content bytes. That number is a different accounting boundary from JSON bytes and excludes the installed code and runtime.

The plan was independently executed on the concrete source and checked for goal/cost. Every step was monitored. A deliberately changed observation produced `model_mismatch` with no replacement state guessed. After source graph and encoder deletion, an ArtifactMemory reload produced exactly the same abstract plan.

The second experiment removes irrelevant factors before enumerating any concrete graph:

| Measurement | Factored experiment |
|---|---:|
| Source Boolean variables | 1,000 |
| Implicit assignments | 2^1000 (not enumerated) |
| Retained variables | 2 |
| Projected states enumerated | 4 |
| Input specification bytes | 81,642 |
| Deployed record + start + context | 829 bytes |
| Plan | collect, open |
| Checked minimum cost | 3 |

The source deliberately contains 998 irrelevant variables. The compiler identifies them through expression dependencies, not by their names. This is a favorable synthetic structure, not evidence for compression of arbitrary thousand-dimensional tasks. The record includes source specification digests and implementation fingerprints. Implementation sources are separately charged: factored compiler 5,288 bytes, planning module 8,776 bytes, imported query-memory module 17,811 bytes; Python/standard library and filesystem overhead remain outside this boundary. The deployed JSON was read from disk after removing the in-memory source specification.

42 focused tests pass. New checks cover 1,728 three-state single-action models and all three start states, 100 two-action models checked against an independent Bellman-Ford value oracle, every initial assignment of a five-variable factored model versus explicit enumeration, dependency-chain/guard preservation, synchronous assignments, hidden traps, changed goals, incomplete-model refusal, bounded-refinement refusal, and observed surprises. A noncompressible control retains all 32 states and increases serialized size.

All models and algorithms are development-time engineering. No LLM, sealed evaluation data, real robot, or inferred physical law participates. Closed-world determinism, supplied complete action models and supplied goal semantics are assumptions. This is not an independent runtime-discovery or general-intelligence result. `recorded/results.json` and `factored-recorded/results.json` preserve the actual runs; reruns can differ in timings while reproducing logical outputs.

## Goal-cost preservation increment

`python -B -m experiments.astra_planning_memory.goal_cost --output /tmp/goal-cost-new` compares preservation obligations on the same supplied model. Full behavior exceeds the variable ceiling; the new explicit `preserve='goal_cost'` mode removes a goal-irrelevant action and its 998 guard variables, retains two variables, and returns a plan verified in the original model. See `goal-cost-recorded/` and [the proof and controls](../../docs/research/reconstructive-memory/GOAL_COST_MEMORY.md). Its deployed bundle is 756 bytes, excluding implementation/runtime. Add `tests.test_astra_goal_memory` to the test command above: the combined suite now passes 47 tests. Earlier records and source sizes above describe their own fingerprinted implementations.
