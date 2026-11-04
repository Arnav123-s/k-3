# Expanded demo edit

[Watch the expanded preview](kuttaka3-demo-expanded.mp4). The preview is 3:00. Its 0:22–1:10 slot now contains three short examples. The intro and the sections from 1:10 onward are taken from the earlier edit.

| Time | On screen | Suggested narration |
|---|---|---|
| 0:22–0:44 | Accelerated modular square-root terminal replay | Glimmer proposes checks and a method for modular square roots. The saved method passed all eleven private cases in four calls. |
| 0:44–0:57 | W4-o drag-repair trace summary | A reference and a blind re-derivation both rejected the first drag method. The repaired method passed all five private cases. |
| 0:57–1:10 | Spark's largest recorded lead as a scorecard | At 00:19:04 UTC on November 4, Spark had six verified solves after 45 dispatched calls. Spark with Kuttaka had nine after 52. |

The drag section summarizes the recorded trace, and the Spark section shows one checkpoint from the recorded terminal timeline. The largest observed lead was three solves. Among checkpoints tied at that lead, this one had the smallest difference in dispatched calls (seven). The modular square-root replay is the existing terminal rendition. For the owner-recorded Muse Code take, use [RECORD_THIS.md](RECORD_THIS.md).

- [Modular square-root evidence](https://github.com/Arnav123-s/kuttaka/tree/32c6f37/results/demo/2026-11-02-h-m4-sqrtmod)
- [W4-o repair and scoring evidence](https://github.com/Arnav123-s/kuttaka/blob/32c6f37/results/solve-suite/w4o-glimmer-fresh/FINAL_REPORT.md)
- [Spark checkpoint and terminal transcript](https://github.com/Arnav123-s/kuttaka/tree/d905468/results/external/race-snapshot-20261104T155245Z/lcb-meta-spark-race)

To rebuild the expanded preview, run `python assemble.py` once to generate the base edit and prepare the local font, then run `python expand-demo.py`. Both scripts use FFmpeg, with an optional FFMPEG environment variable for its path.

The 3:40 capture cut inserts two excerpts from the archived LiveCodeBench terminal captures after the Spark scorecard. From 1:10 to 1:30, Glimmer + Kuttaka reaches 2/12 at 92 dispatched calls while Glimmer alone is at 0/12 at 101 calls. From 1:30 to 1:50, Wonder + Kuttaka catches up to 10/12 at 64 dispatched calls. Wonder alone is already at 10/12 at 40 calls. Each excerpt displays four recorded rows with the waiting time shortened. The original closing sections resume at 1:50.

In the 3:07 cut, the demos end at 1:50. The hard-suite card runs from 1:50 to 1:56 and the graded and growth card runs from 1:56 to 2:02. Each reveals the scores in sequence. The full 65-second original closing segment starts at 2:02. Rebuild from the 3:40 capture cut with `python tighten-results.py` after preparing the font with assemble.py.
