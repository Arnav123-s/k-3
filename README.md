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

The [packaged engine](engine/) is **kuttaka/3**, based on builder pin `89deb23`. It includes the CLI, HTTP/MCP server, method library, and tests. It is licensed under [Apache-2.0](LICENSE).

## What the demo shows

In the earlier W4-o development run, Glimmer solved **8/12 alone** and **12/12 with Kuttaka**, using 12 and 53 model calls respectively. The Kuttaka rescore passed **79/79 private cases**. [Evidence and limitations](EVIDENCE.md) includes the other runs, the benchmark checkpoints, and how to interpret them.

[Film timings](DEMO_EDIT.md) · [Narration script](VOICEOVER.md)

## The idea (optional read)

I propose the following: intelligence is not thinking alone, but thinking orchestrated — with failure, with its own process, with precedent, with the world. What follows is how I arrived at this, the lineage it belongs to, and what I claim for it — no more.

I started from the old texts, read whole, in my free time. Fifteen centuries ago, Āryabhaṭa wrote a procedure in verse — the kuṭṭaka, Āryabhaṭīya, Gaṇitapāda 32–33. Reciprocal division lays down a chain of quotients, the vallī; a bounded guess, the mati, is chosen and then checked — Brahmagupta's rule, a century later, in the Brāhmasphuṭasiddhānta: "multiplied by an assumed number such that the product, having added to it the difference of the remainders, may be exactly divisible"; then back-substitution climbs the chain and assembles the answer. A guess is allowed — only if it is checked. That sentence is the ancestor of everything I built into the gates.

Thirteen centuries later, Euler — who never read a word of Sanskrit mathematics — formalized the same reduction: *De fractionibus continuis* (1737), the *Algebra* (1770). The vallī is the continued-fraction expansion; the climb computes its convergents. One lineage, two names. The method was never a metaphor: reduce the hard problem through a chain into steps you can check. The old way of building knowledge — śāstra: the terse sūtra, then commentary, correction, extension across centuries — worked the same way. Newton wrote it to Hooke in 1676: "If I have seen further it is by standing on the shoulders of Giants." I have tried to build accordingly: checks settled before code, evidence ranked by support, rival methods contested, a ledger keeping the genealogy. Thinking begets thinking.

Then the physics told me the chain loses nothing. Hawking, conceding the Thorne–Hawking–Preskill bet at Dublin in 2004: "It is like burning an encyclopedia. Information is not lost if you keep all the smoke and ashes, but it is difficult to read." When a book is burnt, its contents are the same; information isn't lost. Page made it quantitative in "Information in black hole radiation" (1993): the radiation's entropy rises, then falls — the Page curve — the information preserved in correlations across the whole. Wheeler compressed it to three words in "Information, Physics, Quantum: The Search for Links" (1990): "It from bit." This is the physical license for my central claim: coarse-grain without losing the function, because the contents are the same.

The principle itself I state as evolution states it. Think of it as evolution: when something can think, there's much more cognition with it. We humans are advanced enough that all of this is coarse-grained without losing the function — like pages. Intelligence has multiple dimensions; evolution coarse-grained them, like striation of millions of books. Darwin closed the *Origin* with the grandeur of it: "from so simple a beginning endless forms most beautiful and most wonderful have been, and are being, evolved." Aristotle had the oldest version of my orchestration sentence, in the *Metaphysics*: "the totality is not, as it were, a mere heap, but the whole is something besides the parts." Einstein named the faculty that orchestrates — in his 1929 interview: "Imagination is more important than knowledge. Knowledge is limited. Imagination encircles the world… stimulating progress, giving birth to evolution."

My attempt is at unfolding this and recreating it: not two minds checking each other, but one mind made more cognitive — working systematically, like a machine.

I do not eliminate failure; I put the awareness of failure inside. What is inside is awareness of failure, and how to improve from failure — this emerges infinite. The void is the system knowing its limits. A failure never perceived ends the climb before it starts: a false stop counts as the failure to fix. Beckett's line from *Worstward Ho* is the motto of the climb: "Ever tried. Ever failed. No matter. Try again. Fail again. Fail better." First-try accuracy is not my goal; the climb from failure to success, and reaching what the model alone cannot, are. The climb happens inside one prompt — try, fail, perceive the failure, learn, retry — and the checked answer is what comes out. My goal, as I wrote it: making a thinker more cognitive the right way and thinking systematically like a machine, like clawing its way from failure to success — not to be always right on first try. And making impossible possible isn't off the table — if I can do it, if it's in reach, get there.

I claim only what I have proved — that is my rule, and Feynman's sentence from "Cargo Cult Science" (1974) is the standard I hold myself to: "The first principle is that you must not fool yourself — and you are the easiest person to fool." The experiment: the same frozen model, two ways of thinking. Thinking alone: 8/12, four wrong answers. Thinking orchestrated — gates, checks, repair, the climb: 11–12/12, wrong answers down to one, then zero; 79 of 79 hidden cases passed; every number re-executed independently from the raw rows. The negative is in the record too, not under it: on a weaker model the system scored 0/12 — worse than the model alone.

What is proved is the bounded claim: orchestrated, coarse-grained, verified thinking beats thinking alone. The system grows while the model stays fixed. Kuttaka is complete as it is — a limited derivation of the bigger idea. It could be done better.

Separately — and this is not Kuttaka. VAM, the Variational Action Machine, is my own research: an experimental learning machine built on new methods of my own, a more advanced line of work than anything in this submission. Zero memory — the memory is what the network currently is — no context windows, no history buffer, only the present state evolving by the Markov property. No backpropagation — Newton's laws do not support backpropagation in any way at all, nor does biology — only forward mechanisms and local adaptation. Feynman scopes everything each token could become; Newton's least action pulls the needle from the haystack. No scoring, no beams, no softmax — the output comes from the machine's own instrument. It is not part of this project and I am not submitting it — but it is published as the public repo [vam](https://github.com/Arnav123-s/vam), for anyone who wants to see where this goes next.

Both live separate, and Kuttaka is complete in its own way. But there is a horizon: if Kuttaka were ever to evolve, these two independent lines could join — VAM's present-state machine as the substrate, Kuttaka's orchestration as the method — something far more capable and advanced than either alone. That is not this submission, and it is not promised. It is the direction the research points.

The bigger thought is the research program behind it: the old texts, read whole, in free time.

*The philosophy is the inspiration that brings the result to life, not a proof of it.*
