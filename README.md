# Kuttaka 3

**Give your coding model a test bench.** Kuttaka 3 turns a coding prompt into a candidate method, runs it in a pinned Docker sandbox, and returns the method with the evidence that supports it. If a disagreement remains unresolved, it can withhold the answer and show what it found.

[Run it locally](JUDGE_QUICKSTART.md) · [Watch the 3:07 demo](kuttaka3-tight-cut.mp4) · [Inspect the evidence](EVIDENCE.md)

## What happens after you submit a task

1. **Check the checks.** The model proposes a contract and worked examples. Kuttaka preserves your examples and can test proposed checks against a slow reference, a property, or a fresh derivation that has not seen the expected answer.
2. **Try a method.** Kuttaka can retest stored methods and search small, typed compositions of them. The model can also write a candidate or propose smaller pieces for the runtime to assemble. For supported stepwise tasks, [Krama](engine/kuttaka_runtime/solve_krama.py) lets the model specify the next moves while the runtime handles the bounded search.
3. **Act on a failure.** The sandbox runs the candidate. A failure can send the runtime back to question a weak check, repair the method, or break the problem into smaller pieces.
4. **Decide what to return.** The runtime checks for competing answers, labels the support for a delivered method, and can withhold a disputed result. The workspace keeps methods and their evidence so later tasks can test and reuse them.

The model can make the same mistake in multiple derivations. A checked result is backed by the checks it passed. It can still fail on an input those checks missed.

## What you can run here

The Apache-2.0 submission includes the **kuttka/3** runtime in [engine](engine/), its CLI and HTTP/MCP server, the pinned Docker execution path, and the tests. The packaged runtime comes from Kuttaka commit `89deb236a4e0b7e199dd4750523e83d9ed739e3c`. Use Linux or WSL with Python 3.11+ and Docker. The [judge quickstart](JUDGE_QUICKSTART.md) covers a test path with scripted model fixtures and a live path with your own model endpoint.

## What the recorded runs showed

| Earlier development run | Comparison | Result with Kuttaka |
|---|---|---|
| W4-o hard suite (`8ce0951`) | Glimmer alone solved 8/12 after rescoring in 12 model calls | 12/12 in 53 calls. All 79 private cases passed on rescore |
| Graded suite v2 | Glimmer alone solved 16/18 | 18/18 |
| Growth suite | Fresh Kuttaka workspace solved 11/12 | 12/12 with memory in 69 calls |
| Later GROWTH-s (`cff12a9`) | Separate later run | 12/12 in 59 calls |

These are recorded development runs from earlier pins. The W4-o comparison used different call counts and was one run per arm. See the [per-task scorecard](W4O_EVIDENCE.md), [rescore](https://github.com/Arnav123-s/kuttaka/blob/32c6f37/results/solve-suite/w4o-glimmer-fresh/rescored.json), and [parent project results, including losses](https://github.com/Arnav123-s/kuttaka/blob/3409479/HACKATHON.md).

The film also shows selected [LiveCodeBench checkpoints](https://github.com/Arnav123-s/kuttaka/tree/d905468/results/external/race-snapshot-20261104T155245Z). Spark's largest recorded lead was 9/12 with Kuttaka versus 6/12 alone, at 52 versus 45 dispatched calls. At the selected Wonder checkpoint both arms reached 10/12, with 64 versus 40 calls. These are snapshots of separate runs. The [local evidence notes](EVIDENCE.md) are included here. Parent-project links may require repository access.

[Demo timings and sources](DEMO_EDIT.md) · [Narration script and Muse Code prompt](VOICEOVER.md) · [License](LICENSE)
