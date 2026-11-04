"""Run the Erdős #979 demo: one prompt, no examples, the general solver; then the same model alone.

    python experiments/erdos979/run.py --arm kuttaka --out results/erdos979/kuttaka
    python experiments/erdos979/run.py --arm alone   --out results/erdos979/alone
    python experiments/erdos979/score.py results/erdos979/kuttaka/answer.json

kuttaka: `solve_task` exactly as the CLI calls it (nothing task-specific), with a long limit for the final
run of the checked method. If a round ends incomplete, the next round starts on the same workspace
(lessons, partial methods and breakdowns kept), until the system itself reports a checked answer or
--rounds is used up. Scoring is separate (score.py) and never fed back.
alone: one call; the model's code is run once with the same long limit.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from kuttaka_runtime.execution import PythonExecutor, preflight  # noqa: E402
from kuttaka_runtime.models import JsonModel  # noqa: E402
from kuttaka_runtime.runtime import TransportFailure  # noqa: E402
from kuttaka_runtime.solve import Metered, Workspace, settings, solve_task  # noqa: E402
from task import PROMPT  # noqa: E402

ALONE = '''Solve the user's task by writing Python. Return exactly one JSON object, without prose or
Markdown fences: {"source": "<Python source defining run(input) and returning the result>"}.
Use only the Python standard library. run is called once with input null.'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--arm', choices=['kuttaka', 'alone'], required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--model', default='meta/muse-glimmer-30b')
    p.add_argument('--base-url', default='https://integrate.api.nvidia.com/v1')
    p.add_argument('--key-env', default='NVIDIA_API_KEY')
    p.add_argument('--max-calls', type=int, default=120, help='per round')
    p.add_argument('--rounds', type=int, default=10)
    p.add_argument('--run-timeout', type=float, default=6 * 3600)
    p.add_argument('--run-memory', default='2g', choices=PythonExecutor.MEMORY_LIMITS)
    a = p.parse_args()
    if not preflight()['ready']:
        sys.exit('Docker sandbox not ready')
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    log = out / 'rounds.jsonl'
    if a.arm == 'alone':
        counter = {'used': 0, 'limit': 1}
        model = Metered(JsonModel(a.model, a.base_url, protocol='openai', key_env=a.key_env, parse_mode='extract',
                                  max_tokens=16384, timeout=300, stream=True, sampling_fields=False,
                                  reasoning_hint='low', instructions=ALONE), counter)
        started = time.time()
        try:
            reply = model.propose({'task': PROMPT})
        except TransportFailure as error:
            sys.exit(f'transport failure: {error.kind}; rerun')
        source = reply.get('source') if isinstance(reply.get('source'), str) else None
        answer, status = None, 'no_code'
        if source:
            run = PythonExecutor(timeout=a.run_timeout, long_run=True, memory=a.run_memory)({'source': source, 'input': None})
            status = run.get('status')
            try:
                answer = json.loads(run['output']).get('result') if run.get('exit_code') == 0 else None
            except (ValueError, AttributeError):
                answer = None
        row = {'arm': 'alone', 'status': status, 'model_calls': counter['used'], 'source': source,
               'answer': answer, 'seconds': round(time.time() - started, 1)}
        log.write_text(json.dumps(row) + '\n')
        (out / 'answer.json').write_text(json.dumps(answer) + '\n')
        print(json.dumps({k: row[k] for k in ('status', 'answer', 'seconds')}))
        return
    ws = Workspace(out / 'workspace')
    config = settings(model=a.model, base_url=a.base_url, protocol='openai', key_env=a.key_env,
                      max_calls=a.max_calls, run_timeout=a.run_timeout, run_memory=a.run_memory)
    done = len(log.read_text().splitlines()) if log.exists() else 0
    for number in range(done + 1, a.rounds + 1):
        started = time.time()
        try:
            result = solve_task(PROMPT, settings=config, workspace=ws, say=lambda text: print(text, flush=True))
        except TransportFailure as error:
            print(f'round {number}: transport failure {error.kind}; rerun the same command to resume', flush=True)
            sys.exit(3)
        row = {'round': number, 'status': result['status'], 'reason': result.get('reason'),
               'answer': result.get('answer'), 'artifact': result.get('artifact'), 'checks_by': result.get('checks_by'),
               'cost': result['cost'], 'progress': result.get('progress'), 'strategies': result.get('strategies'),
               'seconds': round(time.time() - started, 1)}
        with log.open('a') as handle:
            handle.write(json.dumps(row, default=str) + '\n')
        print(f'round {number}: {row["status"]} ({row["reason"]}) calls {row["cost"]["model_calls"]} {row["seconds"]}s', flush=True)
        (out / 'result.json').write_text(json.dumps(result, indent=1, default=str) + '\n')
        if result['status'] == 'checked' and result.get('answer') is not None:
            (out / 'answer.json').write_text(json.dumps(result.get('answer')) + '\n')
            return
        if result['status'] == 'checked':
            # A checked method with no answer to the task is no checked answer (ERDŐS-979-c and -d stopped here).
            print(f'round {number}: checked, but no answer to the task was delivered; the rounds go on', flush=True)
    if not (out / 'answer.json').exists():
        (out / 'answer.json').write_text('null\n')   # no round delivered a checked answer: scored as such


if __name__ == '__main__':
    main()
