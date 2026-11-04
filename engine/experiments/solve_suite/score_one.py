"""Score one delivered method (a demo run) on its task's hidden cases, with the suite's own scorer.

    python3 experiments/solve_suite/score_one.py --suite hard --task <id> \
        --workspace results/demo/<date>-<task>/workspace --artifact <id> --out results/demo/<date>-<task>/score.json

The method runs in the Docker sandbox with every stored method it uses, exactly as in run.py.
A demonstration is never added to any suite score; this only records what it delivered.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from kuttaka_runtime.demo import load_method  # noqa: E402
from kuttaka_runtime.execution import PythonExecutor, preflight  # noqa: E402
import run  # noqa: E402


def score(suite, task, memory, artifact, executor):
    prefix = {'graded': '', 'growth': 'growth_', 'hard': 'hard_'}[suite]
    cases = json.loads((run.HERE / (prefix + 'hidden.json')).read_text())[task]
    source, libraries = load_method(memory, artifact)
    passed, _ = run.run_cases(executor, source, libraries, cases)
    return {'suite': suite, 'task': task, 'artifact': artifact, 'hidden_passed': passed,
            'hidden_cases': len(cases), 'solved': passed == len(cases)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--suite', choices=['graded', 'growth', 'hard'], required=True)
    p.add_argument('--task', required=True)
    p.add_argument('--workspace', required=True, help='the demo run\'s workspace (its memory/ holds the method)')
    p.add_argument('--artifact', required=True, help='the delivered method id, from the result')
    p.add_argument('--out', required=True)
    args = p.parse_args(argv)
    if not preflight()['ready']:
        sys.exit('Docker sandbox not ready')
    result = score(args.suite, args.task, Path(args.workspace) / 'memory', args.artifact,
                   PythonExecutor(timeout=20, trace='distinct'))
    Path(args.out).write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
