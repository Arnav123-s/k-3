# Kuttaka 3 narration

Record one video and read the quoted lines in order. Pause for two seconds between sections. If you miss a line, pause and restart it. Send the original as **NARRATION.mp4**. Timestamps below are from the 2:47 edit — re-time them to your cut. Read at a natural pace.

## The pitch

"Kuttaka is an attempt at a systematic cognitive substrate: to make the model more self-aware, and to keep developing without the model itself changing. Its weights stay fixed. The system grows — by saving checked methods, and using them on later tasks."

## Show the work

"Here's the task: find a square root modulo a prime. Watch the terminal replay — Kuttaka starts with a contract, examples, and a slow reference. It tests those checks before any code is written, then runs the candidate in a sandbox. The saved method passed all eleven private cases."

## Show a failure that changed the outcome

"On this drag problem, a reference and a fresh derivation both challenged the first method — so Kuttaka rejected it. The repair passed all five private cases. That's the climb: from failure to success. First-try accuracy was never the goal."

## Spark

"On a public benchmark, at the largest recorded lead: nine solves with Kuttaka, six without. Fifty-two calls with Kuttaka, forty-five without. That's a snapshot of a run in progress — not a final score."

## Glimmer

"Same story here. Glimmer with Kuttaka gets its second solve while Glimmer alone is still at zero — ninety-two calls with Kuttaka, one hundred one alone, at this point in the run."

## The short results cards

"In our hard development run, Glimmer went from eight solves to twelve with Kuttaka. A separate rescore passed all seventy-nine private cases."

## The animated results

"Fifty-three calls with Kuttaka versus twelve alone. These are the runs I could complete with the resources and time I had — slow model responses limited how many trials I finished. Some runs improved as Kuttaka itself evolved. With faster responses and more time, I'd test at a larger scale, to see whether these gains hold — or grow."

## The deeper mechanism

"The idea comes from Aryabhata's kuttaka, from the year 499. Break a hard problem into steps you can check. The runtime retests methods it saved, combines pieces, and goes back to a weak check when a candidate fails. And when a dispute can't be settled — Kuttaka says it has no verified answer. An honest 'not known' beats a confident wrong one."

## Close

"Open the Kuttaka 3 repository and follow the judge quickstart. Run the local tests — or bring your own model endpoint, give it a task, and watch what comes back: the method, and everything that supports it. The system develops, so the model doesn't have to."

## Optional Muse Code screen capture

If you include a separate screen take, start before submitting the prompt and leave the resulting checks visible. Send it as **LIVE.mp4**.

```text
Use Kuttaka to solve this task. Write run(input) where input is {"a": int, "p": prime up to about 10^18}. Return the smallest x with 0 <= x < p and x*x = a mod p, or null if no such x exists. Show the method and its checks.
```
