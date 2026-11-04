# kuttka/3 runtime

This directory contains the runnable engine from parent commit `89deb236a4e0b7e199dd4750523e83d9ed739e3c`. The submission packages the Python source, tests, example task specifications, and experiment tooling. The parent repository preserves the historical architecture notes and run artifacts.

## Entry points

- `python -m kuttaka_runtime.solve` runs a task, displays the checked method or the unresolved outcome, and records evidence in a workspace.
- `python -m kuttaka_runtime.serve` exposes the solver over HTTP or MCP. After `python -m pip install -e .`, use `kuttaka` and `kuttaka-serve`.
- `kuttaka library` displays stored methods, support, and lineage. Use the same workspace to inspect methods from an earlier run.

Use Linux or WSL, Python 3.11 or later, and Docker. The [judge quickstart](../JUDGE_QUICKSTART.md) gives the pinned image and runnable commands. Run `python -m kuttaka_runtime.solve --help` and `python -m kuttaka_runtime.serve --help` for model, endpoint, and workspace options.

## Read the implementation

| Mechanism | Source |
|---|---|
| Plan, settle checks, repair, and decide whether to deliver | [solve.py](kuttaka_runtime/solve.py) |
| Run generated methods in the pinned sandbox | [execution.py](kuttaka_runtime/execution.py) |
| Compile a recipe from checked parts | [composition.py](kuttaka_runtime/composition.py) |
| Search bounded compositions of stored methods | [solve_prastara.py](kuttaka_runtime/solve_prastara.py) |
| Run bounded breadth-first search from model-proposed step functions | [solve_krama.py](kuttaka_runtime/solve_krama.py) |
| Optional published sequence witness | [witness.py](kuttaka_runtime/witness.py) |

The witness option uses an external source and stays off in the registered model comparisons. See the [parent architecture at the engine pin](https://github.com/Arnav123-s/kuttaka/blob/89deb23/docs/ARCHITECTURE.md) for the full decision flow, test references, and limits.
