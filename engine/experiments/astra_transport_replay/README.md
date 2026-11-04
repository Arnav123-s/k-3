# Recorded method replay and fresh-process reuse

This development demonstration replays five responses from an original live
integer-equation method acquisition through the current runtime. It reconstructs
the same content-addressed method in empty memory, applies the original four
public checks, and then invokes the stored method from a separate CLI process on
an input absent from those checks and the original deployment input.

The recorded projection at `recorded/evidence.json` contains receipt digests,
artifact and source identifiers, check counts, the protocol rejection, current
runtime observation differences, and the fresh-process result. It excludes raw
provider response bodies, model contexts, and task journals. Reproduction needs
the private original journal, manifest, result, and compressed artifact payload;
they are supplied as command-line paths and are not bundled here.

Example, with those private files available and a new empty output path:

```bash
python -B experiments/astra_transport_replay/run.py \
  --source-events /path/to/acquire/events.jsonl \
  --source-manifest /path/to/acquisition-task.json \
  --source-result /path/to/acquire/result.json \
  --source-payload /path/to/memory/blobs/ARTIFACT_ID.json.zlib \
  --new-input '{"a":4,"b":7,"c":5}' \
  --output /path/to/private-output \
  --recorded-output /path/to/new-public-projection.json
```

The runner uses `ReplayModel` and the pinned Docker image. It makes no provider
calls. The independent new-input check enumerates one complete residue period
for `x` and uses the general-solution period to verify the least nonnegative
solution. Results qualify only on the supplied finite development checks; this
is replay evidence, not new acquisition or a capability-gain claim.
