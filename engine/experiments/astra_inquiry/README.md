# Astra finite inquiry demonstration

This is a deterministic mechanism demonstration. Every world, candidate program, likelihood table, revision, and additional instrument is supplied by the researcher. No LLM, model training, network call, or generated program execution occurs. The generic runtime modules do not contain these domain examples.

From the repository root, use a new output directory:

```sh
python -B -m experiments.astra_inquiry.run --output /tmp/astra-inquiry-new
python -B -m unittest tests.test_astra_runtime tests.test_astra_models tests.test_astra_beliefs tests.test_astra_artifacts tests.test_astra_inquiry
```

The runner refuses an existing output directory to preserve evidence. The committed [results](recorded/results.json) and journals are one reproducible run. All 44 focused tests passed when this increment was recorded.

The same information-gain selector distinguishes authored integer sequences, list operations, and linear force coefficients in one observation each. A supplied fixed-order control needs three, three, and two observations respectively. Both controls have the same candidate and prediction-table access; this counts environment observations rather than total computational cost. These tiny examples are illustrative, not a broad capability benchmark.

A fourth inquiry initially fits a linear model to a small measurement. A larger-input challenge exposes a discrepancy that the surviving candidate cannot explain. The system preserves a question and invalidates the model's qualification and dependent method. The failed model remains available as the baseline for a separately checked arithmetic residual; that new artifact cites both the old model and the measurement, without relying on the old model's truth.

Two researcher-supplied revisions, physical force saturation and sensor clipping, fit both original readings. A new researcher-supplied independent readout separates their predictions. The selector chooses that readout, and the observation supports sensor clipping within the supplied alternatives. All historical evidence is replayed under the revision. The example shows the need to expand what can be observed; it does not show that a model invented the new hypotheses or instrument.

The runner reopens memory and reconstructs the three retained beliefs exactly. Seven unique artifacts occupy 3,561 canonical bytes and 2,132 compressed blob bytes. The artifact journal adds 4,556 bytes; the four inquiry journals add 16,362 bytes. Filesystem overhead and this README/results file are not included in these payload/journal counts. Deduplication and compression do not imply learned abstraction.

Read the [mathematical design and limits](../../docs/research/general-inquiry/README.md) and [thirteen alternative roles](../../docs/research/general-inquiry/alternative-lenses.md). Automatic generation of representations and investigators, qualified execution, and independent transfer evaluation remain research work.
