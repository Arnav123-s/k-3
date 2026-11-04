# Kuttaka 3 narration for the 2:47 cut

Record one video and read the quoted lines in order. Pause for two seconds between sections. If you miss a line, pause and restart it. Send the original as **NARRATION.mp4**. The timestamps are edit marks. Read at a natural pace.

## 0:00–0:22 | The pitch

"Kuttaka is an attempt at a systematic cognitive substrate: to make the model more self-aware, and to keep developing without the model itself changing. Its weights stay fixed. The system grows by saving checked methods and using them on later tasks."

## 0:22–0:44 | Show the work

"Here the task is to find a square root modulo a prime. The terminal replay shows how Kuttaka starts with a contract, examples, and a slow reference. It tests those checks, then runs the candidate in a sandbox. The saved method passed all eleven private cases."

## 0:44–0:57 | Show a failure that changed the outcome

"On this drag problem, a reference and a fresh derivation both challenged the first method. Kuttaka rejected that method. The repair passed all five private cases."

## 0:57–1:10 | Spark

"At the largest recorded Spark lead, Kuttaka had nine solves to six. That snapshot used fifty-two calls with Kuttaka and forty-five without."

## 1:10–1:30 | Glimmer

"Here Glimmer with Kuttaka gets its second solve. Glimmer alone is still at zero. That is ninety-two dispatched calls with Kuttaka and one hundred one alone, at this point in the run."

## 1:30–1:42 | The short results cards

"In our hard development run, Glimmer went from eight solves to twelve with Kuttaka. A separate rescore passed all seventy-nine private cases."

## 1:42–2:12 | The animated results

"The hard-suite run used fifty-three calls with Kuttaka versus twelve alone. These results come from the runs I could complete with the resources and time available. Slow model responses limited how many trials I could finish. Some development runs improved as Kuttaka evolved. With faster responses and more time, I'd test on a larger scale to see whether those gains hold or grow."

## 2:12–2:32 | The deeper mechanism

"The idea comes from Aryabhata's kuttaka. Break a hard problem into steps you can check. The runtime can retest methods it saved, combine pieces, and return to a weak check when a candidate fails. When a dispute remains, Kuttaka can say it has no verified answer."

## 2:32–2:47 | Close

"Open the Kuttaka 3 repository and follow the judge quickstart. Run the local tests, or bring your own model endpoint and give it a task. You'll see the method and what supports it."

## Optional Muse Code screen capture

If you include a separate screen take, start before submitting the prompt and leave the resulting checks visible. Send it as **LIVE.mp4**.

```text
Use Kuttaka to solve this task. Write run(input) where input is {"a": int, "p": prime up to about 10^18}. Return the smallest x with 0 <= x < p and x*x = a mod p, or null if no such x exists. Show the method and its checks.
```
