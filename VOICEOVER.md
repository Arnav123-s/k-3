# Kuttaka 3 narration for the 3:07 cut

Record one video and read the quoted lines in order. Pause for two seconds between sections. If you miss a line, pause and restart it. Send the original as **NARRATION.mp4**. The timestamps are edit marks. Read at a natural pace.

## 0:00–0:22 | The pitch

"Your coding model hands you a program. How do you know it works? Kuttaka gives that model a test bench. It runs the proposed method, shows the evidence behind the result, and can hold back an answer when the evidence disagrees. This is Kuttaka 3."

## 0:22–0:44 | Show the work

"Here the task is to find a square root modulo a prime. The terminal replay shows how Kuttaka starts with a contract, examples, and a slow reference. It tests those checks, then runs the candidate in a sandbox. The saved method passed all eleven private cases."

## 0:44–0:57 | Show a failure that changed the outcome

"On this drag problem, a reference and a fresh derivation both challenged the first method. Kuttaka rejected that method. The repair passed all five private cases."

## 0:57–1:10 | Spark

"At the largest recorded Spark lead, Kuttaka had nine solves to six. That snapshot used fifty-two calls with Kuttaka and forty-five without."

## 1:10–1:30 | Glimmer

"Here Glimmer with Kuttaka gets its second solve. Glimmer alone is still at zero. That is ninety-two dispatched calls with Kuttaka and one hundred one alone, at this point in the run."

## 1:30–1:50 | Wonder

"Wonder tells a different story. Both arms reach ten. Wonder alone gets there with fewer calls. The payoff depends on the model and the task."

## 1:50–2:02 | The short results cards

"In our hard development run, Glimmer went from eight solves to twelve with Kuttaka. A separate rescore passed all seventy-nine private cases."

## 2:02–2:32 | The animated results

"The hard-suite run took fifty-three model calls with Kuttaka versus twelve alone. In an earlier graded run, the score went from sixteen to eighteen out of eighteen. On a suite where tasks build on each other, Kuttaka with memory solved all twelve. The repository links each result to the run that produced it."

## 2:32–2:52 | The deeper mechanism

"The idea comes from Aryabhata's kuttaka. Break a hard problem into steps you can check. The runtime can retest methods it saved, combine pieces, and return to a weak check when a candidate fails. When a dispute remains, Kuttaka can say it has no verified answer."

## 2:52–3:07 | Close

"Open the Kuttaka 3 repository and follow the judge quickstart. Run the local tests, or bring your own model endpoint and give it a task. You'll see the method and what supports it."

## Optional Muse Code screen capture

If you include a separate screen take, start before submitting the prompt and leave the resulting checks visible. Send it as **LIVE.mp4**.

```text
Use Kuttaka to solve this task. Write run(input) where input is {"a": int, "p": prime up to about 10^18}. Return the smallest x with 0 <= x < p and x*x = a mod p, or null if no such x exists. Show the method and its checks.
```
