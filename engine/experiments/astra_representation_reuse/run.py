"""Fresh-process retrieval and execution of an authored representation artifact.

This is a bounded engineered-controller experiment over public authored fixtures.
It tests exact-ID artifact retrieval and a prospective held-out forecast, not
controller resume, model discovery, or a general capability claim.
"""
from __future__ import annotations

import argparse
import gzip
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import shutil

from experiments.astra_representation_search.run import fixtures

from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.representation_search import RepresentationSearch
from kuttaka_runtime.shift_relation import predict_shift_relation
from kuttaka_runtime.store import Journal

CONTEXT = "authored-representation-reuse-demo-v1"
MODULE = "experiments.astra_representation_reuse.run"
PREFIX_SIZE = 12
HELDOUT_SIZE = 6
LIMITS = dict(max_observations=16, max_fits=128, max_order=4, max_degree=4,
              max_columns=12, min_fresh=2)


def _write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True) + "\n")


def _source_hashes():
    root = Path(__file__).resolve().parents[2]
    source_paths = [Path(__file__).resolve(), root / "kuttaka_runtime" / "representation_search.py",
                    root / "kuttaka_runtime" / "shift_relation.py",
                    root / "experiments" / "astra_representation_search" / "run.py"]
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_paths}


def _acquire(args):
    prefix = json.loads(Path(args.input).read_text())
    cursor = 0

    def observe_next():
        nonlocal cursor
        if cursor >= len(prefix):
            raise StopIteration
        value = prefix[cursor]
        cursor += 1
        return value

    store, journal = Path(args.store), Path(args.journal)
    store.parent.mkdir(parents=True, exist_ok=True)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with Journal(journal) as events, ArtifactMemory(store) as memory:
        run_limits = {**LIMITS, "max_observations": min(LIMITS["max_observations"], len(prefix))}
        controller = RepresentationSearch(memory, events, observe_next,
            context=CONTEXT, **run_limits)
        report = controller.run()
        seen_methods = []
        for artifact_id, candidate in report["candidate_support"].items():
            payload = memory.get(artifact_id)
            if payload["kind"] == "method":
                seen_methods.append(artifact_id)
        final_decision = report["decisions"][-1] if report["decisions"] else None
        eligible = [] if final_decision is None else list(final_decision["eligible_ids"])
        # Reuse only a single method selected by the controller's current
        # non-ambiguous decision. Tied/ambiguous choices remain abstentions.
        selected = []
        if final_decision and final_decision["value"] is not None and len(eligible) == 1:
            artifact_id = eligible[0]
            candidate = report["candidate_support"].get(artifact_id)
            if candidate and not candidate["failed"] and not candidate["blocked"] and candidate["hits"] >= report["min_fresh"]:
                try:
                    payload = memory.get_qualified(artifact_id, report["scope"])
                except (PermissionError, KeyError):
                    payload = None
                if payload is not None and payload["kind"] == "method":
                    selected = [artifact_id]
        result = {"process_id": os.getpid(), "source_prefix_count": len(prefix), "observed_count": len(report["history"]),
                  "fit_calls": report["fit_calls"], "forecast_attempts": report["forecast_attempts"],
                  "issued_predictions": report["issued_predictions"],
                  "correct_issued": report["correct_issued"], "wrong_issued": report["wrong_issued"],
                  "max_observations": run_limits["max_observations"], "max_fits": run_limits["max_fits"],
                  "selected_method_ids": selected, "all_method_ids": seen_methods,
                  "selection": {"decision_mode": None if final_decision is None else final_decision["mode"],
                                "eligible_candidate_count": len(eligible),
                                "abstained_on_tie_or_ambiguity": bool(final_decision and final_decision["value"] is None) or len(eligible) != 1},
                  "scope": report["scope"], "status": report["status"]}
    _write(args.output, result)


