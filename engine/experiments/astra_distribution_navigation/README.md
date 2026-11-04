# Probing states absent from the current map

The [v1 protocol](PROTOCOL.md) and sources were frozen at `cb5283f` before the
119-run matrix. This is an authored engineering study with zero endpoint calls.
The learner receives opaque reset/step observations, commits candidate maps,
then probes longer histories under declared distributions. Its fixed-seed runs
explicitly do not issue statistical certificates. A separate exact evaluator
measures whole-map equivalence and bounded trace-disagreement mass after learning.

| Policy / seed | Exact maps / 17 | Wrong finite-test passes | Incomplete | Learning resets | Learning actions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Shortlex depth four | 10 | 7 | 0 | 3,147 | 11,642 |
| Uniform / 701 | 11 | 6 | 0 | 2,820 | 21,558 |
| Uniform / 809 | 11 | 6 | 0 | 2,647 | 20,992 |
| Uniform / 907 | 10 | 7 | 0 | 2,589 | 20,463 |
| Persistent / 701 | 12 | 5 | 0 | 2,644 | 18,443 |
| Persistent / 809 | 11 | 5 | 1 | 3,569 | 34,966 |
| Persistent / 907 | 12 | 5 | 0 | 2,685 | 22,841 |

Persistent probes discover both repeated-action hidden triggers at every seed;
shortlex and uniform probes miss both. Uniform probes sometimes discover the
substring-trigger worlds that persistent probes miss. Broader discovery costs
more actions; these results do not establish equal-cost dominance or a reliable
policy for every world. All outcomes, including the incomplete run, are retained.

The incomplete persistent/809 run on `generated_10_223_colors` reaches the
learner's word-length ceiling while expanding its counterexample table. It is
not an environment impossibility result. This motivates a separately recorded
follow-up on counterexample processing; the v1 source and outcomes stay fixed.

All 118 issued final maps have less than 1/20 disagreement under BOTH common
length-16 distributions, including the wrong shortlex maps. That threshold is
therefore too weak to distinguish the policies on this matrix or to guarantee
finding a rare goal. Every policy misses both rare exact-prefix controls and
the length-17 trigger outside the probe support. That last world has exactly zero
error inside the support while the complete map remains wrong.

There are 78 checked goal-execution receipts. Six generated cases start at G
(6 states, seeds 211/223, both output variants; 10 states, seed 211, both variants).
Those trivial goal successes remain disclosed; the primary map comparison does
not count goal receipts as proof of a correct model.

The [summary](recorded/v1/summary.json) distinguishes accepted final maps from
earlier rejected candidates. [Verification](recorded/v1/verification.json) checks
all 119 journal chains and stores, 395,381 events, 156 constructed schedule hashes,
all attempted costs and source hashes against `cb5283f`. The
[archive](recorded/v1/evidence.tar.gz) contains 659 files and 24,801,175 bytes;
SHA-256 `73540b7ece7adf43cdb068d02f042a867b00b958dcd654aa701e2d619d39ed02`.
It includes complete source snapshots, pre-run design/truth, results, journals,
stores and a per-file manifest. No raw outcome was deleted or rerun.

At this checkpoint, 444 Astra tests ran (437 passed, seven optional Docker skips)
and all 77 disjoint compatibility tests passed. The default remains shortlex.

Reproduce as a new record, from the exact archived sources for v1 behavior:

```sh
python3 -m experiments.astra_distribution_navigation.run --output /tmp/new-distribution-run
```

The [mathematical note](../../docs/research/predictive-state/DISTRIBUTIONAL_NAVIGATION.md)
derives the conditional adaptive-round risk bound and explains why bounded error,
exactness, memory compression and success on a particular goal are different
obligations. This study demonstrates engineered behavior, not an LLM capability
gain or recovery of an undocumented historical algorithm.

## V2: isolate a distinguishing suffix

The [separate v2 protocol](PROTOCOL_SUFFIX.md), implementation and fixed matrix
were frozen at `23431d0`. Thirteen cases are tested at each seed; none starts at
the goal. The new suffix option uses an adjacent-boundary search to extract a
compact refinement obligation from each observed counterexample.

| Refinement / seed | Exact maps / 13 | Wrong finite passes | Incomplete | Learning actions |
| --- | ---: | ---: | ---: | ---: |
| Prefixes / 701 | 11 | 2 | 0 | 18,677 |
| Suffix / 701 | 11 | 2 | 0 | 15,743 |
| Prefixes / 809 | 10 | 2 | 1 | 31,551 |
| Suffix / 809 | 10 | 3 | 0 | 16,019 |
| Prefixes / 907 | 11 | 2 | 0 | 15,492 |
| Suffix / 907 | 11 | 2 | 0 | 16,134 |

The exposed word-limit failure now completes with an exact model: 1,558 learning
actions versus the original incomplete run's 13,360. Across all pairs, suffix
processing uses 47,896 actions versus 65,720, with the same 32/39 exact maps.
However, it has seven wrong finite passes versus six, and zero incomplete versus
one. In particular, `long_trigger_8` at seed 809 now misses `b` followed by eight
`a` actions. Less incidental exploration can lose a distinction even when the
refinement transform is valid. No unconditional accuracy/efficiency dominance is
claimed; both substring-trigger worlds remain wrong in all arms.

All 78 runs, 297,103 journal events, 163 schedule hashes and 65 goal receipts are
retained. An additional evaluator-only audit checks all 60 suffix-refinement
receipts: each trims at the first mismatch, its adjacent boundary separates real
histories, and every cited query receipt exists. The
[v2 archive](recorded/v2/evidence.tar.gz) has 510 files, 18,807,161 bytes and SHA-256
`67b1a676d93abbd339e569fffe465eff547912268be69d8f9b82ba8e1d45242c`.

