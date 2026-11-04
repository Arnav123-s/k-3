"""Real sandbox lifecycle exercise with an explicitly authored policy and device."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.store import Journal
from kuttaka_runtime.discovery import DiscoveryWorkspace
from kuttaka_runtime.goals import GoalLedger
from kuttaka_runtime.execution import PythonExecutor, preflight
from kuttaka_runtime.investigators import run_policy, InvestigatorBudget, investigator_payload


class NoModel:
    def propose(self, context):
        raise AssertionError('this fixture must make zero model calls')


def goal_matches_clarification(goals, answer):
    """Fixture-only semantic check; never treat an arbitrary user ID as consent."""
    snapshot = goals.status({})
    interpretation = snapshot['interpretations'].get(answer.get('goal'), {})
    eid = answer.get('user_evidence')
    signal = snapshot['signals'].get(eid, {})
    return (interpretation.get('description') == 'Predict device readings'
            and interpretation.get('success_criteria') == ['checked predictions on new inputs']
            and eid in interpretation.get('based_on', [])
            and signal.get('source') == 'user'
            and signal.get('content') == {
                'text': 'I need to predict readings on new inputs, not maximize the reading.',
                'attribution': 'scripted user fixture'})


def run(output):
    if not preflight()['ready']:
        raise RuntimeError('pinned sandbox unavailable')
    output.mkdir(parents=True, exist_ok=False)
    goal = 'Help me understand this device well enough to use it.'
    executor = PythonExecutor(timeout=10)
    policy = Path(__file__).with_name('policy.py').read_text()
    seen = []
    checker_attempts = []
    def reading(value):
        """Input {x: integer in [0,10]}. Return {y: integer} from a deterministic device."""
        if not isinstance(value, dict) or set(value) != {'x'} or type(value['x']) is not int or not 0 <= value['x'] <= 10:
            raise ValueError('invalid reading input')
        seen.append(value['x'])
        return {'y': min(2*value['x'], 6)}
    with Journal(output/'events.jsonl') as journal, ArtifactMemory(output/'memory') as memory:
        goals = GoalLedger(goal, journal)
        workspace = DiscoveryWorkspace(memory, journal, executor, {'reading': reading},
            context={'tool_contract': 'authored deterministic device with bounded integer input'},
            max_observations=8, max_executions=32)
        def clarify_purpose(value):
            """Input {}. Return the authored user-fixture clarification; no external user message is sent."""
            if value != {}:
                raise ValueError('empty input required')
            text = 'I need to predict readings on new inputs, not maximize the reading.'
            eid = goals.record_signal({'text': text, 'attribution': 'scripted user fixture'}, source='user')
            return {'text': text, 'evidence': eid}
        tools = {**workspace.tools(), **goals.tools(), 'clarify_purpose': clarify_purpose}
        def checker(original, answer):
            if original != goal or not isinstance(answer, dict) or workspace.active != answer.get('representation'):
                return False
            if not goal_matches_clarification(goals, answer):
                return False
            goals.authorize(answer['goal'], user_evidence=answer['user_evidence'])
            # Independent extra development inputs. The fixture's host is not a
            # benchmark evaluator and these authored checks do not establish transfer.
            source = memory.get(answer['representation'])['body']['source']
            from kuttaka_runtime.programs import _strict_json_output
            for x in (0, 6, 10):
                checker_attempts.append(x)
                journal.append('discovery_development_check_attempt', {'representation': answer['representation'], 'input': {'x': x}})
                receipt = executor({'source': source, 'input': {'tool': 'reading', 'input': {'x': x}}})
                journal.append('discovery_development_check_receipt', {'input': {'x': x}, 'receipt': receipt})
                if receipt['status'] != 'completed' or receipt['exit_code'] != 0:
                    return False
                if _strict_json_output(receipt['output']) != {'y': min(2*x, 6)}:
                    return False
            return True
        pid = memory.put(investigator_payload(policy, name='engineered discovery fixture',
                          description='Authored lifecycle test, not a model-generated investigator'))
        result = run_policy(goal, pid, NoModel(), tools, checker, journal, executor,
            InvestigatorBudget(model_calls=0, tool_calls=24, policy_executions=24, max_steps=24), memory=memory)
        report = {'result': asdict(result), 'policy_artifact': pid,
                  'observation_inputs': seen, 'prediction_executions': workspace.executions,
                  'goal_ledger': goals.status({}), 'active': workspace.active,
                  'checker_extra_executions': len(checker_attempts),
                  'checker_inputs': checker_attempts,
                  'attribution': 'all candidate/policy/device/clarification code authored; real Docker, no live LLM discovery',
                  'implementation': {name: hashlib.sha256(Path('kuttaka_runtime', name).read_bytes()).hexdigest()
                                     for name in ('discovery.py', 'goals.py', 'investigators.py')}}
    (output/'results.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'status': result.status, 'reason': result.reason, 'policy_executions': result.policy_executions,
                      'prediction_executions': report['prediction_executions'], 'observations': seen}))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
