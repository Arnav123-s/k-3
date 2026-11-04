#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

for edition in engine engine-speed engine-best; do
    (
        cd -- "$edition"
        python3 -m kuttaka_runtime --help >/dev/null
        python3 -m kuttaka_runtime.serve --help >/dev/null
        python3 - <<'PY'
import json
from kuttaka_runtime.execution import PythonExecutor, preflight

ready = preflight()
if not ready["ready"]:
    raise SystemExit(f"Docker preflight failed: {ready['reason']}. See JUDGE_QUICKSTART.md.")
result = PythonExecutor()({"source": "def run(x): return sorted(set(x))", "input": [8, 3, 8, -1]})
assert result["exit_code"] == 0, result
assert json.loads(result["output"])["result"] == [-1, 3, 8], result
PY
    )
    printf '%s: CLI, server entry point, and Docker execution passed\n' "$edition"
done
