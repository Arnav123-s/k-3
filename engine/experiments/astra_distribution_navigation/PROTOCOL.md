# Bounded distribution probes: authored development study v1

This protocol is saved with implementation sources before collecting the full
matrix. It is an exploratory engineering study designed after observing the
previous schedule failures, not a sealed or independently preregistered test.
Zero endpoint calls, no training, no revised prior outcomes.

## Mechanism and limits

The same bounded observation-table learner commits a candidate before generating
its challenge words. The new strategy samples lengths uniformly from 0 through
16 and then actions using either independent uniform choices or a persistence
mixture: first action uniform; each subsequent action copies its predecessor with
probability one half and otherwise chooses uniformly from the full alphabet.
Words are independent under the ideal sampling model; actions within a persistent
word are deliberately dependent. Both distributions give every word within the
length cap positive mass. Neither observes anything beyond the cap.

Every complete reset trace is compared with the committed model. A mismatch
invalidates the candidate and refines the observation table; the next candidate
receives a new schedule. Duplicates are retained as fresh reset experiments.
Historical consistency is checked separately and earns no fresh sample credit.

At sampling attempt r, choose the smallest integer n such that

    (1 - epsilon)^n <= delta / (r(r+1)), epsilon = delta = 1/20.

Exact rational arithmetic determines n. Every attempted schedule, including an
incomplete attempt or a repeated identical map, advances r. The stream/contract
and counter persist across learn calls on one learner. Changing the contract
requires a different learner and does not earn a combined statistical claim.
No partial schedule qualifies. A completed seeded schedule receives the existing
finite-test qualification, with statistical_certificate explicitly false.

Under ideal fresh IID word draws independent of the committed candidate, a
candidate with trace-disagreement mass at least epsilon passes all n tests with
probability at most delta/[r(r+1)]. Summing this conditional bound over adaptive
rounds yields at most delta because sum 1/[r(r+1)] = 1. It is a bound on the
procedure's false acceptance probability, not a posterior probability that a
particular map is correct. It requires a stationary deterministic target and
trustworthy reset/observation callbacks.

The implementation uses fixed, public Python PRNG seeds for reproducibility.
Those runs do not establish the independence assumption or a frequentist
confidence certificate for their particular sequences. Seeded replay, seed
selection and a model informed of future probes cannot supply that guarantee.
The theorem is a precisely stated conditional interpretation, not an empirical
claim attached to these runs.

## Fixed matrix and accounting

Seventeen worlds:

- Eight generated reachable worlds: 6/10 states, seeds 211/223, distinct/color
  observations, using the prior generator.
- Two prior concealed-alias controls (delays two/three).
- Two prior repeated-action hidden triggers (length six/eight).
- Two substring-trigger machines, lengths five/seven, generated sequentially
  with random.Random(2029); exact words and transition tables saved before runs.
- One hidden trigger of length 17, outside the sampling support.
- Two rare exact-prefix sequences with sink states, lengths 14/16, generated
  sequentially with random.Random(2039).

Seven arms per world: shortlex depth four once; uniform sampling at seeds
701/809/907; persistent sampling at those same three seeds. All 119 outcomes stay
in the record. Repeated sampling seeds are descriptive replications, not new
independent task families. Cases beginning at the goal are disclosed.

Each learner gets the same 4,096 query, 50,000 action, 32-symbol word and 128-state
ceilings. Actual work may differ. All construction attempts, physical reset/action
attempts and fresh challenges are counted, including rejected candidates. Work
units for count calculation and word construction are not a total CPU measure.
Goal execution is measured separately from learning and is never a substitute
for world-model correctness. No target tables, state counts, evaluator results
or counterexamples enter the learner.

After learning, a separate product-graph checker tests complete finite-world
equivalence. An evaluator-only rational dynamic program computes trace-disagreement
mass under BOTH common length-16 distributions for every issued model, including
the shortlex baseline. That prevents choosing a favorable scoring distribution
separately for each policy. Report incomplete runs, wrong maps passing their
finite tests, maps with actual disagreement >= 1/20, and physical costs separately.
The exact evaluator is never a discovery oracle. No policy default is changed.

## Interpretation

Lower mass under one operating distribution can matter even when exact discovery
fails, but an important rare goal or hazard may lie in that missed mass. An absent
path in the learned map is still only absent in that map. The beyond-support
control makes the limitation observable. A useful comparison must report both
accuracy and cost, rather than selecting only successful runs or counting merely
the final schedule. This study cannot establish model-mediated discovery,
cross-field transfer or a universal problem solver.
