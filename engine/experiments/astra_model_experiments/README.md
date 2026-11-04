# Constructed experiments: cost and missed distinctions

This exploratory study tests an engineered alternative to exhaustive conformance
testing. A candidate Moore map supplies state-access words and transitions.
Reverse product-graph BFS constructs shortest suffixes distinguishing candidate
state pairs. A greedy cover selects suffixes; the finite schedule is
`P · A^<=d · E`. A completed schedule establishes agreement on those tests only.
No target-state bound or complete-world guarantee is assumed.

## First study: preserve the negative result

The fixed matrix has 26 authored cases and three arms: six prior fixtures,
18 seeded reachable graphs (4/8/12 states, seeds 11/29/47, distinct or two-color
observations), and hidden triggers of length six/eight. Every arm has the same
4096-reset, 50,000-action, 32-symbol-word and 128-state ceilings. Schedule sets
and actual consumed resources differ. Exact product BFS compares the resulting
map to evaluator-only world structure **after** learning; its counterexamples
are never returned to the learner.

| Arm | Exact maps / 26 | Wrong maps passing finite schedule | Learning resets | Learning actions |
|---|---:|---:|---:|---:|
| Shortlex through length 4 | 23 | 3 | 4,791 | 17,409 |
| Derived, extra depth 0 | 9 | 17 | 676 | 1,536 |
| Derived, extra depth 1 | 21 | 5 | 4,776 | 21,263 |

All 78 runs completed their declared finite schedules; no run was omitted or
incomplete. The shallow derived scheme is cheap but misses many distinctions.
Depth one uses **3,854 more actions** than shortlex while missing two more maps.
This is not an aggregate efficiency improvement. Both long-trigger controls pass
every schedule yet disagree with their maps after `a^6` or `a^8`.

There are 60 checked goal-execution receipts. A reached goal does not imply that
the whole map is correct. The two generated `4_29` cases already begin at G;
they are preserved, and goal success on those cases is trivial. The primary
comparison is whole-map equivalence and cost, not a pooled goal-success headline.

The [study-v1 record](recorded/study-v1/summary.json) preserves every result.
Its [archive](recorded/study-v1/evidence.tar.gz) contains all 78 journals/stores,
130,165 checked journal events, eight source snapshots, evaluator truth, design
and per-file receipts: 341 files, 8,191,850 compressed bytes, SHA-256
`ee80d8ebd2e7a4a368ad9d371230cb9d68edc5d48857cdf2c477f42f347c8c24`.
The sources and matrix were snapshotted before the full run. This is developer
research, not an independently preregistered or sealed evaluation.

## Follow-up: adaptive choice with concealed-alias controls

The first study is frozen at `eb14790`. A separate follow-up adds an opt-in
`adaptive` policy: consider the shallow derived schedule only when the candidate
has at least two states and distinct observations for every candidate state.
Include every root word through length two, and use the result only if its
projected reset-plus-action cost is lower than shortlex. Otherwise use shortlex.
The constructor-work count is separate from physical interaction cost; it is not
a wall-time or total CPU measurement. The original shortlex default is unchanged.

The fixed follow-up matrix contains six prior fixtures, 18 new generated variants
(6/10/16 states; seeds 101/131/167; visible/color outputs), two long triggers and
two deliberately concealed aliases. All 112 case/arm runs complete their finite
schedules with the same resource ceilings. The two `10_101` and two `10_131`
variants start at G; those four cases remain visible in the record.

| Arm | Exact maps / 28 | Wrong maps passing finite schedule | Learning resets | Learning actions |
|---|---:|---:|---:|---:|
| Shortlex through length 4 | 25 | 3 | 6,304 | 24,458 |
| Derived, extra depth 0 | 9 | 19 | 848 | 2,088 |
| Derived, extra depth 1 | 21 | 7 | 7,654 | 37,257 |
| Adaptive | 21 | 7 | 4,793 | 18,653 |

Adaptive selects the derived schedule in 13 cases and shortlex in 15. Across the
21 cases where both adaptive and shortlex produce exact maps, it saves 774 resets
and 2,928 actions. This restricted comparison excludes failures and is **not** an
overall improvement at equal correctness: adaptive misses four additional maps.
Its lower aggregate work therefore does not justify replacing the default.

Both concealed-alias controls initially yield a candidate whose observations
look like unique state labels. The real world contains extra states with those
same labels. Derived and adaptive schedules miss the distinguishing words `bba`
and `bbba`; shortlex discovers both. All their goal routes still reach G by action
`a`. These are concrete demonstrations that goal success and apparently unique
labels do not certify the map. The exact evaluator remains outside the learner.

The [study-v2 record](recorded/study-v2/summary.json) retains all outcomes and
paired cost comparisons. Its archive includes 112 journals/stores, 258,183 checked
journal events, eight exact source snapshots and all 91 goal-execution receipts.
The archive contains 509 files and 15,938,736 compressed bytes, SHA-256
`0477e9c370f7f46709165b44663bb4bc905e9e2d7971fd1f596b5a04adfe8474`.
An independent audit found the original aggregate counted only final/selected
schedule construction, omitting earlier attempts and discarded constructions.
[The reporting correction](recorded/study-v2/reporting-correction.json) replays
every recorded construction/challenge event: derived-depth-one work is 132,881
units (originally 82,801), and adaptive work is 14,165 (originally 13,326). It also
corrects challenge-attempt totals. Physical reset/action counts, exactness scores
and the table above are unchanged. The original archive/summary are preserved;
only the current reporting code and an explicit reanalysis receipt are added.
Both studies have zero endpoint calls and leave prior model evaluations unchanged.
Validation of the final code before parallel integration: 386 Astra tests (379 pass, seven optional Docker
skips), plus 69 investigator/protocol/campaign compatibility passes.
After integrating `2201d16`, 17 additional constructor-harness tests pass. Its
live-study freeze remains pinned: the gate correctly rejects the changed research
tree. All frozen source hashes still match `2201d16`; no live run or refreeze occurs.
Final publication alignment includes `bbd6d7d` and its reporting/response-feedback
changes. A fresh integrated check runs 420 Astra tests (413 pass, seven optional
Docker skips), plus 77 disjoint compatibility/feedback tests, all passing.

## Reproduction

```bash
python3 -m unittest tests.test_astra_model_experiments tests.test_astra_model_experiment_study
python3 -m experiments.astra_model_experiments.run --output /tmp/astra-schedule-new-run
python3 -m experiments.astra_model_experiments.run --study v2 --output /tmp/astra-schedule-v2-new-run
```

Use a new output directory. Current-source reruns are new records; the original
archive's source snapshots and hashes identify its exact implementation.
No endpoint calls or weight updates occur. The existing shortlex default and
previous frozen model campaigns are unchanged.

The [goal and limits analysis](../../docs/research/predictive-state/OPEN_WORLD_GAPS.md)
explains how this fits the broader task and which historical connections support
the engineering interpretation without attributing modern algorithms to Aryabhata.
