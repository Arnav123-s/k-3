"""Real sandbox protocol check with a disclosed scripted model, not a task win."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from kuttaka_runtime.execution import PythonExecutor
from kuttaka_runtime.investigators import run_policy, InvestigatorBudget
from kuttaka_runtime.store import Journal
from .acquire import write_json


def smoke(candidate_path, output):
    output.mkdir(parents=True, exist_ok=False)
    candidate = json.loads(candidate_path.read_text())
    class ProtocolModel:
        identity = {'model': 'scripted protocol fixture; not live capability evidence'}
        max_tokens = 64
        last_usage = {'input_tokens': 0, 'output_tokens': 0}
        last_response = None
        def propose(self, context):
            observed = any(h.get('action', {}).get('tool') == 'read_value'
                           and h.get('observation') == {'value': 7} for h in context['history'])
            if observed:
                return {'kind': 'answer', 'answer': 7}
            return {'kind': 'experiment', 'tool': 'read_value', 'input': {},
                    'predictions': [{'hypothesis': 'The fixture will return 7', 'outcome': {'value': 7}}]}
    def read_value(value):
        """Protocol fixture. Input {}. Returns {value: integer}."""
        return {'value': 7}
    with Journal(output / 'events.jsonl') as journal:
        result = run_policy('Read the fixture value and submit that integer.', candidate['source'],
                            ProtocolModel(), {'read_value': read_value}, lambda _g, ans: ans == 7,
                            journal, executor=lambda: PythonExecutor(timeout=10),
                            budget=InvestigatorBudget(model_calls=2, tool_calls=2, max_steps=8,
                                                       policy_executions=8, max_response_tokens=64))
    feedback = {'previous_candidate': candidate, 'result': asdict(result),
                'test_attribution': 'Scripted model replies; real Docker policy execution; protocol check only.',
                'expected_behavior': 'The policy must arrange for the requested observation and final answer to reach the broker within the fixture budgets.',
                'observed_behavior': {'model_calls': result.model_calls, 'broker_calls': result.tool_calls},
                'events': [{'kind': e['kind'], 'body': e['body']} for e in Journal.read(output / 'events.jsonl')
                           if e['kind'] in ('model_reply', 'policy_result', 'policy_error', 'policy_execution_failure',
                                            'investigator_observation', 'investigator_finish')]}
    write_json(output / 'feedback.json', feedback)
    write_json(output / 'result.json', {'attribution': feedback['test_attribution'],
                                       'result': asdict(result), 'live_model': False})
    print(json.dumps(asdict(result)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    smoke(args.candidate, args.output)
