"""Retest live retrieval from only the original acquired artifacts.

Use a new runtime snapshot and fresh stores containing the exact original
payloads. New processes must revalidate the imported, unqualified artifacts.
No generated replacement from an earlier warm run is imported.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import zlib

from experiments.astra_live.run import BUDGET, TASKS, invoke, write_json
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.execution import IMAGE


def run(campaign, root):
    campaign, root = campaign.resolve(), root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    source = root / 'source' / 'kuttaka_runtime'
    source.mkdir(parents=True)
    for path in (repo / 'kuttaka_runtime').glob('*.py'):
        (source / path.name).write_bytes(path.read_bytes())
    (root / 'source' / 'retrieve.py').write_bytes(Path(__file__).read_bytes())
    imported = {}
    for task in TASKS:
        result_path = campaign / task / 'acquire/result.json'
        result = json.loads(result_path.read_text())
        if result['status'] != 'checked':
            continue
        artifact = result['answer']['artifact']
        task_root = root / task
        task_root.mkdir()
        for filename in ('acquisition-task.json', 'deployment-task.json'):
            (task_root / filename).write_bytes((campaign / task / filename).read_bytes())
        # Blobs are immutable and installed atomically. Read the selected blob
        # without opening the source store for writing during another run.
        blob = campaign / task / 'memory' / 'blobs' / (artifact + '.json.zlib')
        raw = zlib.decompress(blob.read_bytes())
        if hashlib.sha256(raw).hexdigest() != artifact:
            raise ValueError('Source artifact identity mismatch')
        payload = json.loads(raw)
        with ArtifactMemory(task_root / 'memory') as memory:
            if memory.put(payload) != artifact:
                raise ValueError('Imported artifact identity changed')
        imported[task] = artifact
    metadata = {'source_campaign': str(campaign), 'imported_artifacts': imported,
                'budget_per_model_run': BUDGET, 'execution_image': IMAGE,
                'child_import_root': str(root / 'source'),
                'attribution': 'Development retrieval retest; original acquired artifacts only, no prior qualification imported',
                'source_files_sha256': {str(path.relative_to(root / 'source')): hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in sorted(source.glob('*.py'))}}
    write_json(root / 'campaign.json', metadata)
    summary = {'campaign': metadata, 'runs': []}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, root, task, 'warm', artifact) for task, artifact in imported.items()]
        for future in futures:
            summary['runs'].append(future.result())
            write_json(root / 'summary.json', summary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.campaign, args.output)
