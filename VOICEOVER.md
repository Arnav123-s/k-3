# Kuttaka 3 voiceover and screen recording

Record each numbered take separately. Leave two seconds of silence at each end. The current film is three minutes. Takes 3A and 3B can extend it after the modular square-root clip. Send your raw Muse Code capture as LIVE.mp4 and voice takes as VOICE-01.m4a, VOICE-02.m4a, and so on.

## 1. Hook

"Same model, same weights. Kuttaka adds a step before the answer: it runs the proposed solution against checks and shows what passed."

## 2. Live in Muse Code

"Here I'm asking Muse Code to find a square root modulo a prime. Kuttaka proposes checks and a slower reference method, then uses them to test a candidate in a sandbox. The result includes the method and the checks that support it."

Start recording before you submit the prompt. Keep the task, the tool activity, and the result visible. Let the final result sit on screen for a few seconds.

Prompt to paste:

```text
Use Kuttaka to solve this task. Write run(input) where input is {"a": int, "p": prime up to about 10^18}. Return the smallest x with 0 <= x < p and x*x = a mod p, or null if no such x exists. Show the method and its checks.
```

## 3A. Live terminal demo — stable deduplication

"Here Glimmer gets a short coding task. Remove repeated values while keeping their first occurrence. The example is three, three, one, three. The expected output is three, one. Watch what Kuttaka reports after testing the generated method."

## 3B. Live terminal demo — merging intervals

"Here the intervals arrive out of order, and two of them touch at three. The expected result combines those two and keeps the separate interval. The terminal shows what the sandbox checked and how many model calls were used."

For either live take, leave the output visible. If the terminal says there is no verified answer, read that result as shown.

## 4. Repair example

"For a drag problem in our development suite, a reference and an independent derivation both rejected the first method. The repaired method passed all five private cases."

## 5. LiveCodeBench checkpoint

"At the largest recorded lead, Spark with Kuttaka had nine verified solves from fifty-two dispatched calls. Spark alone had six from forty-five. That's one point in the run, with different call counts."

## 6. Development results

"On our hard development suite, Glimmer alone solved eight of twelve after rescoring. Glimmer with Kuttaka solved all twelve in fifty-three calls, and an independent rescore passed all seventy-nine cases. On the graded suite, it solved eighteen of eighteen, versus sixteen alone. A later growth run solved twelve of twelve in fifty-nine calls."

## 7. How it works and close

"The name comes from Aryabhata's kuttaka method. The idea is to break a hard problem into smaller steps, check each candidate against the conditions, and keep methods that work. If the checks can't support an answer, Kuttaka says so. The code and instructions to try it are public."
