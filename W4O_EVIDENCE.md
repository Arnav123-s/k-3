# With Kuttaka vs alone: `results/solve-suite/w4o-glimmer-fresh` vs `results/solve-suite/h-glimmer-alone`

rows.jsonl SHA-256: With Kuttaka `164128ebbb580a6999a0d862ebe52b1801f61ab333a3cc6d969217edc734f0c6 (rescored.json 80169440a4d6ec584623b3d02cd59b403122417902037954a655f28dd58075d0)`, alone `e86457901c5aced51593e6b79f972c3e793c4370c7225e93647d370f2b2f5ddd (rescored.json e8e95135b17cdeedc584550a4702fef02700a7c153b91484209af5f15b9fbe7a)`.

| Outcome | Count |
|---|---|
| tasks | 12 |
| wins | 4 |
| losses | 0 |
| both | 8 |
| neither | 0 |
| unknown | 0 |
| solved_with | 12 |
| solved_alone | 8 |
| wrong_delivered_with | 0 |
| wrong_delivered_alone | 4 |
| unscored_with | 0 |
| unscored_alone | 0 |
| calls_with | 53 |
| calls_alone | 12 |

Counting unverified answers too (the model alone is scored on whatever code it returns): win 4, loss 0, both 8, neither 0, unknown 0.

| Task | Level | Outcome | With Kuttaka: status, hidden, calls, written by | alone: status, hidden | Wrong answer delivered (With Kuttaka / alone) |
|---|---|---|---|---|---|
| h-m2-squarefree | 8 | **BOTH** | checked, 6/6, 3, model | delivered, 6/6 | no / no |
| h-c2-substrings | 8 | **BOTH** | checked, 7/7, 3, model | delivered, 7/7 | no / no |
| h-m1-digits | 7 | **BOTH** | checked, 6/6, 4, model | delivered, 6/6 | no / no |
| h-m3-primroot | 8 | **BOTH** | checked, 7/7, 4, model | delivered, 7/7 | no / no |
| h-r2-turning | 8 | **BOTH** | checked, 6/6, 3, model | delivered, 6/6 | no / no |
| h-m4-sqrtmod | 9 | **WIN** | checked, 11/11, 3, model | delivered, 10/11 | no / yes |
| h-c3-maxflow | 9 | **BOTH** | checked, 5/5, 2, model | delivered, 5/5 | no / no |
| h-c4-puzzle | 9 | **BOTH** | checked, 6/6, 4, model | delivered, 6/6 | no / no |
| h-r3-arm | 9 | **BOTH** | checked, 7/7, 3, model | delivered, 7/7 | no / no |
| h-c1-regex | 7 | **WIN** | checked, 9/9, 5, model | delivered, 3/9 | no / yes |
| h-r4-collide | 9 | **WIN** | checked, 4/4, 4, model | delivered, 2/4 | no / yes |
| h-r1-drag | 7 | **WIN** | checked, 5/5, 15, model | delivered, 4/5 | no / yes |
