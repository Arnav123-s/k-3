# Kuttaka 3 recording script

The Say lines come from the original video's captions and repository transcript. The 1:10 benchmark section now describes the verified W4-o hard-suite result and the additional project highlights in the revised film.

Record Muse Code from before you submit the prompt until the result and its checks are visible. Use the exact task at the end of this script. Send the raw screen capture as LIVE.mp4, with optional separate narration as VOICE.m4a. Read live-demo lines only when the recording shows the corresponding action or result.

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

**1:10 to 1:55. Best Glimmer run (W4-o scorecard).**
Say:
- "On our hard development suite, Glimmer alone solved eight of twelve after rescoring. Glimmer with Kuttaka solved all twelve in fifty-three calls."
- "An independent rescore passed all seventy-nine cases. On the graded suite, Kuttaka solved eighteen of eighteen, versus sixteen alone."
- "On the growth suite, it solved all twelve with memory. The later growth run solved all twelve in fifty-nine calls."

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
model it did worse than the model alone." See the project evidence links in README.md.


## Exact registered prompt

run(input) receives {"a": int, "p": prime up to about 10^18} and returns the smallest x with 0 <= x < p and x*x = a (mod p), or null if there is none.
