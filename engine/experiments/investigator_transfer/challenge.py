"""Disclosed policy-contract challenge; not live task or transfer evidence."""

import argparse
import json
from pathlib import Path

from kuttaka_runtime.execution import PythonExecutor
from kuttaka_runtime.programs import _strict_json_output
from .acquire import write_json


def challenge(candidate, output):
    output.mkdir(parents=True, exist_ok=False)
    code = json.loads(candidate.read_text())
    action = {'kind': 'experiment', 'tool': 'read_value', 'input': {},
              'predictions': [{'hypothesis': 'stable measurement', 'outcome': {'value': 7}}]}
    base = {'goal': 'Investigate the supplied measurements without discarding evidence.',
            'tools': {'read_value': 'Input {}. Returns {value: integer}.'},
            'remaining': {'model_calls': 2, 'tool_calls': 2, 'policy_executions': 8, 'steps': 8},
            'state': None, 'representation': None, 'model_reply': None}
    records = []
    for second in (7, 8):
        context = dict(base, history=[{'action': action, 'observation': {'value': 7}},
                                     {'action': action, 'observation': {'value': second}}])
        receipt = PythonExecutor(timeout=30)({'source': code['source'], 'input': context})
        parsed = None
        if receipt['status'] == 'completed' and receipt.get('exit_code') == 0:
            try:
                parsed = _strict_json_output(receipt['output'])
            except ValueError:
                pass
        records.append({'input': context, 'receipt': receipt, 'policy_output': parsed})
    feedback = {'candidate': code, 'attribution': 'Authored protocol counterexample; actual Docker execution, no live model or target task.',
                'requirement': 'Contradictory observations must not be erased and then treated as consistent. These two histories differ in whether the same input produced conflicting outputs.',
                'records': records,
                'identical_policy_outputs': records[0]['policy_output'] == records[1]['policy_output']}
    conflict_output = records[1]['policy_output'] or {}
    revision = conflict_output.get('action', {})
    if revision.get('kind') == 'revise':
        context = dict(records[1]['input'], state=conflict_output.get('state'),
                       representation=revision.get('representation'))
        context['history'] = context['history'] + [
            {'action': revision, 'observation': {'status': 'representation_revised'}}]
        receipt = PythonExecutor(timeout=30)({'source': code['source'], 'input': context})
        parsed = None
        if receipt.get('status') == 'completed' and receipt.get('exit_code') == 0:
            try:
                parsed = _strict_json_output(receipt['output'])
            except ValueError:
                pass
        feedback['after_revision'] = {'input': context, 'receipt': receipt, 'policy_output': parsed}
        feedback['revision_repeated_without_new_evidence'] = parsed == conflict_output
    write_json(output / 'feedback.json', feedback)
    print(json.dumps({'identical_policy_outputs': feedback['identical_policy_outputs'],
                      'revision_repeated_without_new_evidence': feedback.get('revision_repeated_without_new_evidence'),
                      'execution_statuses': [r['receipt']['status'] for r in records]}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    challenge(args.candidate, args.output)
