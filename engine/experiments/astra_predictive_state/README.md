# Predictive-state construction: exploratory research record

The [research note](../../docs/research/predictive-state/README.md) gives the
mathematics, sources, attribution, and limitations. This study uses the existing
inquiry, journal, and artifact store to construct and challenge executable
recurrence methods from exact observations, then change the candidate language
when a constant recurrence fails on a Ramanujan series example.

```bash
python -m experiments.astra_predictive_state.run --output /tmp/new-predictive-state
python -m unittest tests.test_astra_recurrence tests.test_astra_rational_ratio tests.test_astra_sine_constraints tests.test_astra_predictive_state
```

The output directory must not exist. `recorded/results.json` is the summary;
`recorded/events.jsonl` preserves the forecast-before-measurement chain, and
`recorded/memory` contains immutable method/question blobs and scoped qualifications.
All seven constructed methods are retrieved by their exact artifact IDs after
closing and reopening storage. Historical hypotheses are tested against the
existing public source transcription, not sealed evaluator data.

Development history is exploratory, not preregistration: the first local run
tested only constant recurrences and historical constraints. Its failure on
Ramanujan's terms motivated an analyst-selected rational-ratio constructor. The
final recording includes both the bounded negative result and the new fit;
it does not hide the first failure or describe the final language as autonomously
invented. Independent review also found and fixed a certificate text-size limit
that caused some valid large fits to reject their own certificates. A regression
test retains that exact counterexample.

This is **ENGINEERED** capability on authored fixtures, with zero endpoint calls.
The same mathematical classes appear under different example labels; that is not
independent transfer across scientific fields. Memory numbers include a separate
cold certificate cost and do not establish total-storage or speed savings.
