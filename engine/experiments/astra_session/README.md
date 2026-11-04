# One investigation across process boundaries

This is an engineering integration test using the existing authored discovery fixture. It joins provisional goals, executable conjectures, evidence, checked method retention and continuation through `InvestigationSession`. No evaluated model authors a policy in this experiment.

```sh
python -B -m experiments.astra_session.run --output /tmp/session-new
python -B -m unittest tests.test_astra_session tests.test_astra_controller_restore tests.test_astra_investigator_resume tests.test_astra_journal_resume
```

The pinned sandbox image must already be present. Output directories must be new. The runner makes zero model requests, does not download images, and starts a separate Python process for each stage.

The uninterrupted condition and the four-process condition reach the same final checked method source with identical cumulative counts: 21 policy executions, 12 prediction executions, three method-check executions and five observations. There are 36 actual sandbox executions per condition, 72 total. Restarts occur after the first contradicted prediction, after retaining the corrected predictor as an unqualified method, and after method validation. No observation is repeated and qualification does not reset the execution budget. The final host check also verifies the scripted goal clarification; model-facing tools cannot authorize their own objective.

The recorded run was first written to `/tmp/astra-session-development-1`, verified and copied without changing its events. `implementation.json` fingerprints the working implementation after verification, not an independently frozen benchmark build. The complete source accompanies this change. Artifact IDs can differ between conditions because derivation evidence refers to different journal histories; source equality and original-task checks are compared explicitly.

`Journal.resume` requires an intact expected tail and an exclusive OS lock. Controller restore validates the evidence chain, source artifacts, context and declared tool contracts. The investigator accepts only an explicit `investigator_pause` at a completed action boundary and preserves state, representation, history, pending model reply, counts and unknown token usage. Changed policy/model/task/budgets or an unfinished effect are rejected. This does not automatically recover a process killed midway through an external action. A host must keep actual tool semantics stable; matching names and descriptions cannot prove an unchanged physical environment.

`retain_representation` copies only the active supported conjecture's exact source, records its origin, and leaves it unqualified as a method. Separate `ProgramTools` checks establish the finite task qualification. The retained `run({tool,input})` input contract does not change silently. Composition supports declared method dependencies and records cooperative sandbox invocation traces; those traces are not tamper-proof or proof that a called result was necessary.

## Unchanged recovered investigator

`recovered_policy.py` tests the same continuation mechanism using the exact model-generated v10 investigator from `experiments/investigator_transfer/recovered/candidate-v10.json`. Its source SHA-256 is `9c35846efb7c8e4b95b66cd9bb358dc8affc57459f9e9884d195870b54c4eb87`; no source edits or retraining occur. It is stored and loaded as artifact `0abb34a61f3aaaa95e2cbeef33d1a21481504f034afdbf88f41d2101ebf2d186`.

The model interface returns four authored replies: observe x=1, x=2, x=4 with predictions from y=2x, then report those readings. The fixture device clips y at 6. The recovered policy itself records support after two matches and retracts support after x=4 contradicts the prediction. The uninterrupted condition and four-process condition have identical policy actions, observations, revisions and final results. Restarts preserve an unconsumed reply, the supported hypothesis, and then its retraction. Three observations, four scripted model-interface calls, four broker calls (including the final checker) and ten sandbox executions occur in each condition: twenty actual sandbox executions total, zero endpoint requests. The policy's exact-string evidence counting and experiment selection limitations are unchanged.

Records are in `recovered-recorded/`, copied from `/tmp/astra-recovered-policy-continuation-1` after verification. The implementation fingerprint was captured after verification, including the offline verifier added after the run. The full investigation-session fixture above separately checks goal/discovery/program controller restoration. This smaller fixture checks the recovered investigator and broker directly; it is not a new live task result, full-session model discovery, source qualification, or transfer evaluation.

```sh
python -B -m experiments.astra_session.recovered_policy --output /tmp/recovered-policy-new
python -B -m experiments.astra_session.recovered_policy --output experiments/astra_session/recovered-recorded --verify-only
```

The offline verification checks the journal chains, derives charged counts from events, checks successful sandbox receipts and exact stored source, and compares semantic events across conditions. It needs neither Docker nor a model endpoint.
