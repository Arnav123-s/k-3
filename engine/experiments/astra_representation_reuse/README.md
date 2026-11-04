# Fresh-process reuse of an authored representation method

This experiment tests whether an exact qualified method artifact can be reopened
by ID and executed in a clean process. The unchanged `RepresentationSearch`
controller receives only a 12-value prefix from the public authored
`r2d1_base` fixture through its bounded observer. It runs the full declared
lattice from the original representation study (order up to 4, degree up to 4,
12-column ceiling, 128 fits, two fresh checks), with no fixture-specific allowed
pair. Reuse proceeds only when the controller's final decision has one eligible,
qualified candidate and its forecasts are non-ambiguous. Ties or ambiguity are
recorded as abstentions; the harness does not choose a convenient candidate.

A separate Python process reopens the artifact store, obtains the method through
`get_qualified` by its exact artifact ID and scope, and applies the existing
generic exact-shift interpreter for six steps. The host fixture generator
independently scores those values; all six are outside acquisition and
prospective support. The artifact stores coefficient data, not executable
Python, and no method is authored by an evaluated model.

A second run provides the same prefix followed by the public
`noisy_counterexample` alteration at index 12. The unchanged controller records
the resulting incorrect issued forecast and invalidates the method for that
run's scope. A new process reopens the contradicted store and confirms the exact
ID is not qualified for reuse there.

The first private exploratory attempt used `allowed_pairs=[[2,1]]` and therefore
a restricted grammar. Its output remains preserved at
`.astra/alignment-reuse/run/` and is not evidence for full-lattice search. The
full-lattice result and packaged evidence are recorded separately under
`recorded/full-lattice-v4/`. The package includes the restricted run's complete
public results, journals, stores, inputs, and a reconstructed source snapshot
whose bytes match the original recorded SHA-256.

Run a fresh private evaluation or create a new immutable evidence package:

```sh
python -m experiments.astra_representation_reuse.run --output .astra/alignment-reuse/new-run
python -m experiments.astra_representation_reuse.run --record --output experiments/astra_representation_reuse/recorded/new-record
```

The package contains child-process receipts, exact IDs/scopes, predictions,
independent scores, per-file hashes, complete journals and stores, and source
snapshots. It uses only public authored fixtures and makes no endpoint calls.
The final full-lattice run used 23 fits and selected one eligible method. Fresh
process reuse scored 6/6; the altered stream retained one wrong issued prediction
before scoped invalidation. The archive verification covers 107 files, eight
event logs (617 records), four reopened stores, and the recovered original source.
The result establishes bounded persistence, retrieval, execution, and scoped
retraction; it does not test controller resume, prove an infinite law, or support
a generalization claim.
