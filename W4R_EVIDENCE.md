# W4-r hard-suite scorecard

Recorded per-task scores and source artifact hashes for the speed-focused edition. The private cases and raw journals are outside this submission.

rows.jsonl SHA-256: With Kuttaka `c2078946679a339d70a40e5732837a7c70af0bc72b181d8a565135ab67073226 (rescored.json fa6aea452bad513aaacd3335e78fd55ff8a85ab4979b467cfc4745a1f33423e3)`, alone `e86457901c5aced51593e6b79f972c3e793c4370c7225e93647d370f2b2f5ddd (rescored.json e8e95135b17cdeedc584550a4702fef02700a7c153b91484209af5f15b9fbe7a)`.

| Outcome | Count |
|---|---|
| tasks | 12 |
| wins | 3 |
| losses | 0 |
| both | 8 |
| neither | 1 |
| unknown | 0 |
| solved_with | 11 |
| solved_alone | 8 |
| wrong_delivered_with | 1 |
| wrong_delivered_alone | 4 |
| unscored_with | 0 |
| unscored_alone | 0 |
| calls_with | 40 |
| calls_alone | 12 |

Counting unverified answers too (the model alone is scored on whatever code it returns): win 3, loss 0, both 8, neither 1, unknown 0.

| Task | Level | Outcome | With Kuttaka: status, hidden, calls, written by | alone: status, hidden | Wrong answer delivered (With Kuttaka / alone) |
|---|---|---|---|---|---|
| h-c2-substrings | 8 | **BOTH** | checked, 7/7, 3, model | delivered, 7/7 | no / no |
| h-m3-primroot | 8 | **BOTH** | checked, 7/7, 3, model | delivered, 7/7 | no / no |
| h-m1-digits | 7 | **BOTH** | checked, 6/6, 3, model | delivered, 6/6 | no / no |
| h-m2-squarefree | 8 | **BOTH** | checked, 6/6, 4, model | delivered, 6/6 | no / no |
| h-r2-turning | 8 | **BOTH** | checked, 6/6, 2, model | delivered, 6/6 | no / no |
| h-m4-sqrtmod | 9 | **WIN** | checked, 11/11, 3, model | delivered, 10/11 | no / yes |
| h-c4-puzzle | 9 | **BOTH** | checked, 6/6, 2, model | delivered, 6/6 | no / no |
| h-c3-maxflow | 9 | **BOTH** | checked, 5/5, 3, model | delivered, 5/5 | no / no |
| h-r3-arm | 9 | **BOTH** | checked, 7/7, 4, model | delivered, 7/7 | no / no |
| h-c1-regex | 7 | **NEITHER** | checked, 8/9, 4, model | delivered, 3/9 | yes / yes |
| h-r1-drag | 7 | **WIN** | checked, 5/5, 4, model | delivered, 4/5 | no / yes |
| h-r4-collide | 9 | **WIN** | checked, 4/4, 5, model | delivered, 2/4 | no / yes |
