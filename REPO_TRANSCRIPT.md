# Demo transcript (3:00): what to say, what is on screen

For the owner's final recording. Read the **Say** lines aloud; they are also the burned-in subtitles. It presents what
was built and what it did, not plans. A number in brackets, `[like this]`, is filled in by the builder from the
verified rows of your runs before the edit. If a run comes out against Kuttaka, the line says so; nothing is shown
that the rows do not support.

**Your recordings:**
1. **The live demo inside Muse Code:** Muse Code hands the demo task `h-m4-sqrtmod` to Kuttaka over MCP
   (`docs/MUSE_CODE.md`). Record the whole run; it is sped up in the edit and labelled.
2. **The race:** the LiveCodeBench run from `docs/handoff/RUNNER_PROMPT_2026-11-03.md`, both arms side by side on
   screen. Record the whole run; the edit keeps the start, the turning points and the end.
3. **Your voice** reading the lines below (or read them while recording 1 and 2). One audio file read from 0:00 at
   the times below is laid over the whole film: `bash demo/video/final/assemble.sh LIVE.mp4 RACE.mp4 VOICE.m4a`.

---

**0:00 to 0:12. Hook (film).**
Say: "Same Meta model. Same weights. One difference: Kuttaka makes it check its own work before it answers."

**0:12 to 0:22. What it is (film).**
Say: "One prompt in, a checked answer out. Kuttaka plugs into Muse Code as one tool, and runs locally with Muse Glimmer
or any model."

**0:22 to 1:10. Live, inside Muse Code (recording 1, sped up).**
Say, as each part appears:
- "Muse Code hands Kuttaka a hard task: square roots modulo a prime."
- "Glimmer states the contract, worked checks, and a slow reference that is obviously correct."
- "Before any code is written, Kuttaka tests the checks against the reference. A wrong check is caught here."
- "Glimmer writes the method. Kuttaka runs it in a sandbox, then tests it beyond its checks."
- "Checked. Muse Code gets back a method, the checks it passed, and what supports each one."

**1:10 to 1:55. The race (recording 2, sped up).**
Say:
- "Now a public benchmark: twelve hard LiveCodeBench problems, judged on private tests neither arm ever sees."
- "On the left, Glimmer alone, retrying freely. On the right, the same Glimmer with Kuttaka."
- "Glimmer alone: [A] of 12. Glimmer with Kuttaka: [K] of 12." (Filled from the verified race rows.)

**1:55 to 2:25. The numbers (film).**
Say:
- "On our hard suite, scored on hidden cases: twelve of twelve with Kuttaka, eight of twelve alone, and not one wrong
  answer delivered, against four."
- "On eighteen graded tasks: eighteen of eighteen. Alone, the model shipped two programs that failed on every input."
- "On twelve tasks that build on each other, Kuttaka kept what worked and solved all twelve."

**2:25 to 2:45. How it works (film).**
Say: "The design comes from Āryabhaṭa's kuṭṭaka, written in 499: check every guess against the given conditions,
never against itself. Reduce a hard problem, guess once at the bottom, rebuild by a fixed rule, keep what worked. And
when nothing can be verified, say so."

**2:45 to 3:00. Close (film).**
Say: "Kuttaka. A checked answer, or an honest 'not known'. Open source, and it runs on your machine."

---

**Honest limits** (one subtitle under the close, kept short): "It helps most with a capable model: with a weaker
model it did worse than the model alone." The full table, negatives included, is in `RESULTS.md`.

After recording: push the recordings and the run folders to `claude/solve-suite-local`, or attach them in this
session. The builder verifies the runs, fills the brackets, and edits the film.
