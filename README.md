# Kuttaka 3

**Give your coding model a test bench.** Kuttaka 3 runs model-written Python in a pinned Docker sandbox, challenges weak checks, and repairs candidates when evidence disagrees. It returns a method with the checks that support it, or reports that it could not verify an answer.

[Judge quickstart](JUDGE_QUICKSTART.md) · [Final demo](kuttaka3-final.mp4) · [Results and limitations](EVIDENCE.md)

## Run it

On Linux or WSL with Python 3.11+ and Docker:

```bash
git clone https://github.com/Arnav123-s/k-3.git
cd k-3/engine
```

Follow the [quickstart](JUDGE_QUICKSTART.md) to run the sandbox tests with scripted models or try a task with your own endpoint.

## How it works

1. **Check the checks.** Keep the user's examples and test proposed checks against a reference, property, or fresh derivation when available.
2. **Find a candidate.** Generate a method, retest a stored method, or assemble checked pieces. For supported stepwise problems, the runtime can handle the bounded search from model-proposed moves.
3. **Use disagreements.** A failed check can lead to repair, decomposition, or a closer look at the check itself. If a dispute remains unresolved, the runtime can withhold the answer.
4. **Keep the evidence.** The method library records what passed, what supports it, and where a method came from.

The [packaged engine](engine/) is **kuttka/3**, based on builder pin `89deb23`. It includes the CLI, HTTP/MCP server, method library, and tests. It is licensed under [Apache-2.0](LICENSE).

## What the demo shows

In the earlier W4-o development run, Glimmer solved **8/12 alone** and **12/12 with Kuttaka**, using 12 and 53 model calls respectively. The Kuttaka rescore passed **79/79 private cases**. [Evidence and limitations](EVIDENCE.md) includes the other runs, the benchmark checkpoints, and how to interpret them.

[Film timings](DEMO_EDIT.md) · [Narration script](VOICEOVER.md)
