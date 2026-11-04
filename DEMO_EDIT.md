# Expanded demo edit

[Watch the expanded preview](kuttaka3-demo-expanded.mp4). The preview is 3:00. Its 0:22–1:10 slot now contains three short examples. The intro and the sections from 1:10 onward are taken from the earlier edit.

| Time | On screen | Suggested narration |
|---|---|---|
| 0:22–0:44 | Accelerated modular square-root terminal replay | Glimmer proposes checks and a method for modular square roots. The saved method passed all eleven private cases in four calls. |
| 0:44–0:57 | W4-o drag-repair trace summary | A reference and a blind re-derivation both rejected the first drag method. The repaired method passed all five private cases. |
| 0:57–1:10 | Spark terminal checkpoint as a scorecard | On this LiveCodeBench checkpoint, Spark had nine verified solves alone and ten with Kuttaka. The arms had dispatched 498 and 559 calls. |

The drag section summarizes the recorded trace, and the Spark section reproduces a recorded checkpoint. The modular square-root replay is the existing terminal rendition. For the owner-recorded Muse Code take, use [RECORD_THIS.md](RECORD_THIS.md).

- [Modular square-root evidence](https://github.com/Arnav123-s/kuttaka/tree/32c6f37/results/demo/2026-11-02-h-m4-sqrtmod)
- [W4-o repair and scoring evidence](https://github.com/Arnav123-s/kuttaka/blob/32c6f37/results/solve-suite/w4o-glimmer-fresh/FINAL_REPORT.md)
- [Spark checkpoint and terminal transcript](https://github.com/Arnav123-s/kuttaka/tree/d905468/results/external/race-snapshot-20261104T155245Z/lcb-meta-spark-race)

To rebuild the expanded preview, run `python assemble.py` once to generate the base edit and prepare the local font, then run `python expand-demo.py`. Both scripts use FFmpeg, with an optional FFMPEG environment variable for its path.
