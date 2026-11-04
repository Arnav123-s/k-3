# Recorded evidence

The submission includes the [per-task W4-o scorecard and artifact hashes](W4O_EVIDENCE.md) and [demo timings](DEMO_EDIT.md). The original run artifacts and reports live in the parent project and may require repository access.

## Earlier development runs

- W4-o, engine pin `8ce0951`: Glimmer with Kuttaka solved 12/12 in 53 calls and passed 79/79 private cases on rescore. Glimmer alone solved 8/12 in 12 calls. Four tasks improved and eight tied.
- W4-r, a separate later run at `cff12a9`: Glimmer with Kuttaka solved 11/12 in 40 calls. The remaining regex solution passed 8/9 private cases. These runs do not isolate the effect of one change.
- Growth: Kuttaka with memory solved 12/12 in 69 calls. With a fresh workspace it solved 11/12 in 52 calls. A separate later run at `cff12a9` solved 12/12 in 59 calls.

## LiveCodeBench terminal excerpts

Selected rows from the archived terminal recordings on November 4, 2026. Calls are dispatched calls at the indicated checkpoint.

| Model | UTC | Model alone | Model + Kuttaka |
|---|---|---|---|
| Spark | 00:19:04 | 6/12, 45 calls | 9/12, 52 calls |
| Wonder | 06:06:19 | 10/12, 40 calls | 10/12, 64 calls |
| Glimmer | 14:20:30 | 0/12, 101 calls | 2/12, 92 calls |

The cut shows selected moments from separate runs. These rows compare observed progress at those moments. Call counts differ.