An initial hybrid preflight then combined depth-zero candidate-distinction tests
with the seeded probes. It still missed that same long-trigger counterexample.
This negative is preserved in the implementation/test commit before the next
revision. Distinguishing existing rows alone does not test every reachable
outcome after a transition. The next proposed experiment adds that separate
obligation; v1/v2 outcomes remain unchanged.

## V3: test outcome reachability after alternative transitions

The [v3 protocol](PROTOCOL_HYBRID.md) and sources were frozen at `d250d3b`.
The constructor adds shortest paths to each known output class from each candidate
state, then tests them after candidate access/transition words. The complete
deterministic component precedes all sampled probes. This fixes the observed
`b+a^8` miss without providing evaluator answers to the learner.

Totals below pool three seeds on the same thirteen exposed cases; they are not
39 independent task families. All 117 runs are retained.

| Method | Exact maps / 39 | Wrong finite passes | Incomplete | Learning actions |
| --- | ---: | ---: | ---: | ---: |
| Prefixes + distribution | 32 | 6 | 1 | 65,720 |
| Suffix + distribution | 32 | 7 | 0 | 47,896 |
| Suffix + outcome-cover hybrid | 33 | 6 | 0 | 125,310 |

The hybrid recovers the two observed failures, but its extra coverage is expensive.
Both substring-trigger worlds still pass with wrong maps at every seed. The
[v3 archive](recorded/v3/evidence.tar.gz) retains 117 journals/stores, 654,726 events,
371 component hashes, 104 hybrid schedule hashes and 98 goal receipts. All costs
and hash commitments were independently recomputed. It contains 769 files,
41,543,326 bytes, SHA-256
`0177fb6d47186a5fee590f426c06b26c38c5d4caf5e74101c26377074d2c2152`.
At this checkpoint 457 Astra tests ran: 450 passed, seven optional Docker skips.

## V4: compact the deterministic tests without dropping prefix obligations

The [v4 protocol](PROTOCOL_COMPACT.md) and sources were frozen at `17303d1`.
Its trie retains only prefix-maximal deterministic words and maps every original
test to a retained extension. For stationary deterministic reset systems, the
full trace on that extension includes the original word's trace. Sampled words,
including duplicates, remain separate fresh tests.

| Hybrid variant / seed | Exact maps / 13 | Wrong finite passes | Incomplete | Learning resets | Learning actions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full / 701 | 11 | 2 | 0 | 7,214 | 41,425 |
| Compact / 701 | 11 | 2 | 0 | 4,362 | 27,975 |
| Full / 809 | 11 | 2 | 0 | 7,406 | 42,443 |
| Compact / 809 | 11 | 2 | 0 | 4,390 | 28,092 |
| Full / 907 | 11 | 2 | 0 | 7,062 | 41,442 |
| Compact / 907 | 11 | 2 | 0 | 4,289 | 28,340 |

Both variants recover 33/39 maps. Compact testing uses 84,407 actions versus
125,310 (32.6% fewer), and 13,041 resets versus 21,682 (39.9% fewer). Coverage
certificates preserve each constructed candidate's deterministic trace obligations;
they do not require identical learning trajectories or prove unknown-world
equivalence. The six wrong maps remain visible. All 78 runs and their evidence
are retained under [v4](recorded/v4/summary.json).

This is a bounded efficiency improvement over the full hybrid. It is still more
expensive than the simpler v3 controls, and does not establish a general
accuracy/cost improvement over every baseline. The original shortlex/prefix
defaults remain unchanged. Host applications can explicitly select the hybrid,
suffix refinement and prefix compaction through `NavigationTools`.

The [v4 archive](recorded/v4/evidence.tar.gz) contains 78 journals/stores, 590,217
events, 404 component hashes, 202 hybrid schedule hashes and 66 goal receipts.
An independent set-based audit verifies all 98 constructed coverage certificates:
18,252 original deterministic obligations map to 8,943 retained words across
those constructions, including plans later discarded. Physical costs in the
table count only actual attempts. The archive has 556 files and 38,151,038 bytes;
SHA-256 `0e03b2d4f80f2cc7331cfffc505d10505a2b2bf489d0cbc5fca9b81df36861b0`.

Across four separately frozen stages, 392 runs and 1,937,427 journal events are
preserved. Final integration includes `dbea69b` and its offline canary preparation.
The combined check runs 486 Astra tests (479 pass, seven optional Docker skips)
and 77 disjoint compatibility tests, all passing. The initial integrated run's
one setup error is retained: a canary test assumed a real checkout `.astra`
directory. Its fixture now uses a temporary repository root; live runner behavior,
protocols and limits are unchanged.

## Agent-facing use

After the host constructs a bounded learner and authorizes an observable goal,
it can configure the existing tools:

```python
tools = NavigationTools(
    learner, goals, goal_output="G",
    challenge_strategy="hybrid",
    counterexample_strategy="suffix",
    hybrid_compact=True,
    sampling={"seed": 701, "max_length": 16, "persistence": True},
).tools()
result = tools["navigation_learn"]({})
```

The agent still submits empty input to learning and exact artifact IDs to route
and execution tools. Goal bindings, ceilings and sampling settings belong to
the host; request arguments cannot override them. The integration test exercises
learning, route retention and checked execution. This API path has no model/vendor
dependency, but compatibility is not evidence of a live-model capability gain.
