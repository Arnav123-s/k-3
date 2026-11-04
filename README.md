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

*It begins with a simple observation about what thinking is.*

### Evolution

Think of it as evolution. "When something can think, there's much more cognition with it. We humans are advanced enough that all of this is coarse-grained without losing the function — like pages."

Intelligence has multiple dimensions. Evolution coarse-grained them — "like striation of millions of books." Darwin saw it in life: "There is grandeur in this view of life… from so simple a beginning endless forms most beautiful and most wonderful have been, and are being, evolved." "Thinking has been fundamental, but how your thinking is orchestrated with everything is what intelligence is."

### The attempt

"My attempt is at unfolding this and recreating it." Not two different minds checking each other — one mind, made more cognitive: working systematically, like a machine.

### Failure, and the void

Failure is not eliminated. What is inside is awareness of failure, and how to improve from failure — "this emerges infinite." The void is the system knowing its limits. "A failure never perceived ends the climb before it starts" — a false stop counts as the failure to fix.

### The climb

"First-try accuracy is not a goal; the climb from failure to success, and reaching what the model alone cannot, are." The climb happens inside one prompt — try, fail, perceive the failure, learn, retry — and the checked answer is what comes out. "Making a thinker more cognitive the right way and think systematically like a machine, like clawing its way from failure to success — it's not to be always right on first try." And "making impossible possible isn't off the table — if you can do it, if it's in reach, get there."

### The method, understood

The method is fifteen centuries old, and it was never a metaphor. Āryabhaṭa's *kuṭṭaka* (*Āryabhaṭīya*, Gaṇitapāda 32–33) is a procedure: reciprocal division writes the quotients one below another — the *vallī*, the chain; a bounded *mati* guess is chosen and checked ("multiplied by an assumed number such that the product, having added to it the difference of the remainders, may be exactly divisible" — Brahmagupta); then back-substitution climbs the chain to assemble the answer. The vallī *is* the continued-fraction expansion; the climb computes its convergents. Euler formalized the same reduction in the West — *De fractionibus continuis* (1737), and indeterminate equations in his *Algebra* (1770). One lineage, two names, thirteen centuries apart: reduce the hard problem through a quotient chain into steps you can check.

### The physics

And the physics says the reduction loses nothing. Hawking, conceding the bet at Dublin in 2004: "It is like burning an encyclopedia. Information is not lost if you keep all the smoke and ashes, but it is difficult to read." Page (1993) made it quantitative: if unitarity holds, the radiation's entropy must rise and then *fall* — the Page curve — the information preserved in correlations across the whole. Coarse-grain without losing the function, because the contents are the same.

### The old way of knowing

The old way of building knowledge knew this shape: śāstra — sūtra (the terse rule), commentary, correction, extension across centuries. Kuttaka is rebuilt with a place for each: checks settled before code, evidence ranked by support, rival methods contested, the ledger keeping the genealogy. "Thinking begets thinking."

### New kinds of perception

The mechanisms are the system gaining new kinds of perception: thinking orchestrated with its own process ("the memory of the steps is outside the thinker"), with another attempt ("each is right where the other is wrong"), with precedent ("knowing isn't the same as learning"), with the world ("if you can't verify your own work then you look for others' works that can verify your work"), with failure (the gates).

### The rule

One rule governs what is claimed: "I'll only scope as much as I have proved." What is proved is the bounded part: orchestrated, coarse-grained, verified thinking beats thinking alone. Kuttaka is complete as it is — a limited derivation of the bigger idea. It could be done better.

### The horizon

Separately, and not part of Kuttaka: the mathematical foundations underneath these ideas are being worked out as VAM, a variational-action formalism (present-state machines, Kraus maps, path sums). It is personal research — I am not submitting it, and it is not part of this project. But it is published as the public repo [vam](https://github.com/Arnav123-s/vam): if you are interested, you are free to look into it.

The bigger thought is the research program behind it: the old texts, read whole, in free time.

*The philosophy is the inspiration that brings the result to life, not a proof of it.*
