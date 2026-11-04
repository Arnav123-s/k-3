# Kuttaka 3 voiceover and screen recording

Record one narration video. Read the sections in order and pause two seconds between them so the audio can be cut to picture. If you miss a line, pause and restart the sentence. Send the original video as NARRATION.mp4. For an optional Muse Code screen capture, send LIVE.mp4 separately.

## 1. Hook

"Same model, same weights. Kuttaka adds a step before the answer: it runs the proposed solution against checks and shows what passed."

## 2. Live in Muse Code

"Here I'm asking Muse Code to find a square root modulo a prime. Kuttaka proposes checks and a slower reference method, then uses them to test a candidate in a sandbox. The result includes the method and the checks that support it."

Start recording before you submit the prompt. Keep the task, the tool activity, and the result visible. Let the final result sit on screen for a few seconds.

Prompt to paste:

```text
Use Kuttaka to solve this task. Write run(input) where input is {"a": int, "p": prime up to about 10^18}. Return the smallest x with 0 <= x < p and x*x = a mod p, or null if no such x exists. Show the method and its checks.
```

## 4. Repair example

"For a drag problem in our development suite, a reference and an independent derivation both rejected the first method. The repaired method passed all five private cases."

## 5. LiveCodeBench checkpoint

"At the largest recorded lead, Spark with Kuttaka had nine verified solves from fifty-two dispatched calls. Spark alone had six from forty-five. That's one point in the run, with different call counts."

## 5A. Glimmer terminal capture

"Here is the moment Glimmer with Kuttaka reached two verified solves. Glimmer alone was still at zero. At that point the two arms had dispatched ninety-two and one hundred one calls, respectively."

## 5B. Wonder terminal capture

"Wonder shows a different result. With Kuttaka it caught up to ten verified solves. Wonder alone had already reached ten with fewer calls."

## 6. Development results

"On our hard development suite, Glimmer alone solved eight of twelve after rescoring. Glimmer with Kuttaka solved all twelve in fifty-three calls, and an independent rescore passed all seventy-nine cases. On the graded suite, it solved eighteen of eighteen, versus sixteen alone. A later growth run solved twelve of twelve in fifty-nine calls."

## 7. How it works and close

"The name comes from Aryabhata's kuttaka method. The idea is to break a hard problem into smaller steps, check each candidate against the conditions, and keep methods that work. If the checks can't support an answer, Kuttaka says so. The code and instructions to try it are public."
