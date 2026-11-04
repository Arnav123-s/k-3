# Executable conjecture revision with uncertain goal interpretations

This is an **authored lifecycle exercise**, not a live-model discovery result. Its investigator policy, three candidate programs, simulated device, scripted clarification and final checks were written during development. It uses the real investigator runtime, content-addressed artifacts, journals and Docker sandbox. No model or external user messages are called.

```sh
python -B -m experiments.astra_discovery.run --output /tmp/discovery-new
python -B -m experiments.astra_discovery.verify /tmp/discovery-new
python -B -m unittest tests.test_astra_discovery tests.test_astra_goals tests.test_astra_discovery_goal_binding tests.test_astra_discovery_verify tests.test_astra_investigators tests.test_astra_models tests.test_investigator_environments tests.test_astra_artifacts
```

The selected Python image must already be available; the runner does not download it. Output directories must be new. See [the inquiry design and limits](../../docs/research/reconstructive-memory/PROMPT_AS_INQUIRY.md).

Expected sequence, verified by the runner and recorded trace:

1. Preserve the original ambiguous request; propose a provisional goal of maximizing the reading.
2. Receive a scripted user clarification asking for predictions, not maximization. Propose a child interpretation without deleting the first.
3. Observe one device response. Propose a linear predictor, replay that observation, and attempt adoption. The host refuses: fitting history is not prospective support.
4. Predict a new response before measuring it. Adopt the locally supported predictor.
5. A new observation contradicts it; active support is suspended.
6. Propose a patch fitting all old data. Its new prediction fails, preserving the failed candidate and evidence.
7. Propose a revised predictor, replay every observation, then predict an unobserved input correctly. Only then adopt it as supported on this finite evidence.
8. The host verifies the clarified goal using the actual cited clarification content and supplied criteria, then runs three additional development checks.

The final recorded run uses 19 sandbox policy executions, 12 sandbox prediction executions, three sandbox final-check executions, five distinct device observations (inputs 1, 2, 4, 5, 3), one scripted clarification and zero model calls. Its final status is `checked`. That status refers to the authored host checks, not general correctness.

The bounded test command runs 59 tests: 58 pass and one optional Docker test in the imported investigator suite is skipped without its environment flag. The separate recorded scenario does exercise actual Docker. Three new authorization tests show that a broad user-request ID or forged success criteria cannot stand in for the fixture's specific clarification. Six verifier tests include the original trace and altered copies with report-count, prediction-receipt, goal-lineage, failure-status and final-check corruption; the altered journals have valid recomputed hashes so semantic checks must reject them.

Development runs at `/tmp/astra-discovery-development-1` and `/tmp/astra-discovery-final` were retained. The final recorded run follows a review fix: the checker now verifies clarification content, source, proposal citation and success criteria, rather than treating any user-evidence ID as authorization. The earlier run also used an unnecessarily revealing fixture context label; the recorded run's public context names only a bounded deterministic device. None of these runs is an independent benchmark, so corrections are disclosed as development changes.

Raw events and source artifacts are retained under `recorded/`. They contain only authored task data. IDs can change if source, context or event content changes. Metadata in `results.json` identifies core implementation hashes. The installed runtime and sandbox are shared dependencies, not included in the artifact's apparent source-byte size.

The verifier checks journal and artifact integrity, revision lineage, prediction-before-measurement ordering, both retained candidate failures, the goal revision and actual execution counts without making new model or sandbox calls. The recorded run passed this verifier.
