# Kuttaka 3

> Kuttaka: an attempt at a systematic cognitive substrate — to make the model more self-aware, and to keep developing without the model itself changing.

The model's weights stay fixed. The workspace grows as Kuttaka saves methods, checks, and the evidence behind them. Later tasks can retest and reuse that work. On the recorded growth suite, Kuttaka solved 12/12 with memory and 11/12 with a fresh workspace.

Here, self-awareness means recognizing when evidence for an answer is weak or conflicting. Kuttaka can withhold a disputed answer. In the recorded W4-o comparison, Glimmer alone delivered four wrong answers and Glimmer with Kuttaka delivered zero, using different call counts. [See the run evidence](EVIDENCE.md).

[Judge quickstart](JUDGE_QUICKSTART.md) · [Final demo](kuttaka3-final.mp4) · [Results and limitations](EVIDENCE.md)

## Choose an engine

| Edition | Directory | Source pin | Why choose it |
|---|---|---|---|
| Final tested | [engine](engine/) | `89deb23` | Default for judges. The cleaned submission passed all 1,248 tests with Docker enabled. |
| Speed-focused (W4-r) | [engine-speed](engine-speed/) | `cff12a9` | Reuses computed values within a solve. The recorded hard-suite run solved 11/12 in 40 model calls. |
| Best recorded score (W4-o) | [engine-best](engine-best/) | `8ce0951` | The recorded hard-suite run solved 12/12 in 53 calls and passed 79/79 private cases on rescore. |

Use one engine directory per run and a separate workspace for each. The historical scores come from separate development runs. The call counts describe those runs. See the [evidence](EVIDENCE.md) for the tradeoffs.

## Run it

On Linux or WSL with Python 3.11+ and Docker:

```bash
git clone https://github.com/Arnav123-s/k-3.git
cd k-3
```

Run `bash smoke-engines.sh` from the repository root after installing the pinned Docker image. The [judge quickstart](JUDGE_QUICKSTART.md) gives the exact commands for the three-engine smoke check, the full default-engine tests, and a live task. No other repository is needed.

## How it works

1. **Check the checks.** Keep the user's examples and test proposed checks against a reference, property, or fresh derivation when available.
2. **Find a candidate.** Generate a method, retest a stored method, or assemble checked pieces. For supported stepwise problems, the runtime can handle the bounded search from model-proposed moves.
3. **Use disagreements.** A failed check can lead to repair, decomposition, or a closer look at the check itself. If a dispute remains unresolved, the runtime can withhold the answer.
4. **Keep the evidence.** The method library records what passed, what supports it, and where a method came from.

The [packaged engine](engine/) is **kuttka/3**, based on builder pin `89deb23`. It includes the CLI, HTTP/MCP server, method library, and tests. It is licensed under [Apache-2.0](LICENSE).

## What the demo shows

In the earlier W4-o development run, Glimmer solved **8/12 alone** and **12/12 with Kuttaka**, using 12 and 53 model calls respectively. The Kuttaka rescore passed **79/79 private cases**. [Evidence and limitations](EVIDENCE.md) includes the other runs, the benchmark checkpoints, and how to interpret them.

[Film timings](DEMO_EDIT.md) · [Narration script](VOICEOVER.md)

## The idea (optional read)

> Kuttaka is an attempt at a systematic cognitive substrate: to make the model more self-aware, and to keep developing without the model itself changing.

The goal is "making a thinker more cognitive the right way and think systematically like a machine, like clawing its way from failure to success — it's not to be always right on first try." And "making impossible possible isn't off the table — if you can do it, if it's in reach, get there."

"First-try accuracy is not a goal; the climb from failure to success, and reaching what the model alone cannot, are." A false stop counts as the failure to fix — "a failure never perceived ends the climb before it starts."

Think of it as evolution: "When something can think, there's much more cognition with it. We humans are advanced enough that all of this is coarse-grained without losing the function — like pages. And Stephen Hawking's work: when a book is burnt, its contents are the same. Information isn't lost."

It is not two different minds. It is one mind, made more cognitive — working systematically, like a machine. Failure is not eliminated. What is inside is awareness of failure, and how to improve from failure.

The model's weights stay fixed. The workspace grows: methods, checks, and the evidence behind them are saved, retested, reused. The first time may be costly; the next times should cost less.

Self-awareness here means recognizing when evidence for an answer is weak or conflicting — and withholding the answer. When nothing can be verified, the system says it has no verified answer.

One rule governs what is claimed: "I'll only scope as much as I have proved." Kuttaka is a limited derivation — a bounded proof of part of the idea: orchestrated, coarse-grained, verified thinking beats thinking alone. It could be done better.

Where the idea points, next:

"Thinking orchestrated with its own process" — the memory of the steps is outside the thinker. The system remembers the walk, not just the answer.

"Thinking orchestrated with another attempt" — each is right where the other is wrong. Complementary attempts, joined into one.

"Thinking orchestrated with precedent" — knowing isn't the same as learning. Stored methods are set against each other and re-derived, not trusted.

"Thinking orchestrated with the world" — if you can't verify your own work, then you look for others' works that can verify your work.

"Thinking begets thinking." Every blindness the system perceives in itself becomes a new kind of perception, and the ledger keeps the genealogy. Awareness of failure, and improvement from failure — this emerges infinite.

The bigger thought is the research program behind it: the old texts, read whole, in free time. Kuttaka is one bounded derivation of it.

The philosophy is the inspiration that brings the result to life, not a proof of it.