def _reuse(args):
    prefix = json.loads(Path(args.input).read_text())
    with ArtifactMemory(args.store) as memory:
        artifact_id, scope = args.artifact, args.scope
        status = memory.status(artifact_id, scope)
        try:
            payload = memory.get_qualified(artifact_id, scope)
        except (PermissionError, KeyError):
            payload = None
        if payload is None or payload["kind"] != "method":
            result = {"process_id": os.getpid(), "artifact_id": artifact_id, "scope_status": status,
                      "executed": False, "reason": "method is not qualified for this scope"}
        else:
            forecast = predict_shift_relation(payload["body"], prefix,
                                              start_index=0, steps=args.steps)
            result = {"process_id": os.getpid(), "artifact_id": artifact_id, "scope_status": status, "executed": True,
                      "order": payload["body"]["order"], "degree": payload["body"]["degree"],
                      "predictions": forecast["values"], "steps": args.steps}
    _write(args.output, result)


def _verify_invalid(args):
    with ArtifactMemory(args.store) as memory:
        status = memory.status(args.artifact, args.scope)
        result = {"process_id": os.getpid(), "artifact_id": args.artifact, "scope_status": status,
                  "reusable": status == "qualified"}
    _write(args.output, result)


def _child(mode, *, store, journal=None, input_file=None, output, artifact=None,
           scope=None, steps=None):
    cmd = [sys.executable, "-m", MODULE, "--worker", mode, "--store", str(store),
           "--output", str(output)]
    if journal is not None:
        cmd += ["--journal", str(journal)]
    if input_file is not None:
        cmd += ["--input", str(input_file)]
    if artifact is not None:
        cmd += ["--artifact", artifact]
    if scope is not None:
        cmd += ["--scope", scope]
    if steps is not None:
        cmd += ["--steps", str(steps)]
    subprocess.run(cmd, check=True, cwd=Path(__file__).resolve().parents[2],
                   stdout=subprocess.DEVNULL)
    return json.loads(Path(output).read_text())


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    streams = fixtures()
    accepted = [str(v) for v in streams["r2d1_base"]]
    contradicted = [str(v) for v in streams["noisy_counterexample"]]
    prefix = accepted[:PREFIX_SIZE]
    withheld = accepted[PREFIX_SIZE:PREFIX_SIZE + HELDOUT_SIZE]
    prefix_path = output / "accepted-prefix.json"
    mismatch_prefix_path = output / "contradicted-prefix.json"
    _write(prefix_path, prefix)
    _write(mismatch_prefix_path, contradicted[:PREFIX_SIZE + 1])

    accepted_store, accepted_journal = output / "accepted-store", output / "accepted-journal.jsonl"
    acquired = _child("acquire", store=accepted_store, journal=accepted_journal,
                      input_file=prefix_path, output=output / "acquisition.json")
    ids = acquired["selected_method_ids"]
    if len(ids) != 1:
        report = {
            "schema": "authored-representation-fresh-process-reuse-v1",
            "attribution": "unchanged engineered exact-shift constructor; public authored fixtures; no evaluated model",
            "endpoint_calls": 0, "source_sha256": _source_hashes(),
            "limits": {**LIMITS, "accepted_training_prefix": PREFIX_SIZE,
                       "heldout_steps": HELDOUT_SIZE, "contradicted_run_observations": PREFIX_SIZE + 1},
            "accepted_acquisition": acquired, "reuse_attempt": {"executed": False,
                "reason": "full-lattice controller selection was tied, ambiguous, or unqualified; abstained"},
            "interpretation": "no forced candidate selection; bounded authored fixture only",
        }
        _write(output / "results.json", report)
        return report
    artifact_id = ids[0]

    # This child process opens the closed store and predicts six unseen values.
    replay = _child("reuse", store=accepted_store, input_file=prefix_path,
                    output=output / "fresh-process-reuse.json", artifact=artifact_id,
                    scope=acquired["scope"], steps=len(withheld))
    predictions = replay.get("predictions", [])
    pair_errors = sum(a != b for a, b in zip(predictions, withheld))
    missing_predictions = max(0, len(withheld) - len(predictions))
    extra_predictions = max(0, len(predictions) - len(withheld))
    prediction_errors = pair_errors + missing_predictions + extra_predictions
    heldout = {"count": len(withheld), "prediction_count": len(predictions),
               "correct": len(withheld) - prediction_errors,
               "incorrect": prediction_errors, "pair_mismatches": pair_errors,
               "missing_predictions": missing_predictions, "extra_predictions": extra_predictions,
               "scored_by_independent_fixture_generator": True,
               "prediction_values": replay["predictions"], "actual_values": withheld}

    # Run the same unchanged controller against the same 12-value prefix followed
    # by the authored alteration at index 12; the runtime itself records failure.
    contradicted_store, contradicted_journal = output / "contradicted-store", output / "contradicted-journal.jsonl"
    contradiction = _child("acquire", store=contradicted_store, journal=contradicted_journal,
                           input_file=mismatch_prefix_path, output=output / "contradiction-run.json")
    if artifact_id not in contradiction["all_method_ids"]:
        raise RuntimeError("contradicted run did not construct the same exact method artifact")
    invalid = _child("verify-invalid", store=contradicted_store,
                     output=output / "fresh-process-invalid-status.json",
                     artifact=artifact_id, scope=contradiction["scope"])
    if acquired["process_id"] == replay["process_id"]:
        raise RuntimeError("acquisition and retrieval ran in the same process")

    source_hashes = _source_hashes()

    report = {
        "schema": "authored-representation-fresh-process-reuse-v1",
        "attribution": "unchanged engineered exact-shift constructor; public authored fixtures; no evaluated model",
        "endpoint_calls": 0,
        "source_sha256": source_hashes,
        "predecessor_note": {
            "status": "restricted-grammar exploratory attempt; not a full-lattice result",
            "private_path": ".astra/alignment-reuse/run/results.json",
            "allowed_pairs": [[2, 1]], "max_order": 2, "max_degree": 1,
            "recorded_source_sha256": "305f6254a10a917de5d7102297ac3bdee0e97ac11777edaac459f019cd017063",
            "results_and_run_files_preserved": True,
            "source_snapshot_available": False,
            "limitation": "initial restricted-run source hash is recorded but its source snapshot was not saved",
            "initial_results_included_in_evidence_archive": True},
        "limits": {**LIMITS, "accepted_training_prefix": PREFIX_SIZE,
                   "heldout_steps": HELDOUT_SIZE, "contradicted_run_observations": PREFIX_SIZE + 1},
        "accepted_acquisition": acquired,
        "fresh_process_reuse": replay,
        "heldout_score": heldout,
        "contradicted_context": contradiction,
        "fresh_process_contradiction_check": invalid,
        "incorrect_prediction_counts": {
            "accepted_training_prefix": acquired["wrong_issued"],
            "fresh_process_heldout": prediction_errors,
            "contradicted_context_before_invalidation": contradiction["wrong_issued"]},
        "interpretation": "generic exact-shift interpreter executed stored coefficient data by exact ID; no Python code synthesis, controller resume, or generalization claim",
    }
    _write(output / "results.json", report)
    return report


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(output):
    """Run and package complete public fixture evidence with integrity receipts."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="astra-representation-reuse-") as temporary:
        evidence = Path(temporary) / "evidence"
        report = run(evidence)
        root = Path(__file__).resolve().parents[2]
        predecessor = root / ".astra" / "alignment-reuse" / "run"
        predecessor_source = root / ".astra" / "alignment-reuse" / "reconstructed-initial-source.py"
        if predecessor.is_dir():
            shutil.copytree(predecessor, evidence / "initial-restricted-attempt",
                            ignore=shutil.ignore_patterns("*.lock", ".writer.lock"))
        if predecessor_source.is_file():
            old_result = json.loads((predecessor / "results.json").read_text())
            expected_hash = old_result["source_sha256"]["experiments/astra_representation_reuse/run.py"]
            if _sha(predecessor_source) != expected_hash:
                raise ValueError("reconstructed predecessor source does not match recorded source hash")
            snapshot_target = evidence / "sources" / "initial-restricted-run.py"
            snapshot_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(predecessor_source, snapshot_target)
            report["predecessor_note"].update({
                "source_snapshot_available": True,
                "source_snapshot_archive_path": "sources/initial-restricted-run.py",
                "source_snapshot_sha256": expected_hash,
                "limitation": None})
            (evidence / "results.json").write_text(json.dumps(report, sort_keys=True) + "\n")
        source_paths = [Path(__file__).resolve(), root / "kuttaka_runtime" / "representation_search.py",
                        root / "kuttaka_runtime" / "shift_relation.py",
                        root / "experiments" / "astra_representation_search" / "run.py"]
        for source in source_paths:
            destination = evidence / "sources" / source.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        # Validate journals and reopen every store before archiving.
        journal_files = sorted(evidence.rglob("*.jsonl"))
        for journal in journal_files:
            Journal.read(journal)
        store_dirs = sorted(path for path in evidence.rglob("*")
                            if path.is_dir() and path.name.endswith("-store"))
        for store in store_dirs:
            with ArtifactMemory(store) as memory:
                memory.stats()
        files = sorted(path for path in evidence.rglob("*") if path.is_file()
                       and not path.name.endswith(".lock") and path.name != ".writer.lock")
        hashes = {path.relative_to(evidence).as_posix(): _sha(path) for path in files}
        archive = output / "evidence.tar.gz"
        with archive.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as bundle:
                for path in files:
                    info = bundle.gettarinfo(str(path), arcname=path.relative_to(evidence).as_posix())
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.mode = 0o644
                    with path.open("rb") as source:
                        bundle.addfile(info, source)
        summary = {"schema": "authored-representation-fresh-process-reuse-record-v1",
                   "results": report, "evidence_archive": archive.name,
                   "evidence_sha256": _sha(archive), "evidence_bytes": archive.stat().st_size,
                   "evidence_files": len(files), "file_sha256": hashes,
                   "validated_journals": len(journal_files),
                   "validated_journal_events": sum(len(Journal.read(path)) for path in journal_files),
                   "reopened_artifact_stores": len(store_dirs)}
        (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        # Read the archive back and check every included file hash.
        with tarfile.open(archive, "r:gz") as bundle:
            extracted = {member.name: hashlib.sha256(bundle.extractfile(member).read()).hexdigest()
                         for member in bundle.getmembers() if member.isfile()}
        if extracted != hashes:
            raise ValueError("packaged evidence archive hash manifest mismatch")
        verification = {"evidence_sha256": _sha(archive), "file_hashes_verified": len(extracted),
                        "journals_validated": len(journal_files),
                        "journal_events_validated": summary["validated_journal_events"],
                        "stores_reopened": len(store_dirs),
                        "source_hashes_match": all(_sha(source) == report["source_sha256"][str(source.relative_to(root))]
                                                    for source in source_paths),
                        "predecessor_source_hash_resolved": bool(predecessor_source.is_file() and
                            _sha(predecessor_source) == json.loads((predecessor / "results.json").read_text())
                            ["source_sha256"]["experiments/astra_representation_reuse/run.py"])}
        (output / "verification.json").write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--worker", choices=("acquire", "reuse", "verify-invalid"))
    parser.add_argument("--store")
    parser.add_argument("--journal")
    parser.add_argument("--input")
    parser.add_argument("--artifact")
    parser.add_argument("--scope")
    parser.add_argument("--steps", type=int)
    args, _ = parser.parse_known_args()
    if args.worker:
        {"acquire": _acquire, "reuse": _reuse, "verify-invalid": _verify_invalid}[args.worker](args)
    else:
        (record if args.record else run)(args.output)


if __name__ == "__main__":
    main()
